"""Posts the AI writes in a chat for the project's channel: «предложения постов».

The AI gives a post with its suggest_post tool (chat.py) — or the person makes an answer of the AI a post
(«Оформить как пост»). The chat shows it as a post: its text in Telegram's formatting, its pictures and videos;
the person edits it and has the bot send it to their own Telegram, ready to publish.

Its pictures and videos come from the posts its facts come from (its sources, the most important first): every
picture of theirs, the same picture reposted by several channels once. The server picks a few at first (the
first of the most important sources, then the next ones), the person changes the choice. Some cannot go with
it, and say so: a channel that forbids copying its posts (its copy in our group is our own text, and its
pictures are not taken around that), a «кружок» or a GIF among other pictures (Telegram allows neither in an
album), a video of a private channel that is not in our group.

The bot sends them in their original quality without downloading them anywhere: a post's copy in the
aggregator group has them, and a copy forwarded there for a moment gives the bot their file_id (kept, so it is
done once). A photo not in the group is read by the worker in full size; a video not there is passed to
Telegram by its link on Telegram's own servers (tg_web.py; Telegram takes up to 20 MB so). Videos never go
through our Telegram accounts.
"""

import logging
from dataclasses import dataclass
import re
from datetime import timedelta

from sqlalchemy import delete, func, select, update

from . import settings_store, tg_html, tg_web
from .db import SessionLocal
from .models import (BotFile, Channel, ChatPost, ChatPostSource, ChatThread, Member, Post, Project, ProjectChannel,
                     TopicPost, User, utcnow)
from .notify import BotError, bot_call, bot_upload
from .originals import full_photo, group_message
from .services import media_position, media_slots, picture_hash, preview_path, same_picture, video_url

log = logging.getLogger(__name__)

AUTO_MEDIA = 4  # pictures the server picks at first; the person may take up to MAX_MEDIA
MAX_MEDIA = 10  # Telegram's album
MAX_SOURCES = 30

WHY = {
    "protected": "Канал запретил копировать свои материалы — бот не сможет это приложить",
    "private_video": "Этого видео нет в нашей группе, а канал закрытый — бот не сможет его приложить",
}


# --- the pictures to choose from ----------------------------------------------------------------------


def in_group(post: Post) -> tuple[int | str, int] | None:
    """The post's copy in the aggregator group, with its pictures: (the group, its first message)."""
    if post.repost_status != "done" or post.repost_copy:
        return None
    return group_message(post.repost_url)


def _kind(post: Post, kind: str) -> str:
    return "gif" if kind == "video" and post.media_type == "gif" else kind


async def sources_of(session, chat_post_id: int) -> list[tuple[Post, Channel]]:
    return list((await session.execute(
        select(Post, Channel).join(Channel, Channel.id == Post.channel_id)
        .join(ChatPostSource, ChatPostSource.post_id == Post.id)
        .where(ChatPostSource.chat_post_id == chat_post_id).order_by(ChatPostSource.rank)
    )).all())


def candidates(sources: list[tuple[Post, Channel]]) -> list[dict]:
    """Every picture and video of the sources, in their order, the same picture once."""
    out: list[dict] = []
    hashes: list[int] = []
    for post, ch in sources:
        for slot, kind in media_slots(post):
            path = preview_path(post.id, slot)
            if not path.is_file():
                continue
            h = picture_hash(post.id, slot, path.stat().st_size)
            if h is not None and any(same_picture(h, other) for other in hashes):
                continue
            if h is not None:
                hashes.append(h)
            kind = _kind(post, kind)
            why = None
            if post.repost_copy:
                why = WHY["protected"]
            elif kind in ("video", "gif") and in_group(post) is None and not ch.username:
                why = WHY["private_video"]
            out.append({
                "post_id": post.id, "slot": slot, "kind": kind,
                "url": f"/api/media/{post.id}" + (f"?i={slot}" if slot else ""),
                "full_url": f"/api/media/{post.id}/full?i={slot}" if kind == "photo" else None,
                "video_url": video_url(post, slot, "video" if kind == "gif" else kind),
                "why": why,
            })
    return out


def auto_pick(cands: list[dict]) -> list[dict]:
    """The first picks: the first picture of each source by importance, then the second ones… (AUTO_MEDIA);
    only what can go, and a GIF only alone (Telegram puts none in an album)."""
    usable = [c for c in cands if not c["why"]]
    by_post: dict[int, list[dict]] = {}
    for c in usable:
        by_post.setdefault(c["post_id"], []).append(c)
    rounds: list[dict] = []
    depth = max((len(v) for v in by_post.values()), default=0)
    for i in range(depth):
        rounds += [v[i] for v in by_post.values() if i < len(v)]
    if rounds and rounds[0]["kind"] == "gif":
        return [{"post": rounds[0]["post_id"], "slot": rounds[0]["slot"]}]
    return [{"post": c["post_id"], "slot": c["slot"]} for c in rounds if c["kind"] != "gif"][:AUTO_MEDIA]


def chosen(post: ChatPost, cands: list[dict]) -> list[dict]:
    """The post's pictures as they are shown, in its order (a source gone since: its pictures go too)."""
    by_key = {(c["post_id"], c["slot"]): c for c in cands}
    return [by_key[(m["post"], m["slot"])] for m in post.media or [] if (m["post"], m["slot"]) in by_key]


def check_media(media: list, cands: list[dict]) -> list[dict] | str:
    """The person's choice of pictures, as kept — or what is wrong with it, for people."""
    by_key = {(c["post_id"], c["slot"]): c for c in cands}
    out: list[dict] = []
    for m in media:
        key = (m.get("post"), m.get("slot")) if isinstance(m, dict) else None
        c = by_key.get(key)
        if c is None:
            return "Такого фото или видео нет среди источников поста"
        if c["why"]:
            return c["why"]
        if {"post": key[0], "slot": key[1]} not in out:
            out.append({"post": key[0], "slot": key[1]})
    if len(out) > MAX_MEDIA:
        return f"В посте Telegram — не больше {MAX_MEDIA} фото и видео"
    if len(out) > 1 and any(by_key[(m["post"], m["slot"])]["kind"] == "gif" for m in out):
        return "GIF в Telegram отправляется только один, без других фото и видео"
    return out


# --- making one ------------------------------------------------------------------------------------------


async def _project_posts(session, project_id: int, ids: list) -> list[int]:
    """The ids that are posts of the project's channels, in their order, each once."""
    wanted = [i for i in dict.fromkeys(ids) if isinstance(i, int) and not isinstance(i, bool)][:MAX_SOURCES]
    if not wanted:
        return []
    found = set((await session.execute(
        select(Post.id).where(Post.id.in_(wanted), Post.channel_id.in_(
            select(ProjectChannel.channel_id).where(ProjectChannel.project_id == project_id)))
    )).scalars())
    return [i for i in wanted if i in found]


async def _topic_posts(session, topic_id: int | None) -> list[int]:
    if topic_id is None:
        return []
    return list((await session.execute(
        select(Post.id).join(TopicPost, TopicPost.post_id == Post.id).where(TopicPost.topic_id == topic_id)
        .order_by(Post.views.desc()).limit(MAX_SOURCES)
    )).scalars())


async def create(thread: ChatThread, text: str, source_ids: list, *, tool_call_id: str | None = None,
                 message_id: int | None = None, strict: bool = True) -> ChatPost | str:
    """A post for the chat; or why not, for the AI to fix. Too long for a caption under pictures: `strict` — the AI
    is asked for a shorter one (pictures matter); else it goes without pictures (chosen once it is shorter)."""
    return await _create(thread.project_id, text, source_ids, thread_id=thread.id, topic_id=thread.topic_id,
                         tool_call_id=tool_call_id, message_id=message_id, strict=strict, in_chat=True)


async def _picked(s, project_id: int, topic_id: int | None, text: str, source_ids: list,
                  strict: bool) -> tuple[list[int], list[dict]] | str:
    """(the post's sources, the pictures chosen among theirs); or why the text cannot go, for the AI."""
    ids = await _project_posts(s, project_id, source_ids)
    # a topic's posts (a chat begun from one, or a post for one), after the ones the AI named
    ids += [i for i in await _topic_posts(s, topic_id) if i not in ids]
    ids = ids[:MAX_SOURCES]
    rows = (await s.execute(select(Post, Channel).join(Channel, Channel.id == Post.channel_id)
                            .where(Post.id.in_(ids)))).all() if ids else []
    order = {i: n for n, i in enumerate(ids)}
    sources = sorted(rows, key=lambda r: order[r[0].id])
    media = auto_pick(candidates(sources))
    size, limit = tg_html.length(text), tg_html.limit(bool(media))
    if size > limit:
        if strict and media and size <= tg_html.TEXT_LIMIT:
            return (f"too long: {size} characters, and a post with pictures takes at most {limit}. Call "
                    f"suggest_post again with a shorter post (pictures matter for a channel post)")
        if size > tg_html.TEXT_LIMIT:
            return f"too long: {size} characters, a Telegram post takes at most {tg_html.TEXT_LIMIT}"
        media = []
    return ids, media


async def rewrite_for_topic(post_id: int, text: str, source_ids: list) -> ChatPost | str:
    """A post the AI wrote for a topic, written anew after big news in it (nobody edited it yet): the new text,
    its sources and pictures; its schedule stays."""
    text = tg_html.clean(text)
    if not tg_html.plain(text).strip():
        return "the post is empty"
    async with SessionLocal() as s:
        post = await s.get(ChatPost, post_id)
        picked = await _picked(s, post.project_id, post.topic_id, text, source_ids, strict=False)
        if isinstance(picked, str):
            return picked
        ids, media = picked
        post.ai_text = post.text = text
        post.media = media
        await s.execute(delete(ChatPostSource).where(ChatPostSource.chat_post_id == post.id))
        s.add_all(ChatPostSource(chat_post_id=post.id, post_id=i, rank=n) for n, i in enumerate(ids))
        await s.commit()
        return post


async def create_for_topic(project_id: int, topic_id: int, text: str, source_ids: list) -> ChatPost | str:
    """A post the AI wrote by itself for a new topic («Публикации»): no chat; too long for pictures, without them."""
    return await _create(project_id, text, source_ids, topic_id=topic_id, strict=False)


async def create_written(project_id: int, text: str, source_ids: list, by: str) -> ChatPost | str:
    """A post its author wrote themselves (the API, the MCP connector): to «Публикации», with the pictures of the
    posts they name; too long for pictures, without them."""
    return await _create(project_id, text, source_ids, strict=False, written_by=by)


async def _create(project_id: int, text: str, source_ids: list, *, thread_id: int | None = None,
                  topic_id: int | None = None, tool_call_id: str | None = None, message_id: int | None = None,
                  strict: bool = True, in_chat: bool = False, written_by: str | None = None) -> ChatPost | str:
    text = tg_html.clean(text)
    if not tg_html.plain(text).strip():
        return "the post is empty"
    async with SessionLocal() as s:
        picked = await _picked(s, project_id, topic_id, text, source_ids, strict)
        if isinstance(picked, str):
            return picked
        ids, media = picked
        post = ChatPost(project_id=project_id, thread_id=thread_id, topic_id=None if in_chat else topic_id,
                        tool_call_id=tool_call_id, message_id=message_id, ai_text=text, text=text, media=media,
                        written_by=written_by)
        s.add(post)
        await s.flush()
        s.add_all(ChatPostSource(chat_post_id=post.id, post_id=i, rank=n) for n, i in enumerate(ids))
        await s.commit()
        return post


_TME = re.compile(r"https?://t\.me/(?:c/(\d+)|([A-Za-z0-9_]{4,32}))/(\d+)")


async def linked_posts(project_id: int, text: str) -> list[int]:
    """The project's posts an answer links to (t.me links), in their order: an answer made a post by hand gets
    its pictures from them."""
    async with SessionLocal() as s:
        out: list[int] = []
        for internal, username, msg in _TME.findall(text or ""):
            cond = Channel.tg_id == int(internal) if internal else func.lower(Channel.username) == username.lower()
            pid = await s.scalar(select(Post.id).join(Channel, Channel.id == Post.channel_id).where(
                cond, Post.msg_id == int(msg), Channel.id.in_(
                    select(ProjectChannel.channel_id).where(ProjectChannel.project_id == project_id))))
            if pid is not None and pid not in out:
                out.append(pid)
        return out


# --- to the person's Telegram -------------------------------------------------------------------------------


async def _bot_file(token: str, post: Post, slot: int, group: tuple[int | str, int]) -> tuple[str, str] | None:
    """(kind, file_id) of the post's picture as the bot knows it: from its copy in the group, once."""
    async with SessionLocal() as s:
        known = await s.get(BotFile, (post.id, slot))
        if known is not None:
            return known.kind, known.file_id
    pos = media_position(post, slot)
    if pos is None:
        return None
    chat, first = group
    try:
        sent = await bot_call(token, "forwardMessage", chat_id=chat, from_chat_id=chat, message_id=first + pos,
                              disable_notification=True)
    except BotError as exc:
        log.info("post %s/%s: no copy in the group to take: %s", post.id, slot, exc)
        return None
    try:
        await bot_call(token, "deleteMessage", chat_id=chat, message_id=sent["message_id"])
    except BotError as exc:
        log.warning("post %s/%s: the forwarded copy stays in the group: %s", post.id, slot, exc)
    if sent.get("photo"):
        found = ("photo", sent["photo"][-1]["file_id"])
    elif sent.get("animation"):
        found = ("animation", sent["animation"]["file_id"])
    elif sent.get("video"):
        found = ("video", sent["video"]["file_id"])
    elif sent.get("video_note"):
        found = ("video_note", sent["video_note"]["file_id"])
    else:
        return None
    async with SessionLocal() as s:
        if await s.get(BotFile, (post.id, slot)) is None:
            s.add(BotFile(post_id=post.id, slot=slot, kind=found[0], file_id=found[1]))
            await s.commit()
    return found


async def _item(token: str, post: Post, ch: Channel, slot: int, kind: str, n: int,
                files: dict, dry: bool = False) -> tuple[dict | None, str | None]:
    """One picture or video as a Bot API InputMedia (an upload goes into `files`); or why it cannot go."""
    if post.repost_copy:
        return None, "канал запретил копирование"
    group = in_group(post)
    if group is not None:
        found = await _bot_file(token, post, slot, group)
        if found is not None:
            if found[0] == "video_note":
                return None, "кружок нельзя приложить к посту"
            return {"type": found[0], "media": found[1]}, None
    if kind == "photo":
        if dry:  # only what can go is asked: the photo itself is read when the post goes
            return {"type": "photo", "media": "dry"}, None
        data = await full_photo(post.id, slot)
        if data is None:
            return None, "фото не удалось загрузить"
        files[f"p{n}"] = (f"photo{n}.jpg", data, "image/jpeg")
        return {"type": "photo", "media": f"attach://p{n}"}, None
    pos = media_position(post, slot)
    ids = post.album_ids or [post.msg_id]
    if not ch.username or pos is None or pos >= len(ids):
        return None, "видео недоступно боту"
    video = await tg_web.video(ch.username, ids[pos])
    if not video.get("url"):
        return None, "видео недоступно боту"
    if video["kind"] == "round":
        return None, "кружок нельзя приложить к посту"
    # Telegram fetches it by the link itself: up to 20 MB
    return {"type": "animation" if video["kind"] == "gif" else "video", "media": video["url"], "link": True}, None


_SINGLE = {"photo": ("sendPhoto", "photo"), "video": ("sendVideo", "video"), "animation": ("sendAnimation", "animation")}


@dataclass
class Delivered:
    """A post as it went out: its messages, the one with its text and how it carries it (text | caption)."""

    ids: list[int]
    text_id: int | None
    kind: str | None

    @property
    def first(self) -> int:
        return self.ids[0]


async def _deliver(token: str, chat_id: int, text: str, items: list[dict], files: dict) -> Delivered:
    """Sends the post."""
    caption_ok = tg_html.length(text) <= tg_html.CAPTION_LIMIT
    if not items:
        sent = await bot_call(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")
        return Delivered([sent["message_id"]], sent["message_id"], "text")
    clean = [{k: v for k, v in i.items() if k != "link"} for i in items]
    used = {k: v for k, v in files.items() if any(i["media"] == f"attach://{k}" for i in clean)}

    async def call(method: str, params: dict):
        if used:
            return await bot_upload(token, method, params, used)
        return await bot_call(token, method, http_timeout=120, **params)

    caption = {"caption": text, "parse_mode": "HTML"} if caption_ok and text else {}
    if len(clean) == 1:
        method, field = _SINGLE[clean[0]["type"]]
        sent = [await call(method, {"chat_id": chat_id, field: clean[0]["media"], **caption,
                                    **({"supports_streaming": True} if field == "video" else {})})]
    else:
        clean[0] = {**clean[0], **caption}
        sent = await call("sendMediaGroup", {"chat_id": chat_id, "media": clean})
    ids = [m["message_id"] for m in sent]
    if caption:
        return Delivered(ids, ids[0], "caption")
    if text:
        extra = await bot_call(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML")
        return Delivered([*ids, extra["message_id"]], extra["message_id"], "text")
    return Delivered(ids, None, None)


async def _prepare(post_id: int, dry: bool = False) -> tuple[ChatPost, str, list[dict], dict, list[str]]:
    """(post, the bot's token, its pictures and videos as Bot API InputMedia, uploads, what of them cannot go — for
    people). `dry`: only what cannot go matters, nothing is downloaded."""
    async with SessionLocal() as s:
        post = await s.get(ChatPost, post_id)
        token = str((await settings_store.get_all(s))["notify_bot_token"])
        sources = {p.id: (p, ch) for p, ch in await sources_of(s, post_id)}
    if not token:
        raise BotError("бот не настроен")
    items: list[dict] = []
    files: dict = {}
    skipped: list[str] = []
    cands = {(c["post_id"], c["slot"]): c for c in candidates(list(sources.values()))}
    for n, m in enumerate(post.media or []):
        c = cands.get((m["post"], m["slot"]))
        if c is None:
            skipped.append("пост-источник удалён")
            continue
        p, ch = sources[m["post"]]
        item, why = await _item(token, p, ch, m["slot"], c["kind"], n, files, dry)
        if item is None:
            skipped.append(why or "не удалось приложить")
        else:
            items.append(item)
    # Telegram puts no GIF in an album: a GIF goes alone
    if len(items) > 1 and any(i["type"] == "animation" for i in items):
        skipped += ["GIF нельзя приложить вместе с другими фото и видео"] * sum(i["type"] == "animation" for i in items)
        items = [i for i in items if i["type"] != "animation"]
    if post.cover:
        # «Обложка»: made now, the first picture; no GIF goes with it
        if any(i["type"] == "animation" for i in items):
            skipped += ["GIF нельзя приложить вместе с обложкой"]
            items = [i for i in items if i["type"] != "animation"]
        if not dry:
            from . import covers

            async with SessionLocal() as s:
                files["cover"] = ("cover.jpg", await covers.for_post(s, post), "image/jpeg")
        items = [{"type": "photo", "media": "attach://cover" if not dry else "dry"}, *items][:MAX_MEDIA]
    return post, token, items, files, skipped


TOO_BIG = "видео больше 20 МБ"


async def send(post_id: int, chat_id: int) -> list[str]:
    """Sends the post to `chat_id` from the bot; what of its pictures could not go, for people (it goes without
    them)."""
    post, token, items, files, skipped = await _prepare(post_id)
    try:
        await _deliver(token, chat_id, post.text, items, files)
    except BotError as exc:
        # a video Telegram could not take by its link (bigger than 20 MB): without it
        if not any(i.get("link") for i in items) or "blocked" in str(exc).lower():
            raise
        log.info("chat post %s: a video by link failed (%s), sent without it", post_id, exc)
        skipped += [TOO_BIG] * sum(bool(i.get("link")) for i in items)
        await _deliver(token, chat_id, post.text, [i for i in items if not i.get("link")], files)
    async with SessionLocal() as s:
        (await s.get(ChatPost, post_id)).sent_at = utcnow()
        await s.commit()
    return skipped


# --- to the project's channel ------------------------------------------------------------------------------------


class ChannelNotReady(Exception):
    """Why the bot cannot publish in the channel: missing (not in it) | rights (may not post)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Target:
    """The project's channel as the bot addresses it: its id, or its @username."""

    chat_id: int | str
    username: str | None


async def publish_target(session, project: Project) -> Target | None:
    """Where the bot publishes the project's posts. A channel chosen among those with the bot has its id; one chosen
    by its link (any channel — the bot need not be there) is the project's own tracked channel: its id once our
    account has read it, or its @username. None: no channel chosen, or not known yet."""
    if project.channel_chat_id is not None:
        return Target(project.channel_chat_id, project.channel_username)
    ch = await session.scalar(select(Channel).join(ProjectChannel, ProjectChannel.channel_id == Channel.id).where(
        ProjectChannel.project_id == project.id, ProjectChannel.own))
    if ch is None:
        return None
    username = ch.username or project.channel_username
    if ch.tg_id:
        return Target(int(f"-100{ch.tg_id}"), username)
    return Target(f"@{username}", username) if username else None


async def check_channel(token: str, chat_id: int | str) -> None:
    """The bot is an admin of the channel allowed to post there — asked of Telegram at the moment, rights change."""
    try:
        member = await bot_call(token, "getChatMember", chat_id=chat_id, user_id=int(token.split(":")[0]))
    except BotError as exc:
        log.info("the bot in channel %s: %s", chat_id, exc)
        raise ChannelNotReady("missing") from exc
    status = member.get("status")
    if status == "creator" or (status == "administrator" and member.get("can_post_messages")):
        return
    raise ChannelNotReady("rights" if status == "administrator" else "missing")


NOT_READY = {
    "missing": "Бот @{bot} не добавлен в канал «{title}». Добавьте его администратором канала с правом «Публикация "
               "сообщений».",
    "rights": "У бота @{bot} нет права публиковать сообщения в канале «{title}». Откройте в Telegram управление "
              "каналом → «Администраторы» → @{bot} и включите «Публикация сообщений».",
}


def not_ready_text(reason: str, bot: str, title: str, staff: bool) -> str:
    text = NOT_READY[reason].format(bot=bot, title=title)
    return text + " Попросите об этом владельца проекта." if staff else text


def channel_title(project: Project) -> str:
    return project.channel_title or (f"@{project.channel_username}" if project.channel_username else "канал проекта")


def message_url(chat_id: int | str, username: str | None, message_id: int) -> str:
    if username:
        return f"https://t.me/{username}/{message_id}"
    return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{message_id}"


async def publish(post_id: int, chat_id: int | str, username: str | None, *, partial: bool,
                  by: str | None = None) -> dict:
    """Publishes the post in the project's channel from the bot. What of its pictures cannot go is asked about
    first ({published: False, skipped}): only `partial` publishes without them. Then {published: True, url,
    skipped}."""
    async with SessionLocal() as s:
        token = str((await settings_store.get_all(s))["notify_bot_token"])
    if not token:
        raise BotError("бот не настроен")
    await check_channel(token, chat_id)  # before anything is asked of Telegram for the pictures
    post, token, items, files, skipped = await _prepare(post_id)
    if skipped and not partial:
        return {"published": False, "skipped": skipped}
    try:
        went = await _deliver(token, chat_id, post.text, items, files)
    except BotError as exc:
        # nothing went out: a video Telegram could not take by its link (bigger than 20 MB) is asked about too
        links = sum(bool(i.get("link")) for i in items)
        if not links:
            raise
        log.info("chat post %s: a video by link failed (%s)", post_id, exc)
        if not partial:
            return {"published": False, "skipped": skipped + [TOO_BIG] * links}
        skipped += [TOO_BIG] * links
        went = await _deliver(token, chat_id, post.text, [i for i in items if not i.get("link")], files)
    url = message_url(chat_id, username, went.first)
    async with SessionLocal() as s:
        row = await s.get(ChatPost, post_id)
        row.published_at, row.published_url, row.published_by = utcnow(), url, by
        row.published_chat, row.published_ids = str(chat_id), went.ids
        row.published_text_id, row.published_text_kind = went.text_id, went.kind
        row.published_edited_at = row.withdrawn_at = row.withdrawn_by = None
        await s.commit()
    return {"published": True, "url": url, "skipped": skipped}


# --- what went out: changed in the channel, or taken back ---------------------------------------------------------

DELETE_WINDOW = timedelta(hours=48)  # Telegram lets a bot delete its message this long after it went out
OLD_EDIT = "Этот пост вышел до обновления сайта — его текст можно изменить только в Telegram."
OLD_DELETE = "Этот пост вышел до обновления сайта — удалить его можно только в Telegram."
LATE_DELETE = ("Telegram даёт боту удалить пост только в первые 48 часов после публикации — удалите его в канале "
               "вручную.")


@dataclass
class Published:
    """Where a published post is: its chat, all its messages (None: not known), the one with its text and how."""

    chat: int | str
    ids: list[int] | None
    text_id: int | None
    kind: str | None


def published_of(post: ChatPost, channel: int | str | None) -> Published | None:
    """Where the post went out. One published before its messages were kept: its first message from its link, in
    the project's channel (`channel`) — the text's place guessed by how it was sent; all its messages are known only
    when it was one."""
    if post.published_at is None:
        return None
    if post.published_chat:
        chat = post.published_chat
        return Published(int(chat) if chat.lstrip("-").isdigit() else chat, post.published_ids,
                         post.published_text_id, post.published_text_kind)
    try:
        first = int((post.published_url or "").rstrip("/").rsplit("/", 1)[1])
    except (IndexError, ValueError):
        return None
    if channel is None:
        return None
    media = post.media or []
    if not media:
        return Published(channel, [first], first, "text")
    caption = tg_html.length(post.text) <= tg_html.CAPTION_LIMIT
    return Published(channel, [first] if len(media) == 1 and caption else None, first if caption else None,
                     "caption" if caption else None)


def channel_actions(post: ChatPost, channel: int | str | None) -> dict | None:
    """«Изменить в канале» / «Удалить из канала» for the card: may each be done, and why not (for people)."""
    where = published_of(post, channel)
    if where is None:
        return None
    why_edit = None if where.text_id is not None else OLD_EDIT
    why_delete = (OLD_DELETE if not where.ids
                  else LATE_DELETE if utcnow() - post.published_at > DELETE_WINDOW else None)
    return {"edit": why_edit is None, "why_edit": why_edit, "delete": why_delete is None, "why_delete": why_delete,
            # «Подпись под фото»: a caption takes 1024 characters, a message 4096
            "limit": tg_html.limit(where.kind == "caption")}


async def _token() -> str:
    async with SessionLocal() as s:
        token = str((await settings_store.get_all(s))["notify_bot_token"])
    if not token:
        raise BotError("бот не настроен")
    return token


async def edit_published(post_id: int, text: str, channel: int | str | None) -> None:
    """The published post's text changed in the channel, and here (the pictures stay: Telegram changes an album's
    only one by one). ValueError: it cannot be done — for people."""
    async with SessionLocal() as s:
        post = await s.get(ChatPost, post_id)
    where = published_of(post, channel)
    if where is None or where.text_id is None:
        raise ValueError(OLD_EDIT)
    limit = tg_html.limit(where.kind == "caption")
    if tg_html.length(text) > limit:
        raise ValueError(f"Текст длиннее, чем Telegram разрешает {'под фото и видео' if where.kind == 'caption' else 'в посте'}: "
                         f"{tg_html.length(text)} из {limit} символов")
    token = await _token()
    try:
        if where.kind == "caption":
            await bot_call(token, "editMessageCaption", chat_id=where.chat, message_id=where.text_id, caption=text,
                           parse_mode="HTML")
        else:
            await bot_call(token, "editMessageText", chat_id=where.chat, message_id=where.text_id, text=text,
                           parse_mode="HTML")
    except BotError as exc:
        if "not modified" not in str(exc).lower():  # the same text: nothing to change, all is well
            raise
    async with SessionLocal() as s:
        row = await s.get(ChatPost, post_id)
        if text != row.text:
            row.text, row.edited_at = text, utcnow()
        row.published_edited_at = utcnow()
        await s.commit()


async def withdraw(post_id: int, by: str | None, channel: int | str | None) -> None:
    """The published post taken back from the channel: all its messages deleted. It stays here, «Удалён из канала»,
    and may be published again. ValueError: it cannot be done — for people."""
    async with SessionLocal() as s:
        post = await s.get(ChatPost, post_id)
    where = published_of(post, channel)
    if where is None or not where.ids:
        raise ValueError(OLD_DELETE)
    if utcnow() - post.published_at > DELETE_WINDOW:
        raise ValueError(LATE_DELETE)
    await bot_call(await _token(), "deleteMessages", chat_id=where.chat, message_ids=where.ids)
    async with SessionLocal() as s:
        row = await s.get(ChatPost, post_id)
        row.published_at = row.published_url = row.published_by = None
        row.published_chat = row.published_ids = row.published_text_id = row.published_text_kind = None
        row.published_edited_at = None
        row.withdrawn_at, row.withdrawn_by = utcnow(), by
        await s.commit()


async def can_publish(post_id: int, chat_id: int | str, *, partial: bool) -> list[str]:
    """Before a publishing is scheduled: the bot may post in the channel (ChannelNotReady), and what of the
    pictures cannot go — asked about unless `partial`."""
    async with SessionLocal() as s:
        token = str((await settings_store.get_all(s))["notify_bot_token"])
    if not token:
        raise BotError("бот не настроен")
    await check_channel(token, chat_id)
    *_, skipped = await _prepare(post_id, dry=True)
    return [] if partial else skipped


LATE = timedelta(hours=1)  # a scheduled post this late (the server was down) is not published: news go stale
LATE_TEXT = "Сервер был недоступен в назначенное время, поэтому пост не опубликован, чтобы не выйти с опозданием."


async def publish_due() -> None:
    """The scheduled posts whose time came: the bot publishes them; one that cannot go is not published — the card
    says why, and the bot tells its author in Telegram."""
    now = utcnow()
    async with SessionLocal() as s:
        due = (await s.execute(select(ChatPost.id, ChatPost.scheduled_at).where(
            ChatPost.scheduled_at.is_not(None), ChatPost.scheduled_at <= now).order_by(ChatPost.scheduled_at))).all()
    for post_id, at in due:
        async with SessionLocal() as s:
            # taken: «Опубликовать сейчас» on the site at the same moment finds it no longer scheduled
            taken = await s.execute(update(ChatPost).where(ChatPost.id == post_id, ChatPost.scheduled_at == at)
                                    .values(scheduled_at=None))
            await s.commit()
            if not taken.rowcount:
                continue
            post = await s.get(ChatPost, post_id)
            thread = await s.get(ChatThread, post.thread_id) if post.thread_id else None
            project = await s.get(Project, post.project_id)
            target = await publish_target(s, project) if project else None
            cfg = await settings_store.get_all(s)
        error = None
        if now - at > LATE:
            error = LATE_TEXT
        elif target is None:
            error = "Канал проекта не выбран."
        else:
            try:
                done = await publish(post_id, target.chat_id, target.username, partial=post.scheduled_partial,
                                     by=post.scheduled_by)
                if not done["published"]:
                    error = f"Не всё получилось бы приложить: {'; '.join(done['skipped'])}."
            except ChannelNotReady as exc:
                error = not_ready_text(exc.reason, str(cfg["notify_bot_username"]), channel_title(project),
                                       staff=post.scheduled_by is not None and not by_owner(post.scheduled_by))
            except BotError as exc:
                log.warning("scheduled chat post %s: %s", post_id, exc)
                error = f"Telegram не принял пост: {exc}"
        if error is None:
            log.info("scheduled chat post %s published", post_id)
            continue
        async with SessionLocal() as s:
            row = await s.get(ChatPost, post_id)
            row.publish_error, row.publish_error_at = error, at
            await s.commit()
        await _tell_scheduler(post, thread, project, error)


OWNER_NAME = "владелец"  # who the owner is, as «who did it» says (web/deps.py)
AUTOPOST_NAME = "автопостинг"  # publications.py
RUBRIC_NAME = "рубрика «{}»"  # a rubric's issue that went straight to the channel (rubrics.py)


def by_owner(by: str | None) -> bool:
    """Done by the owner or for them — autoposting, a rubric: what went wrong is told to the owner."""
    return by in (OWNER_NAME, AUTOPOST_NAME) or (by or "").startswith("рубрика «")


async def _tell_scheduler(post: ChatPost, thread: ChatThread | None, project: Project | None, error: str) -> None:
    """Whoever scheduled it, by the bot: a staff member by their login; the owner — for their own and for
    autoposting; one scheduled before it was recorded who did: the author of its chat."""
    if project is None:
        return
    async with SessionLocal() as s:
        token = str((await settings_store.get_all(s))["notify_bot_token"])
        by = post.scheduled_by
        if by and not by_owner(by):
            person = await s.scalar(select(Member).where(Member.project_id == project.id, Member.login == by))
        elif by is None and thread and thread.member_id:
            person = await s.get(Member, thread.member_id)
        else:
            person = await s.get(User, project.owner_id)
    chat_id = getattr(person, "tg_user_id", None)
    if not token or not chat_id:
        return
    where = f"в канале «{channel_title(project)}»" if project else "в канале"
    try:
        await bot_call(token, "sendMessage", chat_id=chat_id,
                       text=f"Не удалось опубликовать запланированный пост {where}. {error}\n\n"
                            f"Пост остался на сайте, в «AI» → «Публикации»: оттуда его можно опубликовать снова.")
    except BotError as exc:
        log.info("telling the author of a scheduled post: %s", exc)
