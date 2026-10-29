from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Channel, Post, utcnow
from app.web.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def authed(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "secret"})
    assert r.status_code == 200
    return client


def test_requires_login(client):
    assert TestClient(app).get("/api/posts").status_code == 401


def test_wrong_password(client):
    r = TestClient(app).post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_add_channels_dedupes_and_reports_invalid(authed):
    r = authed.post("/api/channels", json={"links": "@first_channel, t.me/First_Channel\nhttps://example.com/x\nt.me/+hash123"})
    body = r.json()
    assert [c["username"] for c in body["added"]] == ["first_channel", None]
    assert body["duplicates"] == ["t.me/First_Channel"]
    assert body["invalid"] == ["https://example.com/x"]
    again = authed.post("/api/channels", json={"links": "@first_channel"}).json()
    assert again["added"] == [] and again["duplicates"] == ["@first_channel"]


async def _seed():
    async with SessionLocal() as s:
        ch = Channel(link="@seeded", username="seeded", tg_id=123, title="Seeded", status="active")
        s.add(ch)
        await s.flush()
        now = utcnow()
        s.add_all(
            [
                Post(channel_id=ch.id, msg_id=4, posted_at=now - timedelta(minutes=5), text="just now", views=10, er=0.5),
                Post(channel_id=ch.id, msg_id=1, posted_at=now - timedelta(minutes=30), text="fresh", views=100, er=2.0),
                Post(channel_id=ch.id, msg_id=2, posted_at=now - timedelta(hours=3), text="older", views=900, er=1.0),
                Post(channel_id=ch.id, msg_id=3, posted_at=now - timedelta(days=2), text="old", views=500, er=9.0),
            ]
        )
        await s.commit()
        return ch.id


def test_posts_period_and_sort(authed):
    channel_id = authed.portal.call(_seed)

    newest = authed.get("/api/posts", params={"channel_id": channel_id}).json()["items"]
    assert [p["msg_id"] for p in newest] == [4, 1, 2, 3]
    assert newest[0]["url"] == "https://t.me/seeded/4"
    assert newest[0]["posted_at"].endswith("Z")

    hour = authed.get("/api/posts", params={"channel_id": channel_id, "period": "1h"}).json()["items"]
    assert [p["msg_id"] for p in hour] == [4, 1]

    twenty = authed.get("/api/posts", params={"channel_id": channel_id, "period": "20m"}).json()["items"]
    assert [p["msg_id"] for p in twenty] == [4]

    by_views = authed.get("/api/posts", params={"channel_id": channel_id, "period": "24h", "sort": "views"}).json()
    assert [p["msg_id"] for p in by_views["items"]] == [2, 1, 4]

    by_er = authed.get("/api/posts", params={"channel_id": channel_id, "sort": "er", "limit": 1}).json()
    assert [p["msg_id"] for p in by_er["items"]] == [3]
    assert by_er["has_more"] is True


def test_settings_validation(authed):
    assert authed.get("/api/settings").json()["retention_days"] == 30
    r = authed.put("/api/settings", json={"retention_days": 45, "aggregator_channel": " @agg "})
    assert r.status_code == 200
    assert r.json()["retention_days"] == 45
    assert r.json()["aggregator_channel"] == "@agg"
    assert authed.put("/api/settings", json={"retention_days": 0}).status_code == 422
    assert authed.put("/api/settings", json={"unknown": 1}).status_code == 422


def test_pause_and_delete_channel(authed):
    ch = authed.post("/api/channels", json={"links": "@to_delete"}).json()["added"][0]
    assert authed.patch(f"/api/channels/{ch['id']}", json={"action": "pause"}).json()["status"] == "paused"
    assert authed.patch(f"/api/channels/{ch['id']}", json={"action": "pause"}).status_code == 409
    assert authed.delete(f"/api/channels/{ch['id']}").status_code == 200
    ids = [c["id"] for c in authed.get("/api/channels").json()["items"]]
    assert ch["id"] not in ids
