"""The API key on the site's own methods: everything its holder may do on the site, nothing more (api_routes)."""

from functools import partial

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Project, User, utcnow
from app.web import api_routes, deps
from app.web import members as members_api

from test_chat_posts import _setup, model  # noqa: F401
from test_members import member_login
from test_user_channels import ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def by_key(app, key: str) -> TestClient:
    """A program with the key and nothing else: no cookie of the site."""
    return TestClient(app, headers={"X-API-Key": key})


def test_every_method_of_the_site_is_decided_for_keys(ctx):
    """A new method of the site is either a key's (with its words for the reference) or the site's alone, with why."""
    app, _ = ctx
    methods = {f"{m.upper()} {path}" for path, ops in app.openapi()["paths"].items() if not path.startswith("/api/v1")
               for m in ops}
    assert methods - set(api_routes.BY_KEY) - set(api_routes.SITE_ONLY) == set()
    assert set(api_routes.BY_KEY) - methods == set() and set(api_routes.SITE_ONLY) - methods == set()
    assert set(api_routes.BY_KEY) & set(api_routes.SITE_ONLY) == set()
    rights = {"anyone", "project", "owner", "own_ai", "admin", "member", *deps.MEMBER_RIGHTS}
    assert all(set(m.who.split(",")) <= rights and m.text and m.group for m in api_routes.BY_KEY.values())


async def _other_project(tg_id: int) -> int:
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        project = Project(owner_id=user.id)
        s.add(project)
        await s.commit()
        return project.id


async def _expire(tg_id: int) -> None:
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        for p in (await s.execute(select(Project).where(Project.owner_id == user.id))).scalars():
            p.plan_expires_at = utcnow().replace(year=2000)
        await s.commit()


def test_the_owner_s_key_manages_the_project(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9991)
    ids = run(client, partial(_setup, 9991))
    api = by_key(app, owner.get("/api/api-key").json()["key"])

    me = api.get("/api/auth/me").json()
    assert me["role"] == "user" and me["project"]["id"] == ids["project"]
    # what the owner does on the site, the key does — and the site sees it
    assert api.put("/api/publications/settings", json={"daily_limit": 5}).status_code == 200
    assert owner.get("/api/publications").json()["settings"]["daily_limit"] == 5
    rubric = api.post("/api/rubrics", json={"preset": "day"}).json()
    assert api.put(f"/api/rubrics/{rubric['id']}", json={"name": "Вечер", "mode": "auto"}).json()["name"] == "Вечер"
    assert [r["name"] for r in owner.get("/api/rubrics").json()["items"]] == ["Вечер"]
    trigger = api.post("/api/triggers", json={"words": ["ракета"], "mode": "forms", "channels_mode": "all",
                                              "channel_ids": [], "notify": False, "notify_dm": False})
    assert trigger.status_code == 200 and trigger.json()["items"][0]["words"] == ["ракета"]
    made = api.post("/api/members", json={"login": "key.made", "ai_chat": True}).json()
    assert made["member"]["login"] == "key.made" and made["password"]
    assert api.put("/api/topics/settings", json={"enabled": True}).json()["enabled"] is True
    assert api.delete(f"/api/rubrics/{rubric['id']}").json() == {"ok": True}

    # the site's own: sign-ins, devices, other projects
    for method, url in (("get", "/api/devices"), ("post", "/api/projects"), ("get", "/api/projects"),
                        ("post", "/api/auth/intros"), ("post", "/api/ai/connect")):
        r = getattr(api, method)(url, **({"json": {"key": "feed", "origin": "x"}} if method == "post" else {}))
        assert r.status_code == 403 and r.json()["detail"] == deps.NOT_BY_KEY, (method, url, r.status_code)
    # only the key's project: not the owner's other one
    other = run(client, partial(_other_project, 9991))
    assert api.put(f"/api/projects/{other}/channel", json={"link": "@whatever"}).status_code == 404
    assert api.put(f"/api/projects/{ids['project']}/channel", json={"link": "@"}).status_code == 422

    # a wrong key, none at all, and a project without a plan
    assert TestClient(app, headers={"X-API-Key": "km_nope"}).get("/api/auth/me").status_code == 401
    assert TestClient(app, headers={"Authorization": f"Bearer {api.headers['x-api-key']}"}).get(
        "/api/auth/me").json()["role"] == "user"
    run(client, partial(_expire, 9991))
    r = api.get("/api/auth/me")
    assert r.status_code == 403 and "тарифа" in r.json()["detail"]


def test_a_key_is_its_holder_even_in_a_signed_in_browser(ctx, model):
    """The key decides, not the cookie: a staff member's key in the owner's browser is the staff member."""
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9992)
    run(client, partial(_setup, 9992))
    password = owner.post("/api/members", json={"login": "key.staff"}).json()["password"]
    staff, _ = member_login(app, "key.staff", password)
    key = staff.get("/api/api-key").json()["key"]
    assert owner.get("/api/auth/me", headers={"X-API-Key": key}).json()["role"] == "member"
    assert owner.get("/api/members", headers={"X-API-Key": key}).status_code == 403
    assert owner.get("/api/members").status_code == 200
    # signed out everywhere by a new password: the key too
    [member] = owner.get("/api/members").json()["items"]
    owner.post(f"/api/members/{member['id']}/password")
    assert TestClient(app, headers={"X-API-Key": key}).get("/api/auth/me").status_code == 401


def test_the_read_api_and_the_site_s_methods_share_the_rate(ctx, monkeypatch):
    app, client = ctx
    owner = sign_in(app, client, 9993)
    api = by_key(app, owner.get("/api/api-key").json()["key"])
    monkeypatch.setattr(deps, "API_RATE_LIMIT", 3)
    deps._calls.clear()
    assert api.get("/api/v1/stats").status_code == 200
    assert api.get("/api/auth/me").status_code == 200
    assert api.get("/api/channels").status_code == 200
    r = api.get("/api/auth/me")
    assert r.status_code == 429 and r.headers["retry-after"]
    assert api.get("/api/v1/stats").json()["error"]["code"] == "rate_limited"
    deps._calls.clear()


def _names(reference: dict) -> set[str]:
    return {f"{m['method']} {m['path']}" for g in reference["groups"] for m in g["methods"]}


def test_the_reference_shows_what_the_key_may(ctx, model):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9994)
    run(client, partial(_setup, 9994))
    mine = owner.get("/api/api-key/reference").json()
    names = _names(mine)
    assert {"POST /api/rubrics", "PUT /api/publications/settings", "POST /api/chat-posts/{post_id}/publish",
            "GET /api/posts"} <= names
    assert not any(n.startswith(("GET /api/accounts", "GET /api/devices")) for n in names)
    assert "POST /api/members/me/telegram" not in names  # a staff member's own
    [rubric] = [m for g in mine["groups"] for m in g["methods"] if f"{m['method']} {m['path']}" == "PUT /api/rubrics/{rubric_id}"]
    assert rubric["body_kind"] == "json" and {"name": "enabled", "type": "boolean", "required": False,
                                               "nullable": True, "options": None} in rubric["body"]
    assert rubric["params"] == [{"name": "rubric_id", "type": "integer", "required": True, "nullable": False,
                                 "options": None, "in": "path"}]
    [posts] = [m for g in mine["groups"] for m in g["methods"] if f"{m['method']} {m['path']}" == "GET /api/posts"]
    period = next(p for p in posts["params"] if p["name"] == "period")
    assert period["options"] == ["20m", "1h", "6h", "24h", "7d", "30d", "all"] and period["default"] == "all"
    [logo] = [m for g in mine["groups"] for m in g["methods"] if m["path"] == "/api/covers/logo" and m["method"] == "POST"]
    assert logo["body_kind"] == "form-data" and logo["body"][0]["type"] == "file"

    # a staff member: only what their rights allow
    plain = member_login(app, "ref.plain", owner.post("/api/members", json={"login": "ref.plain"}).json()["password"])[0]
    names = _names(plain.get("/api/api-key/reference").json())
    assert {"GET /api/posts", "GET /api/topics", "POST /api/members/me/telegram"} <= names
    assert not names & {"POST /api/rubrics", "POST /api/chat-posts/{post_id}/publish", "GET /api/chats",
                        "POST /api/channels", "PUT /api/smart", "POST /api/topics/{topic_id}/hide"}
    publisher = member_login(app, "ref.pub", owner.post(
        "/api/members", json={"login": "ref.pub", "ai_publish": True, "ai_teach": True}).json()["password"])[0]
    names = _names(publisher.get("/api/api-key/reference").json())
    assert {"POST /api/chat-posts/{post_id}/publish", "POST /api/topics/{topic_id}/hide",
            "POST /api/smart/posts/{post_id}/correct"} <= names
    assert not names & {"POST /api/rubrics", "GET /api/chats", "PUT /api/smart"}
    # the same by the key
    assert _names(by_key(app, publisher.get("/api/api-key").json()["key"]).get("/api/api-key/reference").json()) == names


def test_the_admin_s_key_and_any_page_may_call(ctx):
    app, client = ctx
    admin = TestClient(app)
    assert admin.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
    api = by_key(app, admin.get("/api/api-key").json()["key"])
    assert api.get("/api/auth/me").json()["role"] == "admin"
    assert api.get("/api/settings").status_code == 200
    names = _names(api.get("/api/api-key/reference").json())
    assert "GET /api/accounts" in names and "POST /api/rubrics" not in names
    # a page of another site may call it with a key (never with the site's cookie: "*" takes no credentials)
    r = api.options("/api/rubrics")
    assert r.status_code == 204 and r.headers["access-control-allow-origin"] == "*"
    assert "X-API-Key" in r.headers["access-control-allow-headers"] and "PATCH" in r.headers["access-control-allow-methods"]
