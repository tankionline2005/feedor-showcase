import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import notify, originals, settings_store
from app.db import SessionLocal, engine
from app.logins import confirm_login
from app.models import BotChat, Channel, LoginRequest, Post, Project, ProjectGroup, User, utcnow
from app.notify import BotError
from app.plans import plan_json
from app.web import auth, groups

BOT_ID = 4242
ME = 9601  # the user's Telegram id
PUBLIC = -100777001  # @editors_room: public, the user is in it
PRIVATE = -100777002  # private, the user added the bot themselves
STRANGERS = -100777003  # @strangers_room: the bot is there, the user is not


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []
        self.migrated: dict[int, int] = {}
        self.kicked: set[int] = set()

    async def __call__(self, token, method, **params):
        chat = params.get("chat_id")
        if method == "getChat":
            known = {"@editors_room": (PUBLIC, "supergroup", "Редакция"), PRIVATE: (PRIVATE, "supergroup", "Закрытая"),
                     "@strangers_room": (STRANGERS, "supergroup", "Чужие"), "@some_channel": (-1009, "channel", "Канал")}
            if chat not in known:
                raise BotError("Bad Request: chat not found")
            cid, kind, title = known[chat]
            return {"id": cid, "type": kind, "title": title, "username": chat[1:] if isinstance(chat, str) else None}
        if method == "getChatMember":
            if params["user_id"] == BOT_ID:
                return {"status": "member"}
            return {"status": "left" if chat == STRANGERS else "member"}
        if chat in self.migrated:
            raise BotError("Bad Request: group chat was upgraded to a supergroup chat",
                           {"migrate_to_chat_id": self.migrated[chat]})
        if chat in self.kicked:
            raise BotError("Forbidden: bot was kicked from the supergroup chat")
        self.sent.append((method, params))
        return True


@pytest.fixture
def ctx(monkeypatch):
    asyncio.run(engine.dispose())
    from app.web.main import app

    auth._starts.clear()
    bot = FakeBot()
    monkeypatch.setattr(groups, "bot_call", bot)
    monkeypatch.setattr(originals, "bot_call", bot)
    with TestClient(app) as client:

        async def seed():
            async with SessionLocal() as s:
                await s.execute(delete(LoginRequest))
                await s.execute(delete(User))
                await s.execute(delete(BotChat))
                await s.execute(delete(Channel).where(Channel.link == "@grp_chan"))
                await settings_store.update(s, {"notify_bot_username": "feedor_test_bot",
                                                "notify_bot_token": f"{BOT_ID}:x"})
                ch = Channel(link="@grp_chan", username="grp_chan", tg_id=96001, title="Chan", status="active")
                s.add(ch)
                await s.flush()
                post = Post(channel_id=ch.id, msg_id=5, posted_at=utcnow(), text="news", repost_status="done",
                            repost_url="https://t.me/c/3000000001/70", repost_copy=False)
                s.add(post)
                await s.commit()
                return post.id

        post_id = client.portal.call(seed)
        browser = TestClient(app)
        code = browser.post("/api/auth/tg/start").json()["link"].rsplit("login_", 1)[1]
        client.portal.call(confirm_login, code, {"id": ME, "first_name": "Редактор"})
        assert browser.get("/api/auth/tg/status").json()["status"] == "ok"

        async def give_plan():
            async with SessionLocal() as s:
                user = await s.scalar(select(User).where(User.tg_user_id == ME))
                project = await s.scalar(select(Project).where(Project.owner_id == user.id))
                project.plan = "pro"  # five groups
                await s.commit()

        client.portal.call(give_plan)
        browser.post("/api/channels", json={"links": "@grp_chan"})
        yield client, browser, bot, post_id


def bot_added(client, chat_id: int, title: str, by: int, status: str = "member"):
    change = {"chat": {"id": chat_id, "type": "supergroup", "title": title}, "from": {"id": by},
              "new_chat_member": {"status": status, "user": {"id": BOT_ID}}}
    client.portal.call(notify.remember_chat, change)


def test_adding_groups(ctx):
    client, browser, bot, _ = ctx
    state = browser.get("/api/groups").json()
    assert state["items"] == [] and state["limit"] == 5 and state["bot_username"] == "feedor_test_bot"

    # a private group shows up once the user adds the bot to it; somebody else's group does not
    bot_added(client, PRIVATE, "Закрытая", by=ME)
    bot_added(client, STRANGERS, "Чужие", by=111)
    assert [g["chat_id"] for g in browser.get("/api/groups").json()["found"]] == [PRIVATE]
    state = browser.post("/api/groups", json={"chat_id": PRIVATE}).json()
    assert [(g["chat_id"], g["title"]) for g in state["items"]] == [(PRIVATE, "Закрытая")] and state["found"] == []

    # an invite link cannot be resolved by a bot: the user is told what to do instead
    r = browser.post("/api/groups", json={"link": "https://t.me/+AbCdEf123"})
    assert r.status_code == 422 and "@feedor_test_bot" in r.json()["detail"]

    # a public one by its link
    state = browser.post("/api/groups", json={"link": "https://t.me/editors_room"}).json()
    assert [g["chat_id"] for g in state["items"]] == [PRIVATE, PUBLIC]

    # not a member: refused, so nobody posts into a stranger's group; channels and unknown chats too
    assert browser.post("/api/groups", json={"link": "@strangers_room"}).status_code == 403
    assert browser.post("/api/groups", json={"link": "@some_channel"}).status_code == 422
    r = browser.post("/api/groups", json={"link": "@nowhere_room"})
    assert r.status_code == 409 and "добавьте" in r.json()["detail"]

    # as many as the project's plan allows
    async def add_third():
        async with SessionLocal() as s:
            user = await s.scalar(select(User).where(User.tg_user_id == ME))
            for n in range(3):
                s.add(ProjectGroup(project_id=user.project_id, chat_id=-100777009 - n, title=f"Ещё {n}"))
            await s.commit()

    client.portal.call(add_third)
    r = browser.post("/api/groups", json={"link": "@editors_room"})
    assert r.status_code == 409 and "не больше 5 групп" in r.json()["detail"]

    # removed from the group: the user sees it
    bot_added(client, PRIVATE, "Закрытая", by=ME, status="kicked")
    removed = {g["chat_id"]: g["bot_removed"] for g in browser.get("/api/groups").json()["items"]}
    assert removed[PRIVATE] is True and removed[PUBLIC] is False

    assert browser.delete(f"/api/groups/{PUBLIC}").json()["items"][-1]["chat_id"] != PUBLIC
    assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
    assert client.get("/api/groups").status_code == 403  # the admin has no groups


def test_sending_a_post_to_a_group(ctx):
    client, browser, bot, post_id = ctx
    bot_added(client, PRIVATE, "Закрытая", by=ME)
    browser.post("/api/groups", json={"chat_id": PRIVATE})

    r = browser.post(f"/api/posts/{post_id}/original", json={"chat_id": PRIVATE})
    assert r.json() == {"ok": True, "to": "Закрытая"}
    assert bot.sent[-1] == ("forwardMessages", {"chat_id": PRIVATE, "from_chat_id": -1003000000001, "message_ids": [70]})

    # still to the private chat without a group
    browser.post(f"/api/posts/{post_id}/original")
    assert bot.sent[-1][1]["chat_id"] == ME

    # a group that is not theirs
    assert browser.post(f"/api/posts/{post_id}/original", json={"chat_id": STRANGERS}).status_code == 404

    # the group became a supergroup with a new id: followed and remembered
    bot.migrated[PRIVATE] = -100888001
    assert browser.post(f"/api/posts/{post_id}/original", json={"chat_id": PRIVATE}).json()["ok"] is True
    assert bot.sent[-1][1]["chat_id"] == -100888001
    assert [g["chat_id"] for g in browser.get("/api/groups").json()["items"]] == [-100888001]

    # the bot was removed: a clear message instead of "try later"
    bot.kicked.add(-100888001)
    r = browser.post(f"/api/posts/{post_id}/original", json={"chat_id": -100888001})
    assert r.status_code == 409 and "Закрытая" in r.json()["detail"] and "@feedor_test_bot" in r.json()["detail"]


def test_each_project_has_groups_of_its_own(ctx):
    client, browser, bot, post_id = ctx
    bot_added(client, PRIVATE, "Закрытая", by=ME)
    browser.post("/api/groups", json={"chat_id": PRIVATE})

    # another project of the same owner: no groups without a plan; with one, no groups yet, and it may have its own
    second = browser.post("/api/projects").json()["id"]
    assert browser.post(f"/api/projects/{second}/open").status_code == 200
    assert browser.get("/api/groups").json()["limit"] == 0
    assert browser.post("/api/groups", json={"link": "@editors_room"}).status_code == 403

    async def give_plan():
        async with SessionLocal() as s:
            (await s.get(Project, second)).plan = "pro"
            await s.commit()

    client.portal.call(give_plan)
    state = browser.get("/api/groups").json()
    assert state["items"] == [] and state["limit"] == 5
    assert [g["chat_id"] for g in state["found"]] == [PRIVATE]  # the owner's group with the bot, to add here too
    assert [g["chat_id"] for g in browser.post("/api/groups", json={"link": "@editors_room"}).json()["items"]] == [PUBLIC]
    # a post goes only to this project's groups
    assert browser.post(f"/api/posts/{post_id}/original", json={"chat_id": PRIVATE}).status_code == 404

    # the plan speaks of groups again: each has its own number of them
    assert plan_json("pro")["groups"] == 5


def test_an_owner_s_groups_go_into_each_of_their_projects_once():
    from app.db import _groups_to_projects

    async def scenario():
        async with SessionLocal() as s:
            user = User(tg_user_id=96099, first_name="Старый")
            s.add(user)
            await s.flush()
            one, two = Project(owner_id=user.id), Project(owner_id=user.id)
            s.add_all([one, two])
            await s.commit()
            uid, ids = user.id, {one.id, two.id}
        async with engine.begin() as conn:
            from sqlalchemy import text

            await conn.execute(text("CREATE TABLE IF NOT EXISTS user_groups (user_id INTEGER, chat_id BIGINT, "
                                    "title VARCHAR(255), username VARCHAR(64), created_at DATETIME)"))
            await conn.execute(text("DELETE FROM user_groups"))
            await conn.execute(text("INSERT INTO user_groups VALUES (:u, -100960, 'Команда', NULL, '2026-10-01')"),
                               {"u": uid})
            await conn.run_sync(_groups_to_projects)
            await conn.execute(text("DROP TABLE user_groups"))
        async with SessionLocal() as s:
            rows = (await s.execute(select(ProjectGroup).where(ProjectGroup.chat_id == -100960))).scalars().all()
            return {(g.project_id, g.title) for g in rows}, ids

    rows, ids = asyncio.run(scenario())
    assert rows == {(i, "Команда") for i in ids}

