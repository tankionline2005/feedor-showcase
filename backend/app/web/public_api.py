"""External API, authenticated by a key (X-API-Key or Bearer): the data at /api/v1, read only, in a form of its own;
and everything else the site does, through the site's own methods (deps.require_viewer, api_routes).

The admin has one key (in the settings) and sees everything; every project has its own key
(projects.api_key) and sees only the channels it tracks, the way the web UI shows them. A project's
staff member has a key of their own (members.api_key) that sees the same and dies with the account.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.sql import Select
from starlette.middleware.base import BaseHTTPMiddleware

from .. import settings_store
from ..projects import new_api_key
from ..channel_stats import channel_stats as project_channel_stats
from ..models import Channel, Member, Post, Project, ProjectChannel, Topic, TopicUpdate, utcnow
from ..plans import plan_of
from ..scope import can_see, tracked_channel_ids, user_status, visible_channels
from ..services import preview_path
from .channels import STATS_WINDOW, channel_stats
from . import api_routes
from .deps import API_RATE_LIMIT, DB, KeyRefused, Principal, Viewer, given_key, key_principal
from .deps import _calls  # noqa: F401  (the keys' rate counts, shared with the site's methods; tests clear them)
from .posts import Period, SortKey, iso, media_slots, post_url, posts_query, rated_threshold, smart_filter
from .smart_api import verdict_feed
from .topics_api import ORDER as TOPICS_ORDER
from .topics_api import _posts_of as topic_posts
from ..fr_plus import fr_plus

PREFIX = "/api/v1"
RATE_LIMIT = API_RATE_LIMIT  # requests per minute
CHANNEL_STATUSES = ("active", "pending", "paused", "error", "restricted")
USER_CHANNEL_STATUSES = ("active", "pending", "error")  # what a user is told, see scope.user_status

router = APIRouter(prefix=PREFIX, tags=["external api"])
manage = APIRouter(prefix="/api/api-key", tags=["external api"])


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers


def _error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    """Error format and CORS for the external API (the web UI keeps its own conventions)."""

    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError):
        return _error(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith(PREFIX):
            code = "not_found" if exc.status_code == 404 else "http_error"
            message = "Нет такого метода" if exc.status_code == 404 else str(exc.detail)
            return _error(exc.status_code, code, message)
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        if request.url.path.startswith(PREFIX):
            first = exc.errors()[0] if exc.errors() else {}
            where = ".".join(str(p) for p in first.get("loc", [])[1:])
            return _error(422, "invalid_params", f"{where}: {first.get('msg', 'неверный параметр')}")
        return await request_validation_exception_handler(request, exc)

    class Cors(BaseHTTPMiddleware):
        # any page may call the API with a key; never with the site's cookie: "*" lets no credentials through
        async def dispatch(self, request, call_next):
            if not request.url.path.startswith(("/api/", "/mcp", "/oauth/", "/.well-known/")):
                return await call_next(request)
            if request.method == "OPTIONS":
                response = Response(status_code=204)
            else:
                response = await call_next(request)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = ("X-API-Key, Authorization, Content-Type, "
                                                                "Mcp-Protocol-Version, Mcp-Session-Id")
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Expose-Headers"] = "Retry-After, WWW-Authenticate, Mcp-Session-Id"
            return response

    app.add_middleware(Cors)


# --- auth and rate limit ------------------------------------------------------


async def ensure_key(db) -> str:
    key = str((await settings_store.get_all(db))["api_key"])
    if not key:
        key = new_api_key()
        await settings_store.update(db, {"api_key": key})
    return key


async def require_key(request: Request) -> Principal:
    """The key's holder (deps.key_principal), refused in the read API's own error format."""
    try:
        return await key_principal(given_key(request))
    except KeyRefused as exc:
        raise ApiError(exc.status, exc.code, exc.message, exc.headers) from exc


Key = Annotated[Principal, Depends(require_key)]


# --- serializers --------------------------------------------------------------


def channel_ref(ch: Channel, own_id: int | None = None, *, for_user: bool = False) -> dict:
    ref = {
        "id": ch.id,
        "title": ch.title or ch.link,
        "username": ch.username,
        "url": f"https://t.me/{ch.username}" if ch.username else None,
    }
    if for_user:
        ref["is_my_channel"] = ch.id == own_id  # the project's own channel (see /project)
    return ref


def post_json(post: Post, ch: Channel, *, full: bool = False, for_user: bool = False, own_id: int | None = None) -> dict:
    data = {
        "id": post.id,
        "channel": channel_ref(ch, own_id, for_user=for_user),
        "msg_id": post.msg_id,
        "url": post_url(post, ch),
        "repost_url": post.repost_url,
        "deleted_at": iso(post.deleted_at),
        "posted_at": iso(post.posted_at),
        "text": post.text,
        # text read off the post's pictures (OCR, see ocr.py): "" when they have none; None for posts
        # collected before OCR came, and for a fresh post still waiting in the queue (seconds)
        "extracted_text_from_media": post.extracted_text_from_media,
        "media_type": post.media_type,
        "media_count": post.media_count,
        "preview_url": f"{PREFIX}/posts/{post.id}/preview" if post.has_preview else None,
        # a picture of every album item that has one
        "previews": [
            {"url": f"{PREFIX}/posts/{post.id}/preview" + (f"?i={slot}" if slot else ""), "kind": kind}
            for slot, kind in media_slots(post)
        ],
        "metrics": {
            "views": post.views,
            "reactions": post.reactions,
            "comments": post.comments,
            "forwards": post.forwards,
            "er": post.er,
            "fr": post.fr,
            "fr_plus": fr_plus(post.fr, ch.fr_base),
        },
        "reactions": [{"emoji": r["e"], "count": r["c"]} for r in post.reactions_top or []],
        "rated": post.views >= rated_threshold(ch.subscribers),
        "metrics_updated_at": iso(post.metrics_updated_at),
    }
    if full:
        data["html"] = post.html
        data["album_ids"] = post.album_ids
        data["repost_status"] = post.repost_status
    if for_user:
        # our aggregator group is the admin's business
        del data["repost_url"]
        data.pop("repost_status", None)
    return data


def channel_json(ch: Channel, stats: dict | None, *, for_user: bool = False, own_id: int | None = None) -> dict:
    stats = stats or {}
    return {
        **channel_ref(ch, own_id, for_user=for_user),
        "subscribers": ch.subscribers,
        "status": user_status(ch)[0] if for_user else ch.status,
        "posts_per_day": round(stats.get("posts", 0) / STATS_WINDOW.days, 1),
        "avg_views": int(stats.get("views") or 0),
        "avg_er": round(stats.get("er") or 0, 2),
        "avg_fr": round(stats.get("fr") or 0, 2),
        "last_post_at": iso(ch.last_post_at),
        "added_at": iso(ch.created_at),
    }


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


async def own_channel_id(db, owner: Principal) -> int | None:
    """The id of the project's own channel among the tracked ones, once it is collected."""
    if owner.project_id is None:
        return None
    return await db.scalar(select(ProjectChannel.channel_id).where(
        ProjectChannel.project_id == owner.project_id, ProjectChannel.own
    ))


async def my_channel_json(db, owner: Principal) -> dict | None:
    """The channel the project is about: everything the monitoring collects is there to grow it.
    None until the owner chooses it in the settings."""
    ch_id = await own_channel_id(db, owner)
    ch = await db.get(Channel, ch_id) if ch_id else None
    if ch is None:
        return None
    project = await db.get(Project, owner.project_id)
    data = {
        "title": ch.title or project.channel_title or ch.link,
        "username": ch.username,
        "url": f"https://t.me/{ch.username}" if ch.username else None,
        "telegram_id": int(f"-100{ch.tg_id}") if ch.tg_id else project.channel_chat_id,
        # its posts are collected like any channel's: this id goes into /posts?channel= (or just channel=my)
        "channel_id": ch.id,
        "collected": ch.status == "active",
        "subscribers": ch.subscribers,
    }
    if ch.status == "active":
        stats = (await channel_stats(db, [ch.id])).get(ch.id) or {}
        data["stats_7d"] = {
            "posts_per_day": round(stats.get("posts", 0) / STATS_WINDOW.days, 1),
            "avg_views": int(stats.get("views") or 0),
            "avg_er": round(stats.get("er") or 0, 2),
            "avg_fr": round(stats.get("fr") or 0, 2),
        }
    return data


async def _resolve_channels(db, owner: Principal, values: list[str]) -> list[int]:
    ids: list[int] = []
    for raw in (v.strip() for item in values for v in item.split(",")):
        if not raw:
            continue
        if raw.lower() == "my":
            own = await own_channel_id(db, owner)
            if own is None:
                raise ApiError(404, "no_my_channel", "Канал проекта не выбран — см. /project")
            ids.append(own)
            continue
        if raw.isdigit():
            found = await db.scalar(select(Channel.id).where(Channel.id == int(raw), Channel.status != "deleting"))
        else:
            name = raw.removeprefix("@").removeprefix("https://t.me/").lower()
            found = await db.scalar(
                select(Channel.id).where(func.lower(Channel.username) == name, Channel.status != "deleting")
            )
        if found is None or not await can_see(db, owner, found):
            raise ApiError(404, "unknown_channel", f"Канал не найден: {raw}")
        ids.append(found)
    return ids


# --- methods ------------------------------------------------------------------


@router.get("/posts")
async def posts(
    owner: Key,
    db: DB,
    period: Period = "all",
    since: datetime | None = None,
    until: datetime | None = None,
    sort: SortKey = "new",
    channel: list[str] = Query(default=[]),
    q: str | None = Query(default=None, max_length=200),
    smart: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    channel_ids = await _resolve_channels(db, owner, channel)
    stmt = posts_query(period=period, sort=sort, channel_ids=channel_ids or None, q=q,
                       since=_utc(since), until=_utc(until), visible=visible_channels(owner))
    feed = await _smart_feed(db, owner) if smart else None
    if feed is not None:
        stmt = _smart(stmt, feed, owner)
    rows = (await db.execute(stmt.offset(offset).limit(limit + 1))).all()
    has_more = len(rows) > limit
    own = await own_channel_id(db, owner)
    return {
        "items": [_with_smart(post_json(r[0], r[1], for_user=not owner.is_admin, own_id=own), r, feed)
                  for r in rows[:limit]],
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


@router.get("/posts/new")
async def new_posts(
    owner: Key,
    db: DB,
    after_id: int = Query(default=0, ge=0),
    smart: bool = False,
    limit: int = Query(default=100, ge=1, le=100),
) -> dict:
    stmt = (
        select(Post, Channel)
        .join(Channel, Post.channel_id == Channel.id)
        .where(Post.id > after_id, Channel.status != "deleting")
        .order_by(Post.id)
        .limit(limit + 1)
    )
    if (visible := visible_channels(owner)) is not None:
        stmt = stmt.where(Post.channel_id.in_(visible))
    feed = await _smart_feed(db, owner) if smart else None
    if feed is not None:
        # only what the AI has already read: a newer post comes once it is read, so nothing is skipped
        stmt = _smart(stmt, feed, owner).where(Post.id <= (feed.cursor_post_id or 0))
    rows = (await db.execute(stmt)).all()
    has_more = len(rows) > limit
    own = await own_channel_id(db, owner)
    items = [_with_smart(post_json(r[0], r[1], for_user=not owner.is_admin, own_id=own), r, feed)
             for r in rows[:limit]]
    last_id = items[-1]["id"] if items else after_id
    if feed is not None and not has_more:
        # everything the AI has read is given: the turned-down posts up to there need no asking again
        last_id = max(last_id, feed.cursor_post_id or 0)
    return {"items": items, "last_id": last_id, "has_more": has_more}


async def _smart_feed(db, owner: Principal):
    """The smart feed the key's person sees on the site: the project's, or a staff member's own."""
    if owner.is_admin:
        raise ApiError(422, "invalid_params", "smart: умная лента есть только у проектов")
    feed = await verdict_feed(db, owner)
    if feed is None:
        raise ApiError(409, "smart_feed_off", "Умная лента выключена или ИИ проекта сейчас недоступен — включите её на "
                                              "сайте")
    return feed


def _smart(stmt: Select, feed, owner: Principal) -> Select:
    # the feed reads the channels the project watches, not its own
    return smart_filter(stmt.where(Post.channel_id.in_(tracked_channel_ids(owner.project_id))), feed)


def _with_smart(data: dict, row, feed) -> dict:
    """A post of the smart feed: who let it in and why."""
    if feed is not None:
        score, reason, judged, override, override_reason = row[2], row[3], row[4], row[5], row[6]
        if override is not None:
            data["smart"] = {"decided_by": "person", "reason": override_reason, "score": None}
        elif judged is not None and score >= 0:
            data["smart"] = {"decided_by": "ai", "reason": reason, "score": score}
        else:  # from before the feed was on, or a kind of post it cannot read: simply shown
            data["smart"] = {"decided_by": None, "reason": None, "score": None}
    return data


async def _post(db, owner: Principal, post_id: int) -> tuple[Post, Channel]:
    row = (
        await db.execute(
            select(Post, Channel)
            .join(Channel, Post.channel_id == Channel.id)
            .where(Post.id == post_id, Channel.status != "deleting")
        )
    ).first()
    if row is None or not await can_see(db, owner, row[1].id):
        raise ApiError(404, "not_found", "Пост не найден (возможно, удалён по сроку хранения)")
    return row


@router.get("/posts/{post_id}")
async def post(post_id: int, owner: Key, db: DB) -> dict:
    p, c = await _post(db, owner, post_id)
    return post_json(p, c, full=True, for_user=not owner.is_admin, own_id=await own_channel_id(db, owner))


@router.get("/posts/{post_id}/preview")
async def post_preview(post_id: int, owner: Key, db: DB, i: int = Query(default=0, ge=0, le=20)) -> FileResponse:
    await _post(db, owner, post_id)
    path = preview_path(post_id, i)
    if not path.is_file():
        raise ApiError(404, "no_preview", "У поста нет превью")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})


@router.get("/channels")
async def channels(
    owner: Key,
    db: DB,
    status: list[str] = Query(default=[]),
) -> dict:
    wanted = [s for item in status for s in item.split(",") if s]
    allowed = CHANNEL_STATUSES if owner.is_admin else USER_CHANNEL_STATUSES
    if bad := [s for s in wanted if s not in allowed]:
        raise ApiError(422, "invalid_params", f"status: неизвестный статус {bad[0]} (есть {', '.join(allowed)})")
    stmt = select(Channel).where(Channel.status != "deleting").order_by(Channel.created_at)
    if (visible := visible_channels(owner)) is not None:
        stmt = stmt.where(Channel.id.in_(visible))
    stats = await channel_stats(db)
    own = await own_channel_id(db, owner)
    items = [channel_json(c, stats.get(c.id), for_user=not owner.is_admin, own_id=own)
             for c in (await db.execute(stmt)).scalars().all()]
    data = {"items": [c for c in items if not wanted or c["status"] in wanted]}
    if not owner.is_admin:
        data["my_channel"] = await my_channel_json(db, owner)
    return data


@router.get("/channels/{channel_id}")
async def channel(channel_id: int, owner: Key, db: DB) -> dict:
    ch = await db.get(Channel, channel_id)
    if ch is None or ch.status == "deleting" or not await can_see(db, owner, ch.id):
        raise ApiError(404, "not_found", "Канал не найден")
    return channel_json(ch, (await channel_stats(db, [ch.id])).get(ch.id), for_user=not owner.is_admin,
                        own_id=await own_channel_id(db, owner))


@router.get("/stats")
async def stats(owner: Key, db: DB) -> dict:
    since = utcnow() - timedelta(hours=24)
    visible = visible_channels(owner)
    for_user = not owner.is_admin

    def mine(stmt, column=Post.channel_id):
        return stmt if visible is None else stmt.where(column.in_(visible))

    by_status: dict[str, int] = {}
    for ch in (await db.execute(mine(select(Channel).where(Channel.status != "deleting"), Channel.id))).scalars():
        status = user_status(ch)[0] if for_user else ch.status
        by_status[status] = by_status.get(status, 0) + 1
    top = (await db.execute(posts_query(sort="views", since=since, visible=visible).limit(5))).all()
    own = await own_channel_id(db, owner)
    data = {
        "channels": {
            **{s: by_status.get(s, 0) for s in (USER_CHANNEL_STATUSES if for_user else CHANNEL_STATUSES)},
            "total": sum(by_status.values()),
        },
        "posts_total": await db.scalar(mine(select(func.count()).select_from(Post))) or 0,
        "posts_24h": await db.scalar(mine(select(func.count()).where(Post.posted_at >= since))) or 0,
        "top_24h": [post_json(p, c, for_user=for_user, own_id=own) for p, c in top],
        "generated_at": iso(utcnow()),
    }
    if for_user:
        data["my_channel"] = await my_channel_json(db, owner)
    if not for_user:
        # reposting to our aggregator group is the admin's business
        data["reposted_24h"] = await db.scalar(
            select(func.count()).where(Post.reposted_at >= since, Post.repost_status == "done")
        ) or 0
    return data


@router.get("/project")
async def project(owner: Key, db: DB) -> dict:
    """The project the key belongs to: its channel (my_channel), plan and how many channels it tracks."""
    if owner.project_id is None:
        raise ApiError(404, "not_found", "У ключа администратора нет проекта")
    p = await db.get(Project, owner.project_id)
    own = await own_channel_id(db, owner)
    tracked = await db.scalar(select(func.count()).where(
        ProjectChannel.project_id == p.id, ProjectChannel.channel_id != (own or 0)
    )) or 0
    plan = plan_of(owner.plan)
    return {
        "title": p.channel_title or "Проект",
        "my_channel": await my_channel_json(db, owner),
        "plan": {"id": plan.id, "title": plan.title, "expires_at": iso(p.plan_expires_at)} if plan else None,
        # channels the project watches besides its own
        "channels": {"tracked": tracked, "limit": plan.channels if plan else 0},
        "generated_at": iso(utcnow()),
    }


def _project_only(owner: Principal) -> None:
    if owner.project_id is None:
        raise ApiError(404, "not_found", "У ключа администратора нет проекта")


@router.get("/project/stats")
async def project_stats(
    owner: Key,
    db: DB,
    period: str = Query(default="30d", pattern="^(7d|30d|90d)$"),
    tz: int = Query(default=0, ge=-900, le=900),
) -> dict:
    """«Статистика» of the project's own channel, as on the site: subscribers over time, every day's posts and
    their numbers, the hours that work best, and the same for the channels it tracks to compare with."""
    _project_only(owner)
    own = await own_channel_id(db, owner)
    if own is None:
        raise ApiError(404, "no_my_channel", "Канал проекта не выбран — выберите его в настройках проекта")
    channel = await db.get(Channel, own)
    others = list((await db.execute(select(ProjectChannel.channel_id).where(
        ProjectChannel.project_id == owner.project_id, ~ProjectChannel.own))).scalars())
    data = await project_channel_stats(db, own, others, period, tz, channel.subscribers)
    return {"my_channel": await my_channel_json(db, owner), **data, "generated_at": iso(utcnow())}


def topic_json(topic: Topic, items: list[tuple[Post, Channel]], own: int | None) -> dict:
    channels: list[dict] = []
    for _p, ch in items:
        if not any(c["id"] == ch.id for c in channels):
            channels.append(channel_ref(ch, own, for_user=True))
    return {
        "id": topic.id,
        "title": topic.title,
        "summary": topic.summary,
        "importance": topic.importance,
        "covered": topic.covered,
        "followup": topic.covered and topic.followup_at is not None,
        "created_at": iso(topic.created_at),
        "updated_at": iso(topic.updated_at),
        "last_post_at": iso(topic.last_post_at),
        "sources": len(channels),
        "channels": channels,
        "posts_count": len(items),
        "post_ids": [p.id for p, _ in items],
        "views": sum(p.views for p, _ in items),
    }


@router.get("/topics")
async def topics(
    owner: Key,
    db: DB,
    updated_after: datetime | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """«Темы»: the news the AI made of the channels' posts, as the site lists them (hidden ones are not given).
    With updated_after: the topics made or updated since, the oldest first, to keep in step."""
    _project_only(owner)
    query = select(Topic).where(Topic.project_id == owner.project_id, Topic.hidden_at.is_(None))
    if updated_after is not None:
        query = query.where(Topic.updated_at > _utc(updated_after)).order_by(Topic.updated_at, Topic.id)
    else:
        query = query.order_by(*TOPICS_ORDER)
    rows = (await db.execute(query.offset(offset).limit(limit + 1))).scalars().all()
    page = rows[:limit]
    posts = await topic_posts(db, [t.id for t in page])
    own = await own_channel_id(db, owner)
    has_more = len(rows) > limit
    return {
        "items": [topic_json(t, posts.get(t.id, []), own) for t in page],
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


@router.get("/topics/{topic_id}")
async def topic(topic_id: int, owner: Key, db: DB) -> dict:
    """A topic whole: its timeline, and its posts in full, the latest first."""
    _project_only(owner)
    t = await db.get(Topic, topic_id)
    if t is None or t.project_id != owner.project_id or t.hidden_at is not None:
        raise ApiError(404, "not_found", "Тема не найдена (возможно, скрыта или удалена по сроку хранения)")
    items = (await topic_posts(db, [t.id])).get(t.id, [])
    own = await own_channel_id(db, owner)
    data = topic_json(t, items, own)
    data["timeline"] = [{"at": iso(u.at), "text": u.text} for u in (await db.execute(
        select(TopicUpdate).where(TopicUpdate.topic_id == t.id).order_by(TopicUpdate.at, TopicUpdate.id)
    )).scalars()]
    data["posts"] = [post_json(p, ch, for_user=True, own_id=own) for p, ch in reversed(items)]
    return data


# --- the key and the reference, for Settings → API (and the key itself) --------------


@manage.get("")
async def get_key(viewer: Viewer, db: DB) -> dict:
    if viewer.is_admin:
        return {"key": await ensure_key(db), "rate_limit": RATE_LIMIT}
    # a staff member's own key, or the project's
    holder = await db.get(Member, viewer.member_id) if viewer.member_id else await db.get(Project, viewer.project_id)
    if not holder.api_key:
        holder.api_key = new_api_key()
        await db.commit()
    return {"key": holder.api_key, "rate_limit": RATE_LIMIT}


@manage.post("/reset")
async def reset_key(viewer: Viewer, db: DB) -> dict:
    key = new_api_key()
    if viewer.is_admin:
        await settings_store.update(db, {"api_key": key})
    else:
        holder = await db.get(Member, viewer.member_id) if viewer.member_id else await db.get(Project, viewer.project_id)
        holder.api_key = key
        await db.commit()
    return {"key": key, "rate_limit": RATE_LIMIT}


def _kind(schema: dict, components: dict) -> tuple[str, list | None, bool]:
    """A parameter's type as the reference shows it: (type, its options, may it be null)."""
    nullable = False
    if "anyOf" in schema:
        kinds = [x for x in schema["anyOf"] if x.get("type") != "null"]
        nullable = len(kinds) < len(schema["anyOf"])
        if len(kinds) != 1:
            return " | ".join(_kind(x, components)[0] for x in kinds), None, nullable
        schema = kinds[0]
    if "$ref" in schema:
        schema = components.get(schema["$ref"].rsplit("/", 1)[-1], {})
    if "enum" in schema:
        return "string", schema["enum"], nullable
    kind = schema.get("type", "object")
    if kind == "array":
        return f"array of {_kind(schema.get('items', {}), components)[0]}", None, nullable
    if kind == "string" and schema.get("format") == "date-time":
        return "string (ISO 8601)", None, nullable
    if kind == "string" and (schema.get("format") == "binary" or "contentMediaType" in schema):
        return "file", None, nullable
    return kind, None, nullable


def _field(name: str, schema: dict, required: bool, components: dict, where: str | None = None) -> dict:
    kind, options, nullable = _kind(schema, components)
    out = {"name": name, "type": kind, "required": required, "nullable": nullable, "options": options}
    if "default" in schema and schema["default"] is not None:
        out["default"] = schema["default"]
    if where:
        out["in"] = where
    return out


def _body(op: dict, components: dict) -> tuple[list[dict] | None, str | None]:
    """A method's body: its fields and how it goes (json or form-data); a body of any keys has no fields listed."""
    for content_type, content in op.get("requestBody", {}).get("content", {}).items():
        schema = content.get("schema", {})
        if "anyOf" in schema:  # an optional body
            schema = next((x for x in schema["anyOf"] if x.get("type") != "null"), {})
        if "$ref" in schema:
            schema = components.get(schema["$ref"].rsplit("/", 1)[-1], {})
        required = set(schema.get("required", []))
        fields = [_field(name, s, name in required, components) for name, s in schema.get("properties", {}).items()]
        return fields, "form-data" if "form-data" in content_type else "json"
    return None, None


@manage.get("/reference")
async def reference(request: Request, viewer: Viewer) -> dict:
    """«Управление» in Settings → API: the site's methods this person's key may call (api_routes.BY_KEY) — only those
    their rights allow — each with what it does, and its parameters and body as the method itself takes them: the
    reference cannot fall behind the methods."""
    schema = request.app.openapi()
    components = schema.get("components", {}).get("schemas", {})
    order = {name: i for i, name in enumerate(api_routes.BY_KEY)}
    found: list[tuple[int, str, dict]] = []
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            name = f"{method.upper()} {path}"
            entry = api_routes.BY_KEY.get(name)
            if entry is None or not api_routes.may(entry.who, viewer):
                continue
            body, body_kind = _body(op, components)
            found.append((order[name], entry.group, {
                "method": method.upper(), "path": path, "text": entry.text,
                "params": [_field(p["name"], p.get("schema", {}), p.get("required", False), components, p["in"])
                           for p in op.get("parameters", [])],
                "body": body, "body_kind": body_kind,
            }))
    groups: dict[str, list[dict]] = {}
    for _, group, item in sorted(found, key=lambda x: x[0]):
        groups.setdefault(group, []).append(item)
    return {"rate_limit": RATE_LIMIT, "groups": [{"title": g, "methods": items} for g, items in groups.items()]}
