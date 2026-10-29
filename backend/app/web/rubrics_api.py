"""AI → «Публикации» → «Рубрики» (rubrics.py): the owner's alone — the list, a new one from a preset, its settings
saved as they change, an example issue written at once."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .. import ai, publications, rubrics, schedule
from ..models import ChatPost, Project, Rubric, utcnow
from ..scope import tracked_channel_ids
from .deps import DB, Principal, Viewer
from .posts import iso

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rubrics", tags=["rubrics"])


def _owner_only(viewer: Principal) -> None:
    if not viewer.is_owner:
        raise HTTPException(status_code=403, detail="Рубрики настраивает владелец проекта")


def rubric_out(r: Rubric) -> dict:
    sched, now = schedule.of("rubrics", r.schedule), utcnow()
    next_at = schedule.next_run(sched, now, r.last_run_at, r.created_at) if r.enabled else None
    return {
        "id": r.id, "name": r.name, "enabled": r.enabled, "source": r.source, "period_hours": r.period_hours,
        "max_items": r.max_items, "min_importance": r.min_importance, "channel_ids": r.channel_ids or [],
        "words": r.words or [], "prompt": r.prompt, "header": r.header, "footer": r.footer, "picture": r.picture,
        "mode": r.mode, "last_run_at": iso(r.last_run_at), "last_error": r.last_error,
        # the schedule, as the topics' (ScheduleEditor on the page)
        "schedule": sched, "schedule_summary": schedule.summary(sched),
        "rule_summaries": [schedule.rule_summary(x) for x in sched["rules"]],
        "schedule_options": schedule.OPTIONS["rubrics"],
        "next_run_at": iso(next_at) + "Z" if next_at else None,
        "upcoming": [iso(m) + "Z" for m in schedule.upcoming(sched, now, r.last_run_at, r.created_at)] if r.enabled
        else [],
        "per_day": round(schedule.per_day(sched) or 0, 1),
        "waiting_posts": None,
    }


def _options() -> dict:
    return {"periods": rubrics.PERIODS, "item_counts": rubrics.ITEMS, "most": rubrics.MOST,
            "presets": [{"key": k, "name": p["name"]} for k, p in rubrics.PRESETS.items()]}


async def _mine(db, viewer: Principal, rubric_id: int) -> Rubric:
    _owner_only(viewer)
    r = await db.get(Rubric, rubric_id)
    if r is None or r.project_id != viewer.project_id:
        raise HTTPException(status_code=404, detail="Рубрика не найдена")
    return r


@router.get("")
async def list_rubrics(viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    rows = (await db.execute(select(Rubric).where(Rubric.project_id == viewer.project_id)
                             .order_by(Rubric.id))).scalars().all()
    return {"items": [rubric_out(r) for r in rows], **_options()}


class NewIn(BaseModel):
    preset: str = "blank"


@router.post("")
async def create_rubric(body: NewIn, viewer: Viewer, db: DB) -> dict:
    """A new rubric from a preset — off until the owner turns it on, to look it over first."""
    _owner_only(viewer)
    preset = rubrics.PRESETS.get(body.preset)
    if preset is None:
        raise HTTPException(status_code=422, detail="Такой заготовки нет")
    count = await db.scalar(select(func.count()).select_from(Rubric).where(Rubric.project_id == viewer.project_id))
    if count >= rubrics.MOST:
        raise HTTPException(status_code=409, detail=f"В проекте может быть не больше {rubrics.MOST} рубрик")
    # the person's zone comes with the schedule's first change on the page
    sched = schedule.normalize("rubrics", {"rules": [{"kind": "times", "days": preset["days"] or schedule.ALL_DAYS,
                                                      "times": preset["times"]}]})
    r = Rubric(project_id=viewer.project_id, name=preset["name"], schedule=schedule.stamped(sched, utcnow()),
               source=preset["source"], period_hours=preset["period_hours"], max_items=preset["max_items"],
               min_importance=preset["min_importance"], prompt=preset["prompt"], header=preset["header"])
    db.add(r)
    await db.commit()
    return rubric_out(r)


class RubricIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    schedule: dict | None = None
    source: str | None = None
    period_hours: int | None = None
    max_items: int | None = None
    min_importance: int | None = None
    channel_ids: list[int] | None = None
    words: list[str] | str | None = None
    prompt: str | None = Field(default=None, max_length=rubrics.PROMPT_LIMIT)
    header: str | None = Field(default=None, max_length=rubrics.EDGE_LIMIT)
    footer: str | None = Field(default=None, max_length=rubrics.EDGE_LIMIT)
    picture: str | None = None
    mode: str | None = None


def _bad(what: str = "Такого варианта нет"):
    return HTTPException(status_code=422, detail=what)


@router.put("/{rubric_id}")
async def save_rubric(rubric_id: int, body: RubricIn, viewer: Viewer, db: DB) -> dict:
    r = await _mine(db, viewer, rubric_id)
    if body.name is not None:
        name = " ".join(body.name.split())
        if not name:
            raise _bad("Назовите рубрику")
        r.name = name[:rubrics.NAME_LIMIT]
    if body.schedule is not None:
        try:
            r.schedule = schedule.stamped(schedule.normalize("rubrics", {**body.schedule, "posts": None}), utcnow())
        except ValueError as exc:
            raise _bad(str(exc)) from exc
    for field, allowed in (("source", rubrics.SOURCES), ("period_hours", rubrics.PERIODS),
                           ("max_items", rubrics.ITEMS), ("min_importance", list(range(1, 11))),
                           ("picture", rubrics.PICTURES), ("mode", rubrics.MODES)):
        value = getattr(body, field)
        if value is not None:
            if value not in allowed:
                raise _bad()
            setattr(r, field, value)
    if body.channel_ids is not None:
        mine = set((await db.execute(tracked_channel_ids(viewer.project_id))).scalars())
        if any(c not in mine for c in body.channel_ids):
            raise _bad("Такого канала нет в проекте")
        r.channel_ids = list(dict.fromkeys(body.channel_ids)) or None
    if body.words is not None:
        raw = body.words.split(",") if isinstance(body.words, str) else body.words
        words = [w for w in dict.fromkeys(" ".join(str(x).split())[:40] for x in raw) if w]
        if len(words) > rubrics.WORDS_MOST:
            raise _bad(f"Не больше {rubrics.WORDS_MOST} слов")
        r.words = words or None
    for field in ("prompt", "header", "footer"):
        value = getattr(body, field)
        if value is not None:
            setattr(r, field, value.strip())
    if body.enabled is not None:
        if body.enabled and not r.enabled:
            r.last_error = None
            # its times count from now: those passed while it was off are not issues missed, to make up at once
            r.schedule = schedule.stamped(schedule.of("rubrics", r.schedule), utcnow())
        r.enabled = body.enabled
    await db.commit()
    return rubric_out(r)


@router.delete("/{rubric_id}")
async def delete_rubric(rubric_id: int, viewer: Viewer, db: DB) -> dict:
    """Its issues that went out or are to stay in «Публикации»; its suggestions nobody took go with it."""
    r = await _mine(db, viewer, rubric_id)
    await db.execute(delete(ChatPost).where(ChatPost.rubric_id == r.id, publications.forgettable()))
    await db.delete(r)
    await db.commit()
    return {"ok": True}


@router.post("/{rubric_id}/example")
async def example(rubric_id: int, viewer: Viewer, db: DB) -> dict:
    """«Сделать пример сейчас»: an issue written at once, by the rubric as it is now, only to look at — nothing is
    saved or published."""
    r = await _mine(db, viewer, rubric_id)
    project = await db.get(Project, viewer.project_id)
    try:
        text, _ = await rubrics.compose(r, project.owner_id)
    except rubrics.NothingToTake as exc:
        raise _bad(str(exc).replace(" — выпуск не вышел.", ".")) from exc
    except ai.AiPaused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ai.AiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"text": text}
