"""Login requests ("Войти через Telegram") as the bot sees them: confirm or decline.

The web side (web/auth.py) creates a request and hands its browser the session once it is confirmed.
"""

from datetime import timedelta

from sqlalchemy import delete, select

from .db import SessionLocal
from .models import Device, LoginRequest, User, utcnow
from .projects import new_project

LOGIN_TTL = timedelta(minutes=5)  # how long a login code waits for the bot
LOGIN_PREFIX = "login_"  # /start login_<code>
COOKIE_LIFE = timedelta(days=30)  # the session cookie's max_age (web/main.py)


async def forget_old() -> None:
    """The worker: devices («Устройства») nobody used for longer than a cookie lives, signed out or not (a
    signed-out one is kept that long, so an old copy of its cookie stays refused), and old login requests."""
    async with SessionLocal() as s:
        await s.execute(delete(Device).where(Device.last_seen_at < utcnow() - COOKIE_LIFE - timedelta(days=1)))
        await s.execute(delete(LoginRequest).where(LoginRequest.created_at < utcnow() - timedelta(days=1)))
        await s.commit()


async def confirm_login(token: str, tg_user: dict) -> tuple[str, User | None, bool]:
    """Called by the bot when a person presses "Подтвердить вход".
    Returns (result, user, is_new): result is ok | expired | blocked."""
    async with SessionLocal() as s:
        req = await s.get(LoginRequest, token)
        if req is None or req.kind != "bot" or req.status != "pending" or utcnow() - req.created_at > LOGIN_TTL:
            return "expired", None, False
        user = await s.scalar(select(User).where(User.tg_user_id == tg_user["id"]))
        is_new = user is None
        if user is None:
            user = User(tg_user_id=tg_user["id"])
            s.add(user)
        user.first_name = tg_user.get("first_name")
        user.last_name = tg_user.get("last_name")
        user.username = tg_user.get("username")
        if user.blocked:
            req.status = "declined"
            await s.commit()
            return "blocked", user, False
        await s.flush()
        if is_new:  # every user has a project to work in from the start, with «Free Trial»
            from .billing import start_trial

            project = new_project(user.id)
            s.add(project)
            await start_trial(s, user, project)
            await s.flush()
            user.project_id = project.id
        req.status, req.user_id = "confirmed", user.id
        await s.commit()
        return "ok", user, is_new


async def decline_login(token: str) -> bool:
    async with SessionLocal() as s:
        req = await s.get(LoginRequest, token)
        if req is None or req.kind != "bot" or req.status != "pending":
            return False
        req.status = "declined"
        await s.commit()
        return True


async def login_request(token: str) -> LoginRequest | None:
    async with SessionLocal() as s:
        req = await s.get(LoginRequest, token)
        if req is None or req.kind != "bot" or req.status != "pending" or utcnow() - req.created_at > LOGIN_TTL:
            return None
        return req
