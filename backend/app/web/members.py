""""Совместный мониторинг": the owner gives their team staff accounts for the current project, a
member signs in with the login and password, and may link their own Telegram.

A password is shown once, when it is made; only its scrypt hash is kept. Resetting it signs the
member out everywhere. How many members a project may have depends on its plan.
"""

import asyncio
import secrets
import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from .. import ai, settings_store
from ..members import (
    LINK_PREFIX,
    LINK_TTL,
    LOGIN_RE,
    LOGIN_RULES,
    check_password,
    hash_password,
    new_password,
    normalize_login,
)
from ..models import AiUsage, ChatThread, Device, McpConnection, Member, Project, User, utcnow
from ..plans import plan_of, project_plan
from ..projects import new_api_key
from ..billing import NO_PLAN_TEXT
from .deps import DB, MEMBER_RIGHTS, STAFF_FROZEN, STAFF_PAUSED, Principal, Viewer
from .devices import sign_in
from .posts import iso

router = APIRouter(prefix="/api/members", tags=["members"])

FAILED_LIMIT, FAILED_WINDOW = 5, 15 * 60  # wrong passwords per IP, seconds
_failed: dict[str, deque[float]] = defaultdict(deque)


def member_json(m: Member, chat_spent: float = 0.0) -> dict:
    return {
        "id": m.id,
        "login": m.login,
        **{right: getattr(m, right) for right in MEMBER_RIGHTS},
        "ai_chat_limit_usd": m.ai_chat_limit_usd,
        # what their chats with the AI cost this month
        "chat_spent": round(chat_spent, 4),
        "telegram": {"username": m.tg_username, "name": m.tg_name} if m.tg_user_id else None,
        "created_at": iso(m.created_at),
        "last_seen_at": iso(m.last_seen_at),
        # over the plan's limit: cannot sign in till a plan with room for them is bought
        "paused": m.paused_at is not None,
    }


def _owner_only(viewer: Principal) -> None:
    if not viewer.is_owner:
        raise HTTPException(status_code=403, detail="Сотрудниками управляет владелец проекта")


async def chat_spent(db, member_ids: list[int]) -> dict[int, float]:
    """What each staff member's chats with the AI cost this month (USD)."""
    if not member_ids:
        return {}
    return {mid: float(cost or 0) for mid, cost in (await db.execute(
        select(AiUsage.member_id, func.sum(ai.PAID))
        .where(AiUsage.member_id.in_(member_ids), AiUsage.operation == "chat", AiUsage.at >= ai.month_start())
        .group_by(AiUsage.member_id)
    )).all()}


async def _state(db, viewer: Principal) -> dict:
    members = (await db.execute(
        select(Member).where(Member.project_id == viewer.project_id).order_by(Member.created_at)
    )).scalars().all()
    plan = plan_of(viewer.plan)
    limit = plan.members if plan else 0
    spent = await chat_spent(db, [m.id for m in members])
    return {
        "items": [member_json(m, spent.get(m.id, 0.0)) for m in members],
        "limit": limit,  # None: no limit
        "has_plan": plan is not None,
    }


@router.get("")
async def list_members(viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    return await _state(db, viewer)


class Rights(BaseModel):
    can_edit_channels: bool = False
    ai_chat: bool = False
    ai_chat_limit_usd: float | None = Field(default=None, ge=0, le=10_000)
    ai_teach: bool = False
    ai_configure: bool = False
    ai_publish: bool = False


class AddIn(Rights):
    login: str = Field(max_length=64)


@router.post("")
async def add_member(body: AddIn, viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    plan = plan_of(viewer.plan)
    if plan is None:
        raise HTTPException(status_code=403, detail=NO_PLAN_TEXT)
    if plan.members == 0:
        raise HTTPException(status_code=409, detail=f"Тариф {plan.title} не включает сотрудников — выберите тариф "
                                                    "в настройках")
    count = await db.scalar(select(func.count()).where(Member.project_id == viewer.project_id,
                                                       Member.paused_at.is_(None))) or 0
    if plan.members is not None and count >= plan.members:
        raise HTTPException(status_code=409, detail=f"По тарифу {plan.title} — не больше {plan.members} сотрудников")
    login = normalize_login(body.login)
    if not LOGIN_RE.match(login):
        raise HTTPException(status_code=422, detail=LOGIN_RULES)
    if await db.scalar(select(Member.id).where(Member.login == login)):
        raise HTTPException(status_code=409, detail="Такой логин уже занят — придумайте другой")
    password = new_password()
    member = Member(project_id=viewer.project_id, login=login, password_hash=await hash_password(password),
                    api_key=new_api_key(), **body.model_dump(exclude={"login"}))
    db.add(member)
    await db.commit()
    # the only time the password is ever shown
    return {"member": member_json(member), "password": password}


async def _mine(db, viewer: Principal, member_id: int) -> Member:
    member = await db.get(Member, member_id)
    if member is None or member.project_id != viewer.project_id:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    return member


class PatchIn(BaseModel):
    can_edit_channels: bool | None = None
    ai_chat: bool | None = None
    ai_chat_limit_usd: float | None = Field(default=None, ge=0, le=10_000)  # null: no limit
    ai_teach: bool | None = None
    ai_configure: bool | None = None
    ai_publish: bool | None = None


@router.patch("/{member_id}")
async def patch_member(member_id: int, body: PatchIn, viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    member = await _mine(db, viewer, member_id)
    # applies to their very next request
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None or field == "ai_chat_limit_usd":  # the limit alone may be taken away
            setattr(member, field, value)
    await db.commit()
    return member_json(member, (await chat_spent(db, [member.id])).get(member.id, 0.0))


@router.post("/{member_id}/password")
async def reset_password(member_id: int, viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    member = await _mine(db, viewer, member_id)
    password = new_password()
    member.password_hash = await hash_password(password)
    member.session_version += 1  # signed out on every device
    member.api_key = new_api_key()  # and their API key, which does all they may, stops working too
    await db.execute(delete(McpConnection).where(McpConnection.member_id == member.id))  # and the AI apps they let in
    await db.execute(update(Device).where(Device.member_id == member.id).values(revoked=True))
    await db.commit()
    return {"member": member_json(member), "password": password}


@router.delete("/{member_id}")
async def delete_member(member_id: int, viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    member = await _mine(db, viewer, member_id)
    from ..publications import keep_out_of_chats  # it builds on the chat

    # their chats go with them; what of them went out stays in «Публикации»
    await keep_out_of_chats(db, select(ChatThread.id).where(ChatThread.member_id == member.id))
    await db.delete(member)
    await db.commit()
    return {"ok": True}


# --- a member ------------------------------------------------------------------------


class LoginIn(BaseModel):
    login: str = Field(max_length=64)
    password: str = Field(max_length=128)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.post("/login")
async def member_login(body: LoginIn, request: Request, db: DB) -> dict:
    ip = _ip(request)
    failed = _failed[ip]
    now = time.monotonic()
    while failed and now - failed[0] > FAILED_WINDOW:
        failed.popleft()
    if len(failed) >= FAILED_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много неудачных попыток — попробуйте через 15 минут")
    member = await db.scalar(select(Member).where(Member.login == normalize_login(body.login)))
    ok = member is not None and await check_password(body.password, member.password_hash)
    project = await db.get(Project, member.project_id) if ok else None
    owner = await db.get(User, project.owner_id) if project else None
    if not ok or owner is None:
        failed.append(now)
        await asyncio.sleep(1)  # slows down password guessing
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    if owner.blocked:
        raise HTTPException(status_code=403, detail="Доступ к аккаунту закрыт администратором")
    if not project_plan(project):
        raise HTTPException(status_code=403, detail=STAFF_FROZEN)
    if member.paused_at is not None:
        raise HTTPException(status_code=403, detail=STAFF_PAUSED)
    failed.clear()
    await sign_in(request, db, member=member)
    return {"ok": True}


def _member_only(viewer: Principal) -> None:
    if viewer.role != "member":
        raise HTTPException(status_code=400, detail="Только для сотрудников")


@router.post("/me/telegram")
async def link_telegram(viewer: Viewer, db: DB) -> dict:
    """A link to the bot: whoever opens it gets their Telegram linked to this staff account."""
    _member_only(viewer)
    bot = str((await settings_store.get_all(db))["notify_bot_username"])
    if not bot:
        raise HTTPException(status_code=503, detail="Бот сейчас не подключён")
    member = await db.get(Member, viewer.member_id)
    member.link_token, member.link_token_at = secrets.token_urlsafe(24), utcnow()
    await db.commit()
    return {"link": f"https://t.me/{bot}?start={LINK_PREFIX}{member.link_token}",
            "expires_in": int(LINK_TTL.total_seconds())}


@router.delete("/me/telegram")
async def unlink_telegram(viewer: Viewer, db: DB) -> dict:
    _member_only(viewer)
    member = await db.get(Member, viewer.member_id)
    member.tg_user_id = member.tg_username = member.tg_name = None
    member.link_token = None
    await db.commit()
    return {"ok": True}

