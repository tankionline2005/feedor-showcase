"""The «AI» sheet of the feed: the smart feed the viewer sees, and setting it up (see smart_feed.py)."""

from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import ai
from ..models import AiUsage, Post, SmartFeed, SmartVerdict, utcnow
from ..scope import tracked_channel_ids
from ..smart_feed import DEFAULT_PROMPT, REASON_LIMIT, feed_for, turn_on
from .deps import DB, Principal, Viewer

router = APIRouter(prefix="/api/smart", tags=["smart feed"])

PROMPT_LIMIT = 4000


def _not_admin(viewer: Principal) -> None:
    if viewer.is_admin:
        raise HTTPException(status_code=403, detail="Умная лента — для владельцев проектов и их сотрудников")


async def _state(db, viewer: Principal) -> dict:
    feed, editable = await feed_for(db, viewer)
    eng = await ai.engine(db, viewer.project_id, viewer.user_id)
    linked = eng.linked
    day = utcnow() - timedelta(hours=24)
    # what the feed costs (and what is left on their own OpenRouter): the owner's money, the owner's to see
    spent = await db.scalar(select(func.coalesce(func.sum(ai.PAID), 0.0)).where(
        AiUsage.user_id == viewer.user_id, AiUsage.project_id == feed.project_id,
        AiUsage.operation.in_(("filter", "vision")), AiUsage.at >= day, AiUsage.member_id.is_(None),
    )) if viewer.is_owner else None
    # posts the AI read in a day, and those shown of them (the person's word first)
    judged, matched = (await db.execute(
        select(func.count(), func.count().filter(func.coalesce(SmartVerdict.override, SmartVerdict.match).is_(True)))
        .where(SmartVerdict.feed_id == feed.id, SmartVerdict.created_at >= day, SmartVerdict.score >= 0)
    )).one()
    key = eng.key if linked and eng.own and viewer.is_owner else None
    correctable = feed.enabled and _may_correct(viewer, feed, editable)
    balance = await ai.balance(viewer.user_id, key) if key else None
    await db.commit()
    return {
        "linked": linked,
        "paused": eng.stop if linked else None,
        "editable": editable,
        # hiding and showing its posts with why: whoever sets it up, and staff allowed to teach the AI
        "can_correct": correctable,
        "enabled": feed.enabled,
        "prompt": feed.prompt,
        "default_prompt": DEFAULT_PROMPT,
        # which model reads the feed: only the owner chooses it (and sees it)
        "model": eng.model("filter") if viewer.is_owner and eng.own and linked else None,
        "day": {"cost": round(float(spent), 6) if spent is not None else None, "judged": judged, "matched": matched},
        "balance_left": balance.get("credits_left") if balance else None,
    }


@router.get("")
async def get_smart(viewer: Viewer, db: DB) -> dict:
    _not_admin(viewer)
    return await _state(db, viewer)


class PutIn(BaseModel):
    enabled: bool | None = None
    prompt: str | None = Field(default=None, max_length=PROMPT_LIMIT)
    reset_prompt: bool = False


@router.put("")
async def put_smart(body: PutIn, viewer: Viewer, db: DB) -> dict:
    _not_admin(viewer)
    feed, editable = await feed_for(db, viewer)
    if not editable:
        raise HTTPException(status_code=403, detail="Владелец проекта не разрешил вам настраивать умную ленту")
    if body.enabled is True and not feed.enabled:
        eng = await ai.engine(db, viewer.project_id, viewer.user_id)
        if not eng.linked:
            raise HTTPException(status_code=409, detail="Сначала подключите OpenRouter.ai в настройках"
                                if eng.own else eng.stop or ai.SHARED_DOWN)
        await turn_on(db, feed)
    elif body.enabled is False:
        feed.enabled = False
    if body.reset_prompt:
        feed.prompt = DEFAULT_PROMPT
    elif body.prompt is not None:
        if not body.prompt.strip():
            raise HTTPException(status_code=422, detail="Промпт пустой — опишите, какие посты показывать")
        feed.prompt = body.prompt.strip()
    await db.commit()
    return await _state(db, viewer)


def _may_correct(viewer: Principal, feed: SmartFeed, editable: bool) -> bool:
    # whoever sets it up, and a staff member allowed to teach the project's AI
    return editable or viewer.may("ai_teach")


async def _correctable(db, viewer: Principal, post_id: int) -> tuple[SmartFeed, SmartVerdict | None]:
    _not_admin(viewer)
    feed, editable = await feed_for(db, viewer)
    if not _may_correct(viewer, feed, editable):
        raise HTTPException(status_code=403, detail="Владелец проекта не разрешил вам обучать ИИ")
    if not feed.enabled:
        raise HTTPException(status_code=409, detail="Умная лента выключена")
    post = await db.scalar(select(Post.id).where(Post.id == post_id,
                                                 Post.channel_id.in_(tracked_channel_ids(feed.project_id))))
    if post is None:
        raise HTTPException(status_code=404, detail="Пост не найден")
    verdict = await db.scalar(select(SmartVerdict).where(SmartVerdict.feed_id == feed.id,
                                                         SmartVerdict.post_id == post_id))
    return feed, verdict


class CorrectIn(BaseModel):
    show: bool
    reason: str = Field(max_length=REASON_LIMIT)


@router.post("/posts/{post_id}/correct")
async def correct(post_id: int, body: CorrectIn, viewer: Viewer, db: DB) -> dict:
    """«Скрыть» a post the AI kept, or «Показать» one it turned down — with why: the AI learns from it."""
    feed, verdict = await _correctable(db, viewer, post_id)
    reason = " ".join(body.reason.split())
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="Напишите, почему")
    if verdict is None:
        # never read by the AI (from before the feed was on): it is shown, so it can only be hidden
        verdict = SmartVerdict(feed_id=feed.id, post_id=post_id, match=True, score=-1)
        db.add(verdict)
    verdict.override, verdict.override_reason, verdict.override_at = body.show, reason, utcnow()
    verdict.override_by = viewer.who
    await db.commit()
    return {"ok": True}


@router.delete("/posts/{post_id}/correct")
async def uncorrect(post_id: int, viewer: Viewer, db: DB) -> dict:
    """«Отменить»: the AI's decision again, and no lesson from this post any more."""
    _feed, verdict = await _correctable(db, viewer, post_id)
    if verdict is not None:
        if verdict.score < 0:
            await db.delete(verdict)  # the AI never read it: back to being simply shown
        else:
            verdict.override = verdict.override_reason = verdict.override_at = verdict.override_by = None
        await db.commit()
    return {"ok": True}


async def verdict_feed(db, viewer: Principal) -> SmartFeed | None:
    """The smart feed whose verdicts the viewer's feed shows (None: it is off)."""
    if viewer.is_admin or not (await ai.engine(db, viewer.project_id, viewer.user_id)).linked:
        return None  # without an OpenRouter the smart feed rests: everything is shown
    feed, _ = await feed_for(db, viewer)
    await db.commit()
    return feed if feed.enabled else None

