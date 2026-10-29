"""AI → «Темы»: the project's topics, a topic with its timeline, gallery and sources, and their
settings (see topics.py)."""

import asyncio
import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import ai, ai_runs, schedule, topics
from ..models import Channel, Member, Post, Project, Topic, TopicPost, TopicSeen, TopicState, TopicUpdate, utcnow
from ..services import media_slots, picture_hash, preview_path, same_picture, video_url
from .deps import DB, Principal, Viewer
from .posts import avatar_url, favorite_ids, iso, post_out

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/topics", tags=["topics"])

UPDATED_FOR = timedelta(hours=6)  # «Обновлена» shows this long after an update, unless seen


def _viewer_only(viewer: Principal) -> None:
    if viewer.is_admin:
        raise HTTPException(status_code=403, detail="Темы — для владельцев проектов и их сотрудников")


def _seen_filter(viewer: Principal):
    return TopicSeen.member_id == viewer.member_id if viewer.member_id else (
        (TopicSeen.user_id == viewer.user_id) & TopicSeen.member_id.is_(None))


def gallery(posts: list[Post], limit: int | None = None) -> list[dict]:
    """The pictures of a topic's posts, each picture once (the same photo from several channels too)."""
    out: list[dict] = []
    hashes: list[int] = []
    for p in posts:
        for slot, kind in media_slots(p):
            path = preview_path(p.id, slot)
            if not path.is_file():
                continue
            h = picture_hash(p.id, slot, path.stat().st_size)
            if h is not None and any(same_picture(h, other) for other in hashes):
                continue
            if h is not None:
                hashes.append(h)
            out.append({
                "url": f"/api/media/{p.id}" + (f"?i={slot}" if slot else ""),
                "full_url": f"/api/media/{p.id}/full?i={slot}" if kind == "photo" else None,
                "video_url": video_url(p, slot, kind),
                "kind": kind, "post_id": p.id,
            })
            if limit and len(out) >= limit:
                return out
    return out


async def _posts_of(db, topic_ids: list[int]) -> dict[int, list[tuple[Post, Channel]]]:
    rows = (await db.execute(
        select(TopicPost.topic_id, Post, Channel)
        .join(Post, Post.id == TopicPost.post_id).join(Channel, Channel.id == Post.channel_id)
        .where(TopicPost.topic_id.in_(topic_ids)).order_by(Post.posted_at)
    )).all()
    out: dict[int, list[tuple[Post, Channel]]] = {}
    for topic_id, p, ch in rows:
        out.setdefault(topic_id, []).append((p, ch))
    return out


async def _grown(db, topic_ids: list[int]) -> set[int]:
    """Topics that got something new after they began (more than one timeline line)."""
    return set((await db.execute(
        select(TopicUpdate.topic_id).where(TopicUpdate.topic_id.in_(topic_ids))
        .group_by(TopicUpdate.topic_id).having(func.count() > 1)
    )).scalars())


def _topic_json(topic: Topic, items: list[tuple[Post, Channel]], seen_at, media_limit: int | None, grew: bool) -> dict:
    channels: list[dict] = []
    for _p, ch in items:
        if not any(c["id"] == ch.id for c in channels):
            channels.append({"id": ch.id, "title": ch.title or ch.link, "avatar_url": avatar_url(ch)})
    first = items[0] if items else None
    return {
        "id": topic.id,
        "title": topic.title,
        "summary": topic.summary,
        "created_at": iso(topic.created_at),
        "updated_at": iso(topic.updated_at),
        "covered": topic.covered,
        "covered_by": topic.covered_by,  # put by hand, by whom; None: by the AI
        # big news after the channel told it: a reason for one more post
        "followup": topic.covered and topic.followup_at is not None,
        # something new was added lately, and this person has not opened it since
        "updated": grew and utcnow() - topic.updated_at < UPDATED_FOR and (seen_at is None or seen_at < topic.updated_at),
        # sources: the channels that wrote about it, each once however many posts it made
        "sources": len(channels),
        "posts_count": len(items),
        "hidden": topic.hidden_at is not None,
        "hidden_at": iso(topic.hidden_at),
        "hide_reason": topic.hide_reason,
        "hidden_by": topic.hidden_by,
        "channels": channels,
        "views": sum(p.views for p, _ in items),
        "first": {"channel": first[1].title or first[1].link, "posted_at": iso(first[0].posted_at)} if first else None,
        "media": gallery([p for p, _ in items], media_limit),
    }


# by runs, the newest first, and within a run the hottest first (see topics.py); older topics, made
# before runs were ranked, by their last update
ORDER = (func.coalesce(Topic.ranked_at, Topic.updated_at).desc(), func.coalesce(Topic.importance, 0).desc(),
         Topic.id.desc())


@router.get("")
async def list_topics(
    viewer: Viewer, db: DB, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=200),
    hidden: bool = False,
) -> dict:
    """The topics, or (hidden) those hidden in the project, the latest hidden first."""
    _viewer_only(viewer)
    query = select(Topic).where(Topic.project_id == viewer.project_id)
    query = (query.where(Topic.hidden_at.is_not(None)).order_by(Topic.hidden_at.desc(), Topic.id.desc()) if hidden
             else query.where(Topic.hidden_at.is_(None)).order_by(*ORDER))
    rows = (await db.execute(query.offset(offset).limit(limit + 1))).scalars().all()
    page = rows[:limit]
    posts = await _posts_of(db, [st.id for st in page])
    seen = dict((await db.execute(
        select(TopicSeen.topic_id, TopicSeen.seen_at).where(TopicSeen.topic_id.in_([st.id for st in page]), _seen_filter(viewer))
    )).all())
    grown = await _grown(db, [st.id for st in page])
    return {
        "items": [_topic_json(st, posts.get(st.id, []), seen.get(st.id), 6, st.id in grown) for st in page],
        "has_more": len(rows) > limit,
    }


@router.get("/settings")
async def get_settings(viewer: Viewer, db: DB) -> dict:
    _viewer_only(viewer)
    return await _settings(db, viewer)


async def _settings(db, viewer: Principal) -> dict:
    account = await ai.account_of(db, viewer.user_id)
    eng = await ai.engine(db, viewer.project_id, viewer.user_id)
    state = await topics.state_of(db, viewer.project_id)
    member = await db.get(Member, viewer.member_id) if viewer.member_id else None
    await db.commit()
    return {
        **await topics.schedule_info(db, viewer.project_id, state, account),
        "linked": eng.linked,
        "paused": eng.stop if eng.linked else None,
        # the owner, and staff allowed to: settings, teaching the AI; «Собрать сейчас»: the owner's only
        "editable": viewer.may("ai_configure"),
        "can_run": viewer.is_owner,
        "can_teach": viewer.may("ai_teach"),
        "enabled": state.enabled,
        "prompt": state.prompt if viewer.may("ai_configure") else None,
        "default_prompt": topics.DEFAULT_PROMPT if viewer.may("ai_configure") else None,
        "last_run_at": iso(state.last_run_at),
        "run": ai_runs.shown(state.run),
        "notify": member.ai_topic_notify if member else state.notify_owner,
        # staff get the bot's messages once they link their Telegram in the settings
        "telegram_linked": bool(member.tg_user_id) if member else True,
    }


class SettingsIn(BaseModel):
    enabled: bool | None = None
    prompt: str | None = Field(default=None, max_length=4000)
    reset_prompt: bool = False
    schedule: dict | None = None


@router.put("/settings")
async def put_settings(body: SettingsIn, viewer: Viewer, db: DB) -> dict:
    _viewer_only(viewer)
    if not viewer.may("ai_configure"):
        raise HTTPException(status_code=403, detail="Владелец проекта не разрешил вам настраивать темы")
    state = await topics.state_of(db, viewer.project_id)
    if body.enabled is not None:
        if body.enabled and not state.enabled:
            # from now on: the posts of the last interval, not all since the topics were last on
            await topics.from_now(db, state, await ai.account_of(db, viewer.user_id))
            state.covered_cursor_post_id = None  # «Есть в вашем канале»: the channel's posts from now on
        state.enabled = body.enabled
    if body.reset_prompt:
        state.prompt = topics.DEFAULT_PROMPT
    elif body.prompt is not None:
        if not body.prompt.strip():
            raise HTTPException(status_code=422, detail="Промпт пустой — опишите, что для вас тема")
        state.prompt = body.prompt.strip()
    if body.schedule is not None:
        try:
            state.schedule = schedule.stamped(schedule.normalize("topics", body.schedule), utcnow())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return await _settings(db, viewer)


class NotifyIn(BaseModel):
    on: bool


@router.put("/notify")
async def put_notify(body: NotifyIn, viewer: Viewer, db: DB) -> dict:
    """New topics in the person's Telegram, from the bot."""
    _viewer_only(viewer)
    if viewer.member_id:
        member = await db.get(Member, viewer.member_id)
        if body.on and not member.tg_user_id:
            raise HTTPException(status_code=409, detail="Сначала привяжите Telegram в настройках")
        member.ai_topic_notify = body.on
    else:
        (await topics.state_of(db, viewer.project_id)).notify_owner = body.on
    await db.commit()
    return await _settings(db, viewer)


_tasks: set[asyncio.Task] = set()  # kept referenced, or a run could be garbage-collected


@router.get("/run")
async def get_run(viewer: Viewer, db: DB) -> dict | None:
    """The run going (the page locks «Собрать сейчас» and shows how far), or how the last one ended."""
    _viewer_only(viewer)
    state = await db.get(TopicState, viewer.project_id)
    return ai_runs.shown(state.run if state else None)


@router.post("/run")
async def run_now(viewer: Viewer, db: DB) -> dict | None:
    """«Собрать сейчас»: a run right away instead of waiting for the schedule."""
    _viewer_only(viewer)
    if not viewer.is_owner:
        raise HTTPException(status_code=403, detail="Запускать ИИ вручную может только владелец проекта")
    account = await ai.account_of(db, viewer.user_id)
    eng = await ai.engine(db, viewer.project_id, viewer.user_id)
    if eng.stop:
        raise HTTPException(status_code=409, detail=eng.stop)
    await topics.state_of(db, viewer.project_id)
    await db.commit()
    project = await db.get(Project, viewer.project_id)
    # claimed here, not in the task: the page asking right after already sees it going
    run = await ai_runs.Run.claim(TopicState, project.id, "topics")
    if run is not None:
        async def go() -> None:
            try:
                await topics.run_project(project, account, force=True, run=run)
            except Exception:  # noqa: BLE001
                log.exception("topics run of project %s failed", project.id)

        _tasks.add(task := asyncio.create_task(go()))
        task.add_done_callback(_tasks.discard)
    # one going already (the schedule's): the page follows that one
    return ai_runs.shown((await db.get(TopicState, project.id, populate_existing=True)).run)


def _teaching(viewer: Principal) -> None:
    _viewer_only(viewer)
    if not viewer.may("ai_teach"):
        raise HTTPException(status_code=403, detail="Владелец проекта не разрешил вам обучать ИИ")


async def _topic(db, viewer: Principal, topic_id: int) -> Topic:
    topic = await db.get(Topic, topic_id)
    if topic is None or topic.project_id != viewer.project_id:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    return topic


class HideIn(BaseModel):
    reason: str = Field(max_length=topics.REASON_LIMIT)


@router.post("/{topic_id}/hide")
async def hide(topic_id: int, body: HideIn, viewer: Viewer, db: DB) -> dict:
    """«Скрыть тему», with why: off the list for the whole project, and the AI learns from the reason
    what topics the project does not want."""
    _teaching(viewer)
    topic = await _topic(db, viewer, topic_id)
    reason = " ".join(body.reason.split())
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="Напишите, почему тема вам не подходит")
    topic.hidden_at, topic.hide_reason, topic.hidden_by = utcnow(), reason, viewer.who
    await db.commit()
    return {"ok": True}


@router.post("/{topic_id}/unhide")
async def unhide(topic_id: int, viewer: Viewer, db: DB) -> dict:
    """«Вернуть»: the topic is back on the list, and the AI no longer counts it as one not wanted."""
    _teaching(viewer)
    topic = await _topic(db, viewer, topic_id)
    topic.hidden_at = topic.hide_reason = topic.hidden_by = None
    await db.commit()
    return {"ok": True}


@router.post("/{topic_id}/not-covered")
async def not_covered(topic_id: int, viewer: Viewer, db: DB) -> dict:
    """«Это не так» on «Есть в вашем канале»: the AI was wrong, the project's channel did not tell it."""
    _teaching(viewer)
    topic = await _topic(db, viewer, topic_id)
    topic.covered, topic.covered_dismissed, topic.covered_by = False, True, None
    topic.covered_at = topic.followup_at = None
    await db.commit()
    return {"ok": True}


@router.post("/{topic_id}/covered")
async def mark_covered(topic_id: int, viewer: Viewer, db: DB) -> dict:
    """«Есть в вашем канале» put by hand: the project's channel told it, though the AI did not see it."""
    _teaching(viewer)
    topic = await _topic(db, viewer, topic_id)
    topics.mark_covered(topic, viewer.who)
    await db.commit()
    return {"ok": True}


@router.get("/{topic_id}")
async def get_topic(topic_id: int, viewer: Viewer, db: DB) -> dict:
    _viewer_only(viewer)
    topic = await _topic(db, viewer, topic_id)
    items = (await _posts_of(db, [topic.id])).get(topic.id, [])
    seen = await db.scalar(select(TopicSeen).where(TopicSeen.topic_id == topic.id, _seen_filter(viewer)))
    out = _topic_json(topic, items, seen.seen_at if seen else None, None, topic.id in await _grown(db, [topic.id]))
    out["timeline"] = [{"at": iso(u.at), "text": u.text} for u in (await db.execute(
        select(TopicUpdate).where(TopicUpdate.topic_id == topic.id).order_by(TopicUpdate.at, TopicUpdate.id)
    )).scalars()]
    starred = await favorite_ids(db, viewer, [p.id for p, _ in items])
    out["posts"] = [{**post_out(p, ch, for_user=True), "favorite": p.id in starred} for p, ch in reversed(items)]
    # opened: «Обновлена» goes for this person
    if seen is None:
        db.add(TopicSeen(topic_id=topic.id, user_id=None if viewer.member_id else viewer.user_id,
                         member_id=viewer.member_id))
    else:
        seen.seen_at = utcnow()
    await db.commit()
    return out

