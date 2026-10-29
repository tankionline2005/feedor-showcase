"""The worker's own small HTTP service for the web app, on the internal network only.

The full-size photo of a post is not stored anywhere: when someone opens it, the web app asks the
worker, which reads it through an account from our aggregator group (or, when the group has no
copy of it, from the channel itself) into memory and hands the bytes over. A post's comments come the
same way (comments.py). Only the worker has Telegram connections — a second connection of the same
session would get it logged out.
"""

import asyncio
import hmac
import logging

import uvicorn
from sqlalchemy import select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route
from telethon.tl import types

from .. import settings_store
from ..config import settings
from ..db import SessionLocal
from ..models import Channel, Post
from ..originals import group_message
from .comments import Comments, CommentsUnavailable
from .pool import AccountPool, AccountUnavailable
from .reposter import Reposter

log = logging.getLogger(__name__)

PORT = 8001
MAX_BYTES = 15 * 1024 * 1024  # a Telegram photo is far smaller; anything bigger is not a photo
_downloads = asyncio.Semaphore(3)


def _allowed(request: Request) -> bool:
    return hmac.compare_digest(request.headers.get("x-internal-key", ""), settings.secret_key)


def slot_position(post: Post, slot: int) -> tuple[str, int] | None:
    """(kind, position in the album) of the post's `slot`-th picture."""
    if post.previews is not None:
        if 0 <= slot < len(post.previews) and post.previews[slot]:
            return post.previews[slot]["k"], post.previews[slot]["p"]
        return None
    if slot == 0 and post.has_preview:  # saved before per-item pictures: the first item
        return ("photo" if post.media_type in ("photo", "webpage") else "other"), 0
    return None


def _photo_of(msg) -> object | None:
    if not isinstance(msg, types.Message):
        return None
    if isinstance(msg.photo, types.Photo):
        return msg.photo
    wp = msg.web_preview
    return wp.photo if wp is not None and isinstance(getattr(wp, "photo", None), types.Photo) else None


class Internal:
    def __init__(self, pool: AccountPool, reposter: Reposter) -> None:
        self.pool = pool
        self.reposter = reposter
        self.comments = Comments(pool)

    async def photo(self, request: Request) -> Response:
        if not _allowed(request):
            return PlainTextResponse("forbidden", status_code=403)
        post_id, slot = int(request.path_params["post_id"]), int(request.query_params.get("i", 0))
        async with SessionLocal() as s:
            row = (await s.execute(
                select(Post, Channel).join(Channel, Post.channel_id == Channel.id).where(Post.id == post_id)
            )).first()
            cfg = await settings_store.get_all(s)
        found = slot_position(row[0], slot) if row else None
        if found is None or found[0] != "photo":
            return PlainTextResponse("no such photo", status_code=404)
        post, ch = row
        async with _downloads:
            data = await self._from_group(post, found[1], str(cfg["aggregator_channel"]))
            if data is None:
                data = await self._from_channel(post, ch, found[1])
        if not data:
            return PlainTextResponse("cannot get the photo now", status_code=503)
        if len(data) > MAX_BYTES:
            return PlainTextResponse("too big for a photo", status_code=413)
        return Response(data, media_type="image/jpeg")

    async def _from_group(self, post: Post, pos: int, ref: str) -> bytes | None:
        target = group_message(post.repost_url) if post.repost_status == "done" else None
        if target is None or post.repost_copy or not ref:
            return None  # not in the group, or only our text copy is
        message_id = target[1] + pos  # an album is forwarded as consecutive messages
        for w in self.pool.available():
            try:
                peer = await self.reposter._target(w, ref)
                msg = await self.pool.call(w, w.client.get_messages, peer, ids=message_id)
                media = _photo_of(msg)
                if media is None:
                    return None
                return await self.pool.call(w, w.client.download_media, media, file=bytes)
            except AccountUnavailable:
                continue
            except Exception as exc:  # noqa: BLE001 — try the next account, then the channel
                log.info("full photo of post %s from the group via account %s: %r", post.id, w.id, exc)
        return None

    async def _from_channel(self, post: Post, ch: Channel, pos: int) -> bytes | None:
        w = self.pool.get(ch.account_id)
        if w is None or not ch.tg_id:
            return None
        ids = post.album_ids or [post.msg_id]
        if pos >= len(ids):
            return None
        try:
            peer = await self.pool.input_peer(w, ch)
            media = _photo_of(await self.pool.call(w, w.client.get_messages, peer, ids=ids[pos]))
            if media is None:
                return None
            return await self.pool.call(w, w.client.download_media, media, file=bytes)
        except Exception as exc:  # noqa: BLE001
            log.info("full photo of post %s from its channel: %r", post.id, exc)
            return None

    # --- comments (comments.py): JSON; 409 {"unavailable": reason} when Telegram does not give them

    async def _post(self, request: Request) -> tuple[Post, Channel] | None:
        async with SessionLocal() as s:
            row = (await s.execute(select(Post, Channel).join(Channel, Post.channel_id == Channel.id)
                                   .where(Post.id == int(request.path_params["post_id"])))).first()
        return (row[0], row[1]) if row else None

    async def _comments_call(self, request: Request, fn) -> Response:
        if not _allowed(request):
            return PlainTextResponse("forbidden", status_code=403)
        found = await self._post(request)
        if found is None:
            return PlainTextResponse("no such post", status_code=404)
        try:
            return await fn(*found)
        except CommentsUnavailable as exc:
            return JSONResponse({"unavailable": exc.reason}, status_code=409)
        except Exception:  # noqa: BLE001
            log.exception("comments of post %s", found[0].id)
            return JSONResponse({"unavailable": "busy"}, status_code=409)

    async def comment_page(self, request: Request) -> Response:
        after = max(0, int(request.query_params.get("after", 0)))

        async def run(post, ch):
            return JSONResponse(await self.comments.page(post, ch, after))
        return await self._comments_call(request, run)

    async def comment_media(self, request: Request) -> Response:
        comment_id, what = int(request.path_params["comment_id"]), request.query_params.get("what", "thumb")

        async def run(post, ch):
            got = await self.comments.media(post, ch, comment_id, what)
            if got is None:
                return PlainTextResponse("nothing like that", status_code=404)
            return Response(got[0], media_type=got[1])
        return await self._comments_call(request, run)

    async def comment_thread(self, request: Request) -> Response:
        async def run(post, ch):
            return JSONResponse({"group": await self.comments.group_username(post, ch)})
        return await self._comments_call(request, run)

    def app(self) -> Starlette:
        return Starlette(routes=[
            Route("/photo/{post_id:int}", self.photo),
            Route("/comments/{post_id:int}", self.comment_page),
            Route("/comments/{post_id:int}/thread", self.comment_thread),
            Route("/comments/{post_id:int}/{comment_id:int}/media", self.comment_media),
        ])

    async def serve(self) -> None:
        server = uvicorn.Server(uvicorn.Config(self.app(), host="0.0.0.0", port=PORT, log_level="warning"))
        server.install_signal_handlers = lambda: None  # the worker handles its own shutdown
        await server.serve()
