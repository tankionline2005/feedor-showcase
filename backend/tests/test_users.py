import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import notify, settings_store
from app.db import SessionLocal, engine
from app.models import LoginRequest, User
from app.web import auth, public_api


class FakeBot:
    def __init__(self):
        self.sent: list[dict] = []
        self.edited: list[str] = []

    async def __call__(self, token, method, *, http_timeout=20, **params):
        if method == "sendMessage":
            self.sent.append(params)
        elif method == "editMessageText":
            self.edited.append(params["text"])
        return True


@pytest.fixture
def ctx(monkeypatch):
    asyncio.run(engine.dispose())
    from app.web.main import app

    bot = FakeBot()
    monkeypatch.setattr(notify, "bot_call", bot)
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(notify.Notifier, "emit", lambda self, key, text, **kw: alerts.append((key, text)))
    auth._failed.clear()
    auth._starts.clear()
    public_api._calls.clear()
    with TestClient(app) as client:

        async def reset():
            async with SessionLocal() as s:
                await s.execute(delete(LoginRequest))
                await s.execute(delete(User))
                await settings_store.update(s, {"notify_bot_username": "km_test_bot", "notify_bot_token": "1:x"})

        client.portal.call(reset)
        yield app, client, bot, alerts


def start_login(browser: TestClient) -> str:
    r = browser.post("/api/auth/tg/start", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0"})
    assert r.status_code == 200
    link = r.json()["link"]
    assert link.startswith("https://t.me/km_test_bot?start=login_")
    return link.rsplit("login_", 1)[1]


def bot_update(client, text: str = "", *, button: str | None = None, tg_id: int = 501, chat_type="private"):
    """Delivers one message or button press to the bot, as getUpdates would."""
    person = {"id": tg_id, "first_name": "Коля", "username": "kolya"}
    n = notify.Notifier()

    async def run():
        if button is not None:
            await n.handle_button("1:x", {"id": "q1", "data": button, "from": person,
                                          "message": {"chat": {"id": tg_id}, "message_id": 9}})
        else:
            await n.handle_message("1:x", "alerts-code", {"chat": {"id": tg_id, "type": chat_type}, "text": text})

    client.portal.call(run)


def test_login_through_the_bot(ctx):
    app, client, bot, alerts = ctx
    browser = TestClient(app)
    code = start_login(browser)
    assert browser.get("/api/auth/tg/status").json() == {"status": "pending"}

    # the link alone signs nobody in: the bot asks to confirm, showing where the login comes from
    bot_update(client, f"/start login_{code}")
    prompt = bot.sent[-1]
    assert "Chrome, Windows" in prompt["text"]
    buttons = [b["callback_data"] for row in prompt["reply_markup"]["inline_keyboard"] for b in row]
    assert buttons == [f"login:{code}", f"nologin:{code}"]
    assert browser.get("/api/auth/tg/status").json() == {"status": "pending"}

    bot_update(client, button=f"login:{code}")
    assert "подтверждён" in bot.edited[-1]
    assert [key for key, _ in alerts] == [alerts[0][0]] and "Новый пользователь" in alerts[0][1]

    # another browser that somehow learned the code gets nothing
    stranger = TestClient(app)
    stranger.cookies.clear()
    assert stranger.get("/api/auth/tg/status").json() == {"status": "expired"}

    assert browser.get("/api/auth/tg/status").json() == {"status": "ok"}
    me = browser.get("/api/auth/me").json()
    assert me["role"] == "user" and me["user"] == "Коля" and me["username"] == "kolya"
    assert me["plan"]["id"] == "trial"  # «Free Trial» on sign-up
    # the code works once
    assert browser.get("/api/auth/tg/status").json() == {"status": "expired"}

    # a second login of the same person: same user, no second "new user" alert
    again = TestClient(app)
    code2 = start_login(again)
    bot_update(client, button=f"login:{code2}")
    assert again.get("/api/auth/tg/status").json() == {"status": "ok"}
    assert len(alerts) == 1


def test_user_sees_posts_but_not_management(ctx):
    app, client, bot, _ = ctx
    browser = TestClient(app)
    code = start_login(browser)
    bot_update(client, button=f"login:{code}")
    assert browser.get("/api/auth/tg/status").json()["status"] == "ok"

    for path in ("/api/posts", "/api/channels", "/api/api-key"):
        assert browser.get(path).status_code == 200, path
    for method, path in (
        ("GET", "/api/settings"), ("GET", "/api/accounts"), ("GET", "/api/proxies"), ("GET", "/api/notify"),
        ("GET", "/api/status"), ("GET", "/api/users"), ("PATCH", "/api/channels/1"),
    ):
        r = browser.request(method, path, json={"action": "pause"} if method == "PATCH" else None)
        assert r.status_code == 403, (method, path)


def test_cancel_expired_and_blocked(ctx):
    app, client, bot, _ = ctx

    browser = TestClient(app)
    code = start_login(browser)
    bot_update(client, button=f"nologin:{code}")
    assert bot.edited[-1] == "Вход отменён."
    assert browser.get("/api/auth/tg/status").json() == {"status": "declined"}
    bot_update(client, button=f"login:{code}")  # a cancelled code cannot be confirmed afterwards
    assert browser.get("/api/auth/tg/status").json() == {"status": "declined"}

    late = TestClient(app)
    code = start_login(late)

    async def age():
        async with SessionLocal() as s:
            req = await s.get(LoginRequest, code)
            req.created_at -= timedelta(minutes=6)
            await s.commit()

    client.portal.call(age)
    bot_update(client, f"/start login_{code}")
    assert "устарела" in bot.sent[-1]["text"] and "reply_markup" not in bot.sent[-1]
    assert late.get("/api/auth/tg/status").json() == {"status": "expired"}

    # logins in a group chat are ignored
    sent = len(bot.sent)
    bot_update(client, f"/start login_{start_login(TestClient(app))}", chat_type="group")
    assert len(bot.sent) == sent


def test_blocking_signs_out_and_stops_the_api_key(ctx):
    app, client, bot, _ = ctx
    assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
    admin_key = client.get("/api/api-key").json()["key"]

    browser = TestClient(app)
    code = start_login(browser)
    bot_update(client, button=f"login:{code}")
    browser.get("/api/auth/tg/status")
    users = client.get("/api/users").json()["items"]
    project = users[0]["projects"][0]["id"]
    client.patch(f"/api/users/{users[0]['id']}/projects/{project}", json={"plan": "pro", "expires_at": None})
    key = browser.get("/api/api-key").json()["key"]
    assert key.startswith("km_") and key != admin_key
    assert browser.get("/api/v1/stats", headers={"X-API-Key": key}).status_code == 200

    # a new key replaces the old one
    new_key = browser.post("/api/api-key/reset").json()["key"]
    assert browser.get("/api/v1/stats", headers={"X-API-Key": key}).json()["error"]["code"] == "invalid_key"
    assert client.get("/api/api-key").json()["key"] == admin_key  # the admin's key is separate

    users = client.get("/api/users").json()["items"]
    assert [(u["name"], u["username"], u["blocked"]) for u in users] == [("Коля", "kolya", False)]
    assert client.patch(f"/api/users/{users[0]['id']}", json={"blocked": True}).json()["blocked"] is True

    assert browser.get("/api/posts").status_code == 401
    assert browser.get("/api/v1/stats", headers={"X-API-Key": new_key}).json()["error"]["code"] == "blocked"
    assert client.get("/api/v1/stats", headers={"X-API-Key": admin_key}).status_code == 200

    # and cannot sign in again
    retry = TestClient(app)
    code = start_login(retry)
    bot_update(client, button=f"login:{code}")
    assert "закрыт" in bot.edited[-1]
    assert retry.get("/api/auth/tg/status").json() == {"status": "declined"}


def test_admin_password_form_locks_after_failures(ctx):
    app, *_ = ctx
    browser = TestClient(app)
    for _ in range(auth.FAILED_LIMIT):
        assert browser.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    # even the right password waits now
    assert browser.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 429
    auth._failed.clear()
    r = browser.post("/api/auth/login", json={"username": "admin", "password": "secret"})
    assert r.status_code == 200 and browser.get("/api/auth/me").json()["role"] == "admin"


def test_device_names():
    assert auth.device_of("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile Safari/604.1") == "Safari, iOS"
    assert auth.device_of("Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36") == "Chrome, Android"
    assert auth.device_of("Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36 Edg/130.0") == "Edge, Windows"
    assert auth.device_of("") == "браузер"
