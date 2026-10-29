from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select, update
from telethon import errors
from telethon.tl import types

from app import settings_store
from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, Post
from app.worker import pool as pool_module
from app.worker.collector import Collector
from app.worker.pool import AccountPool, AccountWorker
from app.worker.reposter import Reposter


def msg(id, text="", grouped_id=None):
    return types.Message(
        id=id,
        peer_id=types.PeerChannel(555),
        date=datetime.now(timezone.utc),
        message=text,
        grouped_id=grouped_id,
        views=100,
    )


AGG_GROUP = types.Channel(
    id=4242, title="Агрегатор", photo=types.ChatPhotoEmpty(), date=None,
    megagroup=True, left=True, access_hash=1, username="agg_group",
)


class FakeClient:
    def __init__(self):
        self.forwarded = []
        self.sent = []
        self.requests = []
        self.restricted = False
        self.slow_mode = 0

    async def __call__(self, request):
        self.requests.append(type(request).__name__)

    async def get_entity(self, username):
        assert username == "agg_group"
        return AGG_GROUP

    async def get_input_entity(self, peer):
        if isinstance(peer, types.PeerChannel):
            return types.InputPeerChannel(peer.channel_id, 0)
        raise ValueError(peer)

    async def forward_messages(self, target, ids, source):
        if self.slow_mode:
            raise errors.SlowModeWaitError(request=None, capture=self.slow_mode)
        if self.restricted:
            raise errors.ChatForwardsRestrictedError(request=None)
        self.forwarded.append((target.channel_id, list(ids), source.channel_id))
        # the copies created in the aggregator, numbered from 500
        return [SimpleNamespace(id=500 + len(self.forwarded) * 10 + i) for i in range(len(ids))]

    async def send_message(self, target, text, link_preview=False):
        self.sent.append((target.channel_id, text))

    async def disconnect(self):
        pass


async def test_ingest_dedupes_and_reposts(monkeypatch):
    await engine.dispose()  # connections from the TestClient's event loop
    await init_db()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(pool_module.random, "uniform", lambda a, b: 0)

    pool = AccountPool()
    collector = Collector(pool)
    reposter = Reposter(pool, collector)

    async with SessionLocal() as s:
        # posts left pending by other tests would be picked first (any account may repost them)
        await s.execute(update(Post).where(Post.repost_status == "pending").values(repost_status="skipped"))
        acc = Account(name="worker", session="", status="active")
        s.add(acc)
        await s.flush()
        ch = Channel(link="@src_chan", username="src_chan", tg_id=555, title="Src", status="active", account_id=acc.id)
        s.add(ch)
        await s.commit()
        await settings_store.update(s, {"aggregator_channel": "-1009999"})

    client = FakeClient()
    w = AccountWorker(acc, client)
    pool.workers[acc.id] = w
    await collector.refresh_map()

    # history fetched on join is stored but never reposted
    await collector.ingest(w, ch.id, [msg(10, "old")], backfill=True)
    # an album arrives twice (update stream + polling)
    album = [msg(11, grouped_id=1), msg(12, "caption", grouped_id=1)]
    await collector.on_messages(w, 555, album)
    await collector.on_messages(w, 555, album)
    # posts from unknown channels are ignored
    await collector.on_messages(w, 424242, [msg(1, "foreign")])

    async with SessionLocal() as s:
        posts = (await s.execute(select(Post).where(Post.channel_id == ch.id).order_by(Post.msg_id))).scalars().all()
    assert [(p.msg_id, p.repost_status, p.album_ids, p.text) for p in posts] == [
        (10, "skipped", None, "old"),
        (11, "pending", [11, 12], "caption"),
    ]

    await reposter.step()
    assert client.forwarded == [(9999, [11, 12], 555)]
    async with SessionLocal() as s:
        url = await s.scalar(select(Post.repost_url).where(Post.channel_id == ch.id, Post.msg_id == 11))
    assert url == "https://t.me/c/9999/510"  # numeric aggregator: members-only link to the first album item
    assert 9999 in collector.ignored_tg_ids

    # forwarding disabled in the source channel: text copy with a link
    await collector.on_messages(w, 555, [msg(13, "secret")])
    client.restricted = True
    await reposter.step()
    assert client.sent and client.sent[0][0] == 9999
    assert "secret" in client.sent[0][1] and "https://t.me/src_chan/13" in client.sent[0][1]

    async with SessionLocal() as s:
        statuses = dict((await s.execute(select(Post.msg_id, Post.repost_status).where(Post.channel_id == ch.id))).all())
    assert statuses == {10: "skipped", 11: "done", 13: "done"}
    async with SessionLocal() as s:
        copies = dict((await s.execute(select(Post.msg_id, Post.repost_copy).where(Post.channel_id == ch.id))).all())
    # the bot later forwards a real forward to users, but copies our own text copy (see originals.py)
    assert copies == {10: None, 11: False, 13: True}

    # a public group as the aggregator: the account joins it by itself
    async with SessionLocal() as s:
        await settings_store.update(s, {"aggregator_channel": "@agg_group"})
    client.restricted = False
    await collector.on_messages(w, 555, [msg(14, "to group")])
    await reposter.step()
    assert client.requests == ["JoinChannelRequest"]
    assert client.forwarded[-1] == (4242, [14], 555)
    async with SessionLocal() as s:
        url = await s.scalar(select(Post.repost_url).where(Post.channel_id == ch.id, Post.msg_id == 14))
    assert url == "https://t.me/agg_group/520"  # public group: link by its username

    # slow mode: the post stays queued and this account pauses reposting
    client.slow_mode = 30
    await collector.on_messages(w, 555, [msg(15, "slow")])
    await reposter.step()
    async with SessionLocal() as s:
        status = await s.scalar(select(Post.repost_status).where(Post.channel_id == ch.id, Post.msg_id == 15))
    assert status == "pending"
    assert await reposter.step() == 10  # no account ready to repost
