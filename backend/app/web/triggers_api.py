"""«Триггер-слова» of the feed: the person's triggers in the current project, and setting them up (see
triggers.py). Each person's own: the owner's, and each staff member's; no AI, so no switch for them."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import triggers
from ..models import Channel, Member, Project, ProjectGroup, Trigger, TriggerHit
from ..billing import NO_PLAN_TEXT
from ..plans import plan_of
from ..scope import tracked_channel_ids
from .deps import DB, Principal, Viewer
from .posts import avatar_url, iso

router = APIRouter(prefix="/api/triggers", tags=["triggers"])

MOST_WORDS = 20
WORD_LIMIT = 60


def _only_projects(viewer: Principal) -> None:
    if viewer.is_admin or not viewer.project_id:
        raise HTTPException(status_code=403, detail="Триггер-слова — для владельцев проектов и их сотрудников")


person = triggers.person_key
mine = triggers.of_person


async def new_hits(db, viewer: Principal) -> dict[int, int]:
    """Per trigger of the person: hits after the newest one they have seen."""
    return dict((await db.execute(
        select(Trigger.id, func.count(TriggerHit.id)).join(TriggerHit, TriggerHit.trigger_id == Trigger.id)
        .where(mine(viewer), TriggerHit.id > Trigger.seen_hit_id).group_by(Trigger.id)
    )).all())


def trigger_json(t: Trigger, new: int = 0, total: int = 0) -> dict:
    return {
        "id": t.id, "name": t.name, "words": t.words, "mode": t.mode,
        "channels_mode": t.channels_mode, "channel_ids": t.channel_ids,
        "notify": t.notify, "notify_dm": t.notify_dm, "notify_chat_id": t.notify_chat_id,
        "new": new, "total": total, "created_at": iso(t.created_at),
    }


async def _state(db, viewer: Principal) -> dict:
    items = (await db.execute(select(Trigger).where(mine(viewer)).order_by(Trigger.created_at))).scalars().all()
    new = await new_hits(db, viewer)
    totals = dict((await db.execute(
        select(TriggerHit.trigger_id, func.count()).where(TriggerHit.trigger_id.in_([t.id for t in items]))
        .group_by(TriggerHit.trigger_id))).all()) if items else {}
    linked = True
    if viewer.member_id:
        linked = bool((await db.get(Member, viewer.member_id)).tg_user_id)
    plan = plan_of(viewer.plan)
    return {
        "items": [trigger_json(t, new.get(t.id, 0), totals.get(t.id, 0)) for t in items],
        # the plan's limit is the project's: the owner's and the staff's triggers together
        "used": await _project_count(db, viewer.project_id),
        "limit": plan.triggers if plan else 0,  # None: no limit
        # the bot's messages to the person: a staff member links their Telegram first
        "telegram_linked": linked,
        "can_groups": True,
    }


async def _project_count(db, project_id: int) -> int:
    """The triggers the project watches with: a paused one (over the plan's limit) does not count."""
    return await db.scalar(select(func.count()).select_from(Trigger).where(
        Trigger.project_id == project_id, Trigger.paused_at.is_(None))) or 0


@router.get("")
async def list_triggers(viewer: Viewer, db: DB) -> dict:
    _only_projects(viewer)
    return await _state(db, viewer)


class TriggerIn(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    words: list[str] = Field(max_length=MOST_WORDS)
    mode: Literal["forms", "exact"] = "forms"
    channels_mode: Literal["all", "only"] = "all"
    channel_ids: list[int] = Field(default_factory=list, max_length=2000)
    notify: bool = False
    notify_dm: bool = True
    notify_chat_id: int | None = None


async def _clean(db, viewer: Principal, body: TriggerIn) -> dict:
    words: list[str] = []
    for raw in body.words:
        w = " ".join(raw.split())[:WORD_LIMIT]
        if w and w.lower() not in (x.lower() for x in words):
            words.append(w)
    if not words or not triggers.compile_words(words, body.mode):
        raise HTTPException(status_code=422, detail="Добавьте хотя бы одно слово")
    tracked = set((await db.execute(tracked_channel_ids(viewer.project_id))).scalars())
    channel_ids = sorted(set(body.channel_ids) & tracked)
    if body.channels_mode == "only" and not channel_ids:
        raise HTTPException(status_code=422, detail="Выберите хотя бы один канал")
    chat_id = body.notify_chat_id
    if chat_id is not None:
        if await db.get(ProjectGroup, (viewer.project_id, chat_id)) is None:
            raise HTTPException(status_code=404, detail="Этой группы нет в группах проекта")
    return {
        "name": (" ".join((body.name or "").split()) or words[0])[:64], "words": words, "mode": body.mode,
        "channels_mode": body.channels_mode, "channel_ids": channel_ids, "notify": body.notify,
        "notify_dm": body.notify_dm, "notify_chat_id": chat_id,
    }


@router.post("")
async def add_trigger(body: TriggerIn, viewer: Viewer, db: DB) -> dict:
    _only_projects(viewer)
    plan = plan_of(viewer.plan)
    if plan is None:
        raise HTTPException(status_code=403, detail=NO_PLAN_TEXT)
    if plan.triggers is not None and await _project_count(db, viewer.project_id) >= plan.triggers:
        raise HTTPException(status_code=409, detail=f"По тарифу {plan.title} — не больше {plan.triggers} "
                                                     "триггер-слов в проекте (ваших и сотрудников вместе)")
    trigger = Trigger(project_id=viewer.project_id, person=person(viewer), member_id=viewer.member_id,
                      **await _clean(db, viewer, body))
    db.add(trigger)
    await db.commit()
    await triggers.backfill(trigger.id)  # its feed at once, from the posts already there
    return {**await _state(db, viewer), "id": trigger.id}


async def _trigger(db, viewer: Principal, trigger_id: int) -> Trigger:
    trigger = await db.scalar(select(Trigger).where(Trigger.id == trigger_id, mine(viewer)))
    if trigger is None:
        raise HTTPException(status_code=404, detail="Триггер-слово не найдено")
    return trigger


@router.put("/{trigger_id}")
async def put_trigger(trigger_id: int, body: TriggerIn, viewer: Viewer, db: DB) -> dict:
    _only_projects(viewer)
    trigger = await _trigger(db, viewer, trigger_id)
    values = await _clean(db, viewer, body)
    # what it finds changed: its feed is made anew
    anew = any(getattr(trigger, k) != values[k] for k in ("words", "mode", "channels_mode", "channel_ids"))
    for key, value in values.items():
        setattr(trigger, key, value)
    await db.commit()
    if anew:
        await triggers.backfill(trigger.id)
    return await _state(db, viewer)


@router.delete("/{trigger_id}")
async def delete_trigger(trigger_id: int, viewer: Viewer, db: DB) -> dict:
    _only_projects(viewer)
    await db.delete(await _trigger(db, viewer, trigger_id))
    await db.commit()
    return await _state(db, viewer)


@router.post("/{trigger_id}/seen")
async def seen(trigger_id: int, viewer: Viewer, db: DB) -> dict:
    """The person looks at the trigger's feed: what is in it is seen (its badge goes)."""
    _only_projects(viewer)
    trigger = await _trigger(db, viewer, trigger_id)
    trigger.seen_hit_id = await db.scalar(
        select(func.max(TriggerHit.id)).where(TriggerHit.trigger_id == trigger.id)) or trigger.seen_hit_id
    await db.commit()
    return {"ok": True}


@router.get("/channels")
async def channels(viewer: Viewer, db: DB) -> dict:
    """The project's channels to choose from (the ones it tracks)."""
    _only_projects(viewer)
    rows = (await db.execute(select(Channel).where(Channel.id.in_(tracked_channel_ids(viewer.project_id)))
                             .order_by(Channel.title))).scalars().all()
    return {"items": [{"id": c.id, "title": c.title or c.username or c.link, "username": c.username,
                       "avatar_url": avatar_url(c)} for c in rows]}
