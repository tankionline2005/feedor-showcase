"""Live updates for the web UI: a server-sent events stream per open tab.

Posts are written by the worker (another process), so the web app watches a cheap signature
of recent posts — new, deleted, re-counted or reposted ones all change it — and tells the
connected tabs; they refetch what they show. The check runs only while someone is connected.
Each project has a watcher of its own channels, so nobody is woken by posts they cannot see.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from ..db import SessionLocal
from ..models import Post, SmartFeed, SmartVerdict, Trigger, TriggerHit, utcnow
from ..scope import project_channel_ids
from .deps import Viewer

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["live"])

CHECK_EVERY = 3  # seconds between signature checks
PING_EVERY = 20  # keep-alive comment, also how fast a closed tab is noticed
WINDOW = timedelta(days=8)  # metrics stop changing after 7 days


async def signature(project_id: int | None = None) -> tuple:
    """Of every recent post for the admin (None), of the project's channels otherwise."""
    stmt = select(
        func.count(Post.id),
        func.max(Post.id),
        func.max(Post.metrics_updated_at),
        func.max(Post.reposted_at),
    ).where(Post.posted_at > utcnow() - WINDOW)
    if project_id is not None:
        stmt = stmt.where(Post.channel_id.in_(project_channel_ids(project_id)))
    async with SessionLocal() as s:
        sig = tuple((await s.execute(stmt)).one())
        if project_id is not None:
            # the AI's verdicts come seconds after the post: the smart feed shows it only then
            sig += (await s.scalar(select(func.max(SmartVerdict.id)).join(SmartFeed, SmartFeed.id == SmartVerdict.feed_id)
                                   .where(SmartFeed.project_id == project_id)),)
            # and the hits of the triggers, a few seconds after the post too
            sig += (await s.scalar(select(func.max(TriggerHit.id)).join(Trigger, Trigger.id == TriggerHit.trigger_id)
                                   .where(Trigger.project_id == project_id)),)
        return sig


class Watcher:
    def __init__(self, project_id: int | None = None) -> None:
        self.project_id = project_id
        self.version = 0
        self.changed = asyncio.Event()
        self.clients = 0
        self._task: asyncio.Task | None = None

    def join(self) -> None:
        self.clients += 1
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def leave(self) -> None:
        self.clients -= 1

    async def _run(self) -> None:
        last = None
        while self.clients > 0:
            try:
                current = await signature(self.project_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("live: signature check failed: %s", exc)
                current = last
            if last is not None and current != last:
                self.version += 1
                fired, self.changed = self.changed, asyncio.Event()
                fired.set()
            last = current
            await asyncio.sleep(CHECK_EVERY)


watchers: dict[int | None, Watcher] = {}


def watcher_for(project_id: int | None) -> Watcher:
    if project_id not in watchers:
        watchers[project_id] = Watcher(project_id)
    return watchers[project_id]


@router.get("/live")
async def live(request: Request, viewer: Viewer) -> StreamingResponse:
    watcher = watcher_for(None if viewer.is_admin else viewer.project_id)

    async def events() -> AsyncIterator[str]:
        watcher.join()
        try:
            yield "retry: 5000\n\n"  # reconnect delay for the browser
            while not await request.is_disconnected():
                changed = watcher.changed
                try:
                    await asyncio.wait_for(changed.wait(), PING_EVERY)
                    yield f"event: posts\ndata: {watcher.version}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            watcher.leave()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
