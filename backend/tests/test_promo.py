import asyncio
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import settings_store
from app.db import SessionLocal, engine
from app.logins import confirm_login
from app.models import Channel, LoginRequest, PromoCode, User, utcnow
from app.web import auth, promo, public_api


@pytest.fixture
def ctx():
    asyncio.run(engine.dispose())
    from app.web.main import app

    auth._starts.clear()
    promo._tries.clear()
    public_api._calls.clear()
    with TestClient(app) as client:

        async def reset():
            async with SessionLocal() as s:
                await s.execute(delete(LoginRequest))
                await s.execute(delete(PromoCode))
                await s.execute(delete(User))
                await s.execute(delete(Channel).where(Channel.link.like("@promo_%")))
                await settings_store.update(s, {"notify_bot_username": "km_test_bot"})

        client.portal.call(reset)
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
        yield app, client


def sign_in(app, client, tg_id: int) -> tuple[TestClient, int]:
    browser = TestClient(app)
    code = browser.post("/api/auth/tg/start").json()["link"].rsplit("login_", 1)[1]
    client.portal.call(confirm_login, code, {"id": tg_id, "first_name": f"u{tg_id}"})
    assert browser.get("/api/auth/tg/status").json()["status"] == "ok"
    user_id = next(u["id"] for u in client.get("/api/users").json()["items"] if u["tg_user_id"] == tg_id)
    return browser, user_id


def generate(client, plan: str, days: int | None, count: int = 1) -> list[str]:
    res = client.post("/api/promo", json={"plan": plan, "days": days, "count": count}).json()
    return [c["code"] for c in res["items"]]


def days_left(me: dict) -> float:
    expires = datetime.fromisoformat(me["plan"]["expires_at"].removesuffix("Z"))
    return (expires - utcnow()) / timedelta(days=1)


def test_promo_codes_give_extend_and_upgrade_plans(ctx):
    app, client = ctx
    user, _ = sign_in(app, client, 9501)

    # «Free Trial» on sign-up: channels, but no API
    assert user.get("/api/auth/me").json()["plan"]["id"] == "trial"
    key = user.get("/api/api-key").json()["key"]
    assert user.get("/api/v1/stats", headers={"X-API-Key": key}).json()["error"]["code"] == "no_api"

    # the admin generates codes; they are easy to type
    beta = generate(client, "pro", 15, count=20)
    assert len(set(beta)) == 20 and all(re.fullmatch(r"FEED-[A-Z2-9]{4}-[A-Z2-9]{4}", c) for c in beta)
    assert len(client.get("/api/promo").json()["items"]) == 20

    # no plan → Pro for 15 days; case and spaces do not matter
    res = user.post("/api/promo/redeem", json={"code": f"  {beta[0].lower()} "})
    assert res.status_code == 200 and res.json()["plan"]["id"] == "pro"
    me = user.get("/api/auth/me").json()
    assert me["plan"]["title"] == "Pro" and 14.9 < days_left(me) <= 15
    assert user.post("/api/channels", json={"links": "@promo_one"}).status_code == 200

    # the same plan: the days are added to what is left
    user.post("/api/promo/redeem", json={"code": beta[1]})
    assert 29.9 < days_left(user.get("/api/auth/me").json()) <= 30

    # a used or unknown code is not valid
    for code in (beta[0], "FEED-AAAA-AAAA"):
        r = user.post("/api/promo/redeem", json={"code": code})
        assert r.status_code == 409 and r.json()["detail"] == "Промокод недействителен"

    # a better plan replaces it at once, with the code's term (here: forever)
    [unlimited] = generate(client, "agency", None)
    user.post("/api/promo/redeem", json={"code": unlimited})
    me = user.get("/api/auth/me").json()
    assert me["plan"]["id"] == "agency" and me["plan"]["expires_at"] is None and me["plan"]["channels"] == 1000

    # a worse plan is refused, and the code stays unused for someone else
    r = user.post("/api/promo/redeem", json={"code": beta[2]})
    assert r.status_code == 409 and r.json()["detail"] == "Промокод недействителен"
    codes = {c["code"]: c for c in client.get("/api/promo").json()["items"]}
    assert codes[beta[2]]["used_by"] is None and codes[beta[0]]["used_by"]["id"]

    # a forever plan cannot be extended: the code is not wasted
    [more] = generate(client, "agency", 30)
    r = user.post("/api/promo/redeem", json={"code": more})
    assert r.status_code == 409 and "бессрочный" in r.json()["detail"]
    assert client.get("/api/promo").json()["items"][0]["used_by"] is None

    # only users enter codes, only the admin makes them
    assert client.post("/api/promo/redeem", json={"code": more}).status_code == 403
    assert user.post("/api/promo", json={"plan": "pro", "days": 1}).status_code == 403
    assert user.get("/api/promo").status_code == 403

    # an unused code can be deleted, a used one stays as a record
    assert client.delete(f"/api/promo/{beta[3]}").status_code == 200
    assert client.delete(f"/api/promo/{beta[0]}").status_code == 404


def test_admin_sets_plans_and_expired_plans_stop_working(ctx):
    app, client = ctx
    user, user_id = sign_in(app, client, 9502)
    [u] = [u for u in client.get("/api/users").json()["items"] if u["id"] == user_id]
    project_id = u["projects"][0]["id"]

    def set_plan(**body) -> dict:
        """The admin's change of the user's (only) project; returns that project as the admin sees it."""
        return client.patch(f"/api/users/{user_id}/projects/{project_id}", json=body).json()["projects"][0]

    soon = (utcnow() + timedelta(days=3)).isoformat() + "Z"
    u = set_plan(plan="agency", expires_at=soon)
    assert u["plan"]["id"] == "agency" and u["plan"]["expires_at"].startswith(soon[:16])
    assert 2.9 < days_left(user.get("/api/auth/me").json()) <= 3

    # forever
    u = set_plan(expires_at=None)
    assert u["plan"]["expires_at"] is None

    # ran out: the plan no longer works, the admin still sees what it was
    past = (utcnow() - timedelta(minutes=1)).isoformat() + "Z"
    u = set_plan(expires_at=past)
    assert u["plan"] is None and u["expired_plan"]["id"] == "agency"
    assert user.get("/api/auth/me").json()["plan"] is None
    assert user.post("/api/channels", json={"links": "@promo_two"}).status_code == 403
    assert user.get("/api/posts").json() == {"items": [], "has_more": False, "no_plan": True}
    # an expired plan is no plan: any code gives a fresh one
    [code] = generate(client, "pro", 15)
    assert user.post("/api/promo/redeem", json={"code": code}).json()["plan"]["id"] == "pro"

    # taken away
    u = set_plan(plan="")
    assert u["plan"] is None and u["expired_plan"] is None
    assert client.patch(f"/api/users/{user_id}/projects/{project_id}", json={"plan": "gold"}).status_code == 422


def test_guessing_codes_is_slowed_down(ctx):
    app, client = ctx
    user, _ = sign_in(app, client, 9503)
    for _ in range(promo.TRIES_LIMIT):
        assert user.post("/api/promo/redeem", json={"code": "FEED-ZZZZ-ZZZZ"}).status_code == 409
    [code] = generate(client, "pro", 15)
    assert user.post("/api/promo/redeem", json={"code": code}).status_code == 429


