import hashlib
import hmac
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import settings_store
from ..db import SessionLocal
from ..models import Device, McpConnection, Member, Project, User, utcnow
from ..plans import plan_of, project_plan
from ..projects import current_project
from .api_routes import BY_KEY

SEEN_EVERY = timedelta(minutes=5)  # how often last_seen_at is written, not on every request
DEVICE_SEEN_EVERY = timedelta(minutes=1)  # a device's: «в сети» in «Устройства» needs it fresher


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@dataclass(frozen=True)
class Principal:
    """Who is signed in: the admin (password at #/admin), a user (through the bot), or a staff
    member of a user's project (login and password the user gave them)."""

    role: str  # "admin" | "user" | "member"
    user_id: int | None = None  # a member: the project owner's
    name: str | None = None
    username: str | None = None
    # the project the user works in: its channels, plan and API key
    project_id: int | None = None
    plan: str | None = None  # the project's plan in force: "" when none or expired
    plan_expires_at: datetime | None = None
    member_id: int | None = None
    # adding and removing the project's channels: the owner always, a member if allowed
    can_edit_channels: bool = True
    # a member: what the owner allowed them besides reading (MEMBER_RIGHTS)
    rights: frozenset[str] = frozenset()

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_owner(self) -> bool:
        return self.role == "user"

    def may(self, right: str) -> bool:
        """The owner may everything in their project; a staff member what the owner allowed; the admin
        none of a project's own business."""
        return self.is_owner or (self.role == "member" and right in self.rights)

    @property
    def who(self) -> str:
        """Who did it, as the project's people see it: «владелец», or the staff member's login."""
        return OWNER_NAME if self.is_owner else (self.name or "сотрудник")


OWNER_NAME = "владелец"

# what the owner may allow a staff member (the switches of «Совместный мониторинг»)
MEMBER_RIGHTS = ("can_edit_channels", "ai_chat", "ai_teach", "ai_configure", "ai_publish")


BLOCKED = "Доступ к аккаунту закрыт администратором"
# a staff member of a frozen project (its plan ran out), or over its plan's limit (billing.py)
STAFF_FROZEN = "Доступ приостановлен: у проекта закончился тариф"
STAFF_PAUSED = "Доступ приостановлен: тариф проекта не включает столько сотрудников"


MCP_TOKEN_PREFIX = "mcp_"  # the MCP connector's tokens (mcp_oauth), told apart from API keys ("km_")


def token_hash(token: str) -> str:
    """How a token is kept: never itself."""
    return hashlib.sha256(token.encode()).hexdigest()


API_RATE_LIMIT = 120  # requests per minute, per key — the read API and the site's methods together
_calls: dict[str, deque[float]] = defaultdict(deque)


class KeyRefused(Exception):
    """An API key that lets nothing in: why, as the read API's error (public_api) and as the site's (detail)."""

    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers


def given_key(request: Request) -> str:
    """The API key a request carries: X-API-Key, or Authorization: Bearer."""
    given = request.headers.get("x-api-key", "")
    auth = request.headers.get("authorization", "")
    if not given and auth.lower().startswith("bearer "):
        given = auth[7:]
    return given.strip()


async def key_principal(given: str, *, count: bool = True) -> Principal:
    """Whose the key is, as they are on the site: the admin (the key in the settings), a project's owner (the
    project's key) or its staff member (their own key, with the rights the owner gave them) — or the person who let
    an AI app in through the MCP connector (its token). KeyRefused: none, or not now — the account blocked, no plan
    with the API, too many requests."""
    if not given:
        raise KeyRefused(401, "missing_key", "Нужен ключ API в заголовке X-API-Key")
    async with SessionLocal() as s:
        admin_key = str((await settings_store.get_all(s))["api_key"])
        if admin_key and hmac.compare_digest(given.encode(), admin_key.encode()):
            viewer = Principal("admin")
        else:
            if given.startswith(MCP_TOKEN_PREFIX):
                # an AI app let in through the MCP connector's sign-in (mcp_oauth): it is the person who let it in
                conn = await s.scalar(select(McpConnection).where(McpConnection.access_hash == token_hash(given)))
                if conn is None or conn.access_expires_at <= utcnow():
                    raise KeyRefused(401, "invalid_token", "Подключение недействительно — подключите коннектор заново")
                project = await s.get(Project, conn.project_id)
                member = await s.get(Member, conn.member_id) if conn.member_id else None
                if conn.last_used_at is None or utcnow() - conn.last_used_at > SEEN_EVERY:
                    conn.last_used_at = utcnow()
                    await s.commit()
            else:
                project = await s.scalar(select(Project).where(Project.api_key == given))
                member = None if project else await s.scalar(select(Member).where(Member.api_key == given))
                if member is not None:
                    project = await s.get(Project, member.project_id)
            if project is None:
                raise KeyRefused(401, "invalid_key", "Неверный ключ API")
            user = await s.get(User, project.owner_id)
            if user.blocked:
                raise KeyRefused(403, "blocked", BLOCKED)
            plan = project_plan(project)
            if not plan or not plan_of(plan).api:
                raise KeyRefused(403, "no_api", "Нет действующего тарифа с доступом к API")
            if member is not None and member.paused_at is not None:
                raise KeyRefused(403, "paused", STAFF_PAUSED)
            if member is not None:
                viewer = Principal(
                    "member", user.id, member.login, None, project.id, plan, project.plan_expires_at,
                    member_id=member.id, can_edit_channels=member.can_edit_channels,
                    rights=frozenset(r for r in MEMBER_RIGHTS if getattr(member, r)),
                )
            else:
                name = " ".join(x for x in (user.first_name, user.last_name) if x) or user.username
                viewer = Principal("user", user.id, name, user.username, project.id, plan, project.plan_expires_at)
    if not count:
        return viewer
    now = time.monotonic()
    calls = _calls[given]
    while calls and now - calls[0] > 60:
        calls.popleft()
    if len(calls) >= API_RATE_LIMIT:
        retry = int(60 - (now - calls[0])) + 1
        raise KeyRefused(429, "rate_limited", f"Не больше {API_RATE_LIMIT} запросов в минуту",
                         {"Retry-After": str(retry)})
    calls.append(now)
    return viewer


NOT_BY_KEY = "Этот метод есть только на сайте — по ключу API он недоступен"


async def _by_key(request: Request, given: str) -> Principal:
    """The site's method called with an API key: as its holder would on the site — if it is one a key may call
    (api_routes.BY_KEY), and only in the key's own project."""
    try:
        viewer = await key_principal(given)
    except KeyRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message, headers=exc.headers) from exc
    route = request.scope.get("route")
    if f"{request.method} {getattr(route, 'path', '')}" not in BY_KEY:
        raise HTTPException(status_code=403, detail=NOT_BY_KEY)
    project_id = request.path_params.get("project_id")
    if project_id is not None and not viewer.is_admin and str(project_id) != str(viewer.project_id):
        raise HTTPException(status_code=404, detail="Проект не найден")
    return viewer


async def require_viewer(request: Request) -> Principal:
    if given := given_key(request):
        return await _by_key(request, given)
    # sessions from before user accounts carry just {"user": <admin login>}
    if request.session.get("user"):
        return Principal("admin", name=request.session["user"])
    if request.session.get("mid"):
        return await _member(request)
    user_id = request.session.get("uid")
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется вход")
    async with SessionLocal() as s:
        user = await s.get(User, user_id)
        if user is None or user.blocked:
            request.session.clear()
            raise HTTPException(status_code=401, detail=BLOCKED if user else "Требуется вход")
        await _device(s, request, user_id=user.id)
        project = await current_project(s, user, request.session.get("pid"))
        if user.last_seen_at is None or utcnow() - user.last_seen_at > SEEN_EVERY:
            user.last_seen_at = utcnow()
        if s.dirty or s.new:
            await s.commit()
        name = " ".join(x for x in (user.first_name, user.last_name) if x) or user.username
        plan = project_plan(project)
        return Principal(
            "user", user.id, name, user.username, project.id, plan, project.plan_expires_at if plan else None
        )


GONE = "Аккаунт сотрудника удалён или его пароль сменили — войдите снова"
SIGNED_OUT = "Сеанс завершён с другого устройства — войдите снова"


async def _device(s: AsyncSession, request: Request, user_id: int | None = None, member_id: int | None = None) -> None:
    """The session's device (Settings → «Устройства»): refused once signed out from another device; seen now."""
    ip = (request.client.host if request.client else "?")[:64]
    did = request.session.get("did")
    if did is None:
        # a session from before «Устройства» becomes a device now — with an id of its cookie, the same for the
        # requests it sends side by side
        did = "s" + hashlib.sha256(request.cookies.get("km_session", "").encode()).hexdigest()[:40]
        if await s.get(Device, did) is None:
            async with SessionLocal() as own:  # a clash must not roll back the request's session
                own.add(Device(id=did, user_id=None if member_id else user_id, member_id=member_id, ip=ip,
                               user_agent=request.headers.get("user-agent", "")[:512]))
                try:
                    await own.commit()
                except IntegrityError:
                    pass
        request.session["did"] = did
    device = await s.get(Device, did)
    if device is None or device.revoked or device.member_id != member_id             or device.user_id != (None if member_id else user_id):
        request.session.clear()
        raise HTTPException(status_code=401, detail=SIGNED_OUT)
    if utcnow() - device.last_seen_at > DEVICE_SEEN_EVERY or device.ip != ip:
        device.last_seen_at, device.ip = utcnow(), ip


async def _member(request: Request) -> Principal:
    async with SessionLocal() as s:
        member = await s.get(Member, request.session["mid"])
        project = await s.get(Project, member.project_id) if member else None
        owner = await s.get(User, project.owner_id) if project else None
        if member is None or member.session_version != request.session.get("mv") or owner is None:
            request.session.clear()
            raise HTTPException(status_code=401, detail=GONE)
        if owner.blocked:
            request.session.clear()
            raise HTTPException(status_code=401, detail=BLOCKED)
        plan = project_plan(project)
        if not plan or member.paused_at is not None:
            # the plan ran out (the project is frozen), or it has room for fewer staff: back once it is bought
            request.session.clear()
            raise HTTPException(status_code=401, detail=STAFF_FROZEN if not plan else STAFF_PAUSED)
        await _device(s, request, member_id=member.id)
        if member.last_seen_at is None or utcnow() - member.last_seen_at > SEEN_EVERY:
            member.last_seen_at = utcnow()
        if s.dirty or s.new:
            await s.commit()
        return Principal(
            "member", owner.id, member.login, None, project.id, plan, project.plan_expires_at,
            member_id=member.id, can_edit_channels=member.can_edit_channels,
            rights=frozenset(r for r in MEMBER_RIGHTS if getattr(member, r)),
        )


async def require_admin(viewer: Annotated[Principal, Depends(require_viewer)]) -> Principal:
    if not viewer.is_admin:
        raise HTTPException(status_code=403, detail="Доступно только администратору")
    return viewer


DB = Annotated[AsyncSession, Depends(get_session)]
# anyone signed in: the admin or a user
Viewer = Annotated[Principal, Depends(require_viewer)]
# management: accounts, proxies, settings, alerts, users
Admin = Annotated[Principal, Depends(require_admin)]
