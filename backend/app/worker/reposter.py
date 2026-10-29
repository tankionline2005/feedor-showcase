"""Forwards collected posts into the aggregator group, one at a time.

The aggregator is a public group: an account joins it by itself (by @username or an
invite link) and posts as a regular member, so nobody has to be made an admin.
A channel still works too, but then the accounts must be its admins.
"""

import logging
import re
import time
from collections import defaultdict, deque
from datetime import timedelta

from sqlalchemy import select
from telethon import errors, utils
from telethon.tl import types
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

from .. import settings_store
from ..db import SessionLocal
from ..models import Channel, Post, utcnow
from ..notify import esc, notifier
from ..tg.links import parse_channel_link
from ..tg.parse import invite_chat, public_username
from .collector import Collector
from .pool import AccountPool, AccountUnavailable, AccountWorker
from .util import forever

log = logging.getLogger(__name__)

TEXT_LIMIT = 3800  # Telegram allows 4096 characters per message
PEER_FLOOD_PAUSE = 3600  # account flagged for spam: stop reposting through it for a while
HOUR = 3600
FAILS_TO_ALERT = 5  # consecutive failed reposts before an alert
REPOSTS_PER_ACCOUNT_HOUR = 100  # above it the post goes through another account

CANNOT_POST = (
    errors.ChatWriteForbiddenError,
    errors.ChatRestrictedError,
    errors.ChatAdminRequiredError,
    errors.ChatSendMediaForbiddenError,
    errors.ChatSendPlainForbiddenError,
    errors.UserBannedInChannelError,
)
# the account is not (or no longer) a member: forget the cached target so it rejoins
NOT_MEMBER = (errors.ChatGuestSendForbiddenError, errors.ChannelPrivateError)
# the cached aggregator peer no longer exists, e.g. a basic group upgraded to a supergroup
STALE_TARGET = (errors.PeerIdInvalidError, errors.ChatIdInvalidError)

BASIC_GROUP_NOTE = (
    "<b>Ссылки на репосты недоступны</b>\n"
    "Группа-агрегатор — обычная группа, а у сообщений в таких группах нет ссылок, поэтому в ленте "
    "кнопка поста ведёт на оригинал в канале.\n"
    "Чтобы ссылки вели в группу, откройте её настройки и включите «История чата для новых участников: "
    "видна» — Telegram превратит группу в супергруппу."
)


class Reposter:
    """Spreads reposts over all accounts so no single one forwards in bulk.

    A post from a public channel goes through the account with the fewest reposts in the
    last hour (preferring one that already resolved the channel); a private channel can only
    be forwarded by its member. Each account makes at most REPOSTS_PER_ACCOUNT_HOUR.
    """

    def __init__(self, pool: AccountPool, collector: Collector) -> None:
        self.pool = pool
        self.collector = collector
        self._targets: dict[tuple[int, str], object] = {}
        self._links: dict[tuple[int, str], str | None] = {}  # base t.me link of the aggregator
        self._paused_until: dict[int, float] = {}  # account id -> monotonic time
        self._sent: dict[int, deque[float]] = defaultdict(deque)  # account id -> repost times
        self._knows: dict[int, set[int]] = defaultdict(set)  # channel id -> accounts that resolved it
        self._member_only: set[int] = set()  # channels non-members failed to forward from
        self._fail_streak = 0

    async def run(self) -> None:
        await forever("repost", self.step, 2)

    def _ready_workers(self) -> list[AccountWorker]:
        now = time.monotonic()
        return [w for w in self.pool.available() if self._paused_until.get(w.id, 0) <= now]

    def used(self, account_id: int) -> int:
        times = self._sent[account_id]
        while times and time.monotonic() - times[0] > HOUR:
            times.popleft()
        return len(times)

    def choose(self, ch: Channel, workers: list[AccountWorker], limit: int) -> AccountWorker | None:
        capable = [w for w in workers if self.used(w.id) < limit]
        if not ch.username or ch.id in self._member_only:
            return next((w for w in capable if w.id == ch.account_id), None)
        if not capable:
            return None
        least = min(self.used(w.id) for w in capable)
        knows = [w for w in capable if (w.id == ch.account_id or w.id in self._knows[ch.id])
                 and self.used(w.id) <= least + 2]
        return min(knows or capable, key=lambda w: (self.used(w.id), w.id != ch.account_id, w.id))

    async def step(self) -> float:
        workers = self._ready_workers()
        async with SessionLocal() as s:
            cfg = await settings_store.get_all(s)
            target_ref = str(cfg["aggregator_channel"])
            if not target_ref or not workers:
                return 10
            rows = (
                await s.execute(
                    select(Post, Channel)
                    .join(Channel, Post.channel_id == Channel.id)
                    .where(Post.repost_status == "pending", Channel.status.in_(("active", "paused")))
                    .order_by(Post.posted_at)
                    .limit(50)
                )
            ).all()
            picked = next(
                ((p, c, w) for p, c in rows if (w := self.choose(c, workers, REPOSTS_PER_ACCOUNT_HOUR))), None
            )
            if picked is None:
                return 5 if rows else 2
            post, ch, w = picked

            if post.posted_at < utcnow() - timedelta(hours=int(cfg["repost_max_age_hours"])):
                post.repost_status = "skipped"
                post.repost_error = "Слишком старый пост"
                post.reposted_at = utcnow()
                await s.commit()
                return 0

            member = w.id == ch.account_id
            try:
                target = await self._target(w, target_ref)
                try:
                    source = await self.pool.input_peer(w, ch)
                    try:
                        sent_id, copied = await self._send(w, target, source, post, ch)
                    except STALE_TARGET:
                        # find the group again by its link and send the same post there
                        log.info("account %s: aggregator peer is stale, resolving it again", w.id)
                        self._forget_target(w, target_ref)
                        target = await self._target(w, target_ref)
                        sent_id, copied = await self._send(w, target, source, post, ch)
                except (errors.ChannelPrivateError, errors.ChannelInvalidError, ValueError) as exc:
                    if member:
                        raise
                    # a non-member cannot reach this channel: leave it to the member account
                    log.info("channel %s needs its member account to forward (%r)", ch.id, exc)
                    self._member_only.add(ch.id)
                    return 0
                self._knows[ch.id].add(w.id)
                post.repost_status = "done"
                post.repost_error = None
                base = self._links.get((w.id, target_ref))
                post.repost_url = f"{base}/{sent_id}" if base and sent_id else None
                post.repost_copy = copied
                self._sent[w.id].append(time.monotonic())
                self._fail_streak = 0
            except AccountUnavailable:
                return 1
            except errors.SlowModeWaitError as exc:
                # slow mode in the group limits each member separately; the post stays queued
                self._pause(w, exc.seconds, "slow mode")
                return 0
            except errors.PeerFloodError:
                self._pause(w, PEER_FLOOD_PAUSE, "PeerFlood (spam limit)")
                notifier.emit(f"peerflood-{w.id}",
                              f"<b>Спам-ограничение</b>\nАккаунт «{esc(w.label)}» не может пересылать посты, "
                              "репост через него приостановлен на час.")
                return 0
            except NOT_MEMBER as exc:
                self._forget_target(w, target_ref)
                post.repost_status = "failed"
                post.repost_error = f"Аккаунт «{w.label}» не состоит в группе-агрегаторе ({exc.__class__.__name__})"
            except CANNOT_POST:
                self._forget_target(w, target_ref)
                post.repost_status = "failed"
                post.repost_error = (
                    f"Аккаунт «{w.label}» не может писать в группу-агрегатор — проверьте, что участникам "
                    "разрешено отправлять сообщения и медиа и что аккаунт не ограничен"
                )
            except errors.InviteRequestSentError:
                post.repost_status = "failed"
                post.repost_error = "Группа принимает только по заявкам — нужна открытая публичная группа"
            except errors.MessageIdInvalidError:
                post.repost_status = "failed"
                post.repost_error = "Пост удалён в исходном канале"
            except Exception as exc:  # noqa: BLE001
                log.warning("repost of post %s failed: %r", post.id, exc)
                self._forget_target(w, target_ref)
                post.repost_status = "failed"
                post.repost_error = str(exc)[:500]
            post.reposted_at = utcnow()
            if post.repost_status == "failed":
                self._fail_streak += 1
                # once per breakage: a success resets the streak, the next breakage alerts again
                if self._fail_streak == FAILS_TO_ALERT:
                    notifier.emit("repost-failing",
                                  f"<b>Репост не работает</b>\n{self._fail_streak} ошибок подряд. "
                                  f"Последняя: {esc(post.repost_error)}")
            await s.commit()
            return int(cfg["repost_interval_sec"])

    def _forget_target(self, w: AccountWorker, ref: str) -> None:
        """Resolve the aggregator again next time: e.g. a basic group upgraded to a supergroup
        gets a new id, while its invite link keeps pointing at it."""
        self._targets.pop((w.id, ref), None)
        self._links.pop((w.id, ref), None)

    def _pause(self, w: AccountWorker, seconds: int, reason: str) -> None:
        log.warning("account %s: reposting paused for %ss (%s)", w.id, seconds, reason)
        self._paused_until[w.id] = time.monotonic() + seconds

    async def _send(self, w: AccountWorker, target, source, post: Post, ch: Channel) -> tuple[int | None, bool]:
        """Forwards the post (an album as one group). Returns the id of the copy in the aggregator
        and whether it is our own text copy instead of a forward."""
        ids = post.album_ids or [post.msg_id]
        copied = False
        try:
            sent = await self.pool.call(w, w.client.forward_messages, target, ids, source)
        except errors.ChatForwardsRestrictedError:
            # forwarding is disabled in the source channel: send text with a link instead
            sent = await self.pool.call(w, w.client.send_message, target, _copy_text(post, ch), link_preview=False)
            copied = True
        first = sent[0] if isinstance(sent, list) and sent else sent
        return getattr(first, "id", None), copied

    async def _target(self, w: AccountWorker, ref: str):
        """Input peer of the aggregator for this account, joining the group if needed."""
        key = (w.id, ref)
        if key in self._targets:
            return self._targets[key]
        value = ref.strip()
        chat = None
        if re.fullmatch(r"-?\d+", value):
            # numeric id resolves only from the account's own dialogs, i.e. it must already be a member
            raw = value.removeprefix("-")
            peer = await w.client.get_input_entity(
                types.PeerChannel(int(raw[3:])) if value.startswith("-100") else types.PeerChat(int(raw))
            )
        else:
            link = parse_channel_link(value)
            if link.invite_hash:
                invite = await self.pool.call(w, w.client, CheckChatInviteRequest(link.invite_hash))
                if isinstance(invite, (types.ChatInviteAlready, types.ChatInvitePeek)):
                    chat = invite.chat
                else:
                    result = await self.pool.call(w, w.client, ImportChatInviteRequest(link.invite_hash))
                    chat = invite_chat(result)
                    log.info("account %s joined the aggregator by invite", w.id)
            else:
                chat = await self.pool.call(w, w.client.get_entity, link.username)
                if isinstance(chat, types.Channel) and chat.left:
                    await self.pool.call(w, w.client, JoinChannelRequest(chat))
                    log.info("account %s joined the aggregator @%s", w.id, link.username)
            if not isinstance(chat, (types.Channel, types.Chat)):
                raise ValueError("Агрегатор должен быть группой или каналом, а не пользователем")
            peer = utils.get_input_peer(chat)
        if isinstance(peer, types.InputPeerChannel):
            self.collector.ignored_tg_ids.add(peer.channel_id)
        # message links: t.me/<username>/<id> for a public group, t.me/c/<id>/<id> (members only) otherwise
        username = public_username(chat)
        if username:
            self._links[key] = f"https://t.me/{username}"
        elif isinstance(peer, types.InputPeerChannel):
            self._links[key] = f"https://t.me/c/{peer.channel_id}"
        else:
            self._links[key] = None
        self._targets[key] = peer
        if isinstance(peer, types.InputPeerChat):
            await self._warn_basic_group(ref)
        return peer

    async def _warn_basic_group(self, ref: str) -> None:
        """Tells once per aggregator that a basic group gives reposts no links."""
        async with SessionLocal() as s:
            if (await settings_store.get_all(s))["aggregator_basic_warned"] == ref:
                return
            await settings_store.update(s, {"aggregator_basic_warned": ref})
        log.warning("aggregator %s is a basic group: reposts get no message links", ref)
        notifier.emit("aggregator-basic-group", BASIC_GROUP_NOTE)


def _copy_text(post: Post, ch: Channel) -> str:
    text = post.text
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT].rstrip() + "…"
    return f"{text}\n\n{ch.title or ch.link}: {_post_link(post, ch)}".strip()


def _post_link(post: Post, ch: Channel) -> str:
    if ch.username:
        return f"https://t.me/{ch.username}/{post.msg_id}"
    return f"https://t.me/c/{ch.tg_id}/{post.msg_id}"
