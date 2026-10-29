"""People who signed in through the bot, as the admin sees and manages them, with their projects."""

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import billing
from ..models import Channel, Member, Project, ProjectChannel, User
from ..plans import NO_PLAN, plan_json, plan_of, project_plan, usd
from ..projects import project_title
from .channels import channel_out, channel_stats
from .deps import DB, Admin
from .posts import iso

router = APIRouter(prefix="/api/users", tags=["users"])


def project_json(project: Project, index: int, channels: int = 0, members: int = 0) -> dict:
    active = project_plan(project)
    return {
        "id": project.id,
        "title": project_title(project, index),
        # the plan in force; None when the project has none or it has expired
        "plan": plan_json(active, project.plan_expires_at) if active else None,
        # an expired plan, so the admin sees what ran out and when
        "expired_plan": plan_json(project.plan, project.plan_expires_at) if project.plan and not active else None,
        "channels": channels,
        "members": members,  # staff accounts ("Совместный мониторинг")
        "created_at": iso(project.created_at),
    }


def user_json(user: User, projects: list[Project], channels: dict[int, int], members: dict[int, int]) -> dict:
    return {
        "id": user.id,
        "tg_user_id": user.tg_user_id,
        "name": " ".join(x for x in (user.first_name, user.last_name) if x) or None,
        "username": user.username,
        "projects": [project_json(p, i, channels.get(p.id, 0), members.get(p.id, 0)) for i, p in enumerate(projects)],
        "blocked": user.blocked,
        "balance": usd(user.balance),
        "created_at": iso(user.created_at),
        "last_seen_at": iso(user.last_seen_at),
    }


async def _counts(db, column, project_ids: list[int] | None = None) -> dict[int, int]:
    stmt = select(column, func.count()).group_by(column)
    if project_ids is not None:
        stmt = stmt.where(column.in_(project_ids))
    return dict((await db.execute(stmt)).all())


async def _one(db, user: User) -> dict:
    projects = list((await db.execute(
        select(Project).where(Project.owner_id == user.id).order_by(Project.id)
    )).scalars())
    ids = [p.id for p in projects]
    return user_json(user, projects, await _counts(db, ProjectChannel.project_id, ids),
                     await _counts(db, Member.project_id, ids))


@router.get("")
async def list_users(_: Admin, db: DB) -> dict:
    users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    projects: dict[int, list[Project]] = defaultdict(list)
    for p in (await db.execute(select(Project).order_by(Project.id))).scalars():
        projects[p.owner_id].append(p)
    channels, members = await _counts(db, ProjectChannel.project_id), await _counts(db, Member.project_id)
    return {"items": [user_json(u, projects[u.id], channels, members) for u in users]}


class PatchIn(BaseModel):
    blocked: bool


@router.patch("/{user_id}")
async def patch_user(user_id: int, body: PatchIn, _: Admin, db: DB) -> dict:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    # a blocked user is signed out on the next request and their projects' API keys stop working
    user.blocked = body.blocked
    await db.commit()
    return await _one(db, user)


class PlanIn(BaseModel):
    plan: str | None = None  # a plan id, or "" to take the plan away
    expires_at: datetime | None = None  # with `plan`: null = never ends


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@router.patch("/{user_id}/projects/{project_id}")
async def patch_project(user_id: int, project_id: int, body: PlanIn, _: Admin, db: DB) -> dict:
    """The admin sets a project's plan and its end."""
    project = await db.get(Project, project_id)
    user = await db.get(User, user_id)
    if project is None or user is None or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Проект не найден")
    if "plan" in body.model_fields_set:
        if body.plan and plan_of(body.plan) is None:
            raise HTTPException(status_code=422, detail="Нет такого тарифа")
        if body.plan:
            # dev:service:plan only forever; a given plan's period starts now, nothing paid for it
            await billing.give(db, project, body.plan, _naive_utc(body.expires_at))
        else:
            project.plan, project.plan_expires_at = NO_PLAN, None
    elif "expires_at" in body.model_fields_set:
        if not project.plan:
            raise HTTPException(status_code=409, detail="У проекта нет тарифа")
        if (plan := plan_of(project.plan)) and plan.forever and body.expires_at is not None:
            raise HTTPException(status_code=409, detail=f"Тариф {plan.title} выдаётся только бессрочно")
        project.plan_expires_at = _naive_utc(body.expires_at)
        project.reminded_days = None
    await db.commit()
    return await _one(db, user)


@router.get("/{user_id}/projects/{project_id}/channels")
async def project_channels(user_id: int, project_id: int, _: Admin, db: DB) -> dict:
    """The channels one project of the user tracks, its own channel first."""
    project = await db.get(Project, project_id)
    if project is None or project.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Проект не найден")
    rows = (await db.execute(
        select(Channel, ProjectChannel.own, ProjectChannel.created_at)
        .join(ProjectChannel, ProjectChannel.channel_id == Channel.id)
        .where(ProjectChannel.project_id == project.id)
        .order_by(ProjectChannel.own.desc(), ProjectChannel.created_at.desc())
    )).all()
    stats = await channel_stats(db, [c.id for c, _, _ in rows])
    return {"items": [
        {**channel_out(c, stats.get(c.id)), "own": own, "added_at": iso(added)} for c, own, added in rows
    ]}
