import logging
from datetime import datetime, timedelta

import httpx
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import case, delete, func, or_, select, update

from .. import settings_store, tg_web
from ..config import settings
from ..models import Channel, Favorite, Member, Post, ProjectGroup, SmartVerdict, Trigger, TriggerHit, User, utcnow
from ..notify import BotError
from ..originals import group_message, send_original
from ..scope import can_see, tracked_channel_ids, visible_channels
from ..services import media_position, media_slots, preview_path, video_url
from ..triggers import of_person, person_key
from .deps import DB, Viewer
from .smart_api import verdict_feed
from ..fr_plus import fr_plus, fr_plus_sql

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["posts"])

PERIODS = {
    "20m": timedelta(minutes=20),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "all": None,
}
SORTS = {
    "new": Post.posted_at,
    "views": Post.views,
    "er": Post.er,
    "reactions": Post.reactions,
    "comments": Post.comments,
    "forwards": Post.forwards,
    "fr": Post.fr,
    "fr_plus": fr_plus_sql(),
}
# Ratios are noise on a handful of views (1 reaction on 1 view is ER 100%), so in these sorts
# a post competes only once its views reach 1% of the channel's subscribers (100 while the
# subscriber count is unknown); until then it goes after the rated ones.
RATIO_SORTS = {"er", "fr", "fr_plus"}
RATED_SHARE = 0.01
RATED_FALLBACK_VIEWS = 100


def rated_threshold(subscribers: int | None) -> float:
    return subscribers * RATED_SHARE if subscribers else RATED_FALLBACK_VIEWS


def rated_sql():
    return Post.views >= case(
        (Channel.subscribers > 0, Channel.subscribers * RATED_SHARE), else_=RATED_FALLBACK_VIEWS
    )


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def post_url(post: Post, ch: Channel) -> str | None:
    if ch.username:
        return f"https://t.me/{ch.username}/{post.msg_id}"
    if ch.tg_id:
        return f"https://t.me/c/{ch.tg_id}/{post.msg_id}"
    return None


def avatar_url(ch: Channel) -> str | None:
    if not ch.has_avatar:
        return None
    version = int(ch.info_updated_at.timestamp()) if ch.info_updated_at else 0
    return f"/api/channels/{ch.id}/avatar?v={version}"


def media_out(post: Post) -> list[dict]:
    return [
        {
            "url": f"/api/media/{post.id}" + (f"?i={slot}" if slot else ""),
            "kind": kind,
            # the full-size photo is fetched from Telegram when opened, never stored
            "full_url": f"/api/media/{post.id}/full?i={slot}" if kind == "photo" else None,
            # a video plays from Telegram's own servers (tg_web.py)
            "video_url": video_url(post, slot, kind),
        }
        for slot, kind in media_slots(post)
    ]


def post_out(post: Post, ch: Channel, *, for_user: bool = False) -> dict:
    # users never see our aggregator group: for them a post opens in its own channel
    repost_url = None if for_user else post.repost_url
    return {
        "id": post.id,
        "channel": {
            "id": ch.id,
            "title": ch.title or ch.link,
            "username": ch.username,
            "avatar_url": avatar_url(ch),
        },
        "msg_id": post.msg_id,
        # once reposted, the post opens in our aggregator group; the original stays in source_url
        "url": repost_url or post_url(post, ch),
        "source_url": post_url(post, ch),
        "reposted": bool(repost_url),
        # a user can have the bot send them this post ("Посмотреть оригинал поста")
        "sendable": for_user and post.repost_status == "done" and group_message(post.repost_url) is not None,
        "deleted": post.deleted_at is not None,
        "posted_at": iso(post.posted_at),
        "text": post.text,
        "html": post.html,
        "media_type": post.media_type,
        "media_count": post.media_count,
        "preview_url": f"/api/media/{post.id}" if post.has_preview else None,
        "media": media_out(post),
        "views": post.views,
        "forwards": post.forwards,
        "reactions": post.reactions,
        "comments": post.comments,
        "er": post.er,
        "fr": post.fr,
        "fr_plus": fr_plus(post.fr, ch.fr_base),
        # enough views to take a place in the ER / FR / FR+ rankings
        "rated": post.views >= rated_threshold(ch.subscribers),
        "reactions_top": post.reactions_top or [],
        "repost_status": post.repost_status,
        "repost_error": None if for_user else post.repost_error,
        "metrics_updated_at": iso(post.metrics_updated_at),
    }


Period = Literal["20m", "1h", "6h", "24h", "7d", "30d", "all"]
SortKey = Literal["new", "er", "fr", "fr_plus", "forwards", "reactions", "comments", "views"]


def posts_query(
    *,
    period: str = "all",
    sort: str = "new",
    channel_ids: list[int] | None = None,
    q: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    visible=None,
):
    """Posts with their channels; shared by the web UI and the external API.
    `visible`: a subquery of the channel ids the viewer may see (None: all, for the admin)."""
    stmt = select(Post, Channel).join(Channel, Post.channel_id == Channel.id).where(Channel.status != "deleting")
    if visible is not None:
        stmt = stmt.where(Post.channel_id.in_(visible))
    if PERIODS[period] is not None:
        stmt = stmt.where(Post.posted_at >= utcnow() - PERIODS[period])
    if since is not None:
        stmt = stmt.where(Post.posted_at >= since)
    if until is not None:
        stmt = stmt.where(Post.posted_at < until)
    if channel_ids:
        stmt = stmt.where(Post.channel_id.in_(channel_ids))
    if q and q.strip():
        # the post's text, or the text read from its pictures
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Post.text.ilike(like), Post.extracted_text_from_media.ilike(like)))
    order = [SORTS[sort].desc(), Post.posted_at.desc(), Post.id.desc()]
    if sort in RATIO_SORTS:
        order.insert(0, case((rated_sql(), 1), else_=0).desc())
    return stmt.order_by(*order)


def smart_filter(stmt, feed, *, kept: bool = True):
    """«Умная лента» over a posts query: what it shows (kept) or what it turned down. Shown: what the AI kept
    or the person showed, and the posts it never read up to where it has got (those from before it was on);
    a newer post waits until it is read. Adds the verdict's columns: score, reason, post_id, override,
    override_reason, override_by."""
    verdict = (SmartVerdict.post_id == Post.id) & (SmartVerdict.feed_id == feed.id)
    # the person's word, where they overruled the AI, else the AI's
    shown = func.coalesce(SmartVerdict.override, SmartVerdict.match)
    if kept:
        stmt = stmt.outerjoin(SmartVerdict, verdict).where(
            shown.is_(True) | (SmartVerdict.post_id.is_(None) & (Post.id <= (feed.cursor_post_id or 0))))
    else:
        stmt = stmt.join(SmartVerdict, verdict).where(shown.is_(False))
    return stmt.add_columns(SmartVerdict.score, SmartVerdict.reason, SmartVerdict.post_id, SmartVerdict.override,
                            SmartVerdict.override_reason, SmartVerdict.override_by)


def found_on_picture(post: Post, q: str) -> bool:
    """The search found the post by the text on its pictures only."""
    needle = q.casefold()
    return needle not in (post.text or "").casefold() and needle in (post.extracted_text_from_media or "").casefold()


@router.get("/posts")
async def list_posts(
    viewer: Viewer,
    db: DB,
    period: Period = "all",
    sort: SortKey = "new",
    channel_id: int | None = None,
    q: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
    # «Умная лента»: what the AI kept ("true"), or what it turned down ("rejected")
    smart: Literal["true", "rejected"] | None = None,
    # «Триггер-слова»: the posts of one of the person's triggers
    trigger: int | None = None,
    # «Избранное»: the posts the person starred
    favorites: bool = False,
) -> dict:
    if not viewer.is_admin and not viewer.plan:
        # no plan (or it ran out): the feed and the top stay empty until there is one
        return {"items": [], "has_more": False, "no_plan": True}
    # the feed and the top show what the project watches; its own posts are on the channel's page
    visible = visible_channels(viewer) if viewer.is_admin or channel_id else tracked_channel_ids(viewer.project_id)
    stmt = posts_query(period=period, sort=sort, channel_ids=[channel_id] if channel_id else None, q=q,
                       visible=visible)
    # «Умная лента»: what the AI kept, with its few words on why, and the posts it never read (those
    # from before it was turned on stay in the feed); a new post waits until it is read
    feed = await verdict_feed(db, viewer) if smart else None
    feed_id = feed.id if feed else None
    if feed is not None:
        stmt = smart_filter(stmt, feed, kept=smart == "true")
    found: dict[int, list[str]] = {}
    if trigger is not None:
        if viewer.is_admin:
            raise HTTPException(status_code=403, detail="Триггер-слова — для владельцев проектов и их сотрудников")
        if await db.scalar(select(Trigger.id).where(Trigger.id == trigger, of_person(viewer))) is None:
            raise HTTPException(status_code=404, detail="Триггер-слово не найдено")
        stmt = stmt.where(Post.id.in_(select(TriggerHit.post_id).where(TriggerHit.trigger_id == trigger)))
    if favorites:
        if viewer.is_admin:
            raise HTTPException(status_code=403, detail="Избранное — для владельцев проектов и их сотрудников")
        stmt = stmt.where(Post.id.in_(select(Favorite.post_id).where(my_favorites(viewer))))
    rows = (await db.execute(stmt.offset(offset).limit(limit + 1))).all()
    starred = await favorite_ids(db, viewer, [r[0].id for r in rows[:limit]])
    if trigger is not None and rows:
        found = dict((await db.execute(select(TriggerHit.post_id, TriggerHit.found).where(
            TriggerHit.trigger_id == trigger, TriggerHit.post_id.in_([r[0].id for r in rows[:limit]])))).all())
    items = []
    for row in rows[:limit]:
        item = post_out(row[0], row[1], for_user=not viewer.is_admin)
        if not viewer.is_admin:
            item["favorite"] = row[0].id in starred  # «Избранное»: the star on the card
        if row[0].id in found:
            item["trigger_found"] = found[row[0].id]  # highlighted in the card
        if q and q.strip() and found_on_picture(row[0], q.strip()):
            item["found_on_picture"] = True  # the text on the pictures is not shown: the card says where it is
        if feed_id is not None and row[4] is not None:
            item["smart"] = {"score": max(0, row[2]), "reason": row[3], "match": smart == "true",
                             # the person's own decision on it, and why
                             "mine": None if row[5] is None else ("show" if row[5] else "hide"),
                             "mine_reason": row[6], "mine_by": row[7]}
        items.append(item)
    return {"items": items, "has_more": len(rows) > limit, "smart": feed_id is not None}


@router.get("/posts/{post_id}")
async def get_post(post_id: int, viewer: Viewer, db: DB) -> dict:
    """One post, as the feed shows it: on top of its comments page."""
    row = (await db.execute(select(Post, Channel).join(Channel, Post.channel_id == Channel.id)
                            .where(Post.id == post_id))).first()
    if row is None or not await can_see(db, viewer, row[1].id) or (not viewer.is_admin and not viewer.plan):
        raise HTTPException(status_code=404, detail="Пост не найден")
    item = post_out(row[0], row[1], for_user=not viewer.is_admin)
    if not viewer.is_admin:
        item["favorite"] = post_id in await favorite_ids(db, viewer, [post_id])
    return item


def my_favorites(viewer):
    """The viewer's own starred posts in their current project."""
    return (Favorite.project_id == viewer.project_id) & (Favorite.person == person_key(viewer))


async def favorite_ids(db, viewer, post_ids: list[int]) -> set[int]:
    if viewer.is_admin or not post_ids:
        return set()
    return set((await db.execute(select(Favorite.post_id).where(my_favorites(viewer),
                                                                Favorite.post_id.in_(post_ids)))).scalars())


async def _starrable(db, viewer, post_id: int) -> None:
    if viewer.is_admin:
        raise HTTPException(status_code=403, detail="Избранное — для владельцев проектов и их сотрудников")
    channel_id = await db.scalar(select(Post.channel_id).where(Post.id == post_id))
    if channel_id is None or not await can_see(db, viewer, channel_id):
        raise HTTPException(status_code=404, detail="Пост не найден")


@router.put("/posts/{post_id}/favorite")
async def star(post_id: int, viewer: Viewer, db: DB) -> dict:
    """«В избранное»: the post is the person's to come back to — and it is kept past the retention period."""
    await _starrable(db, viewer, post_id)
    if not await favorite_ids(db, viewer, [post_id]):
        db.add(Favorite(project_id=viewer.project_id, person=person_key(viewer), member_id=viewer.member_id,
                        post_id=post_id))
        await db.commit()
    return {"favorite": True}


@router.delete("/posts/{post_id}/favorite")
async def unstar(post_id: int, viewer: Viewer, db: DB) -> dict:
    await _starrable(db, viewer, post_id)
    await db.execute(delete(Favorite).where(my_favorites(viewer), Favorite.post_id == post_id))
    await db.commit()
    return {"favorite": False}


@router.get("/media/{post_id}")
async def media(post_id: int, viewer: Viewer, db: DB, i: int = Query(default=0, ge=0, le=20)) -> FileResponse:
    path = preview_path(post_id, i)
    channel_id = await db.scalar(select(Post.channel_id).where(Post.id == post_id))
    if channel_id is None or not path.is_file() or not await can_see(db, viewer, channel_id):
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})


@router.get("/media/{post_id}/full")
async def full_photo(post_id: int, viewer: Viewer, db: DB, i: int = Query(default=0, ge=0, le=20)) -> Response:
    """The photo in full size: the worker reads it from Telegram into memory and it is passed on
    as is — nothing is kept on the server (the browser caches it for a day)."""
    channel_id = await db.scalar(select(Post.channel_id).where(Post.id == post_id))
    if channel_id is None or not await can_see(db, viewer, channel_id) or (not viewer.is_admin and not viewer.plan):
        raise HTTPException(status_code=404)
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.get(f"{settings.worker_url}/photo/{post_id}", params={"i": i},
                                 headers={"X-Internal-Key": settings.secret_key})
    except httpx.HTTPError as exc:
        log.warning("full photo %s/%s: worker unreachable: %s", post_id, i, exc)
        raise HTTPException(status_code=503, detail="Не удалось загрузить фото, попробуйте позже") from exc
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="У этого поста нет такого фото")
    if r.status_code != 200:
        raise HTTPException(status_code=503, detail="Не удалось загрузить фото, попробуйте позже")
    return Response(r.content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/media/{post_id}/video")
async def video(post_id: int, viewer: Viewer, db: DB, i: int = Query(default=0, ge=0, le=20),
                fresh: bool = False) -> dict:
    """A video of the post, to play on the site: the direct link to it on Telegram's servers, which Telegram's
    public page of the post gives (tg_web.py) — {url, kind, ratio, duration}; or why there is none, for people:
    {error, message}. `fresh`: the link given before stopped playing."""
    row = (await db.execute(select(Post, Channel).join(Channel, Post.channel_id == Channel.id)
                            .where(Post.id == post_id))).first()
    if row is None or not await can_see(db, viewer, row[1].id) or (not viewer.is_admin and not viewer.plan):
        raise HTTPException(status_code=404)
    post, ch = row
    pos = media_position(post, i)
    if pos is None or dict(media_slots(post)).get(i) != "video":
        raise HTTPException(status_code=404, detail="У этого поста нет такого видео")
    ids = post.album_ids or [post.msg_id]
    if pos >= len(ids):
        raise HTTPException(status_code=404, detail="У этого поста нет такого видео")
    if not ch.username:
        return tg_web.unavailable("private")  # a private channel has no public page
    return await tg_web.video(ch.username, ids[pos], fresh=fresh)


class SendIn(BaseModel):
    chat_id: int | None = None  # one of the project's groups («Группы проекта»); none = the person's private chat


GROUP_GONE = ("bot was kicked", "chat not found", "not a member", "need administrator rights",
              "not enough rights", "have no rights", "chat_write_forbidden", "chat_restricted")


@router.post("/posts/{post_id}/original")
async def send_original_post(post_id: int, viewer: Viewer, db: DB, body: SendIn | None = None) -> dict:
    """"Посмотреть оригинал поста": the bot sends the post to the user in Telegram, or to one of
    their own groups. A staff member gets it in their own Telegram, once they have linked it."""
    if viewer.is_admin:
        raise HTTPException(status_code=400, detail="Админ открывает пост в группе")
    row = (await db.execute(select(Post, Channel).join(Channel, Post.channel_id == Channel.id)
                            .where(Post.id == post_id))).first()
    if row is None or not await can_see(db, viewer, row[1].id):
        raise HTTPException(status_code=404)
    post = row[0]
    target = group_message(post.repost_url) if post.repost_status == "done" else None
    if target is None:
        raise HTTPException(status_code=409, detail="Этот пост пока нельзя отправить — попробуйте чуть позже")
    cfg = await settings_store.get_all(db)
    if viewer.role == "member":
        member = await db.get(Member, viewer.member_id)
        to_group = body is not None and body.chat_id is not None
        if member.tg_user_id is None and not to_group:
            raise HTTPException(status_code=409, detail="Привяжите Telegram в настройках, чтобы получать посты")
        # the same as a user's own chat, from here on
        user = User(id=viewer.user_id, tg_user_id=member.tg_user_id)
    else:
        user = await db.get(User, viewer.user_id)
    group = None
    if body is not None and body.chat_id is not None:
        group = await db.get(ProjectGroup, (viewer.project_id, body.chat_id))
        if group is None:
            raise HTTPException(status_code=404, detail="Этой группы нет в группах проекта")
    token, count = str(cfg["notify_bot_token"]), len(post.album_ids or [post.msg_id])
    try:
        try:
            await send_original(token, group.chat_id if group else user.tg_user_id, target[0], target[1],
                                count, post.repost_copy)
        except BotError as exc:
            new_id = exc.parameters.get("migrate_to_chat_id")
            if group is None or not new_id:
                raise
            # the group became a supergroup and got a new id: follow it
            await db.execute(update(ProjectGroup).where(ProjectGroup.chat_id == group.chat_id).values(chat_id=new_id))
            await db.commit()
            group = await db.get(ProjectGroup, (viewer.project_id, new_id))
            await send_original(token, new_id, target[0], target[1], count, post.repost_copy)
    except BotError as exc:
        text = str(exc).lower()
        if group is not None and any(x in text for x in GROUP_GONE):
            raise HTTPException(
                status_code=409,
                detail=f"Бот не может писать в группу «{group.title or group.username}»: проверьте, что "
                       f"@{cfg['notify_bot_username']} в ней состоит и может отправлять сообщения",
            ) from exc
        if group is None and "blocked" in text:
            raise HTTPException(status_code=409, detail=f"Разблокируйте бота @{cfg['notify_bot_username']} в Telegram") from exc
        log.warning("sending post %s for user %s failed: %s", post_id, user.id, exc)
        raise HTTPException(status_code=502, detail="Не удалось отправить пост, попробуйте позже") from exc
    return {"ok": True, "to": group.title if group else None}
