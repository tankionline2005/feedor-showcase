import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import originals, settings_store
from app.db import SessionLocal, engine
from app.logins import confirm_login
from app.models import Channel, LoginRequest, Post, Project, User, utcnow
from app.notify import BotError
from app.web import auth

GROUP = -1003000000001


class FakeBot:
    def __init__(self, origin: str = "channel", blocked: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.origin = origin
        self.blocked = blocked

    async def __call__(self, token, method, **params):
        if self.blocked:
            raise BotError("Forbidden: bot was blocked by the user")
        self.calls.append((method, params))
        if method == "forwardMessage":
            return {"message_id": 77, "forward_origin": {"type": self.origin}}
        return True

    def methods(self):
        return [m for m, _ in self.calls]


def test_group_message_from_repost_links():
    assert originals.group_message("https://t.me/c/3000000001/42") == (GROUP, 42)
    assert originals.group_message("https://t.me/my_aggregator/7") == ("@my_aggregator", 7)
    assert originals.group_message(None) is None
    assert originals.group_message("https://example.com/x/1") is None


@pytest.mark.parametrize("copy, count, origin, expected", [
    (True, 1, "channel", ["copyMessage"]),  # our own text copy: never forwarded, it would show the account
    (False, 3, "channel", ["forwardMessages"]),  # an album goes as one group
    (None, 1, "channel", ["forwardMessage"]),  # unknown, and it is a real forward: done
    (None, 1, "user", ["forwardMessage", "deleteMessage", "copyMessage"]),  # unknown, and it was our copy
    (None, 2, "channel", ["forwardMessage", "deleteMessage", "forwardMessages"]),
])
def test_forward_or_copy(monkeypatch, copy, count, origin, expected):
    bot = FakeBot(origin)
    monkeypatch.setattr(originals, "bot_call", bot)
    asyncio.run(originals.send_original("t", 501, GROUP, 40, count, copy))
    assert bot.methods() == expected
    if "forwardMessages" in expected:
        assert bot.calls[-1][1]["message_ids"] == list(range(40, 40 + count))
    assert all(params["chat_id"] == 501 for _, params in bot.calls)


@pytest.fixture
def ctx(monkeypatch):
    asyncio.run(engine.dispose())
    from app.web.main import app

    auth._starts.clear()
    bot = FakeBot()
    monkeypatch.setattr(originals, "bot_call", bot)
    with TestClient(app) as client:

        async def seed():
            async with SessionLocal() as s:
                await s.execute(delete(LoginRequest))
                await s.execute(delete(User))
                await s.execute(delete(Channel).where(Channel.link.like("@orig_%")))
                await settings_store.update(s, {"notify_bot_username": "km_test_bot", "notify_bot_token": "1:x"})
                ch = Channel(link="@orig_chan", username="orig_chan", tg_id=93001, title="Orig", status="active")
                other = Channel(link="@orig_other", username="orig_other", tg_id=93002, title="Other", status="active")
                s.add_all([ch, other])
                await s.flush()
                now = utcnow()
                posts = [
                    Post(channel_id=ch.id, msg_id=1, posted_at=now, text="sent", repost_status="done",
                         repost_url="https://t.me/c/3000000001/42", repost_copy=False, album_ids=[1, 2]),
                    Post(channel_id=ch.id, msg_id=3, posted_at=now - timedelta(minutes=1), text="queued"),
                    Post(channel_id=other.id, msg_id=1, posted_at=now, text="not theirs", repost_status="done",
                         repost_url="https://t.me/c/3000000001/50", repost_copy=False),
                ]
                s.add_all(posts)
                await s.commit()
                return [p.id for p in posts]

        ids = client.portal.call(seed)
        browser = TestClient(app)
        code = browser.post("/api/auth/tg/start").json()["link"].rsplit("login_", 1)[1]
        client.portal.call(confirm_login, code, {"id": 9401, "first_name": "Оля"})
        assert browser.get("/api/auth/tg/status").json()["status"] == "ok"

        async def give_plan():
            async with SessionLocal() as s:
                user = await s.scalar(select(User).where(User.tg_user_id == 9401))
                project = await s.scalar(select(Project).where(Project.owner_id == user.id))
                project.plan = "pro"
                await s.commit()

        client.portal.call(give_plan)
        browser.post("/api/channels", json={"links": "@orig_chan"})
        yield client, browser, bot, ids


def test_user_gets_the_post_from_the_bot(ctx):
    client, browser, bot, (sent, queued, foreign) = ctx
    feed = {p["id"]: p for p in browser.get("/api/posts?limit=100").json()["items"]}
    assert feed[sent]["sendable"] is True and feed[queued]["sendable"] is False

    assert browser.post(f"/api/posts/{sent}/original").json() == {"ok": True, "to": None}
    assert bot.calls == [("forwardMessages", {"chat_id": 9401, "from_chat_id": GROUP, "message_ids": [42, 43]})]

    assert browser.post(f"/api/posts/{queued}/original").status_code == 409  # not in the group yet
    assert browser.post(f"/api/posts/{foreign}/original").status_code == 404  # not their channel
    assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
    assert client.post(f"/api/posts/{sent}/original").status_code == 400  # the admin opens the group itself
    assert client.get("/api/posts?limit=100").json()["items"][0]["sendable"] is False

    bot.blocked = True
    r = browser.post(f"/api/posts/{sent}/original")
    assert r.status_code == 409 and "@km_test_bot" in r.json()["detail"]




def test_a_member_gets_the_post_in_their_own_telegram(ctx):
    from app.members import link_from_bot
    from app.web import members as members_api

    client, browser, bot, (sent, _queued, _foreign) = ctx
    members_api._failed.clear()
    password = browser.post("/api/members", json={"login": "orig.staff"}).json()["password"]
    staff = TestClient(client.app)
    assert staff.post("/api/members/login", json={"login": "orig.staff", "password": password}).status_code == 200

    # no Telegram linked yet: told what to do
    r = staff.post(f"/api/posts/{sent}/original")
    assert r.status_code == 409 and "Привяжите Telegram" in r.json()["detail"]
    token = staff.post("/api/members/me/telegram").json()["link"].rsplit("staff_", 1)[1]
    client.portal.call(link_from_bot, token, {"id": 77001, "first_name": "Staff"})

    assert staff.post(f"/api/posts/{sent}/original").json() == {"ok": True, "to": None}
    assert bot.calls[-1] == ("forwardMessages", {"chat_id": 77001, "from_chat_id": GROUP, "message_ids": [42, 43]})
    # only the project's groups (staff send there too, with no switch): not some other chat
    assert staff.post(f"/api/posts/{sent}/original", json={"chat_id": -100500}).status_code == 404


def test_admin_is_told_when_the_bot_is_not_in_the_group(monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(originals.notifier, "emit", lambda key, text, **kw: alerts.append(text))
    monkeypatch.setattr(originals, "_bot_in_group", None)
    state = {"inside": False}

    async def bot(token, method, **params):
        assert method == "getChatMember" and params["chat_id"] == GROUP and params["user_id"] == 42
        if not state["inside"]:
            raise BotError("Bad Request: chat not found")
        return {"status": "administrator"}

    monkeypatch.setattr(originals, "bot_call", bot)

    async def run():
        await engine.dispose()
        async with SessionLocal() as s:
            await settings_store.update(s, {"notify_bot_token": "42:new", "notify_bot_username": "new_bot"})
            ch = Channel(link="@orig_group_check", username="orig_group_check", status="active")
            s.add(ch)
            await s.flush()
            s.add(Post(channel_id=ch.id, msg_id=1, posted_at=utcnow(), repost_status="done", reposted_at=utcnow(),
                       repost_url="https://t.me/c/3000000001/99"))
            await s.commit()
        try:
            await originals.bot_in_group_step()
            await originals.bot_in_group_step()  # still outside: said once
            state["inside"] = True
            await originals.bot_in_group_step()
            state["inside"] = False
            await originals.bot_in_group_step()  # removed again: said again
        finally:
            async with SessionLocal() as s:
                await s.execute(delete(Channel).where(Channel.link == "@orig_group_check"))
                await s.commit()

    asyncio.run(run())
    assert len(alerts) == 2 and "@new_bot" in alerts[0]
