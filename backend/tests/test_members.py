from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.members import check_password, hash_password, link_from_bot
from app.models import Member, Project, User, utcnow
from app.web import members as members_api

from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def member_login(app, login: str, password: str):
    browser = TestClient(app)
    return browser, browser.post("/api/members/login", json={"login": login, "password": password})


async def _project_of(tg_id: int) -> int:
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        return await s.scalar(select(Project.id).where(Project.owner_id == user.id))


async def _expire(project_id: int) -> None:
    async with SessionLocal() as s:
        (await s.get(Project, project_id)).plan_expires_at = utcnow() - timedelta(minutes=1)
        await s.commit()


def test_passwords_are_hashed_and_checked():
    async def scenario():
        stored = await hash_password("Секрет-123")
        return stored, await check_password("Секрет-123", stored), await check_password("секрет-123", stored)

    import asyncio
    stored, right, wrong = asyncio.run(scenario())
    assert stored.startswith("scrypt$") and "Секрет" not in stored and right and not wrong


def test_the_owner_gives_the_team_accounts(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9401)  # Pro: up to 3 staff
    assert owner.post("/api/channels", json={"links": "@uc_team_chan"}).json()["added"]
    run(client, _activate, "@uc_team_chan", 94011, 3)

    assert owner.get("/api/members").json() == {"items": [], "limit": 5, "has_plan": True}
    created = owner.post("/api/members", json={"login": "Editor.Anna", "can_edit_channels": False}).json()
    password = created["password"]
    assert created["member"]["login"] == "editor.anna" and len(password) == 14
    assert "password" not in str(owner.get("/api/members").json())  # shown once, never again
    # logins: the rules, and each one only once in the whole service
    assert owner.post("/api/members", json={"login": "я"}).status_code == 422
    assert owner.post("/api/members", json={"login": "EDITOR.anna"}).status_code == 409
    for login in ("ed2", "ed3", "ed4", "ed5"):
        assert owner.post("/api/members", json={"login": login}).status_code == 200
    r = owner.post("/api/members", json={"login": "ed6"})
    assert r.status_code == 409 and "не больше 5" in r.json()["detail"]

    # the member signs in and sees the project's posts and channels, but changes nothing
    _, bad = member_login(app, "editor.anna", "wrong")
    assert bad.status_code == 401
    anna, r = member_login(app, " Editor.Anna ", password)
    assert r.json() == {"ok": True}
    me = anna.get("/api/auth/me").json()
    assert me["role"] == "member" and me["user"] == "editor.anna" and me["can_edit_channels"] is False
    assert me["plan"]["id"] == "pro" and me["member"]["telegram"] is None
    assert [c["link"] for c in anna.get("/api/channels").json()["items"]] == ["@uc_team_chan"]
    assert len(anna.get("/api/posts").json()["items"]) == 3
    assert anna.post("/api/channels", json={"links": "@uc_team_more"}).status_code == 403
    channel_id = anna.get("/api/channels").json()["items"][0]["id"]
    assert anna.delete(f"/api/channels/{channel_id}").status_code == 403
    # the owner's things stay the owner's
    for method, path in (("get", "/api/members"), ("post", "/api/groups"), ("get", "/api/projects"),
                         ("post", "/api/promo/redeem")):
        r = getattr(anna, method)(path, **({"json": {"code": "x"}} if method == "post" else {}))
        assert r.status_code in (400, 403), path

    # allowed to edit channels: at once, without signing in again
    anna_id = created["member"]["id"]
    assert owner.patch(f"/api/members/{anna_id}", json={"can_edit_channels": True}).json()["can_edit_channels"]
    assert anna.post("/api/channels", json={"links": "@uc_team_more"}).json()["added"]
    assert len(owner.get("/api/channels").json()["items"]) == 2  # the owner sees what the member added

    # their own API key sees the project's channels
    key = anna.get("/api/api-key").json()["key"]
    owner_key = owner.get("/api/api-key").json()["key"]
    assert key != owner_key
    assert len(client.get("/api/v1/channels", headers={"X-API-Key": key}).json()["items"]) == 2

    # a new password signs her out everywhere; the old one stops working
    new = owner.post(f"/api/members/{anna_id}/password").json()["password"]
    assert new != password
    assert anna.get("/api/auth/me").status_code == 401
    assert member_login(app, "editor.anna", password)[1].status_code == 401
    anna, r = member_login(app, "editor.anna", new)
    assert r.status_code == 200

    # removed: signed out, the login and the key are gone
    assert owner.delete(f"/api/members/{anna_id}").json() == {"ok": True}
    assert anna.get("/api/auth/me").status_code == 401
    assert client.get("/api/v1/channels", headers={"X-API-Key": key}).json()["error"]["code"] == "invalid_key"
    assert [m["login"] for m in owner.get("/api/members").json()["items"]] == ["ed2", "ed3", "ed4", "ed5"]

    # another owner never sees or touches these members
    stranger = sign_in(app, client, 9402)
    ed2 = owner.get("/api/members").json()["items"][0]["id"]
    assert stranger.get("/api/members").json()["items"] == []
    assert stranger.delete(f"/api/members/{ed2}").status_code == 404

    # the admin sees how many staff each project has
    [u] = [u for u in client.get("/api/users").json()["items"] if u["name"] == "u9401"]
    assert u["projects"][0]["members"] == 4


def test_staff_wait_while_the_project_is_frozen(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9411)
    password = owner.post("/api/members", json={"login": "temp.staff"}).json()["password"]
    staff, _ = member_login(app, "temp.staff", password)
    key = staff.get("/api/api-key").json()["key"]
    assert staff.get("/api/auth/me").status_code == 200

    run(client, _expire, run(client, _project_of, 9411))
    # at once: signed out, cannot sign in, the key does not work
    assert staff.get("/api/auth/me").status_code == 401
    r = member_login(app, "temp.staff", password)[1]
    assert r.status_code == 403 and "закончился тариф" in r.json()["detail"]
    assert client.get("/api/v1/stats", headers={"X-API-Key": key}).json()["error"]["code"] == "no_api"
    # the project is frozen, not emptied: the account waits for the plan to be bought again
    listed = owner.get("/api/members").json()
    assert [m["login"] for m in listed["items"]] == ["temp.staff"] and listed["has_plan"] is False
    assert owner.post("/api/members", json={"login": "temp.again"}).status_code == 403


def test_a_member_links_their_telegram(ctx):
    app, client = ctx
    members_api._failed.clear()

    async def bot_name():
        from app import settings_store
        async with SessionLocal() as s:
            await settings_store.update(s, {"notify_bot_username": "km_test_bot"})
            await s.commit()

    run(client, bot_name)
    owner = sign_in(app, client, 9421)
    password = owner.post("/api/members", json={"login": "tg.staff"}).json()["password"]
    staff, _ = member_login(app, "tg.staff", password)
    assert owner.post("/api/members/me/telegram").status_code == 400  # only staff link a Telegram here

    link = staff.post("/api/members/me/telegram").json()["link"]
    assert link.startswith("https://t.me/km_test_bot?start=staff_")
    token = link.rsplit("staff_", 1)[1]
    member = run(client, link_from_bot, token, {"id": 555001, "first_name": "Аня", "username": "anya_tg"})
    assert member.login == "tg.staff"
    assert staff.get("/api/auth/me").json()["member"]["telegram"] == {"username": "anya_tg", "name": "Аня"}
    assert run(client, link_from_bot, token, {"id": 1}) is None  # a link works once
    assert owner.get("/api/members").json()["items"][0]["telegram"]["username"] == "anya_tg"

    assert staff.delete("/api/members/me/telegram").json() == {"ok": True}
    assert staff.get("/api/auth/me").json()["member"]["telegram"] is None

    # wrong passwords: five, then a pause
    for _ in range(5):
        assert member_login(app, "tg.staff", "nope")[1].status_code == 401
    assert member_login(app, "tg.staff", password)[1].status_code == 429


def test_staff_rows_survive_nothing_of_a_deleted_project(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9431)
    owner.post("/api/members", json={"login": "gone.with.project"})
    first = run(client, _project_of, 9431)
    owner.post("/api/projects")  # a second project, so the first can go
    assert owner.delete(f"/api/projects/{first}").json() == {"ok": True}

    async def left():
        async with SessionLocal() as s:
            return await s.scalar(select(Member.id).where(Member.login == "gone.with.project"))

    assert run(client, left) is None


async def test_the_limits_check_runs_on_its_schedule_not_nonstop():
    """forever() takes a number a step returns for its next delay: a count returned once made a check run again at
    once and keep the server's core busy."""
    from app.worker.maintenance import staff_step

    assert await staff_step() is None
