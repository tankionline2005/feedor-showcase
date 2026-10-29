import asyncio
from datetime import datetime, timezone

import pytest
from types import SimpleNamespace

from sqlalchemy import delete, select, update
from telethon.tl import types

from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, Post, Proxy
from app.proxies import ProxyConfig
from app.worker import collector as collector_module
from app.worker import pool as pool_module
from app.worker.collector import Collector
from app.worker.pool import AccountPool, AccountWorker

TG_ID = 781_001


def entity():
    return types.Channel(id=TG_ID, title="Alerts", photo=types.ChatPhotoEmpty(), date=None, broadcast=True,
                         left=True, access_hash=1, username="conn_lost_chan")


class Client:
    def __init__(self, broken=lambda: ConnectionError("Cannot send requests while disconnected")):
        self.connected = False  # the connection is down until the test brings it back
        self.broken = broken  # what a request raises while it is down

    def is_connected(self):
        return self.connected

    async def __call__(self, request):
        if type(request).__name__ == "GetFullChannelRequest":
            return SimpleNamespace(full_chat=SimpleNamespace(id=TG_ID, participants_count=15_000), chats=[entity()])
        return None

    async def get_entity(self, username):
        if not self.connected:
            raise self.broken()  # as Telethon says it
        return entity()

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(TG_ID, 1)

    async def get_messages(self, peer, **kwargs):
        return [types.Message(id=3, peer_id=types.PeerChannel(TG_ID), date=datetime.now(timezone.utc), message="hi", views=1)]

    async def download_profile_photo(self, entity, file=None):
        return b""

    async def disconnect(self):
        self.connected = False


async def _channel(cid):
    async with SessionLocal() as s:
        return await s.get(Channel, cid)


@pytest.mark.parametrize("broken", [
    lambda: ConnectionError("Cannot send requests while disconnected"),
    lambda: asyncio.IncompleteReadError(b"", 4),  # "0 bytes read on a total of 4 expected bytes"
    lambda: TimeoutError(),
])
async def test_lost_connection_is_not_the_channels_error(monkeypatch, broken):
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
        acc = Account(name="Гена", session="", status="active", max_channels=100)
        s.add(acc)
        await s.flush()
        ch = Channel(link="@conn_lost_chan", username="conn_lost_chan", status="pending")
        s.add(ch)
        await s.commit()

    pool = AccountPool()
    client = Client(broken)
    w = AccountWorker(acc, client)
    pool.workers[acc.id] = w
    collector = Collector(pool)

    # the account's connection drops right while it joins: the channel just waits for the next try
    await collector.join_step()
    waiting = await _channel(ch.id)
    assert (waiting.status, waiting.status_detail) == ("pending", None)

    # connection back: the channel is joined as usual
    client.connected = True
    w.next_join_at = 0
    await collector.join_step()
    joined = await _channel(ch.id)
    assert (joined.status, joined.account_id) == ("active", acc.id)

    async with SessionLocal() as s:
        await s.execute(update(Account).where(Account.id == acc.id).values(enabled=False))
        await s.commit()


async def test_sync_restarts_an_account_whose_client_gave_up(monkeypatch):
    await engine.dispose()
    await init_db()
    async with SessionLocal() as s:
        await s.execute(update(Account).values(enabled=False))
        proxy = Proxy(kind="socks5", host="conn-lost.example", port=1080, key="socks5://conn-lost.example:1080",
                      status="ok", enabled=True)
        s.add(proxy)
        await s.flush()
        acc = Account(name="Roman", session="", status="active", proxy_id=proxy.id)
        s.add(acc)
        await s.commit()
        await s.refresh(proxy)
        await s.refresh(acc)

    started: list[int] = []

    async def fake_start(self, account, target):
        started.append(account.id)

    monkeypatch.setattr(AccountPool, "_start", fake_start)
    pool = AccountPool()
    client = Client()
    client.connected = True
    w = AccountWorker(acc, client)
    w.config = pool_module._config_key(acc, ProxyConfig.of(proxy))
    pool.workers[acc.id] = w

    # connected: left alone
    await pool.sync()
    assert started == [] and pool.workers[acc.id] is w

    # Telethon stopped reconnecting: the old client goes and the account starts again
    client.connected = False
    await pool.sync()
    assert acc.id not in pool.workers and started == [acc.id]

    async with SessionLocal() as s:
        await s.execute(update(Account).where(Account.id == acc.id).values(enabled=False, proxy_id=None))
        await s.execute(delete(Proxy).where(Proxy.id == proxy.id))
        await s.commit()
