import asyncio
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import SessionLocal, _add_missing_columns, _move_to_projects
from app.models import Base, Channel, Project, User, utcnow
from app.projects import new_project
from app.web import projects as projects_api

from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def test_an_old_database_moves_plans_keys_and_channels_into_projects(tmp_path):
    """Before projects a user had the plan, the API key and the channels: each becomes their first project."""

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'old.db').as_posix()}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # the schema as it was: plan and key on the user, a user ↔ channel table
            await conn.execute(text("DROP TABLE project_channels"))
            await conn.execute(text("DROP TABLE promo_codes"))
            await conn.execute(text("DROP TABLE projects"))
            await conn.execute(text("ALTER TABLE users DROP COLUMN project_id"))
            await conn.execute(text("ALTER TABLE users DROP COLUMN balance"))
            await conn.execute(text("ALTER TABLE users DROP COLUMN trial_used_at"))
            await conn.execute(text(
                "CREATE TABLE promo_codes (code VARCHAR(32) PRIMARY KEY, plan VARCHAR(32) NOT NULL, days INTEGER, "
                "created_at DATETIME NOT NULL, used_by INTEGER REFERENCES users(id) ON DELETE SET NULL, "
                "used_at DATETIME)"
            ))
            await conn.execute(text("ALTER TABLE users ADD COLUMN plan VARCHAR(32) DEFAULT '' NOT NULL"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN plan_expires_at DATETIME"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN api_key VARCHAR(64)"))
            await conn.execute(text(
                "CREATE TABLE user_channels (user_id INTEGER, channel_id INTEGER, created_at DATETIME, "
                "PRIMARY KEY (user_id, channel_id))"
            ))
            soon = utcnow() + timedelta(days=5)
            await conn.execute(text(
                "INSERT INTO users (id, tg_user_id, blocked, created_at, plan, plan_expires_at, api_key) VALUES "
                "(1, 11, 0, :now, 'pro', :soon, 'km_one'), (2, 22, 0, :now, '', NULL, 'km_two')"
            ), {"now": utcnow(), "soon": soon})
            for cid in (1, 2):
                await conn.execute(Channel.__table__.insert().values(id=cid, link=f"@old_{cid}", status="active"))
            await conn.execute(text(
                "INSERT INTO user_channels VALUES (1, 1, :now), (1, 2, :now), (2, 2, :now)"
            ), {"now": utcnow()})
            await conn.execute(text(
                "INSERT INTO promo_codes (code, plan, days, created_at, used_by, used_at) "
                "VALUES ('FEED-AAAA-BBBB', 'pro', 15, :now, 1, :now)"
            ), {"now": utcnow()})

        # what init_db does on start, twice: the second time there is nothing left to move
        for _ in range(2):
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.run_sync(_add_missing_columns)
                await conn.run_sync(_move_to_projects)

        async with engine.connect() as conn:
            projects = (await conn.execute(text(
                "SELECT owner_id, plan, plan_expires_at IS NOT NULL, api_key FROM projects ORDER BY owner_id"
            ))).all()
            links = (await conn.execute(text(
                "SELECT p.owner_id, pc.channel_id FROM project_channels pc JOIN projects p ON p.id = pc.project_id "
                "ORDER BY 1, 2"
            ))).all()
            current = (await conn.execute(text(
                "SELECT u.id FROM users u JOIN projects p ON p.id = u.project_id AND p.owner_id = u.id ORDER BY 1"
            ))).scalars().all()
            promo = (await conn.execute(text("SELECT project_id IS NOT NULL FROM promo_codes"))).scalar()
            columns = {r[1] for r in (await conn.execute(text("PRAGMA table_info(users)"))).all()}
            old_keys = (await conn.execute(text("SELECT count(*) FROM users WHERE api_key IS NOT NULL"))).scalar()
            tables = set((await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars())
        await engine.dispose()
        return projects, links, current, promo, columns, old_keys, tables

    projects, links, current, promo, columns, old_keys, tables = asyncio.run(scenario())
    # one project each, with the user's plan, its end and the same API key: nothing breaks for them
    assert projects == [(1, "pro", 1, "km_one"), (2, "", 0, "km_two")]
    assert links == [(1, 1), (1, 2), (2, 2)]
    assert current == [1, 2]  # both open their project
    assert promo  # a used code knows which project it went to
    # the old places are gone (SQLite keeps the UNIQUE key column, emptied)
    assert not {"plan", "plan_expires_at"} & columns and old_keys == 0
    assert "user_channels" not in tables


def test_projects_keep_channels_and_keys_apart(ctx):
    app, client = ctx
    browser = sign_in(app, client, 9301)
    assert browser.post("/api/channels", json={"links": "@uc_first_project"}).json()["added"]
    key_one = browser.get("/api/api-key").json()["key"]

    async def second_project() -> int:
        async with SessionLocal() as s:
            user = await s.scalar(select(User).where(User.tg_user_id == 9301))
            project = new_project(user.id)
            project.plan = "agency"
            s.add(project)
            await s.flush()
            user.project_id = project.id  # what switching to it will do
            await s.commit()
            return project.id

    second = run(client, second_project)
    run(client, _activate, "@uc_first_project", 93011, 2)
    # the second project starts empty, with a plan and a key of its own
    me = browser.get("/api/auth/me").json()
    assert me["project"] == {"id": second} and me["plan"]["id"] == "agency"
    assert browser.get("/api/channels").json()["items"] == []
    assert browser.get("/api/posts").json()["items"] == []
    key_two = browser.get("/api/api-key").json()["key"]
    assert key_two != key_one

    assert browser.post("/api/channels", json={"links": "@uc_second_project"}).json()["added"]
    assert [c["link"] for c in browser.get("/api/channels").json()["items"]] == ["@uc_second_project"]

    # each key sees its own project's channels
    def api_channels(key):
        return [c["username"] for c in client.get("/api/v1/channels", headers={"X-API-Key": key}).json()["items"]]

    assert api_channels(key_one) == ["uc_first_project"]
    assert api_channels(key_two) == ["uc_second_project"]

    # the admin sees both projects of the user, each with its plan and channels
    [u] = [u for u in client.get("/api/users").json()["items"] if u["projects"] and u["projects"][-1]["id"] == second]
    assert [(p["title"], p["plan"]["id"] if p["plan"] else None, p["channels"]) for p in u["projects"]] == [
        ("Проект 1", "pro", 1), ("Проект 2", "agency", 1),
    ]

    # someone else's project is never opened, even if asked for
    other = sign_in(app, client, 9302)
    assert other.get("/api/auth/me").json()["project"]["id"] not in (second, u["projects"][0]["id"])


class ChannelBot:
    """The Bot API as the projects module sees it: two channels, one public, one private."""

    def __init__(self, admins: dict[int, set[int]], can_invite: bool = False):
        self.admins = admins  # chat id -> Telegram ids of its administrators
        self.can_invite = can_invite

    async def __call__(self, token, method, **params):
        chat = params.get("chat_id")
        if method == "getChatMember":
            return {"status": "administrator" if params["user_id"] in self.admins.get(chat, set()) else "member"}
        if method == "getChat":
            if chat == -1001:
                return {"id": chat, "type": "channel", "title": "Мой публичный", "username": "pj_public",
                        "photo": {"small_file_id": "photo-1"}}
            return {"id": chat, "type": "channel", "title": "Мой закрытый"}
        if method == "createChatInviteLink":
            if not self.can_invite:
                raise projects_api.BotError("Bad Request: not enough rights to manage chat invite links")
            return {"invite_link": "https://t.me/+pjPrivateHash"}
        raise AssertionError(method)


async def _bot_in_channels(added_by: int) -> None:
    from app import settings_store
    from app.models import BotChat

    async with SessionLocal() as s:
        await settings_store.update(s, {"notify_bot_token": "1:x", "notify_bot_username": "km_test_bot"})
        for chat_id, title, username in ((-1001, "Мой публичный", "pj_public"), (-1002, "Мой закрытый", None)):
            row = await s.get(BotChat, chat_id) or BotChat(chat_id=chat_id, type="channel", status="administrator")
            row.title, row.username, row.status = title, username, "administrator"
            row.added_by = added_by if chat_id == -1001 else 777  # the private one: someone else added the bot
            s.add(row)
        await s.commit()


async def _give_plan(project_id: int, plan: str = "pro") -> None:
    async with SessionLocal() as s:
        (await s.get(Project, project_id)).plan = plan
        await s.commit()


def test_projects_are_created_switched_and_deleted(ctx):
    app, client = ctx
    browser = sign_in(app, client, 9311)  # comes with a Pro first project
    state = browser.get("/api/projects").json()
    [first] = state["items"]
    assert first["title"] == "Проект 1" and first["current"] and first["channel"] is None and state["can_create"]
    assert browser.post("/api/channels", json={"links": "@uc_p1_a, @uc_p1_b"}).json()["added"]
    run(client, _activate, "@uc_p1_a", 93111, 1)  # joined by an account; @uc_p1_b is still waiting

    # a new project opens at once, empty and without a plan
    second = browser.post("/api/projects").json()["id"]
    me = browser.get("/api/auth/me").json()
    assert me["project"] == {"id": second} and me["plan"] is None
    assert browser.get("/api/channels").json()["items"] == []
    # one more only after this one has a plan
    state = browser.get("/api/projects").json()
    assert not state["can_create"] and state["create_hint"] == "Сначала оформите тариф для проекта «Проект 2»"
    r = browser.post("/api/projects")
    assert r.status_code == 409 and r.json()["detail"] == state["create_hint"]

    # back to the first: its channels are there; another browser of the same person opens the last one
    assert browser.post(f"/api/projects/{first['id']}/open").json() == {"ok": True}
    assert len(browser.get("/api/channels").json()["items"]) == 2
    phone = sign_in(app, client, 9311)
    assert phone.get("/api/auth/me").json()["project"] == {"id": first["id"]}
    phone.post(f"/api/projects/{second}/open")
    assert browser.get("/api/auth/me").json()["project"] == {"id": first["id"]}  # each browser keeps its own

    # someone else's project cannot be opened or deleted
    stranger = sign_in(app, client, 9312)
    assert stranger.post(f"/api/projects/{first['id']}/open").status_code == 404
    assert stranger.delete(f"/api/projects/{first['id']}").status_code == 404

    # deleting the first: its channels stop being tracked by anyone, the browser moves to the other one
    assert browser.delete(f"/api/projects/{first['id']}").json() == {"ok": True}
    assert browser.get("/api/auth/me").json()["project"] == {"id": second}
    assert [p["id"] for p in browser.get("/api/projects").json()["items"]] == [second]

    async def left():
        async with SessionLocal() as s:
            return (await s.execute(
                select(Channel.link, Channel.orphaned_at.is_not(None)).where(Channel.link.in_(["@uc_p1_a", "@uc_p1_b"]))
            )).all()

    # the joined one is left after the grace period, the one never joined is gone at once
    assert run(client, left) == [("@uc_p1_a", True)]
    # the last project stays
    r = browser.delete(f"/api/projects/{second}")
    assert r.status_code == 409 and "единственный" in r.json()["detail"]


def test_the_project_channel_is_any_channel_and_is_tracked_for_free(ctx, monkeypatch):
    app, client = ctx
    me = 9321
    bot = ChannelBot(admins={-1002: {me}})
    monkeypatch.setattr(projects_api, "bot_call", bot)

    async def photo(token, file_id):
        return b"jpeg-" + file_id.encode()

    monkeypatch.setattr(projects_api, "bot_file", photo)
    projects_api._admin_checks.clear()
    run(client, _bot_in_channels, me)
    browser = sign_in(app, client, me)
    project = browser.get("/api/projects").json()["items"][0]["id"]
    assert browser.get("/api/auth/me").json()["needs_channel"] is True  # the first step: no going on without it

    # the public one: this person added the bot; the private one: someone else did, but they administer it
    found = browser.get("/api/projects/channels").json()
    assert sorted((c["title"], c["project"]) for c in found["items"]) == [("Мой закрытый", None), ("Мой публичный", None)]
    assert found["bot_username"] == "km_test_bot"
    # a stranger sees neither
    assert sign_in(app, client, 9322).get("/api/projects/channels").json()["items"] == []

    state = browser.put(f"/api/projects/{project}/channel", json={"chat_id": -1001}).json()
    [p] = state["items"]
    assert p["title"] == "Мой публичный" and p["avatar_url"]
    assert {k: v for k, v in p["channel"].items() if k != "channel_id"} == {
        "title": "Мой публичный", "username": "pj_public", "link": "@pj_public", "status": "pending", "status_detail": None,
    }
    assert browser.get(p["avatar_url"]).content == b"jpeg-photo-1"  # the bot's picture until an account reads it
    assert browser.get("/api/auth/me").json()["needs_channel"] is False

    # tracked like any channel, marked as the project's own, outside the plan's limit, not removable
    [own] = browser.get("/api/channels").json()["items"]
    assert own["link"] == "@pj_public" and own["own"] is True
    from app import plans
    monkeypatch.setitem(plans.PLANS, "pro", plans.Plan("pro", "Pro", 29, 1, True, 2))
    assert browser.post("/api/channels", json={"links": "@uc_pj_other"}).json()["added"]  # 1 of 1, own not counted
    assert browser.post("/api/channels", json={"links": "@uc_pj_more"}).json()["over_limit"] == ["@uc_pj_more"]
    r = browser.delete(f"/api/channels/{own['id']}")
    assert r.status_code == 409 and "канал проекта" in r.json()["detail"]

    # the private one: without the right to invite the bot cannot open it — and nothing changes
    r = browser.put(f"/api/projects/{project}/channel", json={"chat_id": -1002})
    assert r.status_code == 409 and "Пригласительные ссылки" in r.json()["detail"]
    assert browser.get("/api/channels").json()["items"][0]["link"] == "@pj_public"

    # given the right to invite, it is collected by the bot's invite link; the old one stops being the project's
    bot.can_invite = True
    state = browser.put(f"/api/projects/{project}/channel", json={"chat_id": -1002}).json()
    assert state["items"][0]["channel"]["link"] == "t.me/+pjPrivateHash"
    assert [c["link"] for c in browser.get("/api/channels").json()["items"]] == ["t.me/+pjPrivateHash", "@uc_pj_other"]

    # any channel at all, by its link or @username: no bot, nobody has to prove it is theirs
    state = browser.put(f"/api/projects/{project}/channel", json={"link": "https://t.me/uc_pj_anything"}).json()
    [p] = state["items"]
    assert p["title"] == "@uc_pj_anything" and p["channel"]["link"] == "@uc_pj_anything" and p["avatar_url"] is None
    own = [c for c in browser.get("/api/channels").json()["items"] if c["own"]]
    assert [c["link"] for c in own] == ["@uc_pj_anything"]
    # once an account has joined it, the project carries the channel's real name
    run(client, _activate, "@uc_pj_anything", 93219, 1)
    assert browser.get("/api/projects").json()["items"][0]["title"] == "uc_pj_anything"
    assert browser.put(f"/api/projects/{project}/channel", json={"link": "не ссылка"}).status_code == 422

    # a channel already used by another of the person's projects cannot be chosen twice
    run(client, _give_plan, project)
    other = browser.post("/api/projects").json()["id"]
    browser.post(f"/api/projects/{other}/open")
    assert browser.get("/api/auth/me").json()["needs_channel"] is False  # a later project may go without one
    r = browser.put(f"/api/projects/{other}/channel", json={"link": "@uc_pj_anything"})
    assert r.status_code == 409 and "другом вашем проекте" in r.json()["detail"]
    # a channel id from the bot list must really be one of them
    assert browser.put(f"/api/projects/{other}/channel", json={"chat_id": -1009}).status_code == 403


def test_the_api_knows_the_project_channel(ctx, monkeypatch):
    app, client = ctx
    me = 9331
    monkeypatch.setattr(projects_api, "bot_call", ChannelBot(admins={}))

    async def photo(token, file_id):
        return b""

    monkeypatch.setattr(projects_api, "bot_file", photo)
    projects_api._admin_checks.clear()
    run(client, _bot_in_channels, me)
    browser = sign_in(app, client, me)
    project = browser.get("/api/projects").json()["items"][0]["id"]
    key = browser.get("/api/api-key").json()["key"]

    def api(path):
        return client.get(f"/api/v1{path}", headers={"X-API-Key": key}).json()

    # no channel chosen yet: my_channel is empty, channel=my explains why
    info = api("/project")
    assert info["my_channel"] is None and info["plan"]["id"] == "pro" and info["channels"] == {"tracked": 0, "limit": 350}
    assert api("/channels")["my_channel"] is None and api("/stats")["my_channel"] is None
    assert api("/posts?channel=my")["error"]["code"] == "no_my_channel"

    browser.put(f"/api/projects/{project}/channel", json={"chat_id": -1001})
    assert browser.post("/api/channels", json={"links": "@uc_pj_rival"}).json()["added"]
    own_id = run(client, _activate, "@pj_public", 93311, 2)
    run(client, _activate, "@uc_pj_rival", 93312, 3)

    info = api("/project")
    mine = info["my_channel"]
    assert {k: mine[k] for k in ("title", "username", "url", "telegram_id", "channel_id", "collected")} == {
        "title": "pj_public", "username": "pj_public", "url": "https://t.me/pj_public", "telegram_id": -10093311,
        "channel_id": own_id, "collected": True,
    }
    assert mine["stats_7d"]["posts_per_day"] > 0
    assert info["channels"] == {"tracked": 1, "limit": 350}  # its own channel is not counted

    # the project's own posts, by "my" or by its id; every post and channel says whether it is the own one
    own_posts = api("/posts?channel=my")["items"]
    assert len(own_posts) == 2 and all(p["channel"]["is_my_channel"] for p in own_posts)
    assert [p["id"] for p in api(f"/posts?channel={own_id}")["items"]] == [p["id"] for p in own_posts]
    everything = api("/posts?limit=100")["items"]
    assert sorted(p["channel"]["is_my_channel"] for p in everything) == [False] * 3 + [True] * 2
    flags = {c["username"]: c["is_my_channel"] for c in api("/channels")["items"]}
    assert flags == {"pj_public": True, "uc_pj_rival": False}
    assert api("/channels")["my_channel"]["channel_id"] == own_id
    assert api(f"/posts/{own_posts[0]['id']}")["channel"]["is_my_channel"] is True

    # the admin's key has no project
    assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
    admin_key = client.get("/api/api-key").json()["key"]
    r = client.get("/api/v1/project", headers={"X-API-Key": admin_key}).json()
    assert r["error"]["code"] == "not_found"
    assert "is_my_channel" not in client.get("/api/v1/posts?limit=1", headers={"X-API-Key": admin_key}).json()["items"][0]["channel"]


def test_the_admin_sees_who_tracks_a_channel_and_what_a_project_tracks(ctx):
    app, client = ctx
    a, b = sign_in(app, client, 9341), sign_in(app, client, 9342)
    a.post("/api/channels", json={"links": "@uc_watched, @uc_only_a2"})
    b.post("/api/channels", json={"links": "@uc_watched"})
    watched = run(client, _activate, "@uc_watched", 93411, 1)

    followers = client.get(f"/api/channels/{watched}/followers").json()["items"]
    assert [(f["user"]["name"], f["project"]["title"], f["own"]) for f in followers] == [
        ("u9341", "Проект 1", False), ("u9342", "Проект 1", False),
    ]
    assert a.get(f"/api/channels/{watched}/followers").status_code == 403  # the admin's only

    user = followers[0]["user"]["id"]
    project = followers[0]["project"]["id"]
    channels = client.get(f"/api/users/{user}/projects/{project}/channels").json()["items"]
    assert sorted(c["link"] for c in channels) == ["@uc_only_a2", "@uc_watched"]
    assert all(c["own"] is False for c in channels)
    # another user's project is not this user's
    other = followers[1]["project"]["id"]
    assert client.get(f"/api/users/{user}/projects/{other}/channels").status_code == 404
