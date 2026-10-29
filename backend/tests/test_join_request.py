from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import delete, select
from telethon import errors
from telethon.tl import types

from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, Post
from app.worker import collector as collector_module
from app.worker import pool as pool_module
from app.worker.collector import JOIN_REQUESTED, Collector
from app.worker.pool import AccountPool, AccountWorker

HASH = "approvalHash123"
TG_ID = 778_001


def private_channel():
    return types.Channel(id=TG_ID, title="Закрытый", photo=types.ChatPhotoEmpty(), date=None, broadcast=True,
                         access_hash=1)


class ApprovalClient:
    """A channel whose invite link needs an admin's approval."""

    def __init__(self):
        self.approved = False
        self.imports = 0

    async def __call__(self, request):
        name = type(request).__name__
        if name == "CheckChatInviteRequest":
            if self.approved:
                return types.ChatInviteAlready(chat=private_channel())
            return types.ChatInvite(title="Закрытый", photo=types.PhotoEmpty(id=0), participants_count=10,
                                    broadcast=True, request_needed=True, color=0)
        if name == "ImportChatInviteRequest":
            self.imports += 1
            raise errors.InviteRequestSentError(request=None)
        if name == "GetFullChannelRequest":
            return SimpleNamespace(full_chat=SimpleNamespace(id=TG_ID, participants_count=1000), chats=[private_channel()])
        return None

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(TG_ID, 1)

    async def get_messages(self, entity, **kwargs):
        return [types.Message(id=3, peer_id=types.PeerChannel(TG_ID), date=datetime.now(timezone.utc), message="hi", views=5)]

    async def download_profile_photo(self, entity, file=None):
        return b""


async def _channel(cid):
    async with SessionLocal() as s:
        return await s.get(Channel, cid)


async def test_join_request_waits_for_approval_then_joins(monkeypatch):
    await engine.dispose()
    await init_db()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(pool_module.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(collector_module.random, "uniform", lambda a, b: 0)
    async with SessionLocal() as s:
        for old in (await s.execute(select(Channel.id).where(Channel.invite_hash == HASH))).scalars():
            await s.execute(delete(Post).where(Post.channel_id == old))
            await s.execute(delete(Channel).where(Channel.id == old))
        # other tests' leftovers must not take the join slot
        await s.execute(delete(Channel).where(Channel.status.in_(("pending", "restricted"))))
        acc = Account(name="approval", session="", status="active", max_channels=100)
        s.add(acc)
        await s.flush()
        ch = Channel(link=f"t.me/+{HASH}", invite_hash=HASH, status="pending")
        s.add(ch)
        await s.commit()

    pool = AccountPool()
    client = ApprovalClient()
    worker = AccountWorker(acc, client)
    pool.workers[acc.id] = worker
    collector = Collector(pool)

    def next_check():
        collector._approval_check_at.clear()  # skip the 10-minute wait
        worker.next_join_at = 0  # and the pause between joins

    # the request goes out once; the channel waits instead of failing
    await collector.join_step()
    waiting = await _channel(ch.id)
    assert (waiting.status, waiting.status_detail, waiting.account_id) == ("pending", JOIN_REQUESTED, acc.id)
    assert client.imports == 1

    # a later check does not send the request again
    next_check()
    await collector.join_step()
    assert client.imports == 1 and (await _channel(ch.id)).status == "pending"

    # the admin approves: the next check joins the channel
    client.approved = True
    next_check()
    await collector.join_step()
    joined = await _channel(ch.id)
    assert (joined.status, joined.tg_id, joined.status_detail) == ("active", TG_ID, None)
