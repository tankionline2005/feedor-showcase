"""Alerts through a Telegram bot (Bot API).

The worker emits an event every time something breaks (account died, proxy down, repost
failing, channel unavailable, too few proxies, disk filling up). Lasting conditions are
reported when they start, not every minute — the emitting code tracks the transition.
Events are collected for a few seconds and sent as one message to every subscribed chat.
A chat subscribes by opening the bot with the secret link from the settings (/start <code>);
/status returns a summary on demand.

The same bot signs users in to the web UI: "Войти через Telegram" opens it with
/start login_<code>, and the person confirms the login with a button (see logins.py).
"""

import asyncio
import html
import json
import logging
import math
import time
from datetime import timedelta

import httpx
from sqlalchemy import delete, func, select

from . import settings_store
from .db import SessionLocal
from .logins import LOGIN_PREFIX, confirm_login, decline_login, login_request
from .members import LINK_PREFIX, link_from_bot
from .models import Account, BotChat, Channel, NotifyChat, Post, Proxy, utcnow

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/{method}"
BATCH_WINDOW = 20  # seconds to collect events into one message
MESSAGE_LIMIT = 3900


class BotError(Exception):
    def __init__(self, message: str, parameters: dict | None = None):
        super().__init__(message)
        # e.g. {"migrate_to_chat_id": …} when a group became a supergroup
        self.parameters = parameters or {}


async def bot_call(token: str, method: str, *, http_timeout: float = 20, **params) -> object:
    try:
        async with httpx.AsyncClient(timeout=http_timeout) as client:
            r = await client.post(API_URL.format(token=token, method=method), json=params)
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BotError(f"нет связи с Telegram Bot API: {exc}") from exc
    if not data.get("ok"):
        raise BotError(data.get("description") or f"HTTP {r.status_code}", data.get("parameters"))
    return data["result"]


async def bot_document(token: str, chat_id: int, filename: str, data: bytes, caption: str = "") -> object:
    """Sends a file the bot makes (a document, as multipart: a file cannot go as JSON)."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(API_URL.format(token=token, method="sendDocument"),
                                  data={"chat_id": str(chat_id), "caption": caption[:1024], "parse_mode": "HTML"},
                                  files={"document": (filename, data, "text/markdown")})
        result = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BotError(f"нет связи с Telegram Bot API: {exc}") from exc
    if not result.get("ok"):
        raise BotError(result.get("description") or f"HTTP {r.status_code}", result.get("parameters"))
    return result["result"]


async def bot_upload(token: str, method: str, params: dict, files: dict[str, tuple[str, bytes, str]]) -> object:
    """A Bot API call with files of ours (multipart): `files` name → (filename, bytes, type); the params refer to
    them as attach://name, and nested params (a media group) go as JSON."""
    data = {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for k, v in params.items()}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(API_URL.format(token=token, method=method), data=data, files=files)
        result = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise BotError(f"нет связи с Telegram Bot API: {exc}") from exc
    if not result.get("ok"):
        raise BotError(result.get("description") or f"HTTP {r.status_code}", result.get("parameters"))
    return result["result"]


async def bot_file(token: str, file_id: str) -> bytes:
    """Downloads a file the bot can see (e.g. a chat photo) by its file_id."""
    info = await bot_call(token, "getFile", file_id=file_id)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"https://api.telegram.org/file/bot{token}/{info['file_path']}")
            r.raise_for_status()
    except httpx.HTTPError as exc:
        raise BotError(f"не удалось скачать файл: {exc}") from exc
    return r.content


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


async def send(token: str, chat_id: int, text: str) -> None:
    await bot_call(token, "sendMessage", chat_id=chat_id, text=text[:4096], parse_mode="HTML",
                   disable_web_page_preview=True)


async def send_to_all(token: str, text: str) -> int:
    """Sends to every subscribed chat; chats that blocked the bot are unsubscribed."""
    async with SessionLocal() as s:
        chats = list((await s.execute(select(NotifyChat))).scalars())
        sent = 0
        for chat in chats:
            try:
                await send(token, chat.chat_id, text)
                sent += 1
            except BotError as exc:
                message = str(exc).lower()
                if "blocked" in message or "chat not found" in message or "deactivated" in message:
                    log.warning("notify chat %s unsubscribed: %s", chat.chat_id, exc)
                    await s.delete(chat)
                else:
                    log.warning("notify to %s failed: %s", chat.chat_id, exc)
        await s.commit()
    return sent


async def admin_alert(text: str) -> None:
    """At once, from any process (the worker's Notifier batches its own): to every subscribed chat."""
    async with SessionLocal() as s:
        token = str((await settings_store.get_all(s))["notify_bot_token"])
    if token:
        try:
            await send_to_all(token, text)
        except Exception:  # noqa: BLE001 — an alert must never break what raised it
            log.exception("admin alert not sent")


async def tell_user(tg_user_id: int, text: str) -> bool:
    """A message from the bot to a person (HTML); False when it did not go."""
    async with SessionLocal() as s:
        token = str((await settings_store.get_all(s))["notify_bot_token"])
    if not token:
        return False
    try:
        await send(token, tg_user_id, text)
        return True
    except BotError as exc:
        log.info("message to %s not sent: %s", tg_user_id, exc)
        return False


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _counts(parts: list[tuple[int, str]]) -> str:
    return " · ".join(f"{n} {label}" for n, label in parts if n) or "нет"


async def build_summary() -> str:
    since = utcnow() - timedelta(hours=24)
    async with SessionLocal() as s:
        ch = dict((await s.execute(select(Channel.status, func.count()).group_by(Channel.status))).all())
        acc = dict(
            (await s.execute(select(Account.status, func.count()).where(Account.enabled).group_by(Account.status))).all()
        )
        px = dict(
            (await s.execute(select(Proxy.status, func.count()).where(Proxy.enabled).group_by(Proxy.status))).all()
        )
        posts = await s.scalar(select(func.count()).where(Post.posted_at >= since)) or 0
        reposts = dict(
            (
                await s.execute(
                    select(Post.repost_status, func.count()).where(Post.reposted_at >= since).group_by(Post.repost_status)
                )
            ).all()
        )
        top = (
            await s.execute(
                select(Post, Channel)
                .join(Channel, Post.channel_id == Channel.id)
                .where(Post.posted_at >= since)
                .order_by(Post.views.desc())
                .limit(3)
            )
        ).all()

    lines = ["<b>Мониторинг — сводка</b>", ""]
    lines.append("Каналы: " + _counts([(ch.get("active", 0), "активны"), (ch.get("pending", 0), "ждут вступления"),
                                        (ch.get("error", 0), "с ошибкой"), (ch.get("paused", 0), "на паузе"),
                                        (ch.get("restricted", 0), "запрещены во всех странах аккаунтов")]))
    lines.append("Аккаунты: " + _counts([(acc.get("active", 0), "в сети"), (acc.get("flood", 0), "с ограничением"),
                                          (acc.get("waiting", 0), "ждут прокси"), (acc.get("error", 0), "с ошибкой"),
                                          (acc.get("dead", 0), "отключены")]))
    if px:
        lines.append("Прокси: " + _counts([(px.get("ok", 0), "работают"), (px.get("dead", 0), "не работают")]))
    lines.append(
        f"За сутки постов: {posts} · переслано {reposts.get('done', 0)}"
        + (f" · ошибок репоста {reposts['failed']}" if reposts.get("failed") else "")
    )
    if top:
        lines += ["", "<b>Топ по просмотрам</b>"]
        for i, (post, channel) in enumerate(top, 1):
            name = esc(channel.title or channel.link)
            link = f"https://t.me/{channel.username}/{post.msg_id}" if channel.username else None
            views = f"{post.views:,}".replace(",", " ")
            title = f'<a href="{link}">{name}</a>' if link else name
            lines.append(f"{i}. {title} — {views} {plural(post.views, 'просмотр', 'просмотра', 'просмотров')}")
    return "\n".join(lines)


async def remember_chat(change: dict) -> None:
    """The bot was added to a group, promoted, or removed from it: remember the group and who did it."""
    chat = change.get("chat") or {}
    if chat.get("type") not in ("group", "supergroup", "channel"):
        return
    status = (change.get("new_chat_member") or {}).get("status", "member")
    async with SessionLocal() as s:
        row = await s.get(BotChat, chat["id"])
        if row is None:
            row = BotChat(chat_id=chat["id"], type=chat["type"], status=status)
            s.add(row)
        row.type, row.status = chat["type"], status
        row.title, row.username = chat.get("title"), chat.get("username")
        if status in ("member", "administrator"):
            row.added_by = (change.get("from") or {}).get("id") or row.added_by
        row.updated_at = utcnow()
        await s.commit()
    log.info("bot is %s in %s %s (%s)", status, chat["type"], chat["id"], chat.get("title"))


LOGIN_EXPIRED = "Ссылка для входа устарела. Нажмите «Войти через Telegram» на сайте ещё раз."
BLOCKED_TEXT = "Доступ к аккаунту закрыт администратором."


class Notifier:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._last: dict[str, float] = {}
        self._offset = 0
        self._token: str | None = None

    def emit(self, key: str, text: str, *, cooldown: float = 60) -> None:
        """Queues an alert. The short cooldown only swallows an accidental double report of the
        same event (e.g. two loops noticing it at once); lasting conditions are deduplicated by
        the emitting code, which reports transitions."""
        now = time.monotonic()
        if now - self._last.get(key, -math.inf) < cooldown:
            return
        self._last[key] = now
        log.info("notify: %s", key)
        self.queue.put_nowait(text)

    def clear(self, key: str) -> None:
        """The problem is gone: report it again right away if it comes back."""
        self._last.pop(key, None)

    async def run(self) -> None:
        from .worker.util import forever

        await asyncio.gather(self._sender(), forever("bot updates", self.poll_step, 1))

    async def _settings(self) -> dict:
        async with SessionLocal() as s:
            return await settings_store.get_all(s)

    async def _sender(self) -> None:
        while True:
            first = await self.queue.get()
            await asyncio.sleep(BATCH_WINDOW)
            items = [first]
            while not self.queue.empty():
                items.append(self.queue.get_nowait())
            token = str((await self._settings())["notify_bot_token"])
            if not token:
                continue
            # several messages when a burst does not fit into one
            chunk: list[str] = []
            for item in items + [None]:
                if item is None or sum(len(x) + 2 for x in chunk) + len(item) > MESSAGE_LIMIT:
                    if chunk:
                        try:
                            await send_to_all(token, "\n\n".join(chunk))
                        except Exception:  # noqa: BLE001
                            log.exception("sending alerts failed")
                    chunk = []
                if item is not None:
                    chunk.append(item)

    async def poll_step(self) -> float:
        """Handles /start, /stop and /status sent to the bot."""
        cfg = await self._settings()
        token = str(cfg["notify_bot_token"])
        if not token:
            return 15
        if token != self._token:
            self._token, self._offset = token, 0
        try:
            updates = await bot_call(token, "getUpdates", http_timeout=40, offset=self._offset, timeout=25,
                                     allowed_updates=["message", "callback_query", "my_chat_member"])
        except BotError as exc:
            log.warning("bot getUpdates: %s", exc)
            return 30
        for update in updates:
            self._offset = update["update_id"] + 1
            try:
                if "my_chat_member" in update:
                    await remember_chat(update["my_chat_member"])
                elif "callback_query" in update:
                    await self.handle_button(token, update["callback_query"])
                else:
                    await self.handle_message(token, str(cfg["notify_link_code"]), update.get("message") or {})
            except Exception:  # noqa: BLE001
                log.exception("bot update handling failed")
        return 0

    async def ask_login(self, token: str, chat_id: int, code: str) -> None:
        """/start login_<code>: the login is confirmed only by a button, never by the link alone —
        otherwise anyone could send a person their own link and get signed in as them."""
        req = await login_request(code)
        if req is None:
            await send(token, chat_id, LOGIN_EXPIRED)
            return
        await bot_call(
            token, "sendMessage", chat_id=chat_id, parse_mode="HTML",
            text=(
                "<b>Вход на сайт мониторинга</b>\n\n"
                f"Устройство: {esc(req.device or 'неизвестно')}\n"
                f"IP-адрес: {esc(req.ip or 'неизвестен')}\n\n"
                "Подтвердите, если вход запрашиваете вы. Если нет — нажмите «Отмена»."
            ),
            reply_markup={"inline_keyboard": [
                [{"text": "Подтвердить вход", "callback_data": f"login:{code}"}],
                [{"text": "Отмена", "callback_data": f"nologin:{code}"}],
            ]},
        )

    async def handle_button(self, token: str, query: dict) -> None:
        action, _, code = (query.get("data") or "").partition(":")
        message = query.get("message") or {}
        sender = query.get("from") or {}
        if action == "login":
            result, user, is_new = await confirm_login(code, sender)
            text = {"ok": "Вход подтверждён — вернитесь на сайт.", "blocked": BLOCKED_TEXT}.get(result, LOGIN_EXPIRED)
            if is_new and user is not None:
                name = " ".join(x for x in (user.first_name, user.last_name) if x) or "без имени"
                handle = f" (@{esc(user.username)})" if user.username else ""
                self.emit(f"user-new-{user.id}", f"<b>Новый пользователь</b>\n{esc(name)}{handle}")
        elif action == "nologin":
            await decline_login(code)
            text = "Вход отменён."
        else:
            text = None
        await bot_call(token, "answerCallbackQuery", callback_query_id=query["id"])
        if text and message.get("chat"):
            # the buttons go away, so the same message cannot be confirmed twice
            await bot_call(token, "editMessageText", chat_id=message["chat"]["id"],
                           message_id=message["message_id"], text=text)

    async def handle_message(self, token: str, code: str, message: dict) -> None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        if not chat_id or not text.startswith("/"):
            return
        command, _, arg = text.partition(" ")
        command = command.split("@")[0]
        if command == "/start" and arg.strip().startswith(LOGIN_PREFIX):
            if chat.get("type") == "private":
                await self.ask_login(token, chat_id, arg.strip().removeprefix(LOGIN_PREFIX))
            return
        if command == "/start" and arg.strip().startswith(LINK_PREFIX):
            if chat.get("type") == "private":
                member = await link_from_bot(arg.strip().removeprefix(LINK_PREFIX), message.get("from") or chat)
                await send(token, chat_id, (
                    f"Telegram привязан к аккаунту сотрудника <b>{esc(member.login)}</b>. Теперь «Посмотреть "
                    "оригинал поста» на сайте присылает посты сюда."
                ) if member else "Ссылка для привязки устарела. Нажмите «Привязать Telegram» на сайте ещё раз.")
            return
        async with SessionLocal() as s:
            subscribed = await s.get(NotifyChat, chat_id)
            if command == "/start":
                if subscribed:
                    await send(token, chat_id, "Уведомления уже подключены. /status — сводка, /stop — отключить.")
                elif code and arg.strip() == code:
                    title = chat.get("title") or " ".join(
                        x for x in (chat.get("first_name"), chat.get("last_name")) if x
                    ) or chat.get("username")
                    s.add(NotifyChat(chat_id=chat_id, title=title))
                    await s.commit()
                    await send(token, chat_id, "<b>Уведомления подключены.</b>\n"
                                               "Сюда будут приходить сообщения о проблемах: отключённые аккаунты, "
                                               "нерабочие прокси, сломанный репост, недоступные каналы, нехватка "
                                               "прокси и места на сервере.\n"
                                               "/status — сводка сейчас, /stop — отключить.")
                else:
                    await send(token, chat_id, "Это бот сервиса мониторинга Telegram-каналов. Чтобы войти на сайт, "
                                               "нажмите там «Войти через Telegram».")
            elif command == "/stop" and subscribed:
                await s.execute(delete(NotifyChat).where(NotifyChat.chat_id == chat_id))
                await s.commit()
                await send(token, chat_id, "Уведомления отключены. Подключить снова — по ссылке из настроек.")
            elif command == "/status" and subscribed:
                await send(token, chat_id, await build_summary())


notifier = Notifier()
