"""The blue badges: what came since the person last looked — new posts of the feed, new topics, new posts
the AI wrote by topics («Публикации») — and the chats with an answer they have not read yet. Per person and per
project: the owner's are theirs, each staff member has their own.

Looking at a list (the feed, the topics, the publications) moves its mark to now; opening a chat reads it.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..models import ChatMessage, ChatPost, ChatThread, Post, SeenMark, SmartVerdict, Topic, utcnow
from ..scope import tracked_channel_ids
from .chat_api import _mine as mine_chats
from .chat_api import answered as answered_messages
from .deps import DB, Principal, Viewer
from .smart_api import verdict_feed
from .triggers_api import new_hits

router = APIRouter(prefix="/api/badges", tags=["badges"])

KINDS = ("feed", "topics", "posts")
MOST = 99  # counted up to this; the badge says «99+» past it

Kind = Literal["feed", "topics", "posts"]


def _only_projects(viewer: Principal) -> None:
    if viewer.is_admin or not viewer.project_id:
        raise HTTPException(status_code=403, detail="Счётчики — для владельцев проектов и их сотрудников")


def _person(viewer: Principal) -> str:
    return f"m{viewer.member_id}" if viewer.member_id else f"u{viewer.user_id}"


async def _newest_post(db, project_id: int) -> int:
    return await db.scalar(select(func.max(Post.id)).where(Post.channel_id.in_(tracked_channel_ids(project_id)))) or 0


async def _marks(db, viewer: Principal) -> dict[str, SeenMark]:
    """The person's marks; the first time, each starts now (nothing counts as new yet)."""
    def mine():
        return select(SeenMark).where(SeenMark.project_id == viewer.project_id, SeenMark.person == _person(viewer))

    marks = {m.kind: m for m in (await db.execute(mine())).scalars()}
    missing = [k for k in KINDS if k not in marks]
    if missing:
        newest = await _newest_post(db, viewer.project_id)
        for kind in missing:
            marks[kind] = SeenMark(project_id=viewer.project_id, person=_person(viewer), member_id=viewer.member_id,
                                   kind=kind, at=utcnow(), post_id=newest)
            db.add(marks[kind])
        try:
            await db.commit()
        except IntegrityError:
            # another page made them at the same moment: theirs it is
            await db.rollback()
            marks = {m.kind: m for m in (await db.execute(mine())).scalars()}
    return marks


async def _count(db, stmt) -> int:
    return await db.scalar(select(func.count()).select_from(stmt.limit(MOST + 1).subquery())) or 0


@router.get("")
async def badges(viewer: Viewer, db: DB) -> dict:
    _only_projects(viewer)
    marks = await _marks(db, viewer)

    # the feed: posts after the newest one seen; with the smart feed on, only those it keeps
    feed = select(Post.id).where(Post.channel_id.in_(tracked_channel_ids(viewer.project_id)),
                                 Post.id > (marks["feed"].post_id or 0), Post.deleted_at.is_(None))
    smart = await verdict_feed(db, viewer) if viewer.plan else None
    if smart is not None:
        feed = feed.join(SmartVerdict, (SmartVerdict.post_id == Post.id) & (SmartVerdict.feed_id == smart.id)).where(
            func.coalesce(SmartVerdict.override, SmartVerdict.match).is_(True))

    topics = select(Topic.id).where(Topic.project_id == viewer.project_id, Topic.hidden_at.is_(None),
                                    func.coalesce(Topic.ranked_at, Topic.created_at) > marks["topics"].at)
    posts = select(ChatPost.id).where(ChatPost.project_id == viewer.project_id, ChatPost.topic_id.is_not(None),
                                      ChatPost.created_at > marks["posts"].at)

    # chats: an answer (or a failed one) after the person last had the chat open
    answered = (select(ChatMessage.thread_id, func.max(ChatMessage.created_at).label("last"))
                .where(answered_messages())
                .group_by(ChatMessage.thread_id).subquery())
    unread = (select(ChatThread.id).join(answered, answered.c.thread_id == ChatThread.id)
              .where(mine_chats(viewer), ChatThread.read_at.is_not(None), answered.c.last > ChatThread.read_at))
    return {
        "feed": await _count(db, feed) if viewer.plan else 0,
        "topics": await _count(db, topics),
        "posts": await _count(db, posts),
        "chats": await _count(db, unread) if viewer.may("ai_chat") else 0,
        # «Триггер-слова»: the posts with the person's words they have not looked at yet
        "triggers": min(MOST + 1, sum((await new_hits(db, viewer)).values())),
        "most": MOST,
    }


class SeenIn(BaseModel):
    kind: Kind


@router.post("/seen")
async def seen(body: SeenIn, viewer: Viewer, db: DB) -> dict:
    """The person is looking at the list: what is there now is seen."""
    _only_projects(viewer)
    mark = (await _marks(db, viewer))[body.kind]
    mark.at = utcnow()
    if body.kind == "feed":
        mark.post_id = await _newest_post(db, viewer.project_id)
    await db.commit()
    return {"ok": True}
