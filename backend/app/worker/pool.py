"""Pool of Telegram user accounts.

Every API call goes through `AccountPool.call`, which keeps one request at a time per
account with a randomized pause between them, and turns FloodWait / dead-session errors
into account state changes.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import select, update
from telethon import TelegramClient, errors, events
from telethon.tl import types

from .. import settings_store
from ..db import SessionLocal
from ..models import Account, Channel, Proxy, utcnow
from ..notify import esc, notifier, plural
from ..proxies import ProxyConfig, account_loads, pick
from ..services import release_channels
from ..tg.client import account_device, make_client

log = logging.getLogger(__name__)

DEAD_ERRORS = (
    errors.AuthKeyUnregisteredError,
    errors.AuthKeyDuplicatedError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
    errors.SessionRevokedError,
    errors.SessionExpiredError,
    errors.PhoneNumberBannedError,
)

MIN_INTERVAL = 1.0  # seconds between requests of one account

# A broken connection, not an answer from Telegram: "Cannot send requests while disconnected"
# (ConnectionError), "0 bytes read on a total of 4 expected bytes" (asyncio.IncompleteReadError,
# an EOFError) and timeouts (TimeoutError — both are OSError / EOFError subclasses)
NETWORK_ERRORS = (OSError, EOFError)
RETRY_ERROR_AFTER = 300  # seconds before restarting an account that failed to start

MessagesHandler = Callable[["AccountWorker", int, list[types.Message]], Awaitable[None]]


class AccountUnavailable(Exception):
    """The account is in FloodWait or its session died; try later / another account."""


class AccountWorker:
    def __init__(self, account: Account, client: TelegramClient):
        self.id = account.id
        self.account = account
        self.label = account.name or account.phone or f"#{account.id}"
        self.client = client
        self.config: tuple = ()  # what the client was started with; a change means restart
        self.proxy_id: int | None = None
        self.flood_until = 0.0  # monotonic
        self.next_join_at = 0.0  # monotonic
        self.lock = asyncio.Lock()
        self.last_call = 0.0

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.flood_until


def _config_key(acc: Account, proxy: ProxyConfig) -> tuple:
    return (acc.api_id, acc.api_hash, proxy)


WAIT = object()  # sentinel: the account needs a proxy but none is free and working


class AccountPool:
    def __init__(self) -> None:
        self.workers: dict[int, AccountWorker] = {}
        self.on_messages: MessagesHandler | None = None
        # Telegram says a channel changed (its title, @usernames, photo, made private or public)
        self.on_channel_changed: Callable[[int], Awaitable[None]] | None = None
        self._retry_at: dict[int, float] = {}
        self._capacity_short = False  # working proxies do not cover all accounts (alerted once)

    # --- access -------------------------------------------------------------

    def get(self, account_id: int | None) -> AccountWorker | None:
        w = self.workers.get(account_id) if account_id is not None else None
        return w if w and w.available else None

    def available(self) -> list[AccountWorker]:
        return [w for w in self.workers.values() if w.available]

    async def call(self, w: AccountWorker, fn: Callable[..., Awaitable], *args, **kwargs):
        if not w.available or self.workers.get(w.id) is not w:
            raise AccountUnavailable(w.label)
        async with w.lock:
            wait = w.last_call + MIN_INTERVAL + random.uniform(0, 0.6) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await fn(*args, **kwargs)
            except errors.FloodWaitError as exc:
                await self._mark_flood(w, exc.seconds)
                raise AccountUnavailable(f"{w.label}: FloodWait {exc.seconds}s") from exc
            except DEAD_ERRORS as exc:
                await self._mark_dead(w, exc)
                raise AccountUnavailable(f"{w.label}: {exc}") from exc
            except NETWORK_ERRORS as exc:
                # the connection broke mid-request (the proxy dropped it, the client is being
                # restarted, no answer in time): not the channel's fault — the caller retries
                # later; sync() reconnects the account if it stays disconnected
                raise AccountUnavailable(f"{w.label}: {exc!r}") from exc
            finally:
                w.last_call = time.monotonic()

    async def input_peer(self, w: AccountWorker, channel: Channel):
        try:
            return await w.client.get_input_entity(types.PeerChannel(channel.tg_id))
        except ValueError:
            if not channel.username:
                raise
            return await self.call(w, w.client.get_input_entity, channel.username)

    # --- lifecycle ----------------------------------------------------------

    async def sync(self) -> None:
        """Starts/stops clients to match the accounts table and hands out proxies."""
        async with SessionLocal() as s:
            accounts = (
                (await s.execute(select(Account).where(Account.enabled, Account.status != "dead")))
                .scalars()
                .all()
            )
            plan = await self._assign_proxies(s, accounts)
            await s.commit()

        wanted = {a.id: a for a in accounts}
        for account_id, w in list(self.workers.items()):
            acc, target = wanted.get(account_id), plan.get(account_id, WAIT)
            if acc is None or target is WAIT or _config_key(acc, target) != w.config:
                await self._stop(account_id)
            elif not w.client.is_connected():
                # Telethon gave up reconnecting (e.g. the proxy was down for a while): start afresh
                log.warning("account %s lost its connection, restarting it", account_id)
                await self._stop(account_id)

        for acc in accounts:
            target = plan[acc.id]
            if target is WAIT:
                continue
            if acc.id in self.workers:
                w = self.workers[acc.id]
                if acc.status == "flood" and w.available:
                    await self._set_status(acc.id, "active", None)
                continue
            if acc.status == "error" and time.monotonic() < self._retry_at.get(acc.id, 0):
                continue
            await self._start(acc, target)

    async def _assign_proxies(self, s, accounts: list[Account]) -> dict[int, object]:
        """account id -> ProxyConfig or WAIT; never a direct connection. Changes are written to `s`."""
        cfg = await settings_store.get_all(s)
        plan: dict[int, object] = {}
        limit = int(cfg["accounts_per_proxy"])
        proxies = list((await s.execute(select(Proxy))).scalars())
        by_id = {p.id: p for p in proxies}
        loads = await account_loads(s)
        for acc in accounts:
            current = by_id.get(acc.proxy_id)
            if current is not None and current.enabled and current.status != "dead":
                # a waiting account stays bound to its proxy and returns to it when it recovers
                if acc.status == "waiting":
                    acc.status, acc.status_detail = "new", None
                plan[acc.id] = ProxyConfig.of(current)
                continue
            if acc.proxy_id is not None:
                loads[acc.proxy_id] = loads.get(acc.proxy_id, 1) - 1
            new = pick(proxies, loads, limit)
            if new is None:
                # no free working proxy: wait, never fall back to the server's own IP
                plan[acc.id] = WAIT
                detail = (
                    "Список прокси пуст — добавьте прокси"
                    if not proxies
                    else "Нет свободного рабочего прокси — добавьте прокси в пул"
                )
                if acc.status != "waiting" or acc.status_detail != detail:
                    log.warning("account %s waits for a free working proxy", acc.id)
                    acc.status, acc.status_detail = "waiting", detail
                continue
            log.info("account %s: proxy %s -> %s", acc.id, acc.proxy_id, new.id)
            acc.proxy_id = new.id
            loads[new.id] = loads.get(new.id, 0) + 1
            self._retry_at.pop(acc.id, None)  # a new proxy deserves an immediate attempt
            if acc.status == "waiting":
                acc.status, acc.status_detail = "new", None
            plan[acc.id] = ProxyConfig.of(new)

        working = sum(1 for p in proxies if p.enabled and p.status == "ok")
        capacity = working * limit
        self._report_capacity(
            None if len(accounts) <= capacity else (len(accounts), working, capacity, limit, len(proxies))
        )
        return plan

    def _report_capacity(self, shortage: tuple | None) -> None:
        """Alerts once when working proxies stop covering all accounts; again after it was fine."""
        if shortage is None:
            self._capacity_short = False
            return
        if self._capacity_short:
            return
        self._capacity_short = True
        accounts, working, capacity, limit, total = shortage
        short = accounts - capacity
        if total == 0:
            text = "<b>Нет прокси</b>\nСписок прокси пуст — аккаунты не подключаются. Добавьте прокси."
        else:
            text = (
                f"<b>Не хватает прокси</b>\nРабочих прокси: {working} из {total}, мест: {capacity} "
                f"(по {limit} на прокси), аккаунтов: {accounts}. "
                f"{short} {plural(short, 'аккаунт ждёт', 'аккаунта ждут', 'аккаунтов ждут')} подключения — "
                "добавьте прокси."
            )
        notifier.emit("proxy-capacity", text)

    async def _start(self, acc: Account, proxy: ProxyConfig) -> None:
        log.info("starting account %s via %s", acc.id, proxy.label)
        try:
            client = make_client(acc.session, acc.api_id, acc.api_hash, proxy, device=account_device(acc))
        except ValueError as exc:
            await self._set_status(acc.id, "error", str(exc))
            self._retry_at[acc.id] = time.monotonic() + RETRY_ERROR_AFTER
            return
        try:
            await asyncio.wait_for(client.connect(), 60)
            if not await client.is_user_authorized():
                await client.disconnect()
                await self._set_status(acc.id, "dead", "Сессия не авторизована — войдите заново")
                _notify_dead(acc.id, acc.name or acc.phone or f"#{acc.id}", "нужно войти заново")
                return
            me = await client.get_me()
            # fills the entity cache so joined channels resolve by id without extra requests
            await client.get_dialogs(limit=None)
        except DEAD_ERRORS as exc:
            await client.disconnect()
            await self._set_status(acc.id, "dead", f"Сессия недействительна или аккаунт заблокирован ({exc})")
            _notify_dead(acc.id, acc.name or acc.phone or f"#{acc.id}", "нужно заменить")
            return
        except errors.FloodWaitError as exc:
            await client.disconnect()
            self._retry_at[acc.id] = time.monotonic() + exc.seconds
            await self._set_status(acc.id, "error", f"FloodWait {exc.seconds} с при запуске")
            return
        except Exception as exc:  # noqa: BLE001 — network, proxy, etc.
            await client.disconnect()
            self._retry_at[acc.id] = time.monotonic() + RETRY_ERROR_AFTER
            via = f" через прокси {proxy.label}"
            await self._set_status(acc.id, "error", f"Не удалось подключиться{via}: {exc}")
            if acc.proxy_id is not None:
                await self._suspect_proxy(acc.proxy_id)
            return

        w = AccountWorker(acc, client)
        w.config = _config_key(acc, proxy)
        w.proxy_id = acc.proxy_id
        w.label = _display_name(me) or w.label
        self._attach_handlers(w)
        self.workers[acc.id] = w
        async with SessionLocal() as s:
            await s.execute(
                update(Account)
                .where(Account.id == acc.id)
                .values(
                    status="active",
                    status_detail=None,
                    flood_until=None,
                    tg_user_id=me.id,
                    name=_display_name(me),
                    username=me.username,
                    phone=acc.phone or (f"+{me.phone}" if me.phone else None),
                    session=client.session.save(),
                )
            )
            await s.commit()
        log.info("account %s (%s) is up", acc.id, w.label)

    async def _stop(self, account_id: int) -> None:
        w = self.workers.pop(account_id, None)
        if w:
            log.info("stopping account %s", account_id)
            try:
                await w.client.disconnect()
            except Exception:  # noqa: BLE001
                log.exception("disconnect failed")

    async def stop_all(self) -> None:
        for account_id in list(self.workers):
            await self._stop(account_id)

    def _attach_handlers(self, w: AccountWorker) -> None:
        async def on_new(event: events.NewMessage.Event) -> None:
            peer = event.message.peer_id
            if self.on_messages and isinstance(peer, types.PeerChannel):
                await self.on_messages(w, peer.channel_id, [event.message])

        async def on_album(event: events.Album.Event) -> None:
            peer = event.messages[0].peer_id
            if self.on_messages and isinstance(peer, types.PeerChannel):
                await self.on_messages(w, peer.channel_id, list(event.messages))

        w.client.add_event_handler(
            on_new,
            events.NewMessage(
                func=lambda e: e.is_channel and not e.is_group and not e.out and not e.message.grouped_id
            ),
        )
        async def on_changed(update: types.UpdateChannel) -> None:
            if self.on_channel_changed:
                await self.on_channel_changed(update.channel_id)

        w.client.add_event_handler(on_changed, events.Raw(types.UpdateChannel))
        w.client.add_event_handler(
            # an album event has no `out` of its own — its messages do
            on_album, events.Album(func=lambda e: e.is_channel and not e.is_group and not e.messages[0].out)
        )

    # --- state --------------------------------------------------------------

    async def _suspect_proxy(self, proxy_id: int) -> None:
        """An account failed to connect through this proxy: have the checker look at it right away."""
        async with SessionLocal() as s:
            await s.execute(update(Proxy).where(Proxy.id == proxy_id).values(checked_at=None))
            await s.commit()

    async def _set_status(self, account_id: int, status: str, detail: str | None, **extra) -> None:
        async with SessionLocal() as s:
            await s.execute(
                update(Account)
                .where(Account.id == account_id)
                .values(status=status, status_detail=detail, **extra)
            )
            await s.commit()

    async def _mark_flood(self, w: AccountWorker, seconds: int) -> None:
        log.warning("account %s: FloodWait %ss", w.id, seconds)
        w.flood_until = time.monotonic() + seconds
        if seconds >= 3600:
            notifier.emit(
                f"flood-{w.id}",
                f"<b>Ограничение Telegram</b>\nАккаунт «{esc(w.label)}» на паузе {seconds // 3600} ч {seconds % 3600 // 60} мин.",
            )
        await self._set_status(
            w.id, "flood", f"Лимит Telegram, пауза {seconds} с",
            flood_until=utcnow() + timedelta(seconds=seconds),
        )

    async def _mark_dead(self, w: AccountWorker, exc: Exception) -> None:
        log.error("account %s died: %r", w.id, exc)
        _notify_dead(w.id, w.label, "его каналы переданы другим аккаунтам")
        self.workers.pop(w.id, None)
        asyncio.get_running_loop().create_task(w.client.disconnect())
        async with SessionLocal() as s:
            await s.execute(
                update(Account)
                .where(Account.id == w.id)
                .values(status="dead", status_detail=f"Сессия недействительна или аккаунт заблокирован ({exc})")
            )
            await release_channels(s, w.id)
            await s.commit()


def _notify_dead(account_id: int, label: str, consequence: str) -> None:
    notifier.emit(
        f"account-dead-{account_id}",
        f"<b>Аккаунт отключён</b>\n«{esc(label)}»: сессия недействительна или аккаунт заблокирован; {consequence}.",
    )


def _display_name(user) -> str | None:
    name = " ".join(p for p in (user.first_name, user.last_name) if p)
    return name or user.username
