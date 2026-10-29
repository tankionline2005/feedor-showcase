"""Backup accounts inside private channels.

A public channel lost by one account is simply joined by another. A private one (added by an
invite link) is not that easy: the link may be revoked, a join request may never be approved
again. So every private channel keeps a backup account inside (PRIVATE_ACCOUNTS: the main one
and a backup):

- once the main account is in, a backup joins too — after 1–6 hours, so the channel admin does
  not see a burst of strangers at once, and through a different proxy than the main one;
- a backup does nothing while the main account is fine, except a cheap membership check every
  few hours;
- when the main account loses the channel (kicked, banned, its session died) a backup takes over
  at once and a new backup is planned;
- if the invite link stops working, the admin is told that the channel is down to one account.
"""

import logging
import random
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import delete, select
from telethon import errors
from telethon.tl import types
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

from .. import settings_store
from ..db import SessionLocal
from ..models import Account, Channel, ChannelReserve, utcnow
from ..notify import esc, notifier
from ..services import account_loads
from ..tg.parse import invite_chat
from .pool import AccountPool, AccountUnavailable, AccountWorker

log = logging.getLogger(__name__)

JOIN_DELAY = (timedelta(hours=1), timedelta(hours=6))  # after the main account got in
REQUEST_RECHECK = timedelta(minutes=10)
MEMBER_CHECK = timedelta(hours=6)
FAILED_RETRY = timedelta(hours=24)
RETRY_SOON = timedelta(minutes=5)
RETRY_LATER = timedelta(hours=1)
STEP_EVERY = 60
PRIVATE_ACCOUNTS = 2  # accounts inside every private channel: the main one and a backup


def backups_wanted() -> int:
    return max(0, PRIVATE_ACCOUNTS - 1)


def _later(delta: timedelta):
    return utcnow() + delta


class Reserves:
    def __init__(self, pool: AccountPool, on_change: Callable[[], Awaitable[None]]) -> None:
        self.pool = pool
        self.on_change = on_change  # the channel → account map changed

    async def step(self) -> None:
        await self.plan()
        await self.act()

    # --- who should back up which channel ------------------------------------

    async def plan(self) -> None:
        async with SessionLocal() as s:
            need = backups_wanted()
            alive = set((await s.execute(
                select(Account.id).where(Account.enabled, Account.status != "dead")
            )).scalars())
            # backups of accounts that are gone or switched off count for nothing
            await s.execute(delete(ChannelReserve).where(ChannelReserve.account_id.not_in(alive)))
            channels = (await s.execute(
                select(Channel).where(
                    Channel.invite_hash.is_not(None),
                    Channel.status == "active",
                    Channel.account_id.is_not(None),
                    Channel.tg_id.is_not(None),
                )
            )).scalars().all()
            reserves: dict[int, list[ChannelReserve]] = defaultdict(list)
            for r in (await s.execute(select(ChannelReserve))).scalars():
                reserves[r.channel_id].append(r)
            loads = await account_loads(s)
            limits = dict((await s.execute(select(Account.id, Account.max_channels))).all())

            for ch in channels:
                rows = [r for r in reserves[ch.id] if r.account_id != ch.account_id]
                for r in reserves[ch.id]:
                    if r.account_id == ch.account_id:
                        await s.delete(r)  # it became the main account
                # fewer wanted now: drop the ones not inside yet
                extra = len(rows) - need
                for r in sorted(rows, key=lambda r: r.status == "member"):
                    if extra <= 0:
                        break
                    if r.status != "member":
                        await s.delete(r)
                        rows.remove(r)
                        extra -= 1
                if len(rows) >= need:
                    continue
                taken = {ch.account_id, *(r.account_id for r in rows)}
                blocked = set(ch.restricted_for or []) | set(ch.banned_for or [])
                main = self.pool.workers.get(ch.account_id)
                candidates = [
                    w for w in self.pool.available()
                    if w.id not in taken | blocked and w.id in alive and loads.get(w.id, 0) < limits.get(w.id, 0)
                ]
                # another proxy than the main account's, so one broken proxy does not cut both off
                candidates.sort(key=lambda w: (main is not None and w.proxy_id == main.proxy_id,
                                               loads.get(w.id, 0), random.random()))
                for w in candidates[: need - len(rows)]:
                    delay = random.uniform(*(d.total_seconds() for d in JOIN_DELAY))
                    s.add(ChannelReserve(channel_id=ch.id, account_id=w.id, status="planned",
                                         due_at=_later(timedelta(seconds=delay))))
                    loads[w.id] = loads.get(w.id, 0) + 1
                    log.info("channel %s: backup account %s planned in %.1f h", ch.id, w.id, delay / 3600)
            await s.commit()

    # --- joining, waiting for approval, checking membership -------------------

    async def act(self) -> None:
        async with SessionLocal() as s:
            cfg = await settings_store.get_all(s)
            due = (await s.execute(
                select(ChannelReserve, Channel)
                .join(Channel, Channel.id == ChannelReserve.channel_id)
                .where(ChannelReserve.due_at <= utcnow(), Channel.status != "deleting")
                .order_by(ChannelReserve.due_at)
                .limit(30)
            )).all()
        for r, ch in due:
            w = self.pool.get(r.account_id)
            if w is None:
                continue  # in FloodWait or reconnecting: later
            if r.status in ("planned", "failed"):
                now = time.monotonic()
                if now < w.next_join_at:
                    continue
                w.next_join_at = now + int(cfg["join_interval_sec"]) * random.uniform(1.0, 1.5)
                await self._join(w, ch, r)
            elif r.status == "requested":
                await self._join(w, ch, r)
            elif r.status == "member":
                await self._check(w, ch, r)

    async def _join(self, w: AccountWorker, ch: Channel, r: ChannelReserve) -> None:
        status, due, detail = r.status, None, None
        try:
            invite = await self.pool.call(w, w.client, CheckChatInviteRequest(ch.invite_hash))
            if isinstance(invite, types.ChatInviteAlready):
                status, due = "member", _later(MEMBER_CHECK)
            elif getattr(invite, "request_needed", False) and r.status == "requested":
                due = _later(REQUEST_RECHECK)  # still waiting for the channel admin
            else:
                try:
                    chat = invite_chat(await self.pool.call(w, w.client, ImportChatInviteRequest(ch.invite_hash)))
                    status, due = "member", _later(MEMBER_CHECK)
                    if getattr(chat, "id", ch.tg_id) != ch.tg_id:
                        status, due, detail = "failed", _later(FAILED_RETRY), "ссылка ведёт в другой чат"
                except errors.UserAlreadyParticipantError:
                    status, due = "member", _later(MEMBER_CHECK)
                except errors.InviteRequestSentError:
                    status, due = "requested", _later(REQUEST_RECHECK)
        except AccountUnavailable:
            due = _later(RETRY_SOON)
        except (errors.InviteHashExpiredError, errors.InviteHashInvalidError):
            status, due, detail = "failed", _later(FAILED_RETRY), "ссылка-приглашение больше не работает"
            if r.status != "failed":
                notifier.emit(
                    f"reserve-link-{ch.id}",
                    f"<b>Нет запасного аккаунта</b>\n{esc(ch.title or ch.link)}: ссылка-приглашение больше "
                    "не работает, запасной аккаунт вступить не может. Канал читает только один аккаунт.",
                )
        except errors.ChannelsTooMuchError:
            await self._drop(r)  # this account is full: another one is planned instead
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("backup %s for channel %s: %s", w.id, ch.id, exc)
            due, detail = _later(RETRY_LATER), str(exc)[:500]
        if status != r.status:
            log.info("channel %s: backup account %s is %s", ch.id, w.id, status)
        await self._update(r, status=status, due_at=due, detail=detail)

    async def _check(self, w: AccountWorker, ch: Channel, r: ChannelReserve) -> None:
        try:
            peer = await self.pool.input_peer(w, ch)
            await self.pool.call(w, w.client.get_messages, peer, limit=1)
        except AccountUnavailable:
            await self._update(r, due_at=_later(RETRY_SOON))
            return
        except (errors.ChannelPrivateError, errors.ChannelInvalidError):
            # the backup itself was removed from the channel
            log.warning("channel %s: backup account %s lost access", ch.id, w.id)
            await self._drop(r, ban=True)
            notifier.emit(
                f"reserve-lost-{ch.id}-{w.id}",
                f"<b>Запасной аккаунт потерял доступ</b>\n{esc(ch.title or ch.link)}: аккаунт «{esc(w.label)}» "
                "больше не в канале. Попробуем добавить другой запасной.",
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.info("membership check of backup %s in channel %s: %s", w.id, ch.id, exc)
        await self._update(r, due_at=_later(MEMBER_CHECK), detail=None)

    # --- taking over -----------------------------------------------------------

    async def promote(self, ch: Channel, lost: AccountWorker | None = None) -> bool:
        """The main account lost the channel: a backup already inside becomes the main one."""
        async with SessionLocal() as s:
            rows = (await s.execute(
                select(ChannelReserve)
                .where(ChannelReserve.channel_id == ch.id, ChannelReserve.status == "member")
                .order_by(ChannelReserve.created_at)
            )).scalars().all()
            backup = next((self.pool.get(r.account_id) for r in rows if self.pool.get(r.account_id)), None)
            if backup is None:
                return False
            channel = await s.get(Channel, ch.id)
            channel.account_id, channel.status, channel.status_detail = backup.id, "active", None
            if lost is not None:
                # it is not inside any more: never planned as this channel's backup again
                channel.banned_for = sorted(set(channel.banned_for or []) | {lost.id})
            await s.execute(delete(ChannelReserve).where(
                ChannelReserve.channel_id == ch.id, ChannelReserve.account_id == backup.id
            ))
            await s.commit()
        await self.on_change()
        log.warning("channel %s: backup account %s took over%s", ch.id, backup.id,
                    f" from account {lost.id}" if lost else "")
        was = f"основной аккаунт «{esc(lost.label)}» потерял доступ" if lost else "основной аккаунт отключился"
        notifier.emit(
            f"reserve-took-over-{ch.id}",
            f"<b>Сработал запасной аккаунт</b>\n{esc(ch.title or ch.link)}: {was}, канал теперь читает "
            f"«{esc(backup.label)}». Новый запасной добавится сам.",
        )
        return True

    async def leave_all(self, ch: Channel) -> None:
        """The channel is being removed: backups leave it too (best effort)."""
        async with SessionLocal() as s:
            rows = (await s.execute(select(ChannelReserve).where(ChannelReserve.channel_id == ch.id))).scalars().all()
        for r in rows:
            w = self.pool.get(r.account_id)
            if w is None or r.status != "member" or not ch.tg_id:
                continue
            try:
                peer = await self.pool.input_peer(w, ch)
                await self.pool.call(w, w.client, LeaveChannelRequest(peer))
            except Exception as exc:  # noqa: BLE001
                log.info("backup %s leaving channel %s: %s", w.id, ch.id, exc)

    # --- helpers -----------------------------------------------------------------

    async def _update(self, r: ChannelReserve, **values) -> None:
        async with SessionLocal() as s:
            row = await s.get(ChannelReserve, (r.channel_id, r.account_id))
            if row is None:
                return
            for key, value in values.items():
                setattr(row, key, value)
            await s.commit()

    async def _drop(self, r: ChannelReserve, *, ban: bool = False) -> None:
        async with SessionLocal() as s:
            await s.execute(delete(ChannelReserve).where(
                ChannelReserve.channel_id == r.channel_id, ChannelReserve.account_id == r.account_id
            ))
            if ban:
                ch = await s.get(Channel, r.channel_id)
                if ch is not None:
                    ch.banned_for = sorted(set(ch.banned_for or []) | {r.account_id})
            await s.commit()

