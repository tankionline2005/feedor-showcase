import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import notify, settings_store
from app.db import SessionLocal, engine, init_db
from app.models import NotifyChat


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.updates: list[dict] = []

    async def __call__(self, token, method, *, http_timeout=20, **params):
        if method == "getMe":
            if token == "bad":
                raise notify.BotError("Unauthorized")
            return {"username": "km_alerts_bot"}
        if method == "sendMessage":
            if params["chat_id"] == 666:
                raise notify.BotError("Forbidden: bot was blocked by the user")
            self.sent.append((params["chat_id"], params["text"]))
            return {}
        if method == "getUpdates":
            updates, self.updates = self.updates, []
            return updates
        return True


@pytest.fixture
def bot(monkeypatch):
    fake = FakeBot()
    monkeypatch.setattr(notify, "bot_call", fake)
    monkeypatch.setattr("app.web.notify_api.bot_call", fake)
    return fake


async def _reset(token="123:abc", code="secret-code"):
    await engine.dispose()
    await init_db()
    async with SessionLocal() as s:
        await s.execute(delete(NotifyChat))
        await settings_store.update(s, {"notify_bot_token": token, "notify_link_code": code})


def msg(chat_id, text, **chat):
    return {"update_id": chat_id * 10, "message": {"chat": {"id": chat_id, **chat}, "text": text}}


async def test_start_with_code_subscribes_and_stop_unsubscribes(bot):
    await _reset()
    n = notify.Notifier()
    bot.updates = [msg(1, "/start secret-code", first_name="Коля"), msg(2, "/start wrong")]
    assert await n.poll_step() == 0
    async with SessionLocal() as s:
        chats = list((await s.execute(select(NotifyChat))).scalars())
    assert [(c.chat_id, c.title) for c in chats] == [(1, "Коля")]
    assert "подключены" in bot.sent[0][1] and "Войти через Telegram" in bot.sent[1][1]

    bot.updates = [msg(1, "/status"), msg(1, "/stop")]
    await n.poll_step()
    assert "сводка" in bot.sent[2][1]
    async with SessionLocal() as s:
        assert await s.get(NotifyChat, 1) is None


async def test_alerts_are_batched_deduplicated_and_blocked_chats_dropped(bot, monkeypatch):
    await _reset()
    monkeypatch.setattr(notify, "BATCH_WINDOW", 0)
    async with SessionLocal() as s:
        s.add_all([NotifyChat(chat_id=5, title="a"), NotifyChat(chat_id=666, title="blocked")])
        await s.commit()
    n = notify.Notifier()
    n.emit("proxy-dead-1", "first")
    n.emit("proxy-dead-1", "duplicate within cooldown")
    n.emit("account-dead-2", "second")
    sender = asyncio.create_task(n._sender())
    await asyncio.sleep(0.3)
    sender.cancel()
    assert bot.sent == [(5, "first\n\nsecond")]
    async with SessionLocal() as s:
        assert await s.get(NotifyChat, 666) is None
    n.clear("proxy-dead-1")
    n.emit("proxy-dead-1", "again")
    assert n.queue.qsize() == 1


def test_admin_changes_the_bot(bot):
    asyncio.run(_reset(token="123:abc"))
    from app.web.main import app

    with TestClient(app) as client:
        anon = TestClient(app)
        assert anon.put("/api/notify/token", json={"token": "42:good"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200

        async def add_chat():
            async with SessionLocal() as s:
                s.add(NotifyChat(chat_id=5, title="old"))
                await s.commit()

        client.portal.call(add_chat)
        r = client.put("/api/notify/token", json={"token": "bad"})
        assert r.status_code == 422 and "Токен не подошёл" in r.json()["detail"]
        assert len(client.get("/api/notify").json()["chats"]) == 1  # a bad token changes nothing

        state = client.put("/api/notify/token", json={"token": "42:good"}).json()
        assert state["bot_username"] == "km_alerts_bot" and state["chats"] == []  # the old bot's chats are gone
        assert client.post("/api/notify/test").status_code == 409  # nobody subscribed yet

        state = client.put("/api/notify/token", json={"token": ""}).json()
        assert state["configured"] is False

        # the token never shows up among the ordinary settings
        settings = client.get("/api/settings").json()
        assert "notify_bot_token" not in settings
        assert client.put("/api/settings", json={"notify_bot_token": "x"}).status_code == 422


async def test_bot_from_server_config(bot):
    from app.web.notify_api import connect_bot

    await _reset(token="", code="")
    async with SessionLocal() as s:
        s.add(NotifyChat(chat_id=5, title="old"))
        await s.commit()
        await connect_bot(s, "bad")  # not accepted by Telegram: nothing changes
        assert (await settings_store.get_all(s))["notify_bot_token"] == ""

        await connect_bot(s, "42:good")
        cfg = await settings_store.get_all(s)
        assert (cfg["notify_bot_token"], cfg["notify_bot_username"]) == ("42:good", "km_alerts_bot")
        assert cfg["notify_link_code"]
        # recipients of a previous bot cannot hear this one
        assert (await s.execute(select(NotifyChat))).scalars().all() == []

        # the same token again (every restart) keeps recipients and the invite link
        s.add(NotifyChat(chat_id=7, title="me"))
        await s.commit()
        code = cfg["notify_link_code"]
        await connect_bot(s, "42:good")
        assert [c.chat_id for c in (await s.execute(select(NotifyChat))).scalars()] == [7]
        assert (await settings_store.get_all(s))["notify_link_code"] == code
