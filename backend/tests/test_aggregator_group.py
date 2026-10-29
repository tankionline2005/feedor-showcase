from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import delete, select, update
from telethon import errors
from telethon.tl import types

from app import settings_store
from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, Post
from app.worker import pool as pool_module
from app.worker import reposter as reposter_module
from app.worker.collector import Collector
from app.worker.pool import AccountPool, AccountWorker
from app.worker.reposter import Reposter

SRC = 556_001
BASIC = types.Chat(id=5150, title="Агрегатор", photo=types.ChatPhotoEmpty(), participants_count=3, date=None, version=1)
SUPER = types.Channel(id=4315233732, title="Агрегатор", photo=types.ChatPhotoEmpty(), date=None, megagroup=True,
                      access_hash=9)


def msg(id):
    return types.Message(id=id, peer_id=types.PeerChannel(SRC), date=datetime.now(timezone.utc), message="post", views=1)


class GroupClient:
    """A private aggregator group, joined by its invite link, that can be upgraded to a supergroup."""

    def __init__(self):
        self.upgraded = False
        self.forwarded: list[tuple[str, int]] = []

    async def __call__(self, request):
        if type(request).__name__ == "CheckChatInviteRequest":
            return types.ChatInviteAlready(chat=SUPER if self.upgraded else BASIC)
        return None

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(peer.channel_id, 0)

    async def forward_messages(self, target, ids, source):
        if isinstance(target, types.InputPeerChat) and self.upgraded:
            raise errors.PeerIdInvalidError(request=None)  # the basic group is gone after the upgrade
        kind = "chat" if isinstance(target, types.InputPeerChat) else "channel"
        self.forwarded.append((kind, ids[0]))
        return [SimpleNamespace(id=700 + len(self.forwarded))]


async def test_basic_group_is_reported_once_and_upgrade_loses_no_post(monkeypatch):
    await engine.dispose()
    await init_db()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(pool_module.random, "uniform", lambda a, b: 0)
    sent_notes: list[str] = []
    monkeypatch.setattr(reposter_module.notifier, "emit", lambda key, text, **kw: sent_notes.append(key))

    async with SessionLocal() as s:
        await s.execute(update(Post).where(Post.repost_status == "pending").values(repost_status="skipped"))
        old = await s.scalar(select(Channel.id).where(Channel.tg_id == SRC))
        if old:
            await s.execute(delete(Post).where(Post.channel_id == old))
            await s.execute(delete(Channel).where(Channel.id == old))
        acc = Account(name="grp", session="", status="active")
        s.add(acc)
        await s.flush()
        ch = Channel(link="@grp_src", username="grp_src", tg_id=SRC, title="Src", status="active", account_id=acc.id)
        s.add(ch)
        await s.commit()
        await settings_store.update(s, {"aggregator_channel": "t.me/+grpInvite1", "aggregator_basic_warned": ""})

    pool = AccountPool()
    client = GroupClient()
    w = AccountWorker(acc, client)
    pool.workers[acc.id] = w
    collector = Collector(pool)
    await collector.refresh_map()

    async def repost(reposter, msg_id):
        await collector.on_messages(w, SRC, [msg(msg_id)])
        await reposter.step()
        async with SessionLocal() as s:
            return (await s.execute(
                select(Post.repost_status, Post.repost_url).where(Post.channel_id == ch.id, Post.msg_id == msg_id)
            )).one()

    # a basic group: the post goes through, without a link, and the bot says why — once
    reposter = Reposter(pool, collector)
    assert tuple(await repost(reposter, 1)) == ("done", None)
    assert tuple(await repost(reposter, 2)) == ("done", None)
    assert sent_notes == ["aggregator-basic-group"]
    # not again after a restart either
    assert tuple(await repost(Reposter(pool, collector), 3)) == ("done", None)
    assert sent_notes == ["aggregator-basic-group"]

    # chat history turned on: the group becomes a supergroup with a new id; the post is not lost
    client.upgraded = True
    status, url = await repost(reposter, 4)
    assert status == "done" and url.startswith("https://t.me/c/4315233732/")
    assert client.forwarded[-1] == ("channel", 4)
