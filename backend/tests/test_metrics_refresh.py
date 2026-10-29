from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from telethon.tl import types

from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, Post, utcnow
from app.worker import collector as collector_module
from app.worker import pool as pool_module
from app.worker.collector import Collector
from app.worker.pool import AccountPool, AccountWorker

TG_ID = 777_001


class MetricsClient:
    def __init__(self):
        self.calls: list[list[int]] = []

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(peer.channel_id, 0)

    async def get_messages(self, peer, ids):
        self.calls.append(sorted(ids))
        return [
            types.Message(id=i, peer_id=types.PeerChannel(TG_ID), date=datetime.now(timezone.utc), message="", views=999)
            for i in ids
        ]


async def _setup(monkeypatch):
    await engine.dispose()
    await init_db()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(pool_module.random, "uniform", lambda a, b: 0)
    now = utcnow()
    async with SessionLocal() as s:
        old = await s.scalar(select(Channel.id).where(Channel.tg_id == TG_ID))
        if old:  # left by the other test in this file (the database is shared)
            await s.execute(delete(Post).where(Post.channel_id == old))
            await s.execute(delete(Channel).where(Channel.id == old))
        acc = Account(name="metrics", session="", status="active")
        s.add(acc)
        await s.flush()
        ch = Channel(link="@metrics_chan", username="metrics_chan", tg_id=TG_ID, status="active", account_id=acc.id)
        s.add(ch)
        await s.flush()
        s.add_all([
            # 30 minutes old, read 3 minutes ago: due (fresh posts every 2 minutes)
            Post(channel_id=ch.id, msg_id=1, posted_at=now - timedelta(minutes=30), views=1, metrics_updated_at=now - timedelta(minutes=3)),
            # 3 hours old, read 3 minutes ago: not due by itself, rides along in the same request
            Post(channel_id=ch.id, msg_id=2, posted_at=now - timedelta(hours=3), views=1, metrics_updated_at=now - timedelta(minutes=3)),
            # 3 days old, read 10 minutes ago: hourly, so not now
            Post(channel_id=ch.id, msg_id=3, posted_at=now - timedelta(days=3), views=1, metrics_updated_at=now - timedelta(minutes=10)),
        ])
        await s.commit()
        return acc, ch.id


async def _views(channel_id):
    async with SessionLocal() as s:
        rows = await s.execute(select(Post.msg_id, Post.views).where(Post.channel_id == channel_id).order_by(Post.msg_id))
        return dict(rows.all())


async def test_fresh_posts_refresh_often_and_neighbours_ride_along(monkeypatch):
    acc, channel_id = await _setup(monkeypatch)
    pool = AccountPool()
    client = MetricsClient()
    pool.workers[acc.id] = AccountWorker(acc, client)

    await Collector(pool).metrics_step()

    assert client.calls == [[1, 2]]  # one request for the channel
    assert await _views(channel_id) == {1: 999, 2: 999, 3: 1}


async def test_metrics_respect_the_per_account_budget(monkeypatch):
    acc, channel_id = await _setup(monkeypatch)
    monkeypatch.setattr(collector_module, "METRICS_PER_MINUTE", 0)
    pool = AccountPool()
    client = MetricsClient()
    pool.workers[acc.id] = AccountWorker(acc, client)

    await Collector(pool).metrics_step()

    assert client.calls == []
    assert await _views(channel_id) == {1: 1, 2: 1, 3: 1}


async def test_brand_new_posts_refresh_every_few_seconds(monkeypatch):
    acc, channel_id = await _setup(monkeypatch)
    now = utcnow()
    async with SessionLocal() as s:
        # drop the default posts: only the fresh tiers matter here
        await s.execute(delete(Post).where(Post.channel_id == channel_id))
        s.add_all([
            # 1 minute old, read 15 seconds ago: due (every 10 s for the first 2 minutes)
            Post(channel_id=channel_id, msg_id=10, posted_at=now - timedelta(minutes=1), views=1, metrics_updated_at=now - timedelta(seconds=15)),
            # 15 minutes old, read 40 seconds ago: once a minute at that age, so not yet
            Post(channel_id=channel_id, msg_id=11, posted_at=now - timedelta(minutes=15), views=1, metrics_updated_at=now - timedelta(seconds=40)),
        ])
        await s.commit()
    pool = AccountPool()
    client = MetricsClient()
    pool.workers[acc.id] = AccountWorker(acc, client)

    await Collector(pool).metrics_step()

    assert client.calls == [[10]]


class SlowClient(MetricsClient):
    """Every request takes 0.3 s, like a real round trip to Telegram."""

    async def get_messages(self, peer, ids):
        import asyncio

        await asyncio.sleep(0.3)
        return await super().get_messages(peer, ids)


async def test_accounts_read_their_channels_in_parallel(monkeypatch):
    import time as time_module

    await _setup(monkeypatch)
    now = utcnow()
    pool = AccountPool()
    async with SessionLocal() as s:
        for tg in range(777_100, 777_106):
            old = await s.scalar(select(Channel.id).where(Channel.tg_id == tg))
            if old:
                await s.execute(delete(Post).where(Post.channel_id == old))
                await s.execute(delete(Channel).where(Channel.id == old))
        await s.commit()
        for n in range(2):
            acc = Account(name=f"par{n}", session="", status="active")
            s.add(acc)
            await s.flush()
            pool.workers[acc.id] = AccountWorker(acc, SlowClient())
            for k in range(3):
                ch = Channel(link=f"@par_{n}_{k}", username=f"par_{n}_{k}", tg_id=777_100 + n * 3 + k,
                             status="active", account_id=acc.id)
                s.add(ch)
                await s.flush()
                s.add(Post(channel_id=ch.id, msg_id=1, posted_at=now - timedelta(minutes=1), views=1,
                           metrics_updated_at=now - timedelta(seconds=30)))
        await s.commit()

    started = time_module.monotonic()
    await Collector(pool).metrics_step()
    elapsed = time_module.monotonic() - started

    calls = sum(len(w.client.calls) for w in pool.workers.values())
    assert calls == 6
    assert elapsed < 1.4  # one after another it would take 6 x 0.3 = 1.8 s


class DeletedClient(MetricsClient):
    """Message 2 was deleted in the channel: Telegram returns None in its place."""

    async def get_messages(self, peer, ids):
        msgs = await super().get_messages(peer, ids)
        return [None if m.id == 2 else m for m in msgs]


async def test_deleted_posts_are_marked_and_no_longer_read(monkeypatch):
    acc, channel_id = await _setup(monkeypatch)
    pool = AccountPool()
    client = DeletedClient()
    pool.workers[acc.id] = AccountWorker(acc, client)
    collector = Collector(pool)

    await collector.metrics_step()
    async with SessionLocal() as s:
        rows = dict((await s.execute(select(Post.msg_id, Post.deleted_at).where(Post.channel_id == channel_id))).all())
    assert rows[1] is None and rows[2] is not None  # 1 still exists, 2 is gone

    # make both due again: only the surviving post is asked for
    async with SessionLocal() as s:
        await s.execute(update(Post).where(Post.channel_id == channel_id).values(metrics_updated_at=utcnow() - timedelta(hours=2)))
        await s.commit()
    client.calls.clear()
    await collector.metrics_step()
    assert client.calls == [[1, 3]]  # 2 is not asked for any more
