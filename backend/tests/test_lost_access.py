from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import delete, select, update
from telethon import errors
from telethon.tl import types

from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, Post
from app.worker import collector as collector_module
from app.worker import pool as pool_module
from app.worker.collector import BANNED_EVERYWHERE, Collector
from app.worker.pool import AccountPool, AccountWorker

TG_ID = 779_001


def entity():
    return types.Channel(id=TG_ID, title="AntiWL", photo=types.ChatPhotoEmpty(), date=None, broadcast=True,
                         left=True, access_hash=1, username="shut_out_chan")


class Client:
    def __init__(self, banned: bool):
        self.banned = banned

    async def __call__(self, request):
        if self.banned and type(request).__name__ == "JoinChannelRequest":
            raise errors.ChannelPrivateError(request=None)
        if type(request).__name__ == "GetFullChannelRequest":
            return SimpleNamespace(full_chat=SimpleNamespace(id=TG_ID, participants_count=153), chats=[entity()])
        return None

    async def get_entity(self, username):
        return entity()

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(TG_ID, 1)

    async def get_messages(self, peer, **kwargs):
        if self.banned:
            raise errors.ChannelPrivateError(request=None)
        return [types.Message(id=7, peer_id=types.PeerChannel(TG_ID), date=datetime.now(timezone.utc), message="hi", views=3)]

    async def download_profile_photo(self, entity, file=None):
        return b""


async def _channel(cid):
    async with SessionLocal() as s:
        return await s.get(Channel, cid)


async def test_channel_moves_to_another_account_when_one_is_shut_out(monkeypatch):
    await engine.dispose()
    await init_db()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(pool_module.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(collector_module.random, "uniform", lambda a, b: 0)
    notes: list[str] = []
    monkeypatch.setattr(collector_module.notifier, "emit", lambda key, text, **kw: notes.append(key))

    async with SessionLocal() as s:
        old = await s.scalar(select(Channel.id).where(Channel.tg_id == TG_ID))
        if old:
            await s.execute(delete(Post).where(Post.channel_id == old))
            await s.execute(delete(Channel).where(Channel.id == old))
        # other tests' leftovers must not take the join slots or look alive
        await s.execute(delete(Channel).where(Channel.status.in_(("pending", "restricted"))))
        await s.execute(update(Account).values(enabled=False))
        banned_acc = Account(name="Андрей", session="", status="active", max_channels=100)
        s.add(banned_acc)
        await s.flush()
        ch = Channel(link="@shut_out_chan", username="shut_out_chan", tg_id=TG_ID, title="AntiWL", status="active",
                     account_id=banned_acc.id, last_msg_id=5)
        s.add(ch)
        await s.commit()

    pool = AccountPool()
    banned_w = AccountWorker(banned_acc, Client(banned=True))
    pool.workers[banned_acc.id] = banned_w
    collector = Collector(pool)

    # polling hits "private" on a public channel: the account is shut out, the channel waits for another
    await collector.poll_step()
    waiting = await _channel(ch.id)
    assert (waiting.status, waiting.account_id, waiting.banned_for) == ("pending", None, [banned_acc.id])
    assert "Андрей" in waiting.status_detail
    assert notes == [f"channel-banned-{ch.id}-{banned_acc.id}"]

    # no other account yet: the banned one is never picked again, and the reason is said plainly
    await collector.join_step()
    assert (await _channel(ch.id)).status_detail == BANNED_EVERYWHERE

    # another account appears and takes the channel over
    async with SessionLocal() as s:
        other_acc = Account(name="Гена", session="", status="active", max_channels=100)
        s.add(other_acc)
        await s.commit()
    other_w = AccountWorker(other_acc, Client(banned=False))
    pool.workers[other_acc.id] = other_w
    await collector.join_step()
    joined = await _channel(ch.id)
    assert (joined.status, joined.account_id) == ("active", other_acc.id)

    async with SessionLocal() as s:
        await s.execute(update(Account).where(Account.id.in_([banned_acc.id, other_acc.id])).values(enabled=False))
        await s.commit()


class ForgetfulClient(Client):
    """Just restarted: it cannot find the channel by its id, nor by its name, while polling; joining
    finds it again."""

    def __init__(self):
        super().__init__(banned=False)
        self.misses = 2

    async def get_input_entity(self, peer):
        if self.misses:
            self.misses -= 1
            raise ValueError(f'No user has "{peer}" as username')
        return await super().get_input_entity(peer)


async def test_a_channel_the_account_cannot_find_once_is_not_given_up(monkeypatch):
    await engine.dispose()
    await init_db()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(pool_module.random, "uniform", lambda a, b: 0)
    monkeypatch.setattr(collector_module.random, "uniform", lambda a, b: 0)
    notes: list[str] = []
    monkeypatch.setattr(collector_module.notifier, "emit", lambda key, text, **kw: notes.append(key))

    async with SessionLocal() as s:
        old = await s.scalar(select(Channel.id).where(Channel.tg_id == TG_ID))
        if old:
            await s.execute(delete(Post).where(Post.channel_id == old))
            await s.execute(delete(Channel).where(Channel.id == old))
        await s.execute(delete(Channel).where(Channel.status.in_(("pending", "restricted"))))
        await s.execute(update(Account).values(enabled=False))
        acc = Account(name="Андрей", session="", status="active", max_channels=100)
        s.add(acc)
        await s.flush()
        ch = Channel(link="@shut_out_chan", username="shut_out_chan", tg_id=TG_ID, title="AntiWL", status="active",
                     account_id=acc.id, last_msg_id=5)
        s.add(ch)
        await s.commit()

    pool = AccountPool()
    pool.workers[acc.id] = AccountWorker(acc, ForgetfulClient())
    collector = Collector(pool)

    # not "Канал не найден", no message to the admin: the channel is joined anew by the same account
    await collector.poll_step()
    waiting = await _channel(ch.id)
    assert (waiting.status, waiting.status_detail, waiting.account_id) == ("pending", None, acc.id)
    assert notes == []
    await collector.join_step()
    back = await _channel(ch.id)
    assert (back.status, back.account_id) == ("active", acc.id)

    async with SessionLocal() as s:
        await s.execute(update(Account).where(Account.id == acc.id).values(enabled=False))
        await s.commit()
