from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import delete, select, update
from telethon.tl import types

from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, Post, utcnow
from app.tg.parse import restriction_text
from app.worker import pool as pool_module
from app.worker.collector import Collector
from app.worker.pool import AccountPool, AccountWorker

PLACEHOLDER = "This channel can’t be displayed because it violated local laws (Португалия)."
BLOCK = [types.RestrictionReason(platform="all", reason="geo", text=PLACEHOLDER)]


def message(id, text, restricted):
    return types.Message(
        id=id, peer_id=types.PeerChannel(777), date=datetime.now(timezone.utc), message=text,
        restriction_reason=BLOCK if restricted else None, views=10,
    )


class FakeClient:
    """A Telegram account that sees the channel either normally or blocked by local laws."""

    def __init__(self, restricted: bool):
        self.restricted = restricted
        self.requests: list[str] = []

    def entity(self):
        return types.Channel(
            id=777, title="РИА Новости", photo=types.ChatPhotoEmpty(), date=None, broadcast=True, left=True,
            access_hash=1, username="ria_test", restricted=self.restricted,
            restriction_reason=BLOCK if self.restricted else None,
        )

    async def __call__(self, request):
        self.requests.append(type(request).__name__)
        if type(request).__name__ == "GetFullChannelRequest":
            return SimpleNamespace(full_chat=SimpleNamespace(id=777, participants_count=3_000_000), chats=[self.entity()])
        return None

    async def get_entity(self, username):
        if username != "ria_test":
            raise ValueError(username)
        return self.entity()

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(777, 1)

    async def get_messages(self, entity, **kwargs):
        text = PLACEHOLDER if self.restricted else "Свежие новости"
        return [message(5, text, self.restricted)]

    async def download_profile_photo(self, entity, file=None):
        return b""

    async def disconnect(self):
        pass


def test_restriction_text():
    assert restriction_text(message(1, PLACEHOLDER, True)) == PLACEHOLDER
    assert restriction_text(message(1, PLACEHOLDER, False)) == PLACEHOLDER  # placeholder without a reason
    assert restriction_text(message(1, "Обычный пост про local laws", False)) is None
    ios_only = types.Message(id=1, peer_id=types.PeerChannel(1), date=None, message="18+",
                             restriction_reason=[types.RestrictionReason(platform="ios", reason="porn", text="x")])
    assert restriction_text(ios_only) is None  # one-platform blocks do not hide content from us
    # "sensitive" (18+, gambling) is the apps' content filter: the posts still come through
    sensitive = types.Channel(id=1, title="Плагины", photo=types.ChatPhotoEmpty(), date=None, broadcast=True,
                              restriction_reason=[types.RestrictionReason(
                                  platform="all", reason="sensitive",
                                  text="This channel can't be displayed because it may contain sensitive content.")])
    assert restriction_text(sensitive) is None
    geo = types.Channel(id=1, title="Новости", photo=types.ChatPhotoEmpty(), date=None, broadcast=True,
                        restriction_reason=[types.RestrictionReason(
                            platform="all", reason="other", text="This channel can't be displayed because it violated "
                                                                 "local laws (Portugal).")])
    assert restriction_text(geo).endswith("(Portugal).")


async def _setup(monkeypatch):
    await engine.dispose()
    await init_db()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(pool_module.random, "uniform", lambda a, b: 0)
    async with SessionLocal() as s:
        await s.execute(delete(Channel).where(Channel.username == "ria_test"))
        await s.execute(update(Channel).where(Channel.status.in_(("pending", "restricted"))).values(status="paused"))
        await s.execute(delete(Account))
        blocked_acc = Account(name="Аккаунт из РФ", session="", status="active")
        free_acc = Account(name="Аккаунт из NL", session="", status="active")
        s.add_all([blocked_acc, free_acc])
        await s.flush()
        ch = Channel(link="@ria_test", username="ria_test", status="pending")
        s.add(ch)
        await s.commit()
    pool = AccountPool()
    collector = Collector(pool)
    return pool, collector, blocked_acc, free_acc, ch.id


async def get_channel(channel_id):
    async with SessionLocal() as s:
        return await s.get(Channel, channel_id)


async def test_blocked_account_hands_channel_to_another(monkeypatch):
    pool, collector, blocked_acc, free_acc, channel_id = await _setup(monkeypatch)
    blocked_client = FakeClient(restricted=True)
    pool.workers[blocked_acc.id] = AccountWorker(blocked_acc, blocked_client)

    await collector.join_step()
    ch = await get_channel(channel_id)
    assert ch.restricted_for == [blocked_acc.id]
    assert ch.status == "pending" and "Аккаунт из РФ" in ch.status_detail  # another account exists
    assert "(Португалия)" in ch.status_detail
    assert ch.account_id is None
    assert "LeaveChannelRequest" in blocked_client.requests

    # only the blocked account is connected: the channel waits instead of retrying it
    pool.workers[blocked_acc.id].next_join_at = 0
    await collector.join_step()
    assert blocked_client.requests.count("JoinChannelRequest") == 1

    free_client = FakeClient(restricted=False)
    pool.workers[free_acc.id] = AccountWorker(free_acc, free_client)
    await collector.join_step()
    ch = await get_channel(channel_id)
    assert (ch.status, ch.account_id, ch.status_detail) == ("active", free_acc.id, None)
    assert ch.subscribers == 3_000_000


async def test_placeholders_are_dropped_and_channel_waits_for_a_new_account(monkeypatch):
    pool, collector, only_acc, other_acc, channel_id = await _setup(monkeypatch)
    async with SessionLocal() as s:
        await s.execute(delete(Account).where(Account.id == other_acc.id))
        await s.execute(update(Channel).where(Channel.id == channel_id).values(
            status="active", account_id=only_acc.id, tg_id=777))
        s.add_all([
            Post(channel_id=channel_id, msg_id=1, posted_at=utcnow(), text=PLACEHOLDER, repost_status="failed"),
            Post(channel_id=channel_id, msg_id=2, posted_at=utcnow(), text="настоящий пост"),
        ])
        await s.commit()
    client = FakeClient(restricted=True)
    w = AccountWorker(only_acc, client)
    pool.workers[only_acc.id] = w
    await collector.refresh_map()

    # a new post arrives as a placeholder through the update stream
    await collector.on_messages(w, 777, [message(3, PLACEHOLDER, True)])
    ch = await get_channel(channel_id)
    assert ch.status == "restricted" and "новый аккаунт" in ch.status_detail
    async with SessionLocal() as s:
        texts = (await s.execute(select(Post.text).where(Post.channel_id == channel_id))).scalars().all()
    assert texts == ["настоящий пост"]
    assert "LeaveChannelRequest" in client.requests

    # a new account appears: the restricted channel is retried with it
    async with SessionLocal() as s:
        fresh = Account(name="Новый", session="", status="active")
        s.add(fresh)
        await s.commit()
    pool.workers[fresh.id] = AccountWorker(fresh, FakeClient(restricted=False))
    await collector.join_step()
    ch = await get_channel(channel_id)
    assert (ch.status, ch.account_id) == ("active", fresh.id)
    assert ch.restricted_for == [only_acc.id]


TOS = "This channel can’t be displayed because it violated exteraGram's Terms of Service."


async def test_a_block_by_our_app_is_not_tried_on_every_account(monkeypatch):
    """No country at the end, the app's Terms of Service: every account connects as the same app,
    so one refusal is enough — the others would only join and leave in vain."""
    import app.worker.collector as collector_module

    pool, collector, first_acc, second_acc, channel_id = await _setup(monkeypatch)
    monkeypatch.setattr(collector_module, "notifier", SimpleNamespace(emit=lambda *a, **k: None))
    monkeypatch.setattr(collector_module.random, "uniform", lambda a, b: 0)
    tos_block = [types.RestrictionReason(platform="all", reason="terms", text=TOS)]
    client = FakeClient(restricted=True)
    client.entity = lambda: types.Channel(
        id=777, title="Плагины", photo=types.ChatPhotoEmpty(), date=None, broadcast=True, left=True,
        access_hash=1, username="ria_test", restricted=True, restriction_reason=tos_block,
    )
    pool.workers[first_acc.id] = AccountWorker(first_acc, client)

    await collector.join_step()
    ch = await get_channel(channel_id)
    assert ch.status == "restricted" and "официальном Telegram" in ch.status_detail
    assert sorted(ch.restricted_for) == sorted([first_acc.id, second_acc.id])  # the idle account too
    # the other account comes online: it is not sent in
    other = FakeClient(restricted=False)
    pool.workers[second_acc.id] = AccountWorker(second_acc, other)
    await collector.join_step()
    assert "JoinChannelRequest" not in other.requests  # the second account is not sent in
