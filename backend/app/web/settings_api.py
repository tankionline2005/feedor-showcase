from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from .. import perf, settings_store, storage
from ..models import Account, Channel, Post, Proxy, utcnow
from .deps import DB, Admin
from .posts import iso, post_url

router = APIRouter(prefix="/api", tags=["settings"])


async def _public(db) -> dict:
    values = await settings_store.get_all(db)
    return {k: v for k, v in values.items() if k not in settings_store.INTERNAL}


@router.get("/settings")
async def get_settings(_: Admin, db: DB) -> dict:
    return await _public(db)


@router.put("/settings")
async def put_settings(body: dict, _: Admin, db: DB) -> dict:
    if internal := set(body) & settings_store.INTERNAL:
        raise HTTPException(status_code=422, detail=f"Нельзя менять: {', '.join(sorted(internal))}")
    try:
        await settings_store.update(db, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _public(db)


@router.get("/status")
async def status(_: Admin, db: DB) -> dict:
    """Counters for the settings screen."""
    channels = dict((await db.execute(select(Channel.status, func.count()).group_by(Channel.status))).all())
    accounts = dict((await db.execute(select(Account.status, func.count()).group_by(Account.status))).all())
    reposts = dict((await db.execute(select(Post.repost_status, func.count()).group_by(Post.repost_status))).all())
    proxies = dict(
        (await db.execute(select(Proxy.status, func.count()).where(Proxy.enabled).group_by(Proxy.status))).all()
    )
    proxies["disabled"] = await db.scalar(select(func.count()).where(Proxy.enabled.is_(False))) or 0
    last_error = select(Post.repost_error).where(Post.repost_status == "failed", Post.repost_error.is_not(None))
    dismissed = (await settings_store.get_all(db))["repost_error_dismissed_at"]
    if dismissed:
        # failures recorded before the timestamp column existed have no time and count as old
        last_error = last_error.where(Post.reposted_at > datetime.fromisoformat(str(dismissed)))
    last_error = await db.scalar(
        last_error.order_by(Post.reposted_at.desc().nulls_last(), Post.posted_at.desc()).limit(1)
    )
    return {
        "channels": channels,
        "accounts": accounts,
        "reposts": reposts,
        "proxies": proxies,
        "posts": sum(reposts.values()),
        "last_repost_error": last_error,
    }


@router.get("/storage")
async def storage_usage(_: Admin, db: DB) -> dict:
    """Disk usage of the server for Settings → Storage."""
    total, used, free = storage.disk()
    return {
        "total": total,
        "used": used,
        "free": free,
        "database": await storage.database_bytes(db),
        "media": storage.media_bytes(),
        "level": storage.level(free, total),
    }


@router.get("/perf")
async def perf_history(_: Admin, db: DB, period: str = Query("1h", pattern="^(1h|24h|7d)$")) -> dict:
    """Settings → System Performance: CPU, RAM, disk and network of the server over the period."""
    return await perf.history(db, period)


@router.get("/reposts/failed")
async def failed_reposts(
    _: Admin,
    db: DB,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    """Posts whose repost failed, newest failure first (for the Errors screen)."""
    base = (
        select(Post, Channel)
        .join(Channel, Post.channel_id == Channel.id)
        .where(Post.repost_status == "failed", Channel.status != "deleting")
    )
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.execute(
            base.order_by(Post.reposted_at.desc().nulls_last(), Post.posted_at.desc(), Post.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    items = [
        {
            "post_id": p.id,
            "channel": {"id": c.id, "title": c.title or c.link, "username": c.username},
            "url": post_url(p, c),
            "posted_at": iso(p.posted_at),
            "failed_at": iso(p.reposted_at),
            "error": p.repost_error or "Неизвестная ошибка",
            "text": (p.text or "")[:200],
            "media_type": p.media_type,
        }
        for p, c in rows[:limit]
    ]
    return {"items": items, "total": total, "has_more": len(rows) > limit}


@router.post("/status/dismiss-repost-error")
async def dismiss_repost_error(_: Admin, db: DB) -> dict:
    """Hides the current repost error; a newer failure shows up again."""
    await settings_store.update(db, {"repost_error_dismissed_at": utcnow().isoformat()})
    return {"ok": True}
