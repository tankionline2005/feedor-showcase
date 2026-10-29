"""«Триггер-слова»: a person's words to watch for in the project's channels — no AI, only the words.

A trigger has a few words or phrases («БПЛА», «беспилотник», «сбит дрон»); a post with any of them is
a hit: the trigger's own feed, and, if the person wants, the bot telling them (or one of the owner's
groups) at once: «Триггер-слово «БПЛА»», then the post.

Matching: «разные формы» compares the words' dictionary forms (pymorphy3: «взрывы», «взрыва» are
«взрыв»; «взрывной» is a word of its own); short words, abbreviations and words with digits («ПВО»,
«БПЛА», «Ту-22») are taken as they are, the dictionary only guesses at them. «точное совпадение» is the
whole word as written, in any case. A phrase is its words one after another.

Every few seconds the worker reads the posts that came: their text at once, the text on their
pictures again once it is read (OCR, seconds later). A new trigger is filled at once with the posts
already collected; the bot tells only of new ones.
"""

import asyncio
import logging
import re
from datetime import timedelta
from functools import lru_cache

from sqlalchemy import delete, func, select

from . import settings_store
from .db import SessionLocal
from .models import Channel, Member, Post, Project, ProjectGroup, Trigger, TriggerHit, TriggerScan, User, utcnow
from .notify import BotError, bot_call, esc
from .originals import group_message, send_original
from .plans import project_plan
from .scope import tracked_channel_ids

log = logging.getLogger(__name__)

WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:-[0-9A-Za-zА-Яа-яЁё]+)*")
SCAN_BATCH = 500  # posts read in one step
OCR_WAIT = timedelta(minutes=2)  # a post whose pictures are still being read is waited on this long at most
REPOST_WAIT = timedelta(seconds=10)  # the bot waits this long for the post's copy, then sends its text
NOTIFY_BATCH = 50
BACKFILL_DAYS = 14  # a new trigger is filled with the posts of the last two weeks
BACKFILL_POSTS = 20_000
TEXT_LIMIT = 3500  # characters of a post the bot sends when its copy is not ready

_analyzer = None


def _morph():
    global _analyzer
    if _analyzer is None:
        import pymorphy3

        _analyzer = pymorphy3.MorphAnalyzer()
    return _analyzer


def person_key(viewer) -> str:
    """Whose triggers: "m<member id>" for a staff member, "u<user id>" for the owner."""
    return f"m{viewer.member_id}" if viewer.member_id else f"u{viewer.user_id}"


def of_person(viewer):
    """The viewer's own triggers in their current project."""
    return (Trigger.project_id == viewer.project_id) & (Trigger.person == person_key(viewer))


def _plain(word: str) -> str:
    return word.lower().replace("ё", "е")


@lru_cache(maxsize=200_000)
def _forms(plain: str) -> frozenset[str]:
    """The word itself and every dictionary form it may be of."""
    return frozenset({plain, *(_plain(p.normal_form) for p in _morph().parse(plain))})


def _literal(word: str) -> bool:
    """An abbreviation, a short word, a word with digits: the dictionary only guesses at them."""
    return len(word) <= 3 or (word.isupper() and len(word) <= 8) or any(ch.isdigit() for ch in word)


Phrase = list[str | frozenset[str]]  # a word as it is written (exact), or the forms it may take


def compile_words(words: list[str], mode: str) -> list[Phrase]:
    phrases: list[Phrase] = []
    for raw in words:
        tokens = WORD.findall(raw)
        if tokens:
            phrases.append([_plain(t) if mode == "exact" or _literal(t) else _forms(_plain(t)) for t in tokens])
    return phrases


def find(phrases: list[Phrase], text: str) -> list[str]:
    """The trigger's words and phrases in the text, as the text has them (each once)."""
    if not text or not phrases:
        return []
    tokens = WORD.findall(text)
    plain = [_plain(t) for t in tokens]
    found: list[str] = []
    for phrase in phrases:
        n = len(phrase)
        for i in range(len(tokens) - n + 1):
            for j, want in enumerate(phrase):
                got = plain[i + j]
                if isinstance(want, str):
                    if got != want:
                        break
                elif got not in want and not (_forms(got) & want):
                    break
            else:
                hit = " ".join(tokens[i:i + n])
                if hit not in found:
                    found.append(hit)
    return found


def post_text(p: Post) -> str:
    return "\n".join(x for x in (p.text, p.extracted_text_from_media) if x)


# --- which triggers read which channel ----------------------------------------------------------


async def _live_triggers(session) -> dict[int, list[tuple[Trigger, list[Phrase]]]]:
    """channel id -> the triggers that read it (of projects with a plan in force)."""
    triggers = (await session.execute(
        select(Trigger, Project).join(Project, Project.id == Trigger.project_id))).all()
    by_channel: dict[int, list[tuple[Trigger, list[Phrase]]]] = {}
    tracked: dict[int, set[int]] = {}
    for trigger, project in triggers:
        if not project_plan(project) or trigger.paused_at is not None:
            continue  # a frozen project's, or over its plan's limit
        if project.id not in tracked:
            tracked[project.id] = set((await session.execute(tracked_channel_ids(project.id))).scalars())
        phrases = compile_words(trigger.words or [], trigger.mode)
        for channel_id in channels_of(trigger, tracked[project.id]):
            by_channel.setdefault(channel_id, []).append((trigger, phrases))
    return by_channel


def channels_of(trigger: Trigger, tracked: set[int]) -> set[int]:
    chosen = set(trigger.channel_ids or [])
    return tracked & chosen if trigger.channels_mode == "only" else tracked - chosen


async def _record(session, by_channel, posts: list[Post], notify: bool) -> int:
    """Hits of these posts not recorded yet; the number of new ones."""
    if not posts:
        return 0
    known = set((await session.execute(
        select(TriggerHit.trigger_id, TriggerHit.post_id).where(TriggerHit.post_id.in_([p.id for p in posts]))
    )).all())
    added = 0
    for p in posts:
        text = post_text(p)
        for trigger, phrases in by_channel.get(p.channel_id, []):
            if (trigger.id, p.id) in known:
                continue
            found = find(phrases, text)
            if found:
                session.add(TriggerHit(trigger_id=trigger.id, post_id=p.id, found=found,
                                       notified=not (notify and trigger.notify)))
                known.add((trigger.id, p.id))
                added += 1
    return added


# --- the worker ----------------------------------------------------------------------------------


async def triggers_step() -> None:
    async with SessionLocal() as s:
        scan = await s.get(TriggerScan, 1)
        newest = await s.scalar(select(func.max(Post.id))) or 0
        if scan is None:
            scan = TriggerScan(id=1, text_post_id=newest, ocr_post_id=newest)  # from now on
            s.add(scan)
            await s.commit()
            return
        by_channel = await _live_triggers(s)
        if not by_channel:
            scan.text_post_id = scan.ocr_post_id = newest  # nobody watches: nothing to catch up on later
            await s.commit()
            return

        # the posts that came: their text (and the text on their pictures, if it is read already)
        fresh = (await s.execute(select(Post).where(Post.id > scan.text_post_id, Post.deleted_at.is_(None))
                                 .order_by(Post.id).limit(SCAN_BATCH))).scalars().all()
        added = await _record(s, by_channel, list(fresh), notify=True)
        if fresh:
            scan.text_post_id = fresh[-1].id

        # the text on their pictures, once read (a post still being read holds the line, for a while)
        read: list[Post] = []
        cursor = scan.ocr_post_id
        for p in (await s.execute(select(Post).where(Post.id > scan.ocr_post_id, Post.id <= scan.text_post_id)
                                  .order_by(Post.id).limit(SCAN_BATCH))).scalars():
            if p.ocr_status == "pending" and p.created_at > utcnow() - OCR_WAIT:
                break
            if p.extracted_text_from_media and p.deleted_at is None:
                read.append(p)
            cursor = p.id
        added += await _record(s, by_channel, read, notify=True)
        scan.ocr_post_id = cursor
        await s.commit()
    if added:
        log.info("triggers: %d new hits", added)
    await _tell()


async def _tell() -> None:
    """The bot tells of the new hits: «Триггер-слово «…»», then the post (its copy, or its text when
    the copy is not ready within a few seconds)."""
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(TriggerHit, Trigger, Post, Channel)
            .join(Trigger, Trigger.id == TriggerHit.trigger_id).join(Post, Post.id == TriggerHit.post_id)
            .join(Channel, Channel.id == Post.channel_id)
            .where(~TriggerHit.notified).order_by(TriggerHit.id).limit(NOTIFY_BATCH)
        )).all()
        if not rows:
            return
        token = str((await settings_store.get_all(s))["notify_bot_token"])
        for hit, trigger, post, channel in rows:
            copy = group_message(post.repost_url) if post.repost_status == "done" else None
            if copy is None and post.repost_status == "pending" and hit.created_at > utcnow() - REPOST_WAIT:
                continue  # its copy is on its way
            hit.notified = True
            if not token or not trigger.notify:
                continue
            for chat in await _recipients(s, trigger):
                try:
                    await _send(token, chat, trigger, hit, post, channel, copy)
                except BotError as exc:
                    log.info("trigger %s to %s not sent: %s", trigger.id, chat, exc)
        await s.commit()


async def _recipients(session, trigger: Trigger) -> list[int]:
    project = await session.get(Project, trigger.project_id)
    owner = await session.get(User, project.owner_id) if project else None
    if owner is None or owner.blocked:
        return []
    chats: list[int] = []
    if trigger.notify_dm:
        if trigger.member_id:
            member = await session.get(Member, trigger.member_id)
            if member and member.tg_user_id:
                chats.append(member.tg_user_id)
        else:
            chats.append(owner.tg_user_id)
    # the project's group, while it is still among its groups
    if trigger.notify_chat_id and await session.get(ProjectGroup, (trigger.project_id, trigger.notify_chat_id)):
        chats.append(trigger.notify_chat_id)
    return chats


def _link(post: Post, channel: Channel) -> str | None:
    if channel.username:
        return f"https://t.me/{channel.username}/{post.msg_id}"
    return f"https://t.me/c/{channel.tg_id}/{post.msg_id}" if channel.tg_id else None


def prefix(trigger: Trigger, found: list[str], channel: Channel) -> str:
    words = ", ".join(f"«{esc(w)}»" for w in found[:3])
    return (f"🔔 <b>Триггер-слово «{esc(trigger.name)}»</b>\n"
            f"{words} — в «{esc(channel.title or channel.username or channel.link)}»")


async def _send(token: str, chat: int, trigger: Trigger, hit: TriggerHit, post: Post, channel: Channel,
                copy: tuple | None) -> None:
    head = prefix(trigger, hit.found or [], channel)
    if copy is not None:
        await bot_call(token, "sendMessage", chat_id=chat, text=head, parse_mode="HTML",
                       link_preview_options={"is_disabled": True})
        await send_original(token, chat, copy[0], copy[1], len(post.album_ids or [post.msg_id]), post.repost_copy)
        return
    # no copy to send: its text and the link, in one message
    text = post_text(post)
    body = esc(text[:TEXT_LIMIT] + ("…" if len(text) > TEXT_LIMIT else ""))
    link = _link(post, channel)
    await bot_call(token, "sendMessage", chat_id=chat, parse_mode="HTML", link_preview_options={"is_disabled": True},
                   text=head + (f"\n\n{body}" if body else "") + (f'\n\n<a href="{link}">Открыть пост</a>' if link else ""))


# --- a new or changed trigger: its feed from the posts already there -------------------------------


async def backfill(trigger_id: int) -> int:
    """The trigger's feed anew, from the posts of the last two weeks; the bot tells of none of them."""
    async with SessionLocal() as s:
        trigger = await s.get(Trigger, trigger_id)
        if trigger is None:
            return 0
        tracked = set((await s.execute(tracked_channel_ids(trigger.project_id))).scalars())
        channels = channels_of(trigger, tracked)
        await s.execute(delete(TriggerHit).where(TriggerHit.trigger_id == trigger.id))
        rows = (await s.execute(
            select(Post.id, Post.text, Post.extracted_text_from_media)
            .where(Post.channel_id.in_(channels or [0]), Post.deleted_at.is_(None),
                   Post.posted_at >= utcnow() - timedelta(days=BACKFILL_DAYS))
            .order_by(Post.id.desc()).limit(BACKFILL_POSTS)
        )).all()
        phrases = compile_words(trigger.words or [], trigger.mode)

        def match() -> list[tuple[int, list[str]]]:
            out = []
            for pid, text, ocr in rows:
                found = find(phrases, "\n".join(x for x in (text, ocr) if x))
                if found:
                    out.append((pid, found))
            return out

        hits = await asyncio.to_thread(match)
        # the worker may have caught a new post meanwhile: once is enough
        known = set((await s.execute(select(TriggerHit.post_id).where(TriggerHit.trigger_id == trigger.id))).scalars())
        for pid, found in hits:
            if pid not in known:
                s.add(TriggerHit(trigger_id=trigger.id, post_id=pid, found=found, notified=True))
        await s.flush()
        # what was there before is not new to the person
        trigger.seen_hit_id = await s.scalar(select(func.max(TriggerHit.id)).where(TriggerHit.trigger_id == trigger.id)) or 0
        await s.commit()
        return len(hits)
