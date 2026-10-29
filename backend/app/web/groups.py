"""«Группы проекта»: a project's own groups the bot sends posts to, besides the private chat — each project has
its own, as many as its plan allows. The owner adds and removes them; everyone of the project, staff
too, sends posts there.

The owner adds the bot to their group, then adds the group here: a public one by its link, a
private one from the list of groups they added the bot to (the bot learns them from Telegram,
see notify.remember_chat — an invite link cannot be resolved by a bot). Only a member of a group
can add it, so nobody can send posts into a stranger's group the bot happens to be in.
"""

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .. import settings_store
from ..billing import NO_PLAN_TEXT
from ..models import BotChat, ProjectGroup, User
from ..notify import BotError, bot_call, plural
from ..plans import plan_of
from .deps import DB, Viewer
from .posts import iso

router = APIRouter(prefix="/api/groups", tags=["groups"])



def _limit(viewer) -> int | None:
    """Groups a project may have, by its plan; None: no limit."""
    plan = plan_of(viewer.plan)
    return plan.groups if plan else 0
IN_CHAT = ("creator", "administrator", "member", "restricted")
GROUP_TYPES = ("group", "supergroup")
_PUBLIC = re.compile(r"^(?:https?://)?(?:t\.me/|telegram\.me/)?@?([A-Za-z][A-Za-z0-9_]{3,31})/?$")
_INVITE = re.compile(r"(?:t\.me|telegram\.me)/(?:\+|joinchat/)", re.I)


def group_json(g: ProjectGroup, bot_status: str | None = None) -> dict:
    return {
        "chat_id": g.chat_id,
        "title": g.title,
        "username": g.username,
        # the bot was removed from the group: posts cannot be sent there until it is back
        "bot_removed": bot_status in ("left", "kicked"),
        "created_at": iso(g.created_at),
    }


def _user_only(viewer) -> None:
    if not viewer.is_owner:
        raise HTTPException(status_code=403, detail="Группы есть только у владельцев проектов")


async def _bot(db) -> tuple[str, str]:
    cfg = await settings_store.get_all(db)
    if not cfg["notify_bot_token"]:
        raise HTTPException(status_code=503, detail="Бот сейчас не подключён")
    return str(cfg["notify_bot_token"]), str(cfg["notify_bot_username"])


async def _state(db, viewer) -> dict:
    user = await db.get(User, viewer.user_id)
    mine = (await db.execute(
        select(ProjectGroup).where(ProjectGroup.project_id == viewer.project_id).order_by(ProjectGroup.created_at)
    )).scalars().all()
    statuses = dict((await db.execute(
        select(BotChat.chat_id, BotChat.status).where(BotChat.chat_id.in_([g.chat_id for g in mine]))
    )).all()) if mine else {}
    # the owner's own groups with the bot, to add: theirs alone — their staff see the project's groups only
    found = [] if not viewer.is_owner else (await db.execute(
        select(BotChat).where(
            BotChat.added_by == user.tg_user_id,
            BotChat.type.in_(GROUP_TYPES),
            BotChat.status.in_(("member", "administrator")),
            BotChat.chat_id.not_in([g.chat_id for g in mine] or [0]),
        ).order_by(BotChat.updated_at.desc())
    )).scalars().all()
    cfg = await settings_store.get_all(db)
    return {
        "items": [group_json(g, statuses.get(g.chat_id)) for g in mine],
        # groups the owner added the bot to but has not added to this project yet
        "found": [{"chat_id": c.chat_id, "title": c.title, "username": c.username} for c in found],
        "limit": _limit(viewer),  # None: no limit
        "bot_username": cfg["notify_bot_username"] or None,
    }


@router.get("")
async def list_groups(viewer: Viewer, db: DB) -> dict:
    """The project's groups; its staff see them too, to choose where a post goes."""
    if viewer.is_admin:
        raise HTTPException(status_code=403, detail="Группы есть только у проектов")
    return await _state(db, viewer)


class AddIn(BaseModel):
    link: str | None = Field(default=None, max_length=200)
    chat_id: int | None = None  # one of "found"


@router.post("")
async def add_group(body: AddIn, viewer: Viewer, db: DB) -> dict:
    _user_only(viewer)
    count = await db.scalar(select(func.count()).where(ProjectGroup.project_id == viewer.project_id)) or 0
    if (plan := plan_of(viewer.plan)) is None:
        raise HTTPException(status_code=403, detail=NO_PLAN_TEXT)
    if plan.groups is not None and count >= plan.groups:
        raise HTTPException(status_code=409, detail=f"По тарифу {plan.title} — не больше {plan.groups} "
                                                    f"{plural(plan.groups, 'группы', 'групп', 'групп')} в проекте")
    token, bot = await _bot(db)

    if body.chat_id is not None:
        chat_ref: int | str = body.chat_id
    else:
        link = (body.link or "").strip()
        if _INVITE.search(link):
            raise HTTPException(
                status_code=422,
                detail=f"Закрытую группу по ссылке-приглашению бот найти не может. Добавьте @{bot} в группу — "
                       "она сама появится в списке «Группы с ботом» ниже.",
            )
        m = _PUBLIC.match(link)
        if not m:
            raise HTTPException(status_code=422, detail="Укажите ссылку на группу: t.me/name или @name")
        chat_ref = f"@{m.group(1)}"

    try:
        chat = await bot_call(token, "getChat", chat_id=chat_ref)
    except BotError as exc:
        raise HTTPException(status_code=409, detail=f"Бот не видит эту группу — сначала добавьте в неё @{bot}") from exc
    if chat.get("type") not in GROUP_TYPES:
        raise HTTPException(status_code=422, detail="Это не группа: посты можно отправлять только в группы")
    me_id = int(token.split(":")[0])
    user = await db.get(User, viewer.user_id)
    try:
        bot_member = await bot_call(token, "getChatMember", chat_id=chat["id"], user_id=me_id)
        member = await bot_call(token, "getChatMember", chat_id=chat["id"], user_id=user.tg_user_id)
    except BotError as exc:
        raise HTTPException(status_code=409, detail=f"Бот не видит эту группу — сначала добавьте в неё @{bot}") from exc
    if bot_member.get("status") not in IN_CHAT:
        raise HTTPException(status_code=409, detail=f"@{bot} не состоит в этой группе — сначала добавьте его туда")
    if member.get("status") not in IN_CHAT:
        raise HTTPException(status_code=403, detail="Добавить можно только группу, в которой вы состоите")

    if await db.get(ProjectGroup, (viewer.project_id, chat["id"])) is None:
        db.add(ProjectGroup(project_id=viewer.project_id, chat_id=chat["id"], title=chat.get("title"),
                            username=chat.get("username")))
    await db.commit()
    return await _state(db, viewer)


@router.delete("/{chat_id}")
async def delete_group(chat_id: int, viewer: Viewer, db: DB) -> dict:
    _user_only(viewer)
    await db.execute(delete(ProjectGroup).where(ProjectGroup.project_id == viewer.project_id,
                                                ProjectGroup.chat_id == chat_id))
    await db.commit()
    return await _state(db, viewer)
