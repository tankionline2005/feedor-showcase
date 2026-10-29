"""Signing in.

Users: "Войти через Telegram" — the browser gets a one-time code and opens the bot with it
(t.me/<bot>?start=login_<code>); the person confirms the login there (see notify.py) and the
browser, polling /tg/status, gets its session. The code is bound to the browser that asked for it
(kept in its session), so a leaked code signs in nobody else.

Or by a QR code on the login page: a phone signed in scans it (Settings → «Устройства», devices.py) and confirms;
the browser, polling /qr/status, gets the phone's account. Its codes too are bound to the browser that asked.

The admin: login and password, from a form that only the hidden address #/admin shows.
"""

import asyncio
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select

from .. import ai, billing, settings_store
from ..config import settings
from ..logins import LOGIN_PREFIX, LOGIN_TTL
from ..models import LoginRequest, Member, Project, ProjectChannel, User, utcnow
from ..plans import plan_json
from .deps import DB, MEMBER_RIGHTS, Viewer
from .devices import QR_TTL, sign_in, sign_out

router = APIRouter(prefix="/api/auth", tags=["auth"])

FAILED_LIMIT = 5  # wrong admin passwords from one IP …
FAILED_WINDOW = 15 * 60  # … within this many seconds lock the form for that IP
STARTS_LIMIT = 30  # login codes one IP may ask for in STARTS_WINDOW
STARTS_WINDOW = 10 * 60
QR_EVERY = 60  # the login page shows a new QR code this often (seconds)
QR_KEEP = 3  # QR codes a browser may be signed in by: the latest few (a phone may have scanned the one before)

_failed: dict[str, deque[float]] = defaultdict(deque)
_starts: dict[str, deque[float]] = defaultdict(deque)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _hits(log: dict[str, deque[float]], ip: str, window: float) -> deque[float]:
    now = time.monotonic()
    hits = log[ip]
    while hits and now - hits[0] > window:
        hits.popleft()
    return hits


def device_of(user_agent: str) -> str:
    """"Chrome, Windows" — enough for a person to recognise their own login in the bot."""
    ua = user_agent or ""
    browser = next(
        (name for pattern, name in (
            (r"YaBrowser", "Яндекс Браузер"), (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"),
            (r"Firefox/", "Firefox"), (r"Chrome/|CriOS/", "Chrome"), (r"Safari/", "Safari"),
        ) if re.search(pattern, ua)),
        "браузер",
    )
    system = next(
        (name for pattern, name in (
            (r"iPhone|iPad", "iOS"), (r"Android", "Android"), (r"Windows", "Windows"),
            (r"Mac OS X|Macintosh", "macOS"), (r"Linux", "Linux"),
        ) if re.search(pattern, ua)),
        None,
    )
    return f"{browser}, {system}" if system else browser


def me_json(viewer, member: dict | None = None, ai_linked: bool = False, needs_channel: bool = False,
            intros_seen: list | None = None, ai_own: bool = False, notice: dict | None = None) -> dict:
    return {
        "role": viewer.role,
        "user": viewer.name,
        "username": viewer.username,
        # the project the user (or the staff member) works in; None for the admin
        "project": {"id": viewer.project_id} if viewer.project_id else None,
        # the project's plan; None for the admin, and for a project without a plan (or an expired one)
        "plan": plan_json(viewer.plan, viewer.plan_expires_at) if not viewer.is_admin else None,
        "can_edit_channels": viewer.can_edit_channels,
        # a staff member: their login, project and linked Telegram
        "member": member,
        # the project's AI has its OpenRouter: the service's, or the owner's own (dev:service:plan)
        "ai_linked": ai_linked,
        # the project's AI is the owner's own OpenRouter (dev:service:plan): its settings, models and spending
        "ai_own": ai_own,
        # the owner's banner on every screen (billing.notice): the plan ends, the project is frozen, the AI stands
        "notice": notice,
        # an owner none of whose projects has its channel yet: the site asks for one before anything else
        "needs_channel": needs_channel,
        # the sections whose welcome cards this person has seen
        "intros_seen": intros_seen or [],
    }


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginIn, request: Request) -> dict:
    ip = _ip(request)
    failed = _hits(_failed, ip, FAILED_WINDOW)
    if len(failed) >= FAILED_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много неудачных попыток — попробуйте через 15 минут")
    ok_user = hmac.compare_digest(body.username.encode(), settings.admin_username.encode())
    ok_pass = hmac.compare_digest(body.password.encode(), settings.admin_password.encode())
    if not (ok_user and ok_pass):
        failed.append(time.monotonic())
        await asyncio.sleep(1)  # slows down password guessing
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    failed.clear()
    request.session.clear()
    request.session["user"] = body.username
    return {"user": body.username, "role": "admin"}


@router.post("/logout")
async def logout(request: Request, db: DB) -> dict:
    await sign_out(db, request.session.get("did"))
    request.session.clear()
    return {"ok": True}


@router.get("/me")
async def me(viewer: Viewer, db: DB) -> dict:
    member = None
    if viewer.role == "member":
        m = await db.get(Member, viewer.member_id)
        project = await db.get(Project, viewer.project_id)
        member = {
            "login": m.login,
            "project_title": project.channel_title or "Проект",
            "project_username": project.channel_username,
            "telegram": {"username": m.tg_username, "name": m.tg_name} if m.tg_user_id else None,
            # what the owner allowed them (the switches of «Совместный мониторинг»)
            **{right: getattr(m, right) for right in MEMBER_RIGHTS},
        }
    eng = None if viewer.is_admin else await ai.engine(db, viewer.project_id, viewer.user_id)
    person = await _person(db, viewer)
    notice = None
    if viewer.is_owner:
        notice = await billing.notice(db, await db.get(Project, viewer.project_id),
                                      None if eng.own or not eng.linked else eng.stop)
    return me_json(viewer, member, bool(eng and eng.linked),
                   viewer.role == "user" and not await _has_any_channel(db, viewer.user_id),
                   person.intros_seen if person is not None else None, bool(eng and eng.own), notice)


async def _person(db, viewer) -> User | Member | None:
    """Who is signed in, as a row: an owner's User or a staff member's Member (None: the admin)."""
    if viewer.role == "member":
        return await db.get(Member, viewer.member_id)
    if viewer.role == "user":
        return await db.get(User, viewer.user_id)
    return None


# the sections' welcome cards; and the one-time tips about something new («covers»: «Обложки» in «Публикации»)
INTRO_KEYS = {"feed", "triggers", "top", "chat", "topics", "publications", "channels", "groups", "team", "api", "mcp",
              "covers", "plans", "balance", "billing-news"}


class IntroIn(BaseModel):
    key: str


@router.post("/intros")
async def intro_seen(body: IntroIn, viewer: Viewer, db: DB) -> dict:
    """A section's welcome cards were seen: not shown by themselves again (the «?» by the title still opens them)."""
    if body.key not in INTRO_KEYS:
        raise HTTPException(status_code=422, detail="Такого раздела нет")
    person = await _person(db, viewer)
    if person is None:
        return {"intros_seen": []}
    seen = list(person.intros_seen or [])
    if body.key not in seen:
        person.intros_seen = [*seen, body.key]  # a new list: the JSON column notices the change
        await db.commit()
    return {"intros_seen": person.intros_seen}


async def _has_any_channel(db, user_id: int) -> bool:
    """One of the owner's projects has its channel (a project made later may go without one)."""
    return bool(await db.scalar(select(ProjectChannel.channel_id).join(Project, Project.id == ProjectChannel.project_id)
                                .where(Project.owner_id == user_id, ProjectChannel.own).limit(1)))


@router.post("/tg/start")
async def tg_start(request: Request, db: DB) -> dict:
    """A fresh login code and the bot link that carries it."""
    bot = str((await settings_store.get_all(db))["notify_bot_username"])
    if not bot:
        raise HTTPException(status_code=503, detail="Вход через Telegram сейчас недоступен: бот не настроен на сервере")
    ip = _ip(request)
    starts = _hits(_starts, ip, STARTS_WINDOW)
    if len(starts) >= STARTS_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много попыток входа — попробуйте через несколько минут")
    starts.append(time.monotonic())

    await db.execute(delete(LoginRequest).where(LoginRequest.created_at < utcnow() - timedelta(days=1)))
    token = secrets.token_urlsafe(24)
    db.add(LoginRequest(token=token, device=device_of(request.headers.get("user-agent", ""))[:255], ip=ip[:64]))
    await db.commit()
    request.session["login_token"] = token
    return {"link": f"https://t.me/{bot}?start={LOGIN_PREFIX}{token}", "expires_in": int(LOGIN_TTL.total_seconds())}


@router.get("/tg/status")
async def tg_status(request: Request, db: DB) -> dict:
    """pending | expired | declined, or ok once confirmed in the bot (the session is set then)."""
    token = request.session.get("login_token")
    req = await db.get(LoginRequest, token) if token else None
    if req is None or req.status == "used":
        return {"status": "expired"}
    if req.status == "declined":
        return {"status": "declined"}
    if req.status == "pending":
        return {"status": "expired" if utcnow() - req.created_at > LOGIN_TTL else "pending"}
    user = await db.get(User, req.user_id)
    if user is None or user.blocked:
        return {"status": "declined"}
    req.status = "used"
    await sign_in(request, db, user_id=user.id)
    return {"status": "ok"}


@router.post("/qr/start")
async def qr_start(request: Request, db: DB) -> dict:
    """A fresh QR code for the login page; a phone signed in scans it."""
    ip = _ip(request)
    starts = _hits(_starts, ip, STARTS_WINDOW)
    if len(starts) >= STARTS_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много попыток входа — попробуйте через несколько минут")
    starts.append(time.monotonic())
    ua = request.headers.get("user-agent", "")
    token = secrets.token_urlsafe(24)
    db.add(LoginRequest(token=token, kind="qr", device=device_of(ua)[:255], ip=ip[:64], user_agent=ua[:512]))
    await db.commit()
    # the codes before still count (a phone may be confirming one), unless declined
    kept = []
    for old in request.session.get("qr_tokens", []):
        req = await db.get(LoginRequest, old)
        if req is not None and req.status in ("pending", "scanned", "confirmed"):
            kept.append(old)
    request.session["qr_tokens"] = [token, *kept][:QR_KEEP]
    return {"token": token, "expires_in": QR_EVERY}


@router.get("/qr/status")
async def qr_status(request: Request, db: DB) -> dict:
    """ok once a phone confirmed one of the browser's QR codes (the session is set then); scanned while a phone
    is asking its person; declined when they said no; else pending (or expired: no code at all)."""
    reqs = [r for t in request.session.get("qr_tokens", []) if (r := await db.get(LoginRequest, t)) and r.kind == "qr"]
    fresh = [r for r in reqs if utcnow() - r.created_at <= QR_TTL]
    if req := next((r for r in fresh if r.status == "confirmed"), None):
        member = await db.get(Member, req.member_id) if req.member_id else None
        user = None if req.member_id else await db.get(User, req.user_id)
        req.status = "used"
        if member is not None:
            await sign_in(request, db, member=member)
        elif user is not None and not user.blocked:
            await sign_in(request, db, user_id=user.id)
        else:
            await db.commit()
            return {"status": "declined"}
        return {"status": "ok"}
    if any(r.status == "scanned" for r in fresh):
        return {"status": "scanned"}
    if any(r.status == "declined" for r in fresh):
        return {"status": "declined"}
    return {"status": "pending" if fresh else "expired"}
