"""Joining channels, receiving and polling posts, refreshing metrics."""

import asyncio
import logging
import random
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from telethon import errors
from telethon.tl import types
from telethon.tl.functions.channels import GetFullChannelRequest, JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

from .. import settings_store
from ..config import settings
from ..channel_stats import own_channels, record_subscribers
from ..db import SessionLocal
from ..fr_plus import refresh_fr_bases
from ..models import Account, Channel, Post, ProjectChannel, utcnow
from ..notify import esc, notifier
from ..services import account_loads, delete_previews, preview_path
from ..tg.parse import (
    group_messages,
    invite_chat,
    is_placeholder_text,
    metrics,
    own_forwards,
    post_fields,
    preview_slots,
    public_username,
    public_usernames,
    restriction_text,
    without_own_forwards,
)
from .pool import AccountPool, AccountUnavailable, AccountWorker
from .reserves import STEP_EVERY as RESERVES_EVERY
from .reserves import Reserves
from .util import forever

log = logging.getLogger(__name__)

# How often post numbers are re-read, by post age: fresh posts change fast, week-old ones barely.
REFRESH_TIERS = [
    (timedelta(minutes=2), timedelta(seconds=10)),
    (timedelta(minutes=5), timedelta(seconds=20)),
    (timedelta(minutes=10), timedelta(seconds=30)),
    (timedelta(minutes=20), timedelta(minutes=1)),
    (timedelta(hours=1), timedelta(minutes=2)),
    (timedelta(hours=6), timedelta(minutes=5)),
    (timedelta(hours=24), timedelta(minutes=15)),
    (timedelta(days=7), timedelta(hours=1)),
]
# A channel re-read anyway refreshes its other recent posts in the same request (up to 100 ids).
PIGGYBACK_AGE = timedelta(hours=24)
PIGGYBACK_STALE = timedelta(minutes=2)
# Metrics requests per account per minute: polling, joins and reposts keep their share of the
# account's rate limit. Without enough accounts the oldest numbers are simply refreshed first.
METRICS_PER_MINUTE = 30
# The metrics loop runs this often; a post counts as due that much early, so the average gap
# between reads matches its tier instead of overshooting it by up to a loop and a request.
METRICS_LOOP = 5
INFO_REFRESH = timedelta(hours=24)
OWN_INFO_REFRESH = timedelta(hours=1)  # project channels: their subscribers are charted (channel_stats.py)


@dataclass(frozen=True)
class ChannelRef:
    id: int
    account_id: int | None


class JoinError(Exception):
    pass


class JoinAwaitingApproval(Exception):
    """The invite link needs an admin's approval; the request has been sent."""


# shown while a join request waits; also tells the next attempt not to send the request again
# a channel we knew is found neither by its @usernames nor among the account's dialogs
MOVED = "Канал сменил адрес или стал закрытым — добавьте его заново по актуальной ссылке"
JOIN_REQUESTED = "Отправлена заявка на вступление — подключим, как только админ канала её одобрит"
# how often a pending join request is checked for approval
APPROVAL_RECHECK = 600
BACKFILL_PAGE = 100  # messages per request when a new channel's history is read
BACKFILL_MAX = 1000  # a channel posting non-stop does not get more than this on joining
OWN_BACKFILL_DAYS = 30  # a project's own channel brings a month: its statistics want more
LATE = timedelta(days=1)  # a post brought this long after it came out is history, not news (Post.history)
HISTORY_EVERY = 20  # seconds between rounds of catching up the history of older channels


class ChannelRestricted(Exception):
    """Telegram hides the channel from this account (blocked in the account's country)."""


RESTRICTED_EVERYWHERE = (
    "Запрещён в странах всех аккаунтов — подключим автоматически, как только появится новый аккаунт"
)
BANNED_EVERYWHERE = (
    "Канал закрыл доступ всем нашим аккаунтам — подключим автоматически, как только появится новый аккаунт"
)
COUNTRY = re.compile(r"\(([^()]{2,40})\)\s*\.?\s*$")
# "...violated <app>'s Terms of Service": hidden from the app our accounts connect as, in every country
APP_WIDE = re.compile(r"Terms of Service", re.IGNORECASE)
HIDDEN_FROM_OUR_APP = (
    "Telegram скрывает канал от приложения, через которое работают наши аккаунты (нарушение правил), — "
    "в официальном Telegram и на t.me он открывается"
)


class Collector:
    def __init__(self, pool: AccountPool) -> None:
        self.pool = pool
        self.by_tg: dict[int, ChannelRef] = {}
        self.ignored_tg_ids: set[int] = set()  # e.g. the aggregator channel
        self._metrics_calls: dict[int, deque[float]] = defaultdict(deque)
        self._approval_check_at: dict[int, float] = {}  # channel id -> next check of a join request
        self._preview_sem = asyncio.Semaphore(2)
        # pictures of a new channel's history wait in their own queue, so a big channel
        # does not hold up the pictures of fresh posts elsewhere
        self._backfill_preview_sem = asyncio.Semaphore(1)
        self._tasks: set[asyncio.Task] = set()
        self._restricting: set[int] = set()  # channels being handed over right now
        self.reserves = Reserves(pool, self.refresh_map)  # backup accounts in private channels
        pool.on_messages = self.on_messages
        pool.on_channel_changed = self.channel_changed

    async def run(self) -> None:
        await asyncio.gather(
            forever("channel map", self.refresh_map, 10),
            forever("join", self.join_step, 5),
            forever("leave", self.leave_step, 5),
            forever("poll", self.poll_step, 15),
            forever("metrics", self.metrics_step, METRICS_LOOP),
            forever("channel info", self.info_step, 60),
            forever("fr base", self.fr_base_step, 600),
            forever("backup accounts", self.reserves.step, RESERVES_EVERY),
            forever("history catch-up", self.history_step, HISTORY_EVERY),
        )

    # --- incoming posts -----------------------------------------------------

    async def refresh_map(self) -> None:
        async with SessionLocal() as s:
            rows = await s.execute(
                select(Channel.id, Channel.tg_id, Channel.account_id).where(
                    Channel.status == "active", Channel.tg_id.is_not(None)
                )
            )
            self.by_tg = {tg_id: ChannelRef(cid, aid) for cid, tg_id, aid in rows}

    async def on_messages(self, w: AccountWorker, channel_tg_id: int, messages: list) -> None:
        ref = self.by_tg.get(channel_tg_id)
        if ref is None:
            return
        if w.id != ref.account_id and any(restriction_text(m) for m in messages):
            return  # a backup account's view: only the main account's decides where the channel goes
        await self.ingest(w, ref.id, messages, backfill=False)

    async def ingest(self, w: AccountWorker, channel_id: int, messages: list, *, backfill: bool) -> int:
        groups = group_messages(messages)
        if not groups:
            return 0
        # a channel blocked in this account's country arrives as placeholders, not posts
        reason = next((r for g in groups for m in g if (r := restriction_text(m))), None)
        if reason:
            await self.handle_restricted(w, channel_id, reason)
            return 0
        created: list[tuple[Post, list]] = []
        async with SessionLocal() as s:
            ch = await s.get(Channel, channel_id)
            ids = [g[0].id for g in groups]
            existing = set(
                (
                    await s.execute(
                        select(Post.msg_id).where(Post.channel_id == channel_id, Post.msg_id.in_(ids))
                    )
                ).scalars()
            )
            for g in groups:
                fields = post_fields(g)
                if fields["msg_id"] in existing:
                    continue
                status = "skipped" if backfill else "pending"
                post = Post(channel_id=channel_id, repost_status=status, **fields, **metrics(g),
                            history=backfill or fields["posted_at"] < utcnow() - LATE)
                # a new post with pictures gets their text read once they are saved (history is not read)
                if not backfill and preview_slots(g):
                    post.ocr_status = "pending"
                s.add(post)
                created.append((post, g))
            if ch is not None:
                ch.last_msg_id = max(ch.last_msg_id or 0, max(m.id for g in groups for m in g))
                newest = max(p.posted_at for p, _ in created) if created else None
                if newest and (ch.last_post_at is None or newest > ch.last_post_at):
                    ch.last_post_at = newest
            try:
                await s.commit()
            except IntegrityError:
                # the same post arrived through the update stream and polling at once
                await s.rollback()
                log.debug("duplicate post in channel %s, skipped", channel_id)
                return 0

        for post, group in created:
            slots = preview_slots(group)
            if slots:
                task = asyncio.create_task(self._download_previews(w, post.id, slots, history=backfill))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
        if created:
            log.info("channel %s: +%d posts%s", channel_id, len(created), " (backfill)" if backfill else "")
        return len(created)

    async def _download_previews(self, w: AccountWorker, post_id: int, slots: list, *, history: bool = False) -> None:
        """A small picture of every album item that has one (a single post has one item)."""
        saved: list[dict | None] = []
        sem = self._backfill_preview_sem if history else self._preview_sem
        for slot, (pos, media, thumb, kind) in enumerate(slots):
            async with sem:
                try:
                    result = await self.pool.call(
                        w, w.client.download_media, media, file=str(preview_path(post_id, slot)), thumb=thumb
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("preview %s of post %s failed: %s", slot, post_id, exc)
                    result = None
            saved.append({"k": kind, "p": pos} if result else None)
        if any(saved):
            async with SessionLocal() as s:
                await s.execute(
                    update(Post).where(Post.id == post_id).values(has_preview=saved[0] is not None, previews=saved)
                )
                await s.commit()

    # --- polling fallback ---------------------------------------------------

    async def poll_step(self) -> None:
        """Updates for channels are not always pushed, so every channel is also polled now and then."""
        async with SessionLocal() as s:
            cfg = await settings_store.get_all(s)
            cutoff = utcnow() - timedelta(seconds=int(cfg["poll_interval_sec"]))
            channels = (
                (
                    await s.execute(
                        select(Channel)
                        .where(
                            Channel.status == "active",
                            Channel.account_id.is_not(None),
                            Channel.tg_id.is_not(None),
                            or_(Channel.last_polled_at.is_(None), Channel.last_polled_at < cutoff),
                        )
                        .order_by(Channel.last_polled_at.asc().nulls_first())
                        .limit(30)
                    )
                )
                .scalars()
                .all()
            )
        for ch in channels:
            w = self.pool.get(ch.account_id)
            if w is None:
                continue
            try:
                peer = await self.pool.input_peer(w, ch)
                msgs = await self.pool.call(
                    w, w.client.get_messages, peer, limit=30, min_id=ch.last_msg_id or 0
                )
            except AccountUnavailable:
                continue
            except ValueError as exc:
                # the account could not find the channel just now: its cache of channels is empty after a
                # restart and Telegram's lookup by name failed. Not a sign the channel is gone (the two
                # «Канал не найден» of 2026-10-14 were alive): it is joined anew, where a channel that
                # really is gone gets its real error
                log.warning("channel %s: account %s could not find it while polling: %r", ch.id, w.id, exc)
                await self._set_channel(ch.id, status="pending", status_detail=None)
                continue
            except (errors.ChannelPrivateError, errors.ChannelInvalidError) as exc:
                if _shut_out(ch, exc):
                    await self.lost_access(w, ch)
                    continue
                # a private channel shut its main account out: a backup inside takes over
                if ch.invite_hash and isinstance(exc, errors.ChannelPrivateError) and await self.reserves.promote(ch, w):
                    continue
                detail = _join_error_text(exc)
                log.warning("channel %s: polling stopped by account %s: %r", ch.id, w.id, exc)
                await self._set_channel(ch.id, status="error", status_detail=detail)
                notifier.emit(
                    f"channel-error-{ch.id}",
                    f"<b>Канал недоступен</b>\n{esc(ch.title or ch.link)} — сбор остановлен: {esc(detail)}.",
                )
                continue
            await self._names_from(ch, msgs)
            # a channel without a known last message: just remember where we are, do not repost
            await self.ingest(w, ch.id, list(msgs), backfill=ch.last_msg_id is None)
            await self._set_channel(ch.id, last_polled_at=utcnow())

    # --- metrics ------------------------------------------------------------

    def _metrics_budget(self, account_id: int) -> bool:
        """Takes one metrics request from the account's per-minute allowance, if any is left."""
        calls = self._metrics_calls[account_id]
        now = time.monotonic()
        while calls and now - calls[0] > 60:
            calls.popleft()
        if len(calls) >= METRICS_PER_MINUTE:
            return False
        calls.append(now)
        return True

    async def metrics_step(self) -> None:
        now = utcnow()
        early = timedelta(seconds=METRICS_LOOP)
        due = or_(
            *(
                and_(Post.posted_at > now - age, Post.metrics_updated_at < now - max(every - early, every / 2))
                for age, every in REFRESH_TIERS
            )
        )
        async with SessionLocal() as s:
            rows = (
                await s.execute(
                    select(Post, Channel)
                    .join(Channel, Post.channel_id == Channel.id)
                    .where(Channel.status == "active", Channel.account_id.is_not(None), Post.deleted_at.is_(None), due)
                    # freshest first: when the budget runs out, week-old numbers wait, not new posts
                    .order_by(Post.posted_at.desc())
                    .limit(1000)
                )
            ).all()

            by_channel: dict[int, tuple[Channel, list[Post]]] = {}
            for post, ch in rows:
                by_channel.setdefault(ch.id, (ch, []))[1].append(post)
            if by_channel:
                extra = await s.execute(
                    select(Post).where(
                        Post.channel_id.in_(by_channel),
                        Post.posted_at > now - PIGGYBACK_AGE,
                        Post.metrics_updated_at < now - PIGGYBACK_STALE,
                        Post.deleted_at.is_(None),
                    )
                )
                for post in extra.scalars():
                    posts = by_channel[post.channel_id][1]
                    if post not in posts:
                        posts.append(post)

        # Each account reads its own channels in parallel with the others. One account makes about
        # a request a second; a single queue across all accounts made a fresh post wait a minute.
        by_account: dict[int, list[tuple[Channel, list[Post]]]] = defaultdict(list)
        for ch, posts in by_channel.values():
            by_account[ch.account_id].append((ch, posts))
        results = await asyncio.gather(
            *(self._account_metrics(account_id, items) for account_id, items in by_account.items())
        )
        for blocked in results:
            for w, channel_id, reason in blocked:
                await self.handle_restricted(w, channel_id, reason)

    async def _account_metrics(self, account_id: int, items: list[tuple[Channel, list[Post]]]) -> list:
        """Re-reads the numbers of one account's posts; returns channels found blocked for it."""
        blocked: list[tuple[AccountWorker, int, str]] = []
        w = self.pool.get(account_id)
        if w is None:
            return blocked
        for ch, posts in items:
            try:
                peer = await self.pool.input_peer(w, ch)
                for batch in _batches(posts, 100):
                    if not self._metrics_budget(w.id):
                        return blocked  # the rest stays due and goes first next time
                    ids = [i for p in batch for i in (p.album_ids or [p.msg_id])]
                    msgs = await self.pool.call(w, w.client.get_messages, peer, ids=ids)
                    found = {m.id: m for m in msgs if isinstance(m, types.Message)}
                    if reason := next((r for m in found.values() if (r := restriction_text(m))), None):
                        blocked.append((w, ch.id, reason))
                        break
                    read_at = utcnow()
                    updated, missing = [], []
                    for p in batch:
                        group = [found[i] for i in (p.album_ids or [p.msg_id]) if i in found]
                        if group:
                            # our repost to the group is a forward Telegram counts too: not the audience's
                            values = without_own_forwards(metrics(group), own_forwards(p))
                            values.update(id=p.id, metrics_updated_at=read_at)
                            updated.append(values)
                        else:
                            # Telegram returns nothing for a message deleted in its channel
                            missing.append({"id": p.id, "metrics_updated_at": read_at, "deleted_at": read_at})
                    # written per batch, so fresh numbers don't wait for the other channels
                    async with SessionLocal() as s:
                        for rows in (updated, missing):
                            if rows:
                                await s.execute(update(Post), rows)
                        await s.commit()
            except AccountUnavailable:
                return blocked
            except Exception as exc:  # noqa: BLE001
                log.warning("metrics for channel %s failed: %s", ch.id, exc)
        return blocked

    async def info_step(self) -> None:
        """Subscribers, title, username and avatar once a day; every hour for project channels."""
        async with SessionLocal() as s:
            channels = (
                (
                    await s.execute(
                        select(Channel)
                        .where(
                            Channel.status == "active",
                            Channel.account_id.is_not(None),
                            or_(
                                Channel.info_updated_at.is_(None),
                                Channel.info_updated_at < utcnow() - INFO_REFRESH,
                                and_(Channel.id.in_(own_channels()), Channel.info_updated_at < utcnow() - OWN_INFO_REFRESH),
                            ),
                        )
                        .limit(10)
                    )
                )
                .scalars()
                .all()
            )
        for ch in channels:
            w = self.pool.get(ch.account_id)
            if w is None:
                continue
            try:
                peer = await self.pool.input_peer(w, ch)
                info = await self._channel_info(w, peer)
            except AccountUnavailable:
                continue
            except ChannelRestricted as exc:
                await self.handle_restricted(w, ch.id, str(exc))
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning("info for channel %s failed: %s", ch.id, exc)
                info = {}
            await self._set_channel(ch.id, info_updated_at=utcnow(), **info)
            async with SessionLocal() as s:
                await record_subscribers(s, ch.id, info.get("subscribers"))
                await s.commit()

    async def channel_changed(self, tg_id: int) -> None:
        """Telegram says a channel changed (title, @usernames, photo, made private or public): its
        info is read again in the next round of info_step."""
        async with SessionLocal() as s:
            await s.execute(update(Channel).where(Channel.tg_id == tg_id).values(info_updated_at=None))
            await s.commit()

    async def _names_from(self, ch: Channel, msgs) -> None:
        """The channel as it came with its new messages: its @usernames, if they changed."""
        chat = next((c for m in msgs if isinstance(c := getattr(m, "chat", None), types.Channel)), None)
        if chat is None or chat.min or chat.id != ch.tg_id:
            return  # a "min" channel may come without its usernames
        names = public_usernames(chat)
        if names != known_names(ch):
            log.info("channel %s: @usernames %s -> %s", ch.id, known_names(ch), names)
            await self._set_channel(ch.id, username=next(iter(names), None), usernames=names)

    async def fr_base_step(self) -> None:
        async with SessionLocal() as s:
            await refresh_fr_bases(s)

    async def _channel_info(self, w: AccountWorker, entity) -> dict:
        full = await self.pool.call(w, w.client, GetFullChannelRequest(entity))
        chat = next((c for c in full.chats if c.id == full.full_chat.id), None)
        if chat is not None and (reason := restriction_text(chat)):
            raise ChannelRestricted(reason)
        info = {"subscribers": full.full_chat.participants_count}
        if chat is not None:
            info["title"] = chat.title
            info["usernames"] = public_usernames(chat)
            info["username"] = next(iter(info["usernames"]), None)
            avatar = await self.pool.call(w, w.client.download_profile_photo, chat, file=bytes)
            info["avatar"] = avatar or None
            info["has_avatar"] = bool(avatar)
        return info

    # --- joining / leaving --------------------------------------------------

    async def join_step(self) -> None:
        async with SessionLocal() as s:
            cfg = await settings_store.get_all(s)
            # restricted channels are retried as soon as an account outside their block list exists
            pending = (
                (
                    await s.execute(
                        select(Channel)
                        # a channel nobody needs any more is not worth a join
                        .where(Channel.status.in_(("pending", "restricted")), Channel.orphaned_at.is_(None))
                        .order_by(Channel.created_at)
                        .limit(50)
                    )
                )
                .scalars()
                .all()
            )
            if not pending:
                return
            loads = await account_loads(s)
            limits = dict((await s.execute(select(Account.id, Account.max_channels))).all())
            alive = set(
                (await s.execute(select(Account.id).where(Account.enabled, Account.status != "dead"))).scalars()
            )

        async with SessionLocal() as s:
            own = set((await s.execute(own_channels())).scalars())
        now = time.monotonic()
        for ch in pending:
            if now < self._approval_check_at.get(ch.id, 0):
                continue  # a join request is waiting for the channel admin
            restricted = set(ch.restricted_for or [])
            blocked = restricted | set(ch.banned_for or [])
            w = self.pool.get(ch.account_id) if ch.account_id not in blocked else None
            # the main account of a private channel is gone: a backup inside takes over, no re-join
            if w is None and ch.status == "pending" and ch.invite_hash and ch.tg_id and await self.reserves.promote(ch):
                continue
            if w is None:
                candidates = [
                    x for x in self.pool.available()
                    if x.id not in blocked and loads.get(x.id, 0) < limits.get(x.id, 0)
                ]
                if not candidates:
                    if ch.status == "pending" and blocked and not (alive - blocked):
                        if not (alive - restricted):
                            await self._set_channel(ch.id, status="restricted", status_detail=RESTRICTED_EVERYWHERE)
                        elif ch.status_detail != BANNED_EVERYWHERE:
                            await self._set_channel(ch.id, status_detail=BANNED_EVERYWHERE)
                    continue
                w = min(candidates, key=lambda x: (loads.get(x.id, 0), x.next_join_at))
            if now < w.next_join_at:
                continue
            w.next_join_at = now + int(cfg["join_interval_sec"]) * random.uniform(1.0, 1.5)
            loads[w.id] = loads.get(w.id, 0) + 1
            days = int(cfg["backfill_days"])
            await self._join(w, ch, max(days, OWN_BACKFILL_DAYS) if ch.id in own else days)

    async def _join(self, w: AccountWorker, ch: Channel, backfill_days: int) -> None:
        log.info("account %s joins channel %s (%s)", w.id, ch.id, ch.link)
        entity = None
        try:
            entity = await self._resolve_and_join(w, ch)
            async with SessionLocal() as s:
                dup = await s.scalar(select(Channel.id).where(Channel.tg_id == entity.id, Channel.id != ch.id))
            if dup:
                await self._merge(ch.id, dup)
                return
            if reason := restriction_text(entity):
                raise ChannelRestricted(reason)
            info = await self._channel_info(w, entity)
            msgs = await self._history(w, entity, backfill_days)
            if reason := next((r for m in msgs if (r := restriction_text(m))), None):
                raise ChannelRestricted(reason)
        except AccountUnavailable:
            return  # stays pending and is picked up again later
        except JoinAwaitingApproval:
            # the request belongs to this account, so it stays assigned to it
            await self._set_channel(ch.id, status="pending", status_detail=JOIN_REQUESTED, account_id=w.id)
            self._approval_check_at[ch.id] = time.monotonic() + APPROVAL_RECHECK
            return
        except ChannelRestricted as exc:
            await self._set_channel(ch.id, tg_id=entity.id, username=public_username(entity),
                                    usernames=public_usernames(entity), title=entity.title)
            await self.handle_restricted(w, ch.id, str(exc), peer=entity)
            return
        except errors.ChannelsTooMuchError:
            await self._set_channel(ch.id, account_id=None)
            async with SessionLocal() as s:
                count = await s.scalar(select(func.count()).where(Channel.account_id == w.id))
                await s.execute(update(Account).where(Account.id == w.id).values(max_channels=count))
                await s.commit()
            return
        except Exception as exc:  # noqa: BLE001
            if _shut_out(ch, exc):
                await self.lost_access(w, ch)
                return
            await self._set_channel(ch.id, status="error", status_detail=_join_error_text(exc), account_id=None)
            log.warning("join %s failed: %r", ch.link, exc)
            return

        msgs = [m for m in msgs if isinstance(m, types.Message)]
        await self._set_channel(
            ch.id,
            tg_id=entity.id,
            account_id=w.id,
            status="active",
            status_detail=None,
            info_updated_at=utcnow(),
            last_polled_at=utcnow(),
            history_done=True,
            last_msg_id=max((m.id for m in msgs), default=0),
            **info,
        )
        history = _within(msgs, backfill_days)
        if history:
            await self.ingest(w, ch.id, history, backfill=True)
            log.info("channel %s: %d messages of the last %d days fetched", ch.id, len(history), backfill_days)
        await self.refresh_map()

    async def history_step(self) -> None:
        """Channels added before a new channel brought its week of posts get that week now:
        each round every account catches up one of its channels."""
        async with SessionLocal() as s:
            days = int((await settings_store.get_all(s))["backfill_days"])
            todo = (await s.execute(
                select(Channel).where(
                    Channel.history_done.is_(False),
                    Channel.status == "active",
                    Channel.account_id.is_not(None),
                    Channel.tg_id.is_not(None),
                ).order_by(Channel.id)
            )).scalars().all()
            own = set((await s.execute(own_channels())).scalars())
        picked: dict[int, Channel] = {}
        for ch in todo:
            if ch.account_id not in picked and self.pool.get(ch.account_id) is not None:
                picked[ch.account_id] = ch
        if picked:
            await asyncio.gather(*(self._catch_up(ch, max(days, OWN_BACKFILL_DAYS) if ch.id in own else days)
                                   for ch in picked.values()))

    async def _catch_up(self, ch: Channel, days: int) -> None:
        w = self.pool.get(ch.account_id)
        if w is None:
            return
        try:
            peer = await self.pool.input_peer(w, ch)
            msgs = _within(await self._history(w, peer, days), days)
        except AccountUnavailable:
            return  # next round
        except Exception as exc:  # noqa: BLE001 — not worth retrying forever
            log.warning("history of channel %s: %r", ch.id, exc)
            msgs = []
        # known posts are skipped by ingest; history is never reposted
        added = await self.ingest(w, ch.id, msgs, backfill=True) if msgs else 0
        await self._set_channel(ch.id, history_done=True)
        log.info("channel %s: history caught up, +%d posts of the last %d days", ch.id, added, days)

    async def _history(self, w: AccountWorker, entity, days: int) -> list:
        """The channel's messages, newest first, back to `days` ago (at least the newest one, so
        the channel's place is known; at most BACKFILL_MAX). One request per 100 messages."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        msgs: list = []
        offset_id = 0
        while len(msgs) < BACKFILL_MAX:
            page = await self.pool.call(w, w.client.get_messages, entity, limit=BACKFILL_PAGE, offset_id=offset_id)
            page = list(page)
            msgs.extend(page)
            if len(page) < BACKFILL_PAGE or days <= 0 or not page or _date(page[-1]) < cutoff:
                break
            offset_id = page[-1].id
        return msgs

    async def _merge(self, channel_id: int, into_id: int) -> None:
        """The channel turned out to be tracked already under another link (say, a second invite
        link of a private channel): its projects move over to that one, the duplicate goes."""
        async with SessionLocal() as s:
            target = await s.get(Channel, into_id)
            dup = await s.get(Channel, channel_id)
            if target is None or dup is None:
                return
            theirs = set((await s.execute(
                select(ProjectChannel.project_id).where(ProjectChannel.channel_id == into_id)
            )).scalars())
            for pc in (await s.execute(select(ProjectChannel).where(ProjectChannel.channel_id == channel_id))).scalars():
                if pc.project_id not in theirs:
                    s.add(ProjectChannel(project_id=pc.project_id, channel_id=into_id, created_at=pc.created_at))
            target.held = target.held or dup.held
            target.orphaned_at = None
            if target.status == "deleting":
                target.status = "pending"
            await s.flush()
            await s.delete(dup)
            await s.commit()
        log.info("channel %s is the same as channel %s: merged", channel_id, into_id)

    async def _resolve_and_join(self, w: AccountWorker, ch: Channel):
        if ch.invite_hash:
            invite = await self.pool.call(w, w.client, CheckChatInviteRequest(ch.invite_hash))
            if isinstance(invite, types.ChatInviteAlready):
                entity = invite.chat  # already a member (e.g. a join request got approved)
            elif getattr(invite, "request_needed", False) and ch.status_detail == JOIN_REQUESTED and ch.account_id == w.id:
                raise JoinAwaitingApproval()  # this account asked already: wait, don't send it again
            else:
                try:
                    result = await self.pool.call(w, w.client, ImportChatInviteRequest(ch.invite_hash))
                    entity = invite_chat(result)
                except errors.UserAlreadyParticipantError:
                    invite = await self.pool.call(w, w.client, CheckChatInviteRequest(ch.invite_hash))
                    entity = invite.chat
                except errors.InviteRequestSentError as exc:
                    raise JoinAwaitingApproval() from exc
        else:
            entity = await self._find(w, ch)
        if not isinstance(entity, types.Channel) or not entity.broadcast:
            raise JoinError("Это не канал (группа или пользователь)")
        if entity.id in self.ignored_tg_ids:
            raise JoinError("Это канал-агрегатор")
        if not ch.invite_hash and entity.left:
            await self.pool.call(w, w.client, JoinChannelRequest(entity))
        return entity

    async def _find(self, w: AccountWorker, ch: Channel):
        """A public channel, by its @usernames. A channel we know already (tg_id) must stay itself: a
        name it dropped may be another channel's now, so a name only counts when it leads to it; not
        found so (renamed, or made private), the account may still be inside, among its dialogs."""
        # the link keeps the name the channel was added by: the last resort
        names = list(dict.fromkeys([*known_names(ch), ch.link.removeprefix("@")]))
        if ch.tg_id is None:
            return await self.pool.call(w, w.client.get_entity, names[0])
        try:
            return await self.pool.call(w, w.client.get_entity, types.PeerChannel(ch.tg_id))
        except ValueError:
            pass  # not in this account's memory
        for name in names:
            try:
                entity = await self.pool.call(w, w.client.get_entity, name)
            except (ValueError, errors.UsernameNotOccupiedError, errors.UsernameInvalidError):
                continue
            if getattr(entity, "id", None) == ch.tg_id:
                return entity
            log.info("channel %s: @%s leads to another chat now", ch.id, name)
        for dialog in await self.pool.call(w, w.client.get_dialogs, limit=None):
            if getattr(dialog.entity, "id", None) == ch.tg_id:
                return dialog.entity
        raise JoinError(MOVED)

    async def leave_step(self) -> None:
        async with SessionLocal() as s:
            channels = (
                (await s.execute(select(Channel).where(Channel.status == "deleting").limit(20))).scalars().all()
            )
        for ch in channels:
            if ch.account_id is not None and ch.account_id in self.pool.workers:
                w = self.pool.get(ch.account_id)
                if w is None:
                    continue  # account in FloodWait: leave later
                if ch.tg_id:
                    try:
                        peer = await self.pool.input_peer(w, ch)
                        await self.pool.call(w, w.client, LeaveChannelRequest(peer))
                    except AccountUnavailable:
                        continue
                    except Exception as exc:  # noqa: BLE001
                        log.warning("leave %s failed: %s", ch.id, exc)
            await self.reserves.leave_all(ch)
            async with SessionLocal() as s:
                post_ids = (await s.execute(select(Post.id).where(Post.channel_id == ch.id))).scalars().all()
                obj = await s.get(Channel, ch.id)
                if obj is not None and obj.status != "deleting":
                    continue  # added back while we were leaving: it is joined again instead
                if obj is not None:
                    await s.delete(obj)
                await s.commit()
            delete_previews(post_ids)
            log.info("channel %s removed (%d posts)", ch.id, len(post_ids))

    async def lost_access(self, w: AccountWorker, ch: Channel) -> None:
        """A public channel answered "private" to this account only: its admins banned or removed it.
        The account is never assigned to the channel again; another one takes it over."""
        banned = sorted(set(ch.banned_for or []) | {w.id})
        await self._set_channel(
            ch.id,
            banned_for=banned,
            account_id=None,
            status="pending",
            status_detail=f"Канал закрыл доступ аккаунту «{w.label}» — подключаем другой аккаунт",
        )
        await self.refresh_map()
        log.warning("channel %s (%s) shut out account %s; reassigning", ch.id, ch.link, w.id)
        notifier.emit(
            f"channel-banned-{ch.id}-{w.id}",
            f"<b>Канал закрыл доступ аккаунту</b>\n{esc(ch.title or ch.link)} заблокировал или удалил "
            f"аккаунт «{esc(w.label)}». Сбор канала переходит к другому аккаунту.",
        )

    async def handle_restricted(self, w: AccountWorker, channel_id: int, reason: str, *, peer=None) -> None:
        """The channel is blocked in this account's country: hand it to another account.

        The account is added to the channel's block list and leaves it; placeholder posts are
        deleted. With no other usable account the channel waits as "restricted" and is retried
        by join_step once a new account appears.
        """
        if channel_id in self._restricting:
            return
        self._restricting.add(channel_id)
        try:
            async with SessionLocal() as s:
                ch = await s.get(Channel, channel_id)
                if ch is None or ch.status == "deleting":
                    return
                alive = set(
                    (await s.execute(select(Account.id).where(Account.enabled, Account.status != "dead"))).scalars()
                )
                # Telegram names the country at the end: "...violated local laws (Португалия)."
                country = m.group(1) if (m := COUNTRY.search(reason)) else None
                # a Terms of Service block is the app's, and every account is the same app: no use trying them all
                app_wide = country is None and bool(APP_WIDE.search(reason))
                blocked = sorted(set(ch.restricted_for or []) | {w.id} | (alive if app_wide else set()))
                others = alive - set(blocked)
                ch.restricted_for = blocked
                ch.account_id = None
                where = f"стране аккаунта «{w.label}»" + (f" ({country})" if country else "")
                if app_wide:
                    ch.status = "restricted"
                    ch.status_detail = HIDDEN_FROM_OUR_APP
                elif others:
                    ch.status = "pending"
                    ch.status_detail = f"Запрещён в {where} — подключаем через другой аккаунт"
                else:
                    ch.status = "restricted"
                    ch.status_detail = f"Запрещён в {where}. " + RESTRICTED_EVERYWHERE
                title = ch.title or ch.link
                tg_id, username = ch.tg_id, ch.username
                placeholders = [
                    pid for pid, text in (
                        await s.execute(select(Post.id, Post.text).where(Post.channel_id == channel_id))
                    ).all()
                    if is_placeholder_text(text)
                ]
                if placeholders:
                    await s.execute(delete(Post).where(Post.id.in_(placeholders)))
                await s.commit()
            delete_previews(placeholders)
            log.warning("channel %s restricted for account %s (%s); others available: %s",
                        channel_id, w.id, reason, bool(others))

            try:
                if peer is None and tg_id:
                    peer = await self.pool.input_peer(w, Channel(tg_id=tg_id, username=username))
                if peer is not None:
                    await self.pool.call(w, w.client, LeaveChannelRequest(peer))
            except Exception as exc:  # noqa: BLE001
                log.info("leaving restricted channel %s failed: %s", channel_id, exc)

            notifier.emit(
                f"channel-restricted-{channel_id}-{w.id}",
                f"<b>Канал скрыт от нашего приложения</b>\n{esc(title)}: Telegram не показывает его клиенту, через "
                "который работают аккаунты (нарушение правил). В официальном Telegram он открывается."
                if app_wide else
                f"<b>Канал запрещён для аккаунта</b>\n{esc(title)} недоступен аккаунту «{esc(w.label)}» "
                f"(ограничение страны{f': {esc(country)}' if country else ''}). "
                + ("Переключаемся на другой аккаунт." if others else
                   "Других аккаунтов нет — подключим, как только появится новый."),
            )
            await self.refresh_map()
        finally:
            self._restricting.discard(channel_id)

    async def _set_channel(self, channel_id: int, **values) -> None:
        async with SessionLocal() as s:
            await s.execute(update(Channel).where(Channel.id == channel_id).values(**values))
            await s.commit()


def _batches(posts: list[Post], max_ids: int):
    batch: list[Post] = []
    size = 0
    for p in posts:
        n = len(p.album_ids or [p.msg_id])
        if batch and size + n > max_ids:
            yield batch
            batch, size = [], 0
        batch.append(p)
        size += n
    if batch:
        yield batch


def _date(m) -> datetime:
    d = getattr(m, "date", None)
    if d is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _within(msgs: list, days: int) -> list:
    """Messages of the last `days` days; an album that straddles the edge comes whole."""
    if days <= 0:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    msgs = [m for m in msgs if isinstance(m, types.Message)]
    keep = {m.id for m in msgs if _date(m) >= cutoff}
    albums = {m.grouped_id for m in msgs if m.id in keep and m.grouped_id}
    return [m for m in msgs if m.id in keep or (m.grouped_id and m.grouped_id in albums)]


def known_names(ch: Channel) -> list[str]:
    """The channel's public @usernames as we know them (channels from before the list: the one)."""
    if ch.usernames is not None:
        return list(ch.usernames)
    return [ch.username] if ch.username else []


def _shut_out(ch: Channel, exc: Exception) -> bool:
    """"Private" from a channel that is public by username means this account lost access to it
    (banned or removed by the admins), not that the channel is gone — another account can read it."""
    return isinstance(exc, errors.ChannelPrivateError) and bool(ch.username) and not ch.invite_hash


def _join_error_text(exc: Exception) -> str:
    if isinstance(exc, JoinError):
        return str(exc)
    if isinstance(exc, (errors.UsernameNotOccupiedError, errors.UsernameInvalidError, ValueError)):
        return "Канал не найден"
    if isinstance(exc, (errors.InviteHashExpiredError, errors.InviteHashInvalidError)):
        return "Ссылка-приглашение недействительна"
    if isinstance(exc, errors.InviteRequestSentError):
        return "Отправлена заявка на вступление — нужно одобрение админа канала"
    if isinstance(exc, errors.ChannelPrivateError):
        return "Канал приватный или аккаунт в нём заблокирован"
    return f"Ошибка: {exc}"
