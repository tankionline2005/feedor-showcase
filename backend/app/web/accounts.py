"""Telegram accounts: list, interactive login by phone, session import.

Proxies come from the pool: a new account logs in through the least loaded working proxy,
so its very first IP is already the proxy's.
"""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient, errors

from .. import settings_store
from ..models import Account, Proxy
from ..proxies import ProxyConfig, account_loads, choose_for_new_account, pick
from ..services import account_loads, release_channels
from ..tg.client import DEVICE_FIELDS, make_client
from ..tg.session_files import parse_uploads
from .deps import DB, Admin
from .posts import iso

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/accounts", tags=["accounts"])

LOGIN_TTL = 600  # seconds a started login stays valid
NO_FREE_PROXY = "Нет свободного рабочего прокси — добавьте прокси в разделе «Прокси»"


def account_out(acc: Account, channels: int = 0, proxy: Proxy | None = None) -> dict:
    return {
        "id": acc.id,
        "phone": acc.phone,
        "name": acc.name,
        "username": acc.username,
        "enabled": acc.enabled,
        "status": acc.status,
        "status_detail": acc.status_detail,
        "flood_until": iso(acc.flood_until),
        "proxy": {"id": proxy.id, "label": f"{proxy.host}:{proxy.port}", "kind": proxy.kind, "status": proxy.status}
        if proxy
        else None,
        "custom_api": acc.api_id is not None,
        "max_channels": acc.max_channels,
        "channels": channels,
        "created_at": iso(acc.created_at),
    }


async def _account_out(db: AsyncSession, acc: Account) -> dict:
    count = (await account_loads(db)).get(acc.id, 0)
    proxy = await db.get(Proxy, acc.proxy_id) if acc.proxy_id else None
    return account_out(acc, count, proxy)


@router.get("")
async def list_accounts(_: Admin, db: DB) -> dict:
    accounts = (await db.execute(select(Account).order_by(Account.created_at))).scalars().all()
    counts = await account_loads(db)  # backups in private channels take a place too
    proxies = {p.id: p for p in (await db.execute(select(Proxy))).scalars()}
    return {"items": [account_out(a, counts.get(a.id, 0), proxies.get(a.proxy_id)) for a in accounts]}


async def _limit(db: AsyncSession) -> int:
    return int((await settings_store.get_all(db))["accounts_per_proxy"])


async def _proxy_for_login(db: AsyncSession, phone: str | None = None) -> Proxy:
    """The proxy a new login goes through. 409 if none is free: a login never uses the server's own IP."""
    cfg = await settings_store.get_all(db)
    if phone:
        # logging in again: keep the proxy the account already has, if it still works
        existing = await db.scalar(select(Account).where(Account.phone == phone))
        if existing and existing.proxy_id:
            current = await db.get(Proxy, existing.proxy_id)
            if current and current.enabled and current.status == "ok":
                return current
    proxy = await choose_for_new_account(db, cfg)
    if proxy is None:
        raise HTTPException(status_code=409, detail=NO_FREE_PROXY)
    return proxy


# --- login by phone -----------------------------------------------------------


@dataclass
class PendingLogin:
    client: TelegramClient
    phone: str
    phone_code_hash: str
    api_id: int | None
    api_hash: str | None
    proxy_id: int | None
    created: float = field(default_factory=time.monotonic)


_logins: dict[str, PendingLogin] = {}


async def _drop_expired() -> None:
    now = time.monotonic()
    for key, login in list(_logins.items()):
        if now - login.created > LOGIN_TTL:
            _logins.pop(key, None)
            await login.client.disconnect()


class ConnectionIn(BaseModel):
    api_id: int | None = None
    api_hash: str | None = Field(default=None, max_length=64)


def _clean_connection(body: ConnectionIn) -> tuple[int | None, str | None]:
    api_hash = (body.api_hash or "").strip() or None
    if (body.api_id is None) != (api_hash is None):
        raise HTTPException(status_code=422, detail="api_id и api_hash указываются вместе")
    return body.api_id, api_hash


def _via(proxy: Proxy) -> str:
    return f" через прокси {proxy.host}:{proxy.port}"


class StartIn(ConnectionIn):
    phone: str = Field(min_length=5, max_length=32)


@router.post("/login/start")
async def login_start(body: StartIn, _: Admin, db: DB) -> dict:
    await _drop_expired()
    api_id, api_hash = _clean_connection(body)
    phone = "+" + "".join(ch for ch in body.phone if ch.isdigit())
    proxy = await _proxy_for_login(db, phone)
    try:
        client = make_client("", api_id, api_hash, ProxyConfig.of(proxy), receive_updates=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        await asyncio.wait_for(client.connect(), 30)
        sent = await client.send_code_request(phone)
    except errors.PhoneNumberInvalidError as exc:
        await client.disconnect()
        raise HTTPException(status_code=422, detail="Неверный номер телефона") from exc
    except errors.PhoneNumberBannedError as exc:
        await client.disconnect()
        raise HTTPException(status_code=422, detail="Номер заблокирован в Telegram") from exc
    except errors.FloodWaitError as exc:
        await client.disconnect()
        raise HTTPException(status_code=429, detail=f"Слишком много попыток, подождите {exc.seconds} с") from exc
    except Exception as exc:  # noqa: BLE001
        await client.disconnect()
        log.warning("login start failed: %r", exc)
        raise HTTPException(status_code=502, detail=f"Не удалось связаться с Telegram{_via(proxy)}: {exc}") from exc
    login_id = secrets.token_urlsafe(16)
    _logins[login_id] = PendingLogin(client, phone, sent.phone_code_hash, api_id, api_hash, proxy.id)
    return {"login_id": login_id}


class CodeIn(BaseModel):
    login_id: str
    code: str = Field(min_length=3, max_length=12)


class PasswordIn(BaseModel):
    login_id: str
    password: str = Field(min_length=1, max_length=256)


def _pending(login_id: str) -> PendingLogin:
    login = _logins.get(login_id)
    if login is None:
        raise HTTPException(status_code=410, detail="Вход устарел, начните заново")
    return login


async def _save_account(db: AsyncSession, me, session: str, *, phone: str | None, api_id, api_hash, proxy_id) -> Account:
    existing = await db.scalar(select(Account).where(Account.tg_user_id == me.id))
    acc = existing or Account()
    acc.tg_user_id = me.id
    acc.phone = phone or (f"+{me.phone}" if me.phone else acc.phone)
    acc.name = " ".join(p for p in (me.first_name, me.last_name) if p) or me.username
    acc.username = me.username
    acc.session = session
    acc.api_id, acc.api_hash, acc.proxy_id = api_id, api_hash, proxy_id
    acc.status, acc.status_detail, acc.enabled = "new", None, True
    if existing is None:
        db.add(acc)
    await db.commit()
    return acc


async def _finish(login_id: str, db: AsyncSession) -> dict:
    login = _logins.pop(login_id)
    try:
        me = await login.client.get_me()
        session = login.client.session.save()
    finally:
        await login.client.disconnect()
    acc = await _save_account(
        db, me, session, phone=login.phone, api_id=login.api_id, api_hash=login.api_hash, proxy_id=login.proxy_id
    )
    return {"status": "ok", "account": await _account_out(db, acc)}


@router.post("/login/code")
async def login_code(body: CodeIn, _: Admin, db: DB) -> dict:
    login = _pending(body.login_id)
    try:
        await login.client.sign_in(login.phone, body.code.strip(), phone_code_hash=login.phone_code_hash)
    except errors.SessionPasswordNeededError:
        return {"status": "password_needed"}
    except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError) as exc:
        raise HTTPException(status_code=422, detail="Неверный код") from exc
    except errors.PhoneCodeExpiredError as exc:
        _logins.pop(body.login_id, None)
        await login.client.disconnect()
        raise HTTPException(status_code=410, detail="Код истёк, начните заново") from exc
    return await _finish(body.login_id, db)


@router.post("/login/password")
async def login_password(body: PasswordIn, _: Admin, db: DB) -> dict:
    login = _pending(body.login_id)
    try:
        await login.client.sign_in(password=body.password)
    except errors.PasswordHashInvalidError as exc:
        raise HTTPException(status_code=422, detail="Неверный пароль двухэтапной проверки") from exc
    return await _finish(body.login_id, db)


class CancelIn(BaseModel):
    login_id: str


@router.post("/login/cancel")
async def login_cancel(body: CancelIn, _: Admin) -> dict:
    login = _logins.pop(body.login_id, None)
    if login:
        await login.client.disconnect()
    return {"ok": True}


# --- session import -----------------------------------------------------------


class ImportIn(ConnectionIn):
    session: str = Field(min_length=10, max_length=2000)


@router.post("/import")
async def import_session(body: ImportIn, _: Admin, db: DB) -> dict:
    api_id, api_hash = _clean_connection(body)
    proxy = await _proxy_for_login(db)
    try:
        client = make_client(body.session.strip(), api_id, api_hash, ProxyConfig.of(proxy), receive_updates=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Некорректная строка сессии: {exc}") from exc
    try:
        await asyncio.wait_for(client.connect(), 30)
        if not await client.is_user_authorized():
            raise HTTPException(status_code=422, detail="Сессия не авторизована")
        me = await client.get_me()
        session = client.session.save()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Сессия не работает{_via(proxy)}: {exc}") from exc
    finally:
        await client.disconnect()
    acc = await _save_account(
        db, me, session, phone=None, api_id=api_id, api_hash=api_hash, proxy_id=proxy.id
    )
    return {"account": await _account_out(db, acc)}


# --- bulk import from files ---------------------------------------------------

MAX_UPLOAD = 100 * 1024 * 1024


@router.post("/import-files")
async def import_files(_: Admin, db: DB, files: list[UploadFile] = File(...)) -> dict:
    """Adds accounts from .session/.json/.zip/.txt files without connecting.

    The worker connects each one through a pool proxy; an unauthorized session turns "dead".
    """
    uploads: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        data = await f.read()
        total += len(data)
        if total > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="Слишком большие файлы — больше 100 МБ за раз")
        uploads.append((f.filename or "file", data))
    try:
        parsed, errors = parse_uploads(uploads)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    known_sessions = set((await db.execute(select(Account.session))).scalars())
    known_users = {u for u in (await db.execute(select(Account.tg_user_id))).scalars() if u}
    added: list[Account] = []
    duplicates: list[str] = []
    for item in parsed:
        if item.session in known_sessions or (item.user_id and item.user_id in known_users):
            duplicates.append(item.name)
            continue
        known_sessions.add(item.session)
        if item.user_id:
            known_users.add(item.user_id)
        acc = Account(
            session=item.session,
            phone=item.phone,
            tg_user_id=item.user_id,
            name=item.phone or item.name.rsplit(".", 1)[0],
            api_id=item.api_id,
            api_hash=item.api_hash,
            status="new",
            enabled=True,
            **{k: v for k, v in item.device.items() if k in DEVICE_FIELDS},
        )
        db.add(acc)
        added.append(acc)
    await db.commit()
    return {
        "added": len(added),
        "duplicates": duplicates,
        "errors": [{"file": name, "error": error} for name, error in errors],
    }


# --- edit / delete ------------------------------------------------------------


class PatchIn(BaseModel):
    enabled: bool | None = None
    max_channels: int | None = Field(default=None, ge=1, le=1000)


@router.patch("/{account_id}")
async def patch_account(account_id: int, body: PatchIn, _: Admin, db: DB) -> dict:
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(status_code=404)
    if body.max_channels is not None:
        acc.max_channels = body.max_channels
    if body.enabled is not None:
        acc.enabled = body.enabled
        if not body.enabled:
            await release_channels(db, acc.id)
        elif acc.status == "error":
            acc.status = "new"
    await db.commit()
    return await _account_out(db, acc)


@router.post("/{account_id}/change-proxy")
async def change_proxy(account_id: int, _: Admin, db: DB) -> dict:
    """Moves the account to another free working proxy (the worker reconnects it)."""
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(status_code=404)
    proxies = list((await db.execute(select(Proxy))).scalars())
    loads = await account_loads(db)
    exclude = {acc.proxy_id} if acc.proxy_id else set()
    new = pick(proxies, loads, await _limit(db), exclude)
    if new is None:
        raise HTTPException(status_code=409, detail="Нет другого свободного рабочего прокси")
    acc.proxy_id = new.id
    if acc.status in ("waiting", "error"):
        acc.status, acc.status_detail = "new", None
    await db.commit()
    return await _account_out(db, acc)


@router.delete("/{account_id}")
async def delete_account(account_id: int, _: Admin, db: DB) -> dict:
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(status_code=404)
    await release_channels(db, acc.id)
    await db.delete(acc)
    await db.commit()
    return {"ok": True}
