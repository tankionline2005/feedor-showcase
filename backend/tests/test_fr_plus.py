from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.fr_plus import fr_plus, refresh_fr_bases
from app.models import Channel, Post, utcnow
from app.web.main import app


def test_fr_plus_value():
    assert fr_plus(15.0, 10.0) == 1.5
    assert fr_plus(5.0, None) == 0.0 and fr_plus(5.0, 0.0) == 0.0


def test_fr_plus_compares_posts_to_their_own_channel():
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200

        async def seed():
            async with SessionLocal() as s:
                now = utcnow()
                big = Channel(link="@frp_big", username="frp_big", status="active")
                small = Channel(link="@frp_small", username="frp_small", status="active")
                s.add_all([big, small])
                await s.flush()
                s.add_all([
                    # settled history: big usually gets FR 10, small FR 2
                    Post(channel_id=big.id, msg_id=1, posted_at=now - timedelta(days=3), fr=8.0),
                    Post(channel_id=big.id, msg_id=2, posted_at=now - timedelta(days=2), fr=12.0),
                    Post(channel_id=small.id, msg_id=1, posted_at=now - timedelta(days=3), fr=2.0),
                    # a younger post never counts toward the baseline
                    Post(channel_id=small.id, msg_id=2, posted_at=now - timedelta(hours=2), fr=0.1),
                    # today: big gets 15 (×1.5 of its usual), small gets 6 (×3)
                    Post(channel_id=big.id, msg_id=3, posted_at=now - timedelta(minutes=10), fr=15.0),
                    Post(channel_id=small.id, msg_id=3, posted_at=now - timedelta(minutes=10), fr=6.0),
                ])
                await s.commit()
                await refresh_fr_bases(s)
                bases = dict((await s.execute(
                    select(Channel.username, Channel.fr_base).where(Channel.username.in_(["frp_big", "frp_small"]))
                )).all())
                return big.id, small.id, bases

        big, small, bases = client.portal.call(seed)
        assert bases == {"frp_big": 10.0, "frp_small": 2.0}

        def top(sort):
            items = []
            for cid in (big, small):
                items += client.get("/api/posts", params={"channel_id": cid, "period": "1h", "sort": sort}).json()["items"]
            return items

        fresh = {p["channel"]["id"]: p for p in top("fr_plus")}
        assert fresh[big]["fr_plus"] == 1.5 and fresh[small]["fr_plus"] == 3.0

        # across channels: FR puts the big channel first, FR+ the one that beat its usual more
        def ranking(sort):
            items = client.get("/api/posts", params={"period": "1h", "sort": sort, "limit": 100}).json()["items"]
            return [p["channel"]["id"] for p in items if p["channel"]["id"] in (big, small)]

        assert ranking("fr") == [big, small]
        assert ranking("fr_plus") == [small, big]
        assert client.get("/api/posts", params={"sort": "velocity"}).status_code == 422


def test_ratio_sorts_wait_for_enough_views():
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200

        async def seed():
            async with SessionLocal() as s:
                now = utcnow()
                ch = Channel(link="@ratio_chan", username="ratio_chan", status="active")
                s.add(ch)
                await s.flush()
                s.add_all([
                    # brand new: 1 view, 1 reaction — ER 100%, but too few views to mean anything
                    Post(channel_id=ch.id, msg_id=1, posted_at=now - timedelta(minutes=1), views=1, reactions=1, er=100.0),
                    Post(channel_id=ch.id, msg_id=2, posted_at=now - timedelta(minutes=8), views=5000, reactions=150, er=3.0),
                    Post(channel_id=ch.id, msg_id=3, posted_at=now - timedelta(minutes=9), views=800, reactions=8, er=1.0),
                ])
                await s.commit()
                return ch.id

        cid = client.portal.call(seed)

        def order(sort):
            items = client.get("/api/posts", params={"channel_id": cid, "sort": sort}).json()["items"]
            return [p["msg_id"] for p in items]

        assert order("er") == [2, 3, 1]       # rated posts first, the 1-view one after them
        assert order("views") == [2, 3, 1]
        assert order("new") == [1, 2, 3]      # chronological feeds are unaffected


def test_rating_threshold_is_one_percent_of_subscribers():
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200

        async def seed():
            async with SessionLocal() as s:
                now = utcnow()
                ch = Channel(link="@share_chan", username="share_chan", status="active", subscribers=100_000)
                s.add(ch)
                await s.flush()
                s.add_all([
                    # 800 views is below 1% of 100k subscribers — not rated yet despite the high ER
                    Post(channel_id=ch.id, msg_id=1, posted_at=now - timedelta(minutes=3), views=800, er=9.0),
                    Post(channel_id=ch.id, msg_id=2, posted_at=now - timedelta(minutes=9), views=1500, er=2.0),
                ])
                await s.commit()
                return ch.id

        cid = client.portal.call(seed)
        items = client.get("/api/posts", params={"channel_id": cid, "sort": "er"}).json()["items"]
        assert [(p["msg_id"], p["rated"]) for p in items] == [(2, True), (1, False)]
