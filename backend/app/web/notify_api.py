"""The alert bot as the admin sees it: connecting, changing or disconnecting it, its recipients.

The same bot signs users in and sends them posts, so a change matters to everyone. NOTIFY_BOT_TOKEN
in the server's .env only sets up a fresh server (see main.py); after that the admin manages it here.
"""

import logging
import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings_store
from ..models import NotifyChat
from ..notify import BotError, bot_call, send_to_all
from .deps import DB, Admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notify", tags=["notify"])


async def _state(db) -> dict:
    cfg = await settings_store.get_all(db)
    chats = (await db.execute(select(NotifyChat).order_by(NotifyChat.created_at))).scalars().all()
    username = cfg["notify_bot_username"]
    return {
        "configured": bool(cfg["notify_bot_token"]),
        "bot_username": username or None,
        "link": f"https://t.me/{username}?start={cfg['notify_link_code']}" if username else None,
        "chats": [{"chat_id": c.chat_id, "title": c.title} for c in chats],
    }


@router.get("")
async def get_state(_: Admin, db: DB) -> dict:
    return await _state(db)


@router.delete("/chats/{chat_id}")
async def remove_chat(chat_id: int, _: Admin, db: DB) -> dict:
    await db.execute(delete(NotifyChat).where(NotifyChat.chat_id == chat_id))
    await db.commit()
    return await _state(db)


class TokenIn(BaseModel):
    token: str = Field(max_length=200)


@router.put("/token")
async def set_token(body: TokenIn, _: Admin, db: DB) -> dict:
    token = body.token.strip()
    if not token:
        # disconnect the bot entirely
        await settings_store.update(db, {"notify_bot_token": "", "notify_bot_username": "", "notify_link_code": ""})
        await db.execute(delete(NotifyChat))
        await db.commit()
        return await _state(db)
    try:
        await connect_bot(db, token, strict=True)
    except BotError as exc:
        raise HTTPException(status_code=422, detail=f"Токен не подошёл: {exc}") from exc
    return await _state(db)


@router.post("/test")
async def test_message(_: Admin, db: DB) -> dict:
    cfg = await settings_store.get_all(db)
    if not cfg["notify_bot_token"]:
        raise HTTPException(status_code=409, detail="Бот не подключён")
    sent = await send_to_all(str(cfg["notify_bot_token"]), "Тестовое уведомление от мониторинга — всё работает.")
    if not sent:
        raise HTTPException(status_code=409, detail="Нет подключённых чатов — откройте бота по ссылке и нажмите Start")
    return {"sent": sent}


async def connect_bot(db: AsyncSession, token: str, *, strict: bool = False) -> None:
    """Makes `token` the alert bot. A different bot than before starts with no recipients:
    chats of another bot cannot receive messages from this one. `strict`: raise BotError
    when Telegram does not accept the token (otherwise it is logged and nothing changes)."""
    cfg = await settings_store.get_all(db)
    if cfg["notify_bot_token"] == token and cfg["notify_bot_username"]:
        return
    try:
        me = await bot_call(token, "getMe")
    except BotError as exc:
        if strict:
            raise
        log.error("NOTIFY_BOT_TOKEN was not accepted by Telegram: %s", exc)
        return
    try:
        # getUpdates does not work while a webhook is set
        await bot_call(token, "deleteWebhook")
    except BotError:
        pass
    same_bot = cfg["notify_bot_token"] == token
    if not same_bot:
        await db.execute(delete(NotifyChat))
    await settings_store.update(db, {
        "notify_bot_token": token,
        "notify_bot_username": me.get("username") or "",
        "notify_link_code": cfg["notify_link_code"] if same_bot else secrets.token_urlsafe(9),
    })
    log.info("alert bot set to @%s", me.get("username"))
