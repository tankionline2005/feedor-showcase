from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, update
from telethon.tl import types

from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, Post, Project, ProjectChannel, User
from app.tg.parse import public_username, public_usernames
from app.worker import collector as collector_module
from app.worker import pool as pool_module
from app.worker.collector import MOVED, OWN_BACKFILL_DAYS, Collector, JoinError
from app.worker.pool import AccountPool, AccountWorker

from test_user_channels import ctx, run, sign_in  # noqa: F401  (ctx is a fixture)

TG_ID = 779_101


def chat(id=TG_ID, username=None, usernames=(), min=False):
    return types.Channel(id=id, title="Names", photo=types.ChatPhotoEmpty(), date=None, broadcast=True, left=False,
                         access_hash=1, username=username, min=min,
                         usernames=[types.Username(username=n, active=a) for n, a in usernames] or None)


def test_every_public_username_main_first():
    assert public_usernames(chat(username="main")) == ["main"]
    several = chat(usernames=[("first", True), ("sleeping", False), ("second", True)])
    assert public_usernames(several) == ["first", "second"] and public_username(several) == "first"
    assert public_usernames(chat()) == [] and public_username(chat()) is None  # private


class Client:
    """What the account sees: `entity` by its id, `names` by @username, `dialogs` it is in."""

    def __init__(self, entity, names=None, dialogs=(), cached=False):
        self.entity, self.names, self.dialogs, self.cached = entity, names or {}, list(dialogs), cached

    async def __call__(self, request):
        return None

    async def get_entity(self, what):
        if isinstance(what, types.PeerChannel):
            if self.cached:
                return self.entity
            raise ValueError("not in memory")
        if what in self.names:
            return self.names[what]
        raise ValueError(f'No user has "{what}" as username')

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(TG_ID, 1)

    async def get_dialogs(self, limit=None):
        return [SimpleNamespace(entity=e) for e in self.dialogs]

    async def get_messages(self, peer, **kwargs):
        m = types.Message(id=9, peer_id=types.PeerChannel(TG_ID), date=datetime.now(timezone.utc), message="hi")
        m._chat = self.entity  # the channel comes with its messages, as Telegram sends it
        return [m]


async def _setup(**channel) -> tuple[Account, Channel]:
    await engine.dispose()
    await init_db()
    async with SessionLocal() as s:
        old = await s.scalar(select(Channel.id).where(Channel.tg_id == TG_ID))
        if old:
            await s.execute(delete(Post).where(Post.channel_id == old))
            await s.execute(delete(ProjectChannel).where(ProjectChannel.channel_id == old))
            await s.execute(delete(Channel).where(Channel.id == old))
        await s.execute(delete(Channel).where(Channel.status.in_(("pending", "restricted"))))
        await s.execute(update(Account).values(enabled=False))
        acc = Account(name="Имена", session="", status="active", max_channels=100)
        s.add(acc)
        await s.flush()
        ch = Channel(**{"link": "@oldname", "username": "oldname", "tg_id": TG_ID, "title": "Names",
                        "status": "active", "account_id": acc.id, "last_msg_id": 5, **channel})
        s.add(ch)
        await s.commit()
        return acc, ch


async def _channel(cid):
    async with SessionLocal() as s:
        return await s.get(Channel, cid)


async def test_polling_keeps_the_usernames_up_to_date(monkeypatch):
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    acc, ch = await _setup()
    pool = AccountPool()
    client = Client(chat(usernames=[("newmain", True), ("oldname", True)]))
    pool.workers[acc.id] = AccountWorker(acc, client)
    collector = Collector(pool)

    # the main name changed, the old one stayed as a second one
    await collector.poll_step()
    got = await _channel(ch.id)
    assert (got.username, got.usernames) == ("newmain", ["newmain", "oldname"])

    # made private: no public names at all; then public again
    client.entity = chat()
    await collector._set_channel(ch.id, last_polled_at=None)
    await collector.poll_step()
    assert ((await _channel(ch.id)).username, (await _channel(ch.id)).usernames) == (None, [])
    client.entity = chat(username="back")
    await collector._set_channel(ch.id, last_polled_at=None)
    await collector.poll_step()
    assert (await _channel(ch.id)).usernames == ["back"]

    # Telegram says the channel changed: its info is read again in the next round
    await collector._set_channel(ch.id, info_updated_at=datetime.now())
    await collector.channel_changed(TG_ID)
    assert (await _channel(ch.id)).info_updated_at is None


async def test_a_renamed_channel_is_found_and_a_name_it_dropped_is_not_taken_for_it(monkeypatch):
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    acc, ch = await _setup(usernames=["oldname", "alias"])
    pool = AccountPool()
    collector = Collector(pool)

    # its old name belongs to another channel now; the alias is still its own
    w = AccountWorker(acc, Client(chat(), names={"oldname": chat(id=1, username="oldname"),
                                                 "alias": chat(usernames=[("alias", True)])}))
    pool.workers[acc.id] = w
    assert (await collector._find(w, await _channel(ch.id))).id == TG_ID

    # no name leads to it (made private): the account is still inside — found among its dialogs
    w.client = Client(chat(), names={"oldname": chat(id=1, username="oldname")}, dialogs=[chat(id=5), chat()])
    assert (await collector._find(w, await _channel(ch.id))).id == TG_ID

    # nowhere: said plainly, not «Канал не найден», and never the other channel that took the name
    w.client = Client(chat(), names={"oldname": chat(id=1, username="oldname")})
    with pytest.raises(JoinError, match=MOVED.split(" —")[0]):
        await collector._find(w, await _channel(ch.id))


async def test_a_project_channel_brings_a_month(monkeypatch):
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(collector_module.random, "uniform", lambda a, b: 0)
    acc, ch = await _setup(status="pending", account_id=None)
    async with SessionLocal() as s:
        user = User(tg_user_id=779_102, first_name="own")
        s.add(user)
        await s.flush()
        project = Project(owner_id=user.id, plan="pro")
        s.add(project)
        await s.flush()
        s.add(ProjectChannel(project_id=project.id, channel_id=ch.id, own=True))
        await s.commit()
    pool = AccountPool()
    pool.workers[acc.id] = AccountWorker(acc, Client(chat(username="oldname"), cached=True))
    collector = Collector(pool)
    days: list[int] = []

    async def joined(w, channel, backfill_days):
        days.append(backfill_days)

    monkeypatch.setattr(collector, "_join", joined)
    await collector.join_step()
    assert days == [OWN_BACKFILL_DAYS]


def test_a_channel_is_found_by_any_of_its_usernames(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9931)

    async def existing():
        async with SessionLocal() as s:
            await s.execute(delete(Channel).where(Channel.link == "@uc_main_name"))
            s.add(Channel(link="@uc_main_name", username="uc_main_name", usernames=["uc_main_name", "uc_second_name"],
                          status="active", title="Two names"))
            await s.commit()

    async def count():
        async with SessionLocal() as s:
            return await s.scalar(select(func.count()).select_from(Channel).where(Channel.title == "Two names"))

    run(client, existing)
    added = owner.post("/api/channels", json={"links": "@uc_second_name"}).json()
    assert added["added"] and run(client, count) == 1  # the same channel, not a new one
    listed = owner.get("/api/channels").json()["items"]
    assert next(c for c in listed if c["title"] == "Two names")["usernames"] == ["uc_main_name", "uc_second_name"]
