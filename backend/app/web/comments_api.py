"""A post's comments, for its comments page: the worker reads them from Telegram when someone opens them
(worker/comments.py) — nothing is stored. Pictures, stickers and voice messages come from the worker too, when the
page shows them; videos and GIFs play from Telegram's public page of the comment (tg_web.py)."""

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select

from .. import tg_web
from ..config import settings
from ..models import Post
from ..scope import can_see
from .deps import DB, Viewer

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["comments"])

# why the worker could not read them → for people
UNAVAILABLE = {
    "none": "У этого поста нет комментариев.",
    "private": "Комментарии этого поста закрыты: их можно прочитать только в Telegram.",
    "gone": "Пост удалён или больше недоступен.",
    "busy": "Комментарии сейчас недоступны. Попробуйте чуть позже.",
}


async def _check(db, viewer, post_id: int) -> bool:
    """The post is the viewer's to see; True when it is still in its channel (one deleted there has no comments)."""
    row = (await db.execute(select(Post.channel_id, Post.deleted_at).where(Post.id == post_id))).first()
    if row is None or not await can_see(db, viewer, row[0]) or (not viewer.is_admin and not viewer.plan):
        raise HTTPException(status_code=404, detail="Пост не найден")
    return row[1] is None


async def _worker(path: str, params: dict | None = None, timeout: float = 45) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.get(f"{settings.worker_url}{path}", params=params,
                                    headers={"X-Internal-Key": settings.secret_key})
    except httpx.HTTPError as exc:
        log.warning("comments %s: worker unreachable: %s", path, exc)
        raise HTTPException(status_code=503, detail=UNAVAILABLE["busy"]) from exc


def _unavailable(r: httpx.Response) -> str | None:
    if r.status_code == 409:
        return r.json().get("unavailable", "busy")
    if r.status_code == 404:
        return "gone"
    if r.status_code != 200:
        return "busy"
    return None


@router.get("/posts/{post_id}/comments")
async def comments(post_id: int, viewer: Viewer, db: DB, after: int = Query(default=0, ge=0)) -> dict:
    """{total, items, has_more, group} — the comments after the `after`-th, oldest first; or {unavailable,
    message} when Telegram does not give them."""
    if not await _check(db, viewer, post_id):
        return {"unavailable": "gone", "message": UNAVAILABLE["gone"]}
    r = await _worker(f"/comments/{post_id}", {"after": after})
    if (reason := _unavailable(r)) is not None:
        return {"unavailable": reason, "message": UNAVAILABLE.get(reason, UNAVAILABLE["busy"])}
    return r.json()


@router.get("/posts/{post_id}/comments/{comment_id}/media")
async def comment_media(post_id: int, comment_id: int, viewer: Viewer, db: DB,
                        what: Literal["thumb", "full", "file"] = "thumb") -> Response:
    """A comment's photo or video preview (`thumb`), photo in full (`full`), sticker or voice message (`file`) —
    read from Telegram into memory and passed on; the browser keeps it for a day."""
    await _check(db, viewer, post_id)
    r = await _worker(f"/comments/{post_id}/{comment_id}/media", {"what": what}, timeout=90)
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Не удалось найти это в комментарии")
    if r.status_code != 200:
        raise HTTPException(status_code=503, detail="Не удалось загрузить, попробуйте позже")
    return Response(r.content, media_type=r.headers.get("content-type", "application/octet-stream"),
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/posts/{post_id}/comments/{comment_id}/video")
async def comment_video(post_id: int, comment_id: int, viewer: Viewer, db: DB, fresh: bool = False) -> dict:
    """A comment's video or GIF, as a post's (posts.video): the link Telegram's public page of the comment gives;
    a closed discussion group has no such page."""
    await _check(db, viewer, post_id)
    r = await _worker(f"/comments/{post_id}/thread")
    if (reason := _unavailable(r)) is not None:
        return {"error": "missing", "message": UNAVAILABLE.get(reason, UNAVAILABLE["busy"])}
    group = r.json().get("group")
    if not group:
        return tg_web.unavailable("private_group")
    return await tg_web.video(group, comment_id, fresh=fresh)
