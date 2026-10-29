import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.models import Channel, Post, utcnow


def test_dismissed_repost_error_stays_hidden_until_a_new_one():
    asyncio.run(engine.dispose())
    from app.web.main import app

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200

        async def add_failed(msg_id: int, error: str, reposted_at):
            async with SessionLocal() as s:
                ch = Channel(link=f"@status_{msg_id}", username=f"status_{msg_id}", status="active")
                s.add(ch)
                await s.flush()
                s.add(Post(channel_id=ch.id, msg_id=msg_id, posted_at=utcnow(), repost_status="failed",
                           repost_error=error, reposted_at=reposted_at))
                await s.commit()

        # an old failure from before reposted_at existed
        client.portal.call(add_failed, 1, "old error", None)
        assert client.get("/api/status").json()["last_repost_error"] == "old error"

        assert client.post("/api/status/dismiss-repost-error").status_code == 200
        assert client.get("/api/status").json()["last_repost_error"] is None

        client.portal.call(add_failed, 2, "new error", utcnow() + timedelta(seconds=1))
        assert client.get("/api/status").json()["last_repost_error"] == "new error"

        # the Errors screen lists every failure, newest first, dismissed ones included
        body = client.get("/api/reposts/failed", params={"limit": 1}).json()
        assert body["total"] >= 2 and body["has_more"] is True
        newest = body["items"][0]
        assert newest["error"] == "new error" and newest["url"] == "https://t.me/status_2/2"
        rest = client.get("/api/reposts/failed", params={"offset": 1}).json()["items"]
        assert "old error" in [i["error"] for i in rest]

        disk = client.get("/api/storage").json()
        assert disk["total"] >= disk["used"] > 0 and disk["free"] > 0
        assert disk["database"] > 0 and disk["media"] >= 0 and disk["level"] in ("ok", "low", "critical")


def test_disk_levels():
    from app.storage import human, level

    gb = 1024**3
    assert level(20 * gb, 100 * gb) == "ok"
    assert level(10 * gb, 100 * gb) == "low"
    assert level(4 * gb, 100 * gb) == "critical"
    assert human(int(6.2 * gb)) == "6,2 ГБ" and human(5 * 1024**2) == "5,0 МБ"
