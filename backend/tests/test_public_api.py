import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db import SessionLocal, engine
from app.models import Channel, Post, utcnow
from app.web import deps, public_api


@pytest.fixture(scope="module")
def ctx():
    asyncio.run(engine.dispose())
    from app.web.main import app

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200

        async def seed():
            async with SessionLocal() as s:
                await s.execute(update(Channel).where(Channel.status == "active").values(status="paused"))
                a = Channel(link="@api_alpha", username="api_alpha", title="Alpha", tg_id=9001, status="active",
                            subscribers=10_000)
                b = Channel(link="@api_beta", username="api_beta", title="Beta", tg_id=9002, status="active")
                s.add_all([a, b])
                await s.flush()
                now = utcnow()
                posts = [
                    Post(channel_id=a.id, msg_id=1, posted_at=now - timedelta(minutes=20), text="fresh alpha",
                         views=500, fr=5.0, reactions_top=[{"e": "👍", "c": 7}], has_preview=False,
                         extracted_text_from_media="ЯБЛОКИ, ВАРЕНЬЕ\nсливы", ocr_status="done"),
                    Post(channel_id=a.id, msg_id=2, posted_at=now - timedelta(hours=5), text="older alpha",
                         views=9000, fr=90.0),
                    Post(channel_id=b.id, msg_id=1, posted_at=now - timedelta(days=3), text="beta post", views=100),
                ]
                s.add_all(posts)
                await s.commit()
                return a.id, b.id, [p.id for p in posts]

        alpha, beta, post_ids = client.portal.call(seed)
        key = client.get("/api/api-key").json()["key"]
        public_api._calls.clear()
        yield client, key, alpha, beta, post_ids


def get(client, key, path, **params):
    return client.get(f"/api/v1{path}", params=params, headers={"X-API-Key": key})


def test_key_is_required_and_checked(ctx):
    client, key, *_ = ctx
    anon = TestClient(client.app)
    r = anon.get("/api/v1/stats")
    assert r.status_code == 401 and r.json()["error"]["code"] == "missing_key"
    r = anon.get("/api/v1/stats", headers={"X-API-Key": "nope"})
    assert r.json()["error"]["code"] == "invalid_key"
    assert anon.get("/api/v1/stats", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert anon.get("/api/api-key").status_code == 401  # the key itself needs the web login


def test_posts_filters_and_sorting(ctx):
    client, key, alpha, beta, ids = ctx
    assert get(client, key, "/posts", sort="velocity").status_code == 422  # sorting by velocity was removed
    body = get(client, key, "/posts", channel="@api_alpha", sort="new").json()
    assert [p["text"] for p in body["items"]] == ["fresh alpha", "older alpha"]
    first = body["items"][0]
    assert first["channel"] == {"id": alpha, "title": "Alpha", "username": "api_alpha", "url": "https://t.me/api_alpha"}
    assert first["url"] == "https://t.me/api_alpha/1"
    assert "velocity" not in first["metrics"] and first["reactions"] == [{"emoji": "👍", "count": 7}]
    assert "html" not in first and first["preview_url"] is None

    hour = get(client, key, "/posts", channel=[str(alpha), "api_beta"], period="1h").json()["items"]
    assert [p["text"] for p in hour] == ["fresh alpha"]

    since = (utcnow() - timedelta(days=4)).isoformat() + "Z"
    until = (utcnow() - timedelta(days=1)).isoformat() + "Z"
    ranged = get(client, key, "/posts", since=since, until=until, channel="api_beta").json()["items"]
    assert [p["text"] for p in ranged] == ["beta post"]

    page = get(client, key, "/posts", channel="api_alpha", sort="fr", limit=1).json()
    assert page["items"][0]["text"] == "older alpha" and page["has_more"] and page["next_offset"] == 1


def test_new_posts_sync(ctx):
    client, key, _a, _b, ids = ctx
    body = get(client, key, "/posts/new", after_id=ids[0], limit=1).json()
    assert [p["id"] for p in body["items"]] == [ids[1]]
    assert body["last_id"] == ids[1] and body["has_more"] is True
    tail = get(client, key, "/posts/new", after_id=ids[2]).json()
    assert tail == {"items": [], "last_id": ids[2], "has_more": False}


def test_single_post_channels_and_stats(ctx):
    client, key, alpha, beta, ids = ctx
    post = get(client, key, f"/posts/{ids[0]}").json()
    assert post["html"] is not None and "repost_status" in post
    assert post["extracted_text_from_media"] == "ЯБЛОКИ, ВАРЕНЬЕ\nсливы"  # the text on its pictures
    assert get(client, key, f"/posts/{ids[2]}").json()["extracted_text_from_media"] is None  # an old post
    assert get(client, key, "/posts/999999").json()["error"]["code"] == "not_found"
    assert get(client, key, f"/posts/{ids[0]}/preview").json()["error"]["code"] == "no_preview"

    channels = get(client, key, "/channels", status="active").json()["items"]
    assert {c["username"] for c in channels} == {"api_alpha", "api_beta"}
    one = get(client, key, f"/channels/{alpha}").json()
    assert one["subscribers"] == 10_000 and one["posts_per_day"] > 0
    assert get(client, key, "/channels", status="weird").json()["error"]["code"] == "invalid_params"

    stats = get(client, key, "/stats").json()
    assert stats["channels"]["active"] == 2 and stats["posts_24h"] >= 2
    assert stats["top_24h"][0]["text"] == "older alpha"


def test_errors_cors_and_unknown_channel(ctx):
    client, key, *_ = ctx
    r = get(client, key, "/posts", channel="@missing_one")
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_channel"
    r = get(client, key, "/posts", limit=1000)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_params"
    r = get(client, key, "/nothing-here")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert r.headers["access-control-allow-origin"] == "*"
    pre = client.options("/api/v1/posts", headers={"Origin": "https://example.com"})
    assert pre.status_code == 204 and "X-API-Key" in pre.headers["access-control-allow-headers"]
    assert client.get("/api/settings").status_code == 200  # the web API keeps its own format
    assert "api_key" not in client.get("/api/settings").json()


def test_reset_invalidates_old_key_and_rate_limit(ctx, monkeypatch):
    client, key, *_ = ctx
    new_key = client.post("/api/api-key/reset").json()["key"]
    assert new_key != key and new_key.startswith("km_")
    assert get(client, key, "/stats").json()["error"]["code"] == "invalid_key"
    monkeypatch.setattr(deps, "API_RATE_LIMIT", 2)
    public_api._calls.clear()
    assert get(client, new_key, "/stats").status_code == 200
    assert get(client, new_key, "/stats").status_code == 200
    r = get(client, new_key, "/stats")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited" and "retry-after" in r.headers
    public_api._calls.clear()
