from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import delete, select, update
from telethon import errors
from telethon.tl import types

from app import settings_store
from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, ChannelReserve, Post, utcnow
from app.services import account_loads, release_channels
from app.worker import collector as collector_module
from app.worker import pool as pool_module
from app.worker import reserves as reserves_module
from app.worker.collector import Collector
from app.worker.pool import AccountPool, AccountWorker

HASH = "reserveHash777"
TG_ID = 782_001


def chat():
    return types.Channel(id=TG_ID, title="Закрытый клуб", photo=types.ChatPhotoEmpty(), date=None, broadcast=True,
                         access_hash=1)


class Client:
    """One account's view of the private channel."""

    def __init__(self):
        self.member = False
        self.kicked = False  # the channel admin removed this account
        self.link_dead = False
        self.needs_approval = False
        self.calls: list[str] = []

    async def __call__(self, request):
        name = type(request).__name__
        self.calls.append(name)
        if name == "CheckChatInviteRequest":
            if self.link_dead:
                raise errors.InviteHashExpiredError(request=None)
            if self.member:
                return types.ChatInviteAlready(chat=chat())
            return types.ChatInvite(title="Закрытый клуб", photo=types.PhotoEmpty(id=0), participants_count=10,
                                    broadcast=True, request_needed=self.needs_approval, color=0)
        if name == "ImportChatInviteRequest":
            if self.needs_approval:
                raise errors.InviteRequestSentError(request=None)
            self.member = True
            return types.messages.ChatInviteJoinResultOk(updates=SimpleNamespace(chats=[chat()]))
        if name == "LeaveChannelRequest":
            self.member = False
        return None

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(TG_ID, 1)

    async def get_messages(self, peer, **kwargs):
        if self.kicked:
            raise errors.ChannelPrivateError(request=None)
        return [types.Message(id=9, peer_id=types.PeerChannel(TG_ID), date=datetime.now(timezone.utc), message="x", views=1)]


async def _setup(monkeypatch):
    await engine.dispose()
    await init_db()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    # the lower bound: no jitter between requests, the shortest wait before a backup joins
    monkeypatch.setattr(pool_module.random, "uniform", lambda a, b: a)
    alerts: list[str] = []
    monkeypatch.setattr(reserves_module.notifier, "emit", lambda key, text, **kw: alerts.append(key))
    monkeypatch.setattr(collector_module.notifier, "emit", lambda key, text, **kw: alerts.append(key))
    async with SessionLocal() as s:
        old = await s.scalar(select(Channel.id).where(Channel.tg_id == TG_ID))
        if old:
            await s.execute(delete(Post).where(Post.channel_id == old))
            await s.execute(delete(Channel).where(Channel.id == old))
        await s.execute(update(Account).values(enabled=False))
        await s.execute(update(Channel).where(Channel.account_id.is_not(None)).values(account_id=None))
        # other tests' channels waiting to be joined would be joined here too
        await s.execute(delete(Channel).where(Channel.status.in_(("pending", "restricted"))))
        await settings_store.update(s, {"join_interval_sec": 10})
        accs = [Account(name=n, session="", status="active", max_channels=100) for n in ("Main", "SameProxy", "OtherProxy")]
        s.add_all(accs)
        await s.flush()
        ch = Channel(link=f"t.me/+{HASH}", invite_hash=HASH, tg_id=TG_ID, title="Закрытый клуб", status="active",
                     account_id=accs[0].id, last_msg_id=9)
        s.add(ch)
        await s.commit()
    pool = AccountPool()
    clients = {}
    for acc, proxy in zip(accs, (1, 1, 2)):
        clients[acc.id] = Client()
        w = AccountWorker(acc, clients[acc.id])
        w.proxy_id = proxy
        pool.workers[acc.id] = w
    clients[accs[0].id].member = True
    return pool, Collector(pool), [a.id for a in accs], ch.id, clients, alerts


async def _reserves(channel_id):
    async with SessionLocal() as s:
        return list((await s.execute(select(ChannelReserve).where(ChannelReserve.channel_id == channel_id))).scalars())


async def _channel(cid):
    async with SessionLocal() as s:
        return await s.get(Channel, cid)


async def _make_due(channel_id):
    async with SessionLocal() as s:
        await s.execute(update(ChannelReserve).where(ChannelReserve.channel_id == channel_id)
                        .values(due_at=utcnow() - timedelta(seconds=1)))
        await s.commit()


def _ready(pool):
    for w in pool.workers.values():
        w.next_join_at = 0


async def test_backup_joins_later_through_another_proxy_and_takes_over(monkeypatch):
    pool, collector, (main, same_proxy, other_proxy), cid, clients, alerts = await _setup(monkeypatch)
    reserves = collector.reserves

    # a backup is planned on another proxy than the main account's, hours later, not at once
    await reserves.plan()
    [r] = await _reserves(cid)
    assert (r.account_id, r.status) == (other_proxy, "planned")
    assert timedelta(minutes=59) < r.due_at - utcnow() <= timedelta(hours=1)
    await reserves.act()
    assert clients[other_proxy].calls == []  # not due yet

    await _make_due(cid)
    await reserves.act()
    [r] = await _reserves(cid)
    assert r.status == "member" and clients[other_proxy].member
    async with SessionLocal() as s:
        loads = await account_loads(s)
    assert loads[other_proxy] == 1 and loads[main] == 1  # a backup takes a place on its account

    # while the main account is fine the backup only checks now and then that it is still inside
    await _make_due(cid)
    await reserves.act()
    assert (await _reserves(cid))[0].status == "member"

    # the main account is removed from the channel: the backup takes over at once
    clients[main].kicked = True
    await collector.poll_step()
    ch = await _channel(cid)
    assert (ch.status, ch.account_id) == ("active", other_proxy)
    assert main in ch.banned_for
    assert "reserve-took-over-%d" % cid in alerts
    assert await _reserves(cid) == []

    # and a new backup is planned — not the account that was removed
    await reserves.plan()
    [r] = await _reserves(cid)
    assert r.account_id == same_proxy


async def test_backup_waits_for_approval_and_reports_a_dead_link(monkeypatch):
    pool, collector, (main, same_proxy, other_proxy), cid, clients, alerts = await _setup(monkeypatch)
    reserves = collector.reserves
    await reserves.plan()
    await _make_due(cid)

    clients[other_proxy].needs_approval = True
    await reserves.act()
    [r] = await _reserves(cid)
    assert r.status == "requested"
    # the request is not sent again while it waits; once approved the backup is a member
    await _make_due(cid)
    _ready(pool)
    await reserves.act()
    assert clients[other_proxy].calls.count("ImportChatInviteRequest") == 1
    clients[other_proxy].member = True
    await _make_due(cid)
    await reserves.act()
    assert (await _reserves(cid))[0].status == "member"

    # the backup itself gets removed: it is dropped and another one is planned
    clients[other_proxy].kicked = True
    await _make_due(cid)
    await reserves.act()
    assert await _reserves(cid) == [] and other_proxy in (await _channel(cid)).banned_for
    await reserves.plan()
    [r] = await _reserves(cid)
    assert r.account_id == same_proxy

    # the invite link stopped working: the admin hears it once
    clients[same_proxy].link_dead = True
    await _make_due(cid)
    _ready(pool)
    await reserves.act()
    assert (await _reserves(cid))[0].status == "failed"
    await _make_due(cid)
    _ready(pool)
    await reserves.act()
    assert alerts.count(f"reserve-link-{cid}") == 1


async def test_dead_main_account_is_replaced_by_the_backup_without_a_rejoin(monkeypatch):
    pool, collector, (main, same_proxy, other_proxy), cid, clients, alerts = await _setup(monkeypatch)
    await collector.reserves.plan()
    await _make_due(cid)
    await collector.reserves.act()

    # the main account's session died: its channels go back to the queue
    async with SessionLocal() as s:
        await release_channels(s, main)
        await s.commit()
    del pool.workers[main]
    await collector.join_step()
    ch = await _channel(cid)
    assert (ch.status, ch.account_id) == ("active", other_proxy)
    assert "ImportChatInviteRequest" not in clients[same_proxy].calls  # nobody had to join again


async def test_fewer_backups_wanted(monkeypatch):
    pool, collector, accounts, cid, clients, alerts = await _setup(monkeypatch)
    # always 2 accounts in a private channel now, but the planner still trims what is more than wanted
    monkeypatch.setattr(reserves_module, "PRIVATE_ACCOUNTS", 3)
    await collector.reserves.plan()
    assert len(await _reserves(cid)) == 2
    monkeypatch.setattr(reserves_module, "PRIVATE_ACCOUNTS", 1)
    await collector.reserves.plan()
    assert await _reserves(cid) == []

    monkeypatch.setattr(reserves_module, "PRIVATE_ACCOUNTS", 2)
    async with SessionLocal() as s:
        await s.execute(update(Account).where(Account.id.in_(accounts)).values(enabled=False))
        await s.commit()
