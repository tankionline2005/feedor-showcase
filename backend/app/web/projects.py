"""A user's projects: the list for the switcher, a new one, opening one, deleting one, and the
project's own channel.

The project channel is any channel, given by its link or @username (nobody has to prove it is
theirs: it only decides which channel the project's data is about). As a shortcut, the channels
the bot was added to by the person (or that they administer) are listed too; a private one of
those is joined by an invite link the bot makes. The channel is then tracked like any other, for
free: it does not count towards the plan's limit.
"""

import logging
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from .. import settings_store
from ..config import site_name
from ..models import BotChat, Channel, Member, Project, ProjectChannel, PromoCode, User
from ..notify import BotError, bot_call, bot_file
from ..orphans import mark_if_orphaned
from ..plans import plan_json, project_plan
from ..projects import new_project, project_title, user_projects
from ..scope import user_status
from ..tg.links import ChannelLink, parse_channel_link
from .channels import channel_for
from .deps import DB, Principal, Viewer
from .posts import avatar_url

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

BOT_IN = ("administrator", "member")  # the bot's own status in a channel it can still see
CHANNEL_ADMINS = ("creator", "administrator")
ADMIN_CHECK_TTL = 120  # seconds an "is this person an admin of that channel" answer is reused

_admin_checks: dict[tuple[int, int], tuple[bool, float]] = {}


def _owner_only(viewer: Principal) -> None:
    if viewer.role != "user":
        raise HTTPException(status_code=400, detail="Проекты есть только у пользователей")


async def _token(db) -> tuple[str, str]:
    cfg = await settings_store.get_all(db)
    if not cfg["notify_bot_token"]:
        raise HTTPException(status_code=503, detail="Бот сейчас не подключён")
    return str(cfg["notify_bot_token"]), str(cfg["notify_bot_username"])


def project_out(project: Project, index: int, *, current: bool, own: Channel | None, has_photo: bool) -> dict:
    plan = project_plan(project)
    status, detail = user_status(own) if own else (None, None)
    picture = avatar_url(own) if own else None
    return {
        "id": project.id,
        "title": project_title(project, index),
        "channel": {
            "channel_id": own.id,
            "title": own.title or project.channel_title or own.link,
            "username": own.username,
            "link": own.link,
            # active (its posts are collected), pending (an account is joining it) or error
            "status": status,
            "status_detail": detail,
        } if own else None,
        # the channel's own picture once an account reads it; until then the one the bot saw
        "avatar_url": (
            f"/api/projects/{project.id}/avatar?v={picture.rsplit('=', 1)[1] if picture else 'bot'}"
            if picture or has_photo else None
        ),
        "plan": plan_json(plan, project.plan_expires_at) if plan else None,
        # a plan that ran out: the project shows nothing until it has one again
        "expired": bool(project.plan) and not plan,
        "current": current,
    }


async def _state(db, viewer: Principal) -> dict:
    projects = await user_projects(db, viewer.user_id)
    ids = [p.id for p in projects]
    photos = set((await db.execute(
        select(Project.id).where(Project.id.in_(ids), func.length(Project.channel_photo) > 0)
    )).scalars())
    own = {pid: ch for pid, ch in (await db.execute(
        select(ProjectChannel.project_id, Channel)
        .join(Channel, Channel.id == ProjectChannel.channel_id)
        .where(ProjectChannel.project_id.in_(ids), ProjectChannel.own)
    )).all()}
    renamed = False
    for p in projects:
        ch = own.get(p.id)
        # the project is named after its channel: keep the name as the channel's current title
        if ch is not None and ch.title and p.channel_title != ch.title:
            p.channel_title, renamed = ch.title, True
    if renamed:
        await db.commit()
    unpaid = next((i for i, p in enumerate(projects) if not project_plan(p)), None)
    return {
        "items": [
            project_out(p, i, current=p.id == viewer.project_id, own=own.get(p.id), has_photo=p.id in photos)
            for i, p in enumerate(projects)
        ],
        # a new project only once every project has a plan in force
        "can_create": unpaid is None,
        "create_hint": None if unpaid is None else
        f"Сначала оформите тариф для проекта «{project_title(projects[unpaid], unpaid)}»",
    }


@router.get("")
async def list_projects(viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    return await _state(db, viewer)


def _open(request: Request, user: User, project_id: int) -> None:
    """This browser works in the project from now on; another device opens it too until it switches."""
    request.session["pid"] = project_id
    user.project_id = project_id


@router.post("")
async def create_project(request: Request, viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    state = await _state(db, viewer)
    if not state["can_create"]:
        raise HTTPException(status_code=409, detail=state["create_hint"])
    project = new_project(viewer.user_id)
    db.add(project)
    await db.flush()
    _open(request, await db.get(User, viewer.user_id), project.id)
    await db.commit()
    return {"id": project.id}


async def _mine(db, viewer: Principal, project_id: int) -> Project:
    project = await db.get(Project, project_id)
    if project is None or project.owner_id != viewer.user_id:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return project


@router.post("/{project_id}/open")
async def open_project(project_id: int, request: Request, viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    await _mine(db, viewer, project_id)
    _open(request, await db.get(User, viewer.user_id), project_id)
    await db.commit()
    return {"ok": True}


@router.delete("/{project_id}")
async def delete_project(project_id: int, request: Request, viewer: Viewer, db: DB) -> dict:
    """Its channels stop being tracked by it (and go if nobody else needs them), its staff and API
    key go, what is left of its plan is lost. The last project stays."""
    _owner_only(viewer)
    project = await _mine(db, viewer, project_id)
    others = [p for p in await user_projects(db, viewer.user_id) if p.id != project.id]
    if not others:
        raise HTTPException(status_code=409, detail="Это ваш единственный проект — его нельзя удалить")
    links = (await db.execute(select(ProjectChannel).where(ProjectChannel.project_id == project.id))).scalars().all()
    channel_ids = [link.channel_id for link in links]
    for link in links:
        await db.delete(link)
    await db.execute(update(PromoCode).where(PromoCode.project_id == project.id).values(project_id=None))
    await db.execute(delete(Member).where(Member.project_id == project.id))  # its staff go with it
    await db.delete(project)
    await db.flush()
    for ch in (await db.execute(select(Channel).where(Channel.id.in_(channel_ids)))).scalars():
        await mark_if_orphaned(db, ch)
    user = await db.get(User, viewer.user_id)
    if viewer.project_id == project.id or user.project_id == project.id:
        _open(request, user, others[0].id)
    await db.commit()
    log.info("user %s deleted project %s", viewer.user_id, project_id)
    return {"ok": True}


# --- the project channel -------------------------------------------------------------


async def _is_channel_admin(token: str, chat_id: int, tg_user_id: int) -> bool:
    key = (chat_id, tg_user_id)
    cached = _admin_checks.get(key)
    if cached and time.monotonic() - cached[1] < ADMIN_CHECK_TTL:
        return cached[0]
    try:
        member = await bot_call(token, "getChatMember", chat_id=chat_id, user_id=tg_user_id)
        ok = member.get("status") in CHANNEL_ADMINS
    except BotError:
        ok = False
    _admin_checks[key] = (ok, time.monotonic())
    return ok


async def _allowed_channels(db, token: str, user: User) -> list[BotChat]:
    """Channels with the bot that this person may make their project's: they added the bot there,
    or they administer the channel."""
    chats = (await db.execute(
        select(BotChat).where(BotChat.type == "channel", BotChat.status.in_(BOT_IN)).order_by(BotChat.updated_at.desc())
    )).scalars().all()
    return [c for c in chats if c.added_by == user.tg_user_id or await _is_channel_admin(token, c.chat_id, user.tg_user_id)]


@router.get("/channels")
async def candidate_channels(viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    token, bot = await _token(db)
    user = await db.get(User, viewer.user_id)
    projects = await user_projects(db, user.id)
    used = {p.channel_chat_id: (p, i) for i, p in enumerate(projects) if p.channel_chat_id and await _has_own(db, p)}
    items = []
    for c in await _allowed_channels(db, token, user):
        owner = used.get(c.chat_id)
        items.append({
            "chat_id": c.chat_id,
            "title": c.title,
            "username": c.username,
            # already another (or this) project's channel
            "project": {"id": owner[0].id, "title": project_title(*owner)} if owner else None,
        })
    return {"items": items, "bot_username": bot or None}


async def _has_own(db, project: Project) -> bool:
    return bool(await db.scalar(select(ProjectChannel.channel_id).where(
        ProjectChannel.project_id == project.id, ProjectChannel.own
    )))


class ChannelIn(BaseModel):
    # any channel by its link or @username, or one of the channels with the bot (see /channels)
    link: str | None = Field(default=None, max_length=200)
    chat_id: int | None = None


async def _track_own(db, user: User, project: Project, link: ChannelLink) -> Channel:
    """The project channel is tracked automatically, like any channel but outside the limit; the
    previous one stops being the project's."""
    ch = await channel_for(db, link)
    taken = await db.scalar(
        select(ProjectChannel.project_id).join(Project, Project.id == ProjectChannel.project_id)
        .where(ProjectChannel.channel_id == ch.id, ProjectChannel.own, Project.owner_id == user.id,
               Project.id != project.id)
    )
    if taken:
        raise HTTPException(status_code=409, detail="Этот канал уже выбран в другом вашем проекте")
    old = (await db.execute(select(ProjectChannel).where(
        ProjectChannel.project_id == project.id, ProjectChannel.own
    ))).scalars().all()
    for prev in old:
        if prev.channel_id != ch.id:
            await db.delete(prev)
            await db.flush()
            previous = await db.get(Channel, prev.channel_id)
            if previous is not None:
                await mark_if_orphaned(db, previous)
    mine = await db.get(ProjectChannel, (project.id, ch.id))
    newly = mine is None or not mine.own
    if mine is None:
        db.add(ProjectChannel(project_id=project.id, channel_id=ch.id, own=True))
    else:
        mine.own = True  # it was tracked already: now it is the project's own, outside the limit
    if newly and ch.status == "active":
        ch.history_done = False  # a project channel brings a month of posts: the collector catches up
    return ch


BAD_LINK = "Укажите ссылку на канал: t.me/name, @name или ссылку-приглашение t.me/+…"


@router.put("/{project_id}/channel")
async def set_channel(project_id: int, body: ChannelIn, viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    project = await _mine(db, viewer, project_id)
    user = await db.get(User, viewer.user_id)

    if body.chat_id is None:
        # any channel at all, by its link
        try:
            link = parse_channel_link(body.link or "")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=BAD_LINK) from exc
        ch = await _track_own(db, user, project, link)
        project.channel_chat_id = None
        project.channel_title = ch.title or (f"@{link.username}" if link.username else None)
        project.channel_username = link.username
        project.channel_photo = None
        await db.commit()
        return await _state(db, viewer)

    # one of the channels with the bot: the bot knows its title and picture, and opens a private one
    token, bot = await _token(db)
    if body.chat_id not in {c.chat_id for c in await _allowed_channels(db, token, user)}:
        raise HTTPException(status_code=403, detail="Этого канала нет среди каналов с ботом — вставьте ссылку на него")
    try:
        chat = await bot_call(token, "getChat", chat_id=body.chat_id)
    except BotError as exc:
        raise HTTPException(status_code=409, detail="Бот не видит этот канал — вставьте ссылку на него") from exc
    if chat.get("username"):
        link = ChannelLink(username=chat["username"])
    else:
        try:
            invite = await bot_call(token, "createChatInviteLink", chat_id=chat["id"], name=site_name())
            link = parse_channel_link(invite["invite_link"])
        except (BotError, KeyError, ValueError) as exc:
            log.info("project %s: no invite link for private channel %s: %s", project.id, chat["id"], exc)
            raise HTTPException(status_code=409, detail=(
                f"Канал закрытый: дайте @{bot} право «Пригласительные ссылки» или вставьте ссылку-приглашение"
            )) from exc
    await _track_own(db, user, project, link)
    project.channel_chat_id = body.chat_id
    project.channel_title = chat.get("title")
    project.channel_username = chat.get("username")
    photo = (chat.get("photo") or {}).get("small_file_id")
    try:
        project.channel_photo = await bot_file(token, photo) if photo else None
    except BotError as exc:
        log.info("project %s: channel photo not downloaded: %s", project.id, exc)
        project.channel_photo = None
    await db.commit()
    return await _state(db, viewer)


@router.get("/{project_id}/avatar")
async def avatar(project_id: int, viewer: Viewer, db: DB) -> Response:
    _owner_only(viewer)
    project = await _mine(db, viewer, project_id)
    # the channel's own picture once an account reads it, else the one the bot saw
    data = await db.scalar(
        select(Channel.avatar).join(ProjectChannel, ProjectChannel.channel_id == Channel.id)
        .where(ProjectChannel.project_id == project.id, ProjectChannel.own)
    ) or await db.scalar(select(Project.channel_photo).where(Project.id == project.id))
    if not data:
        raise HTTPException(status_code=404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
