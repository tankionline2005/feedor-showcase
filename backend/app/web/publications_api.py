"""AI → «Публикации»: the project's posts that went out or are to (from anyone's chat), and the posts the AI wrote
for the new topics (publications.py). Everyone of the project sees the list; the owner and staff allowed to publish
edit and publish its posts (chat_posts_api.py); its settings are the owner's alone."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from .. import publications, tg_html
from ..models import ChatPost, Topic, utcnow
from .chat_posts_api import card, channel_header, may_edit
from .deps import DB, Principal, Viewer
from .posts import iso

router = APIRouter(prefix="/api/publications", tags=["publications"])

Filter = Literal["all", "scheduled", "published", "suggested"]


def _project_only(viewer: Principal) -> None:
    if viewer.is_admin or not viewer.project_id:
        raise HTTPException(status_code=403, detail="Публикации — для владельцев проектов и их сотрудников")


def _settings_out(s) -> dict:
    return {"topic_posts": s.topic_posts, "posts_limit": s.posts_limit, "prompt": s.prompt or "",
            "autopost": s.autopost, "autopost_hours": s.autopost_hours, "autopost_gap_min": s.autopost_gap_min,
            "min_importance_posts": s.min_importance_posts,
            "daily_limit": s.daily_limit, "autopost_min_sources": s.autopost_min_sources,
            "limits": publications.LIMITS, "hours": publications.HOURS, "gaps": publications.GAPS,
            "daily_limits": publications.DAILY_LIMITS, "sources_options": publications.SOURCES}


# the calendar's moment of a post: when it is to go, when it went, or when it failed to
MOMENT = func.coalesce(ChatPost.scheduled_at, ChatPost.published_at, ChatPost.publish_error_at)
CALENDAR_MOST = 3000  # posts a month's calendar takes at most
SPAN_MOST = timedelta(days=45)


def _utc(at: datetime) -> datetime:
    return (at.astimezone(timezone.utc) if at.tzinfo else at).replace(tzinfo=None)


def _span(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    start, end = _utc(start), _utc(end)
    if end <= start or end - start > SPAN_MOST:
        raise HTTPException(status_code=422, detail="Такого промежутка нет")
    return start, end


def _state(post: ChatPost) -> str:
    return "scheduled" if post.scheduled_at else "published" if post.published_at else "failed"


@router.get("/calendar")
async def calendar(viewer: Viewer, db: DB, start: datetime, end: datetime) -> dict:
    """«Календарь»: the posts that are to go, went out or failed to between `start` and `end` (the page asks for its
    month, in the person's time), each in a few words — its moment, its state, its first line."""
    _project_only(viewer)
    start, end = _span(start, end)
    rows = (await db.execute(
        select(ChatPost).where(ChatPost.project_id == viewer.project_id,
                               ChatPost.scheduled_at.is_not(None) | ChatPost.published_at.is_not(None)
                               | ChatPost.publish_error_at.is_not(None),
                               MOMENT >= start, MOMENT < end)
        .order_by(MOMENT).limit(CALENDAR_MOST)
    )).scalars().all()
    items = []
    for p in rows:
        first = next((line for line in tg_html.plain(p.text).splitlines() if line.strip()), "")
        at = p.scheduled_at or p.published_at or p.publish_error_at
        items.append({"id": p.id, "at": iso(at), "state": _state(p), "title": first.strip()[:90]})
    return {"items": items}


@router.get("")
async def list_publications(viewer: Viewer, db: DB, filter: Filter = "all", offset: int = Query(default=0, ge=0),
                            limit: int = Query(default=20, ge=1, le=50), start: datetime | None = None,
                            end: datetime | None = None) -> dict:
    """«Все / Запланированные / Опубликованные / Предложения ИИ»: the scheduled first (the soonest on top of them),
    then by when they went out or were written, the newest first. With `start` and `end` (a day of the calendar):
    the posts whose moment falls between them, by the clock."""
    _project_only(viewer)
    where = {"all": publications.listed(), "scheduled": ChatPost.scheduled_at.is_not(None),
             "published": ChatPost.published_at.is_not(None), "suggested": publications.suggested()}[filter]
    query = select(ChatPost).where(ChatPost.project_id == viewer.project_id, where)
    if start is not None and end is not None:
        start, end = _span(start, end)
        query = query.where(MOMENT >= start, MOMENT < end).order_by(MOMENT, ChatPost.id)
    else:
        query = query.order_by(ChatPost.scheduled_at.is_(None), ChatPost.scheduled_at,
                               func.coalesce(ChatPost.published_at, ChatPost.created_at).desc(), ChatPost.id.desc())
    rows = (await db.execute(query.offset(offset).limit(limit + 1))).scalars().all()
    header = await channel_header(db, viewer.project_id)
    items = [await card(db, p, header, editable=await may_edit(db, viewer, p)) for p in rows[:limit]]
    return {"items": items, "has_more": len(rows) > limit,
            # the settings' gear: the owner's alone
            "settings": _settings_out(await publications.settings_of(db, viewer.project_id)) if viewer.is_owner else None}


class SettingsIn(BaseModel):
    topic_posts: bool | None = None
    posts_limit: int | None = None
    prompt: str | None = Field(default=None, max_length=publications.PROMPT_LIMIT)
    autopost: Literal["off", "now", "delay"] | None = None
    autopost_hours: int | None = None
    autopost_gap_min: int | None = None
    min_importance_posts: int | None = None
    daily_limit: int | None = None
    autopost_min_sources: int | None = None


# a setting with a few allowed values: its field and the values
CHOICES = {
    "posts_limit": publications.LIMITS, "autopost_hours": publications.HOURS, "autopost_gap_min": publications.GAPS,
    "min_importance_posts": publications.IMPORTANCE,
    "daily_limit": publications.DAILY_LIMITS, "autopost_min_sources": publications.SOURCES,
}


@router.get("/importance")
async def importance_examples(viewer: Viewer, db: DB) -> dict:
    """«Что такое важность»: the bands of the scale, each with a few of the project's own recent topics — the scale is
    easier to see on topics the owner knows."""
    _project_only(viewer)
    since = utcnow() - timedelta(days=7)
    bands = []
    for low, high in publications.BANDS:
        rows = (await db.execute(
            select(Topic.title, Topic.importance).where(
                Topic.project_id == viewer.project_id, Topic.hidden_at.is_(None), Topic.created_at >= since,
                Topic.importance >= low, Topic.importance <= high)
            .order_by(Topic.importance.desc(), Topic.created_at.desc()).limit(3))).all()
        bands.append({"from": low, "to": high, "examples": [{"title": t, "importance": i} for t, i in rows]})
    return {"bands": bands}


@router.put("/settings")
async def save_settings(body: SettingsIn, viewer: Viewer, db: DB) -> dict:
    """The owner's alone: «Создавать тематические посты» (how many a run, the prompt) and «Автопостинг»."""
    _project_only(viewer)
    if not viewer.is_owner:
        raise HTTPException(status_code=403, detail="Настройки публикаций — только у владельца проекта")
    s = await publications.settings_of(db, viewer.project_id)
    for field, allowed in CHOICES.items():
        value = getattr(body, field)
        if value is None:
            continue
        if value not in allowed:
            raise HTTPException(status_code=422, detail="Такого варианта нет")
        setattr(s, field, value)
    if body.prompt is not None:
        s.prompt = body.prompt.strip()
    if body.topic_posts is not None:
        s.topic_posts = body.topic_posts
        if not body.topic_posts:
            s.autopost = "off"  # it publishes those posts: nothing to publish without them
    if body.autopost is not None:
        if body.autopost != "off" and not s.topic_posts:
            raise HTTPException(status_code=409, detail="Сначала включите «Создавать тематические посты»")
        s.autopost = body.autopost
    if s.autopost == "off":
        # autoposting off: what it queued and has not published yet stays a suggestion, it does not go out
        await db.execute(update(ChatPost).where(
            ChatPost.project_id == viewer.project_id, ChatPost.scheduled_by == publications.AUTOPOST_NAME,
            ChatPost.scheduled_at.is_not(None)).values(scheduled_at=None, scheduled_by=None))
    await db.commit()
    return _settings_out(s)
