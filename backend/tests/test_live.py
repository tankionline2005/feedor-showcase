import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal, engine, init_db
from app.models import Channel, Post, utcnow
from app.web import live
from app.web.main import app


def test_live_stream_requires_login():
    assert TestClient(app).get("/api/live").status_code == 401


async def test_watcher_fires_when_posts_change(monkeypatch):
    await engine.dispose()
    await init_db()
    monkeypatch.setattr(live, "CHECK_EVERY", 0.05)
    watcher = live.Watcher()
    watcher.join()
    try:
        await asyncio.sleep(0.2)  # first signature is the baseline, not a change
        assert watcher.version == 0
        changed = watcher.changed

        async with SessionLocal() as s:
            ch = Channel(link="@live_chan", username="live_chan", status="active")
            s.add(ch)
            await s.flush()
            s.add(Post(channel_id=ch.id, msg_id=1, posted_at=utcnow() - timedelta(minutes=1)))
            await s.commit()

        await asyncio.wait_for(changed.wait(), 2)
        assert watcher.version == 1
    finally:
        watcher.leave()
        await asyncio.sleep(0.1)  # let the loop notice and stop
