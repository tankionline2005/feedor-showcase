from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import delete, select, update
from telethon.tl import types

from app import settings_store
from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, Post
from app.worker import collector as collector_module
from app.worker import pool as pool_module
from app.worker.collector import Collector
from app.worker.pool import AccountPool, AccountWorker

TG_ID = 783_001
NOW = datetime.now(timezone.utc)


def entity():
    return types.Channel(id=TG_ID, title="Каждый час", photo=types.ChatPhotoEmpty(), date=None, broadcast=True,
                         left=True, access_hash=1, username="hourly_chan")


class Client:
    """A channel posting every hour for 10 days: message 240 is the newest, 1 the oldest.
    Messages 71–73 form an album right at the 7-day edge."""

    def __init__(self):
        self.pages: list[int] = []

    def message(self, mid: int) -> types.Message:
        date = NOW - timedelta(hours=240 - mid, minutes=30)
        grouped = 99 if mid in (71, 72, 73) else None
        if grouped:
            date = NOW - timedelta(days=7, minutes=-1 if mid == 73 else 1)  # 73 is inside, 71–72 just outside
        return types.Message(id=mid, peer_id=types.PeerChannel(TG_ID), date=date, message=f"post {mid}",
                             grouped_id=grouped, views=10)

    async def __call__(self, request):
        if type(request).__name__ == "GetFullChannelRequest":
            return SimpleNamespace(full_chat=SimpleNamespace(id=TG_ID, participants_count=5000), chats=[entity()])
        return None

    async def get_entity(self, username):
        return entity()

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(TG_ID, 1)

    async def get_messages(self, peer, limit, offset_id=0):
        self.pages.append(offset_id)
        top = (offset_id or 241) - 1
        return [self.message(mid) for mid in range(top, max(top - limit, 0), -1)]

    async def download_profile_photo(self, entity, file=None):
        return b""


async def test_a_new_channel_brings_a_week_of_posts(monkeypatch):
    await engine.dispose()
    await init_db()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(pool_module.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(collector_module.random, "uniform", lambda a, b: 0)
    async with SessionLocal() as s:
        old = await s.scalar(select(Channel.id).where(Channel.tg_id == TG_ID))
        if old:
            await s.execute(delete(Post).where(Post.channel_id == old))
            await s.execute(delete(Channel).where(Channel.id == old))
        await s.execute(delete(Channel).where(Channel.status.in_(("pending", "restricted"))))
        await s.execute(update(Account).values(enabled=False))
        acc = Account(name="Backfill", session="", status="active", max_channels=100)
        s.add(acc)
        await s.flush()
        ch = Channel(link="@hourly_chan", username="hourly_chan", status="pending")
        s.add(ch)
        await settings_store.update(s, {"backfill_days": 7})
        await s.commit()

    pool = AccountPool()
    client = Client()
    pool.workers[acc.id] = AccountWorker(acc, client)
    await Collector(pool).join_step()

    async with SessionLocal() as s:
        joined = await s.get(Channel, ch.id)
        posts = (await s.execute(select(Post).where(Post.channel_id == ch.id))).scalars().all()
    assert joined.status == "active" and joined.last_msg_id == 240
    # 100 messages a request, until the week is behind: two pages, not the whole channel
    assert client.pages == [0, 141]
    # every hour of the last 7 days, and the album at the edge comes whole (one post)
    singles = [p for p in posts if not p.album_ids]
    assert sorted(p.msg_id for p in singles) == list(range(74, 241))  # every hour of the week
    album = [p for p in posts if p.album_ids]
    assert len(album) == 1 and album[0].album_ids == [71, 72, 73]
    assert all(p.repost_status == "skipped" for p in posts)  # history is never reposted
    assert all(p.history for p in posts)  # nor read by the AI as news
    assert min(p.posted_at for p in singles) >= (NOW - timedelta(days=7, minutes=5)).replace(tzinfo=None)

    async with SessionLocal() as s:
        await s.execute(update(Account).where(Account.id == acc.id).values(enabled=False))
        await s.commit()


def test_zero_days_keeps_only_the_place():
    assert collector_module._within([Client().message(240)], 0) == []
    assert [m.id for m in collector_module._within([Client().message(240)], 1)] == [240]


async def test_older_channels_catch_up_their_week(monkeypatch):
    await engine.dispose()
    await init_db()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(pool_module.random, "uniform", lambda a, b: 0)
    async with SessionLocal() as s:
        old = await s.scalar(select(Channel.id).where(Channel.tg_id == TG_ID))
        if old:
            await s.execute(delete(Post).where(Post.channel_id == old))
            await s.execute(delete(Channel).where(Channel.id == old))
        await s.execute(update(Account).values(enabled=False))
        await s.execute(update(Channel).values(history_done=True))  # other tests' channels are not ours
        acc = Account(name="Catch-up", session="", status="active", max_channels=100)
        s.add(acc)
        await s.flush()
        # added before joining brought a week: it has only its last two days
        ch = Channel(link="@hourly_chan", username="hourly_chan", tg_id=TG_ID, title="Каждый час", status="active",
                     account_id=acc.id, last_msg_id=240, history_done=False)
        s.add(ch)
        await s.flush()
        for mid in range(193, 241):
            s.add(Post(channel_id=ch.id, msg_id=mid, posted_at=Client().message(mid).date.replace(tzinfo=None),
                       text=f"post {mid}", repost_status="done", views=99))
        await settings_store.update(s, {"backfill_days": 7})
        await s.commit()

    pool = AccountPool()
    pool.workers[acc.id] = AccountWorker(acc, Client())
    collector = Collector(pool)
    await collector.history_step()

    async with SessionLocal() as s:
        posts = (await s.execute(select(Post).where(Post.channel_id == ch.id))).scalars().all()
        caught_up = (await s.get(Channel, ch.id)).history_done
    ids = sorted(p.msg_id for p in posts if not p.album_ids)
    assert ids == list(range(74, 241))  # the whole week, no copies of the posts it had
    assert all(p.repost_status == "done" for p in posts if p.msg_id >= 193)  # the old ones untouched
    assert all(p.repost_status == "skipped" for p in posts if p.msg_id < 193)  # history is not reposted
    assert caught_up

    # done once: the next round has nothing to do
    client = Client()
    pool.workers[acc.id] = AccountWorker(acc, client)
    await collector.history_step()
    assert client.pages == []

    async with SessionLocal() as s:
        await s.execute(update(Account).where(Account.id == acc.id).values(enabled=False))
        await s.commit()
