"""Money: the balance, plans bought from it, the AI paid from the plan and the balance, limits, the end of a plan."""

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text

from app import ai, billing, settings_store
from app.db import SessionLocal
from app.models import AiUsage, Channel, Member, Project, ProjectChannel, User, utcnow
from app.web import members as members_api

from test_members import member_login
from test_user_channels import ctx, give_plan, run, sign_in  # noqa: F401  (ctx is a fixture)


@pytest.fixture(autouse=True)
def _service_settings(ctx):
    """What a test here sets of the service's money is put back: the other tests' AI is the owner's own."""
    yield
    async def reset():
        async with SessionLocal() as s:
            await settings_store.update(s, {"ai_key": "", "ai_paused": "", "ai_markup_pct": 200,
                                            "payments_live": False})

    run(ctx[1], reset)


async def _project(tg_id: int) -> Project:
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        return await s.scalar(select(Project).where(Project.owner_id == user.id).order_by(Project.id))


async def _user(tg_id: int) -> User:
    async with SessionLocal() as s:
        return await s.scalar(select(User).where(User.tg_user_id == tg_id))


def credit(client, tg_id: int, amount: float):
    user = run(client, _user, tg_id)
    r = client.post(f"/api/billing/admin/users/{user.id}/balance", json={"amount": amount, "note": "Тест"})
    assert r.status_code == 200, r.text
    return r.json()["balance"]


def test_a_new_account_gets_the_trial_once_and_sees_the_plans(ctx):
    app, client = ctx
    from test_user_channels import confirm_login
    from fastapi.testclient import TestClient

    browser = TestClient(app)
    code = browser.post("/api/auth/tg/start").json()["link"].rsplit("login_", 1)[1]
    client.portal.call(confirm_login, code, {"id": 9801, "first_name": "Новый"})
    assert browser.get("/api/auth/tg/status").json()["status"] == "ok"
    me = browser.get("/api/auth/me").json()
    # «Free Trial» on the first project; no end till the payments go live
    assert me["plan"]["id"] == "trial" and me["plan"]["expires_at"] is None and me["plan"]["channels"] == 50
    state = browser.get("/api/billing").json()
    assert state["balance"] == 0 and state["plan"]["title"] == "Free Trial" and state["ai"]["included"] == 1
    assert state["ai"]["renews_at"] is None  # the trial's AI is once
    # the plans everyone sees: dev:service:plan is nobody's but its holders'
    public = TestClient(app).get("/api/plans").json()["items"]
    assert [p["title"] for p in public] == ["Free Trial", "Starter", "Pro", "Studio", "Agency"]
    assert [p["price"] for p in public] == [None, 9, 29, 69, 79]
    assert "dev" not in [p["id"] for p in state["plans"]]
    # a second project gets no trial: it is once a Telegram account
    second = browser.post("/api/projects").json()["id"]
    assert browser.post(f"/api/projects/{second}/open").status_code == 200
    assert browser.get("/api/auth/me").json()["plan"] is None
    user = run(client, _user, 9801)
    assert user.trial_used_at is not None
    # top-ups are not open yet
    r = browser.post("/api/billing/topup")
    assert r.status_code == 503 and r.json()["detail"] == "Пополнение временно недоступно"


def test_buying_upgrading_and_downgrading(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9802)
    run(client, give_plan, 9802, "trial")
    r = owner.post("/api/billing/buy", json={"plan": "pro"})
    assert r.status_code == 402 and "Не хватает $29,00" in r.json()["detail"]
    assert owner.post("/api/billing/buy", json={"plan": "dev"}).status_code == 403
    assert owner.post("/api/billing/buy", json={"plan": "trial"}).status_code == 403

    assert credit(client, 9802, 100) == 100
    bought = owner.post("/api/billing/buy", json={"plan": "pro"}).json()
    assert bought["done"] == {"started": "pro"} and bought["balance"] == 71 and bought["plan"]["id"] == "pro"
    assert bought["ai"]["included"] == 15 and bought["ai"]["used"] == 0
    assert owner.post("/api/billing/buy", json={"plan": "pro"}).status_code == 409  # it is in force already

    # a better one at once: the unused part of the old comes back
    async def half_used():
        async with SessionLocal() as s:
            project = await s.get(Project, (await _project(9802)).id)
            project.plan_started_at -= timedelta(days=15)
            project.plan_expires_at -= timedelta(days=15)
            await s.commit()

    run(client, half_used)
    up = owner.post("/api/billing/buy", json={"plan": "agency"}).json()
    assert up["done"] == {"started": "agency"} and up["balance"] == pytest.approx(71 + 14.5 - 79, abs=0.01)
    # a cheaper one at the end of the period; changing one's mind keeps the plan
    down = owner.post("/api/billing/buy", json={"plan": "starter"}).json()
    assert down["done"] == {"next": "starter"} and down["next_plan"]["id"] == "starter" and down["renew_price"] == 9
    assert owner.post("/api/billing/buy", json={"plan": "agency"}).json()["next_plan"] is None
    history = owner.get("/api/billing/history").json()["items"]
    assert [(h["kind"], h["amount"]) for h in history][:4] == [
        ("plan", -79), ("refund", pytest.approx(14.5, abs=0.01)), ("plan", -29), ("admin", 100)]
    assert history[-1]["title"] == "Начисление" and history[-1]["note"] == "Тест"

    # staff never see the money
    members_api._failed.clear()
    password = owner.post("/api/members", json={"login": "bill.staff"}).json()["password"]
    staff, _ = member_login(app, "bill.staff", password)
    assert staff.get("/api/billing").status_code == 403 and staff.post("/api/billing/buy", json={"plan": "pro"}).status_code == 403


@pytest.fixture
def service_ai(monkeypatch):
    """The service's OpenRouter linked by the admin, and a fake OpenRouter that charges `cost` a request."""
    calls: list[httpx.Request] = []
    state = {"cost": 0.5, "status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/credits"):
            return httpx.Response(200, json={"data": {"total_credits": 10, "total_usage": 7}})
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"label": "Feedor"}})
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": {"message": "no"}})
        return httpx.Response(200, json={"id": "gen", "model": "deepseek/deepseek-v4.1-flash",
                                         "choices": [{"message": {"content": "{}"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": state["cost"]}})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    ai._balance_cache.clear()
    return calls, state


async def _link_service(key: str = "sk-or-service") -> None:
    async with SessionLocal() as s:
        await settings_store.update(s, {"ai_key": ai.encrypt_key(key)})


def test_the_service_s_ai_is_paid_from_the_plan_then_the_balance(ctx, service_ai):
    calls, state = service_ai
    app, client = ctx
    owner = sign_in(app, client, 9803)
    run(client, give_plan, 9803, "trial")  # $1 of AI, once
    project = run(client, _project, 9803)
    assert owner.get("/api/auth/me").json()["ai_linked"] is False  # the admin has not linked it yet
    run(client, _link_service)
    me = owner.get("/api/auth/me").json()
    assert me["ai_linked"] is True and me["ai_own"] is False

    async def ask():
        return await ai.complete(user_id=project.owner_id, project_id=project.id, operation="topics",
                                 messages=[{"role": "user", "content": "hi"}], model="owner/choice")

    run(client, ask)
    sent = calls[-1]
    assert sent.headers["authorization"] == "Bearer sk-or-service"
    assert b'"deepseek/deepseek-v4.1-flash"' in sent.content  # the admin's model, not one asked for
    # $0.50 at OpenRouter is $1.00 for the owner (×2): the trial's AI is spent
    ai_state = owner.get("/api/billing").json()["ai"]
    assert ai_state["used"] == 1 and ai_state["extra_used"] == 0
    with pytest.raises(ai.AiPaused, match="нет денег"):
        run(client, ask)
    credit(client, 9803, 3)
    run(client, ask)  # from the balance now
    state_after = owner.get("/api/billing").json()
    assert state_after["balance"] == 2 and state_after["ai"]["extra_used"] == 1
    history = owner.get("/api/billing/history").json()["items"]
    assert history[0]["kind"] == "ai" and history[0]["amount"] == -1 and history[0]["title"] == "ИИ сверх тарифа"
    # the owner's cap on the extra, and switching it off
    owner.patch("/api/billing", json={"ai_extra_cap": 1})
    with pytest.raises(ai.AiPaused, match="лимит докупки"):
        run(client, ask)
    owner.patch("/api/billing", json={"clear_ai_extra_cap": True, "ai_extra": False})
    with pytest.raises(ai.AiPaused, match="Докупать ИИ"):
        run(client, ask)
    owner.patch("/api/billing", json={"ai_extra": True})
    run(client, ask)

    # «Расход»: what the owner paid, at the service's prices; no models, no tokens, no OpenRouter balance
    usage = owner.get("/api/ai/usage", params={"period": "24h"}).json()
    assert usage["own"] is False and usage["cost"] == 3 and usage["tokens"] is None and usage["balance"] is None
    assert "model" not in usage["totals"]
    # the owner cannot link an OpenRouter of their own
    assert owner.get("/api/ai").status_code == 403

    # the admin sees what it cost and what was paid
    month = client.get("/api/billing/admin").json()
    assert month["linked"] and month["month"]["cost"] == 1.5 and month["month"]["charged"] == 3
    assert month["balance"]["credits_left"] == 3

    # OpenRouter's balance runs out: the AI of everyone stands, the admin is told; topped up, it goes on by itself
    state["status"] = 402
    with pytest.raises(ai.AiPaused, match="временно недоступен"):
        run(client, ask)
    assert client.get("/api/billing/admin").json()["paused_reason"] == "no_credits"
    run(client, ai.resume_paid)
    state["status"] = 200
    run(client, ask)


def test_the_admin_s_markup_and_models(ctx, service_ai, monkeypatch):
    app, client = ctx
    run(client, _link_service)
    assert client.patch("/api/billing/admin", json={"markup_pct": 50}).status_code == 422
    assert client.patch("/api/billing/admin", json={"markup_pct": 300}).json()["markup_pct"] == 300
    owner = sign_in(app, client, 9804)
    project = run(client, _project, 9804)

    async def ask():
        await ai.complete(user_id=project.owner_id, project_id=project.id, operation="chat",
                          messages=[{"role": "user", "content": "hi"}])
        async with SessionLocal() as s:
            return (await s.scalar(select(AiUsage).order_by(AiUsage.id.desc()))).charged

    assert run(client, ask) == 1_500_000  # $0.50 ×3
    assert owner.get("/api/billing").json()["ai"]["used"] == 1.5


def test_limits_pause_what_is_over_and_bring_it_back(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9805)
    run(client, give_plan, 9805, "starter")  # one staff member, 10 triggers
    password = owner.post("/api/members", json={"login": "lim.one"}).json()["password"]

    async def add_channels(n: int) -> None:
        async with SessionLocal() as s:
            project = await _project(9805)
            for i in range(n):
                ch = Channel(link=f"@lim_{i}", username=f"lim_{i}", status="active")
                s.add(ch)
                await s.flush()
                s.add(ProjectChannel(project_id=project.id, channel_id=ch.id,
                                     created_at=utcnow() - timedelta(minutes=n - i)))
            await s.commit()

    run(client, add_channels, 60)
    run(client, give_plan, 9805, "trial")  # 50 channels, no staff
    run(client, billing.limits_step)
    listed = owner.get("/api/channels").json()
    paused = [c["username"] for c in listed["items"] if c["paused"]]
    assert listed["limit"] == 50 and len(paused) == 10 and set(paused) == {f"lim_{i}" for i in range(50, 60)}
    # the staff member cannot sign in while paused
    r = member_login(app, "lim.one", password)[1]
    assert r.status_code == 403 and "не включает столько сотрудников" in r.json()["detail"]
    assert owner.get("/api/members").json()["items"][0]["paused"] is True
    # the owner swaps which channels are read
    ids = {c["username"]: c["id"] for c in listed["items"]}
    r = owner.put(f"/api/channels/{ids['lim_55']}/paused", json={"paused": False})
    assert r.status_code == 409 and "сначала поставьте на паузу другой" in r.json()["detail"]
    assert owner.put(f"/api/channels/{ids['lim_0']}/paused", json={"paused": True}).json() == {"paused": True}
    assert owner.put(f"/api/channels/{ids['lim_55']}/paused", json={"paused": False}).json() == {"paused": False}
    # a plan with room: everything comes back
    credit(client, 9805, 100)
    assert owner.post("/api/billing/buy", json={"plan": "pro"}).json()["paused"] == {
        "channels": 0, "members": 0, "triggers": 0}
    assert member_login(app, "lim.one", password)[1].status_code == 200


async def _age(tg_id: int, days: float, plan: str | None = None) -> None:
    """The plan's end moved: `days` from now (below zero: in the past)."""
    async with SessionLocal() as s:
        project = await s.get(Project, (await _project(tg_id)).id)
        if plan:
            project.plan = plan
        project.plan_started_at = utcnow() + timedelta(days=days) - billing.PERIOD
        project.plan_expires_at = utcnow() + timedelta(days=days)
        await s.commit()


def test_the_end_of_a_plan_renewal_reminders_freeze_and_deletion(ctx, monkeypatch):
    app, client = ctx
    told: list[tuple[int, str]] = []

    async def fake_tell(tg_user_id, text_):
        told.append((tg_user_id, text_))
        return True

    monkeypatch.setattr("app.notify.tell_user", fake_tell)
    owner = sign_in(app, client, 9806)
    run(client, give_plan, 9806, "trial")
    owner.post("/api/channels", json={"links": "@life_chan"})
    credit(client, 9806, 30)
    assert owner.post("/api/billing/buy", json={"plan": "pro"}).json()["balance"] == 1

    # 5 days before the end with too little money: told what is missing, once a day
    run(client, _age, 9806, 4.5)
    run(client, billing.lifecycle_step)
    run(client, billing.lifecycle_step)
    assert len(told) == 1 and "через 5 дней" in told[0][1] and "не хватает $28,00" in told[0][1]
    # enough on the balance: renewed at the end, from the moment it ended
    credit(client, 9806, 40)
    run(client, _age, 9806, -0.01)
    run(client, billing.lifecycle_step)
    assert "продлён" in told[-1][1] and owner.get("/api/billing").json()["balance"] == pytest.approx(12)
    # not enough again: frozen, told once
    owner.patch("/api/billing", json={"auto_renew": False})
    run(client, _age, 9806, -1)
    run(client, billing.lifecycle_step)
    run(client, billing.lifecycle_step)
    frozen = [t for _, t in told if "заморожен" in t]
    assert len(frozen) == 1
    state = owner.get("/api/billing").json()
    assert state["plan"] is None and state["expired_plan"]["id"] == "pro" and state["delete_at"]
    # before the payments are live nothing is deleted
    run(client, _age, 9806, -30)
    run(client, billing.lifecycle_step)
    assert run(client, _project, 9806).plan == "pro"

    async def go_live():
        async with SessionLocal() as s:
            await billing.go_live(s)
            await s.commit()

    run(client, go_live)
    run(client, _age, 9806, -5.5)
    run(client, billing.lifecycle_step)
    assert "удалён через 2 дня" in told[-1][1] or "удалён" in told[-1][1]
    run(client, _age, 9806, -8)
    run(client, billing.lifecycle_step)
    assert "удалён" in told[-1][1] and "без возможности восстановления" in told[-1][1]
    fresh = run(client, _project, 9806)
    assert fresh.plan == "" and owner.get("/api/channels").json()["items"] == []
    assert owner.get("/api/billing").json()["balance"] == pytest.approx(12)  # the money stays


def test_going_live_starts_the_trials(ctx):
    app, client = ctx
    sign_in(app, client, 9807)
    run(client, give_plan, 9807, "trial")

    async def waiting():
        async with SessionLocal() as s:
            (await s.get(Project, (await _project(9807)).id)).plan_expires_at = None
            await s.commit()

    run(client, waiting)
    assert client.get("/api/billing/admin").json()["trials_waiting"] >= 1
    started = client.post("/api/billing/admin/live").json()
    assert started["trials_started"] >= 1 and started["payments_live"] is True
    project = run(client, _project, 9807)
    assert 29.9 < (project.plan_expires_at - utcnow()).total_seconds() / 86400 <= 30
    assert client.post("/api/billing/admin/live").status_code == 409


def test_dev_plan_is_given_forever_by_the_admin_only(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9808)
    user = run(client, _user, 9808)
    project = run(client, _project, 9808)
    soon = (utcnow() + timedelta(days=3)).isoformat() + "Z"
    r = client.patch(f"/api/users/{user.id}/projects/{project.id}", json={"plan": "dev", "expires_at": soon})
    assert r.json()["projects"][0]["plan"]["id"] == "dev" and r.json()["projects"][0]["plan"]["expires_at"] is None
    r = client.patch(f"/api/users/{user.id}/projects/{project.id}", json={"expires_at": soon})
    assert r.status_code == 409 and "только бессрочно" in r.json()["detail"]
    assert client.post("/api/promo", json={"plan": "dev", "days": None}).status_code == 422
    assert client.post("/api/promo", json={"plan": "trial", "days": 30}).status_code == 422
    assert "dev" not in [p["id"] for p in client.get("/api/promo").json()["plans"]]
    state = owner.get("/api/billing").json()
    assert state["plan"]["title"] == "dev:service:plan" and state["ai"] is None
    assert "dev" in [p["id"] for p in state["plans"]]  # its holder sees it
    assert owner.get("/api/auth/me").json()["ai_own"] is True
    assert owner.get("/api/ai").status_code == 200  # their own OpenRouter, as before
    assert owner.post("/api/billing/buy", json={"plan": "pro"}).status_code == 409


def test_the_move_to_paid_plans(ctx):
    """Everyone to the trial, the two projects to dev:service:plan, only they keep their own OpenRouter."""
    from app.db import PLANS_MOVED, _move_to_plans, engine

    async def scenario():
        async with SessionLocal() as s:
            dev = User(tg_user_id=98101, username="TankiOnline2005")
            other = User(tg_user_id=98102, username="someone")
            s.add_all([dev, other])
            await s.flush()
            s.add_all([Project(owner_id=dev.id, plan="pro", channel_title="Шифровальня"),
                       Project(owner_id=dev.id, plan="pro", channel_title="Другое"),
                       Project(owner_id=other.id, plan="agency", channel_title="Шифровальня")])
            from app.models import AiAccount
            s.add_all([AiAccount(user_id=dev.id, key_enc="x"), AiAccount(user_id=other.id, key_enc="y")])
            await s.execute(text("DELETE FROM settings WHERE key = :k"), {"k": PLANS_MOVED})
            await s.commit()
        async with engine.begin() as conn:
            await conn.run_sync(_move_to_plans)
        async with SessionLocal() as s:
            from app.models import AiAccount
            rows = (await s.execute(select(Project.channel_title, Project.plan, Project.owner_id)
                                    .where(Project.owner_id.in_([dev.id, other.id])).order_by(Project.id))).all()
            keys = {a.user_id: a.key_enc for a in (await s.execute(select(AiAccount))).scalars()}
            return rows, keys, dev.id, other.id

    rows, keys, dev_id, other_id = run(client := ctx[1], scenario)
    assert [(t, p) for t, p, _ in rows] == [("Шифровальня", "dev"), ("Другое", "trial"), ("Шифровальня", "trial")]
    assert keys[dev_id] == "x" and keys[other_id] is None


def test_the_dev_projects_are_found_whoever_of_the_two_owns_them(ctx):
    from app.db import DEV_FIXED, _fix_dev_plans, engine

    async def scenario():
        async with SessionLocal() as s:
            owner = User(tg_user_id=98201, username="Kindergarten4631")
            s.add(owner)
            await s.flush()
            s.add_all([Project(owner_id=owner.id, plan="trial", channel_title="ШИФРОВАЛЬНЯ 🔐"),
                       Project(owner_id=owner.id, plan="trial", channel_title="Другое")])
            await s.execute(text("DELETE FROM settings WHERE key = :k"), {"k": DEV_FIXED})
            await s.commit()
        async with engine.begin() as conn:
            await conn.run_sync(_fix_dev_plans)
        async with SessionLocal() as s:
            return (await s.execute(select(Project.channel_title, Project.plan).where(Project.owner_id == owner.id)
                                    .order_by(Project.id))).all()

    assert run(ctx[1], scenario) == [("ШИФРОВАЛЬНЯ 🔐", "dev"), ("Другое", "trial")]
