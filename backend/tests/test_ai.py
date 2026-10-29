import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app import ai
from app.db import SessionLocal
from app.models import AiAccount, AiUsage, User, utcnow
from app.web import members as members_api

from test_user_channels import ctx, give_plan, run, sign_in  # noqa: F401  (ctx is a fixture)

MODELS = {"data": [
    {"id": "anthropic/claude-haiku-5.5", "name": "Haiku", "context_length": 1000,
     "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
     "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
     "supported_parameters": ["structured_outputs", "tools"]},
    {"id": "cheap/text-only", "name": "Cheap", "context_length": 1000,
     "pricing": {"prompt": "0.00000001", "completion": "0.00000002"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
     "supported_parameters": ["structured_outputs"]},
    {"id": "router/auto", "name": "Auto", "pricing": {"prompt": "-1", "completion": "-1"},
     "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "supported_parameters": []},
]}


@pytest.fixture
def openrouter(monkeypatch):
    """A fake OpenRouter: the models list, the key and credits, chat completions (with a status to answer)."""
    calls: list[httpx.Request] = []
    state = {"status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        if path.endswith("/credits"):
            return httpx.Response(200, json={"data": {"total_credits": 10, "total_usage": 2.5}})
        if path.endswith("/key"):
            return httpx.Response(200, json={"data": {"label": "Feedor", "limit": None, "usage_daily": 0.01}})
        if path.endswith("/chat/completions"):
            if state["status"] != 200:
                return httpx.Response(state["status"], json={"error": {"message": "no"}})
            return httpx.Response(200, json={"id": "gen-1", "model": "anthropic/claude-haiku-5.5",
                                             "choices": [{"message": {"content": "{}"}}],
                                             "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                                       "prompt_tokens_details": {"cached_tokens": 40},
                                                       "cost": 0.0012}})
        return httpx.Response(404)

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(ai, "_models_cache", (0.0, []))
    ai._balance_cache.clear()
    return calls, state


async def _user_id(tg_id: int) -> int:
    async with SessionLocal() as s:
        return await s.scalar(select(User.id).where(User.tg_user_id == tg_id))


def link(owner, monkeypatch, key="sk-or-test"):
    """The whole OAuth round: to openrouter.ai and back with a code."""
    async def fake_exchange(code, verifier):
        assert code == "the-code" and len(verifier) > 40
        return key

    monkeypatch.setattr(ai, "exchange_code", fake_exchange)
    url = owner.post("/api/ai/connect", json={"origin": "http://testserver"}).json()["url"]
    q = parse_qs(urlparse(url).query)
    assert url.startswith(ai.AUTH_URL) and q["code_challenge_method"] == ["S256"]
    assert q["callback_url"] == ["http://testserver/api/ai/callback"]
    r = owner.get("/api/ai/callback", params={"code": "the-code", "state": q["state"][0]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/#/openrouter?connected=1"


def test_the_owner_links_openrouter_and_unlinking_keeps_the_settings(ctx, monkeypatch, openrouter):
    app, client = ctx
    owner = sign_in(app, client, 9601)
    run(client, give_plan, 9601, "dev")  # the owner's own OpenRouter
    assert owner.get("/api/auth/me").json()["ai_linked"] is False
    assert owner.post("/api/ai/connect", json={"origin": "https://evil.example"}).status_code == 422
    # a code that comes back without the state we sent is not taken
    owner.post("/api/ai/connect", json={"origin": "http://testserver"})
    r = owner.get("/api/ai/callback", params={"code": "x", "state": "forged"}, follow_redirects=False)
    assert "error" in r.headers["location"]

    link(owner, monkeypatch)
    state = owner.get("/api/ai").json()
    assert state["linked"] and owner.get("/api/auth/me").json()["ai_linked"] is True
    # just linked: the warning on tokens and models waits to be accepted, and once accepted it is kept
    assert state["warning_accepted"] is False
    assert owner.post("/api/ai/accept-warning").json()["warning_accepted"] is True
    assert owner.get("/api/ai").json()["warning_accepted"] is True
    uid = run(client, _user_id, 9601)

    async def stored():
        async with SessionLocal() as s:
            return (await s.get(AiAccount, uid)).key_enc

    enc = run(client, stored)
    assert "sk-or-test" not in enc and ai.decrypt_key(enc) == "sk-or-test"  # kept encrypted

    # models: only those that can do the operation, cheapest first; a wrong one is refused
    filter_models = owner.get("/api/ai/models", params={"operation": "filter"}).json()
    assert [m["id"] for m in filter_models["items"]] == ["cheap/text-only", "anthropic/claude-haiku-5.5"]
    # the pictures' own model is no more: the smart feed reads the text on them
    assert owner.get("/api/ai/models", params={"operation": "vision"}).status_code == 422
    assert "vision" not in [m["operation"] for m in owner.get("/api/ai").json()["models"]]
    saved = owner.put("/api/ai", json={"models": {"filter": "cheap/text-only"}, "monthly_limit_usd": 5}).json()
    assert {m["operation"]: m["model"] for m in saved["models"]}["filter"] == "cheap/text-only"
    assert saved["monthly_limit_usd"] == 5

    # unlinking drops only the key
    after = owner.delete("/api/ai").json()
    assert not after["linked"] and after["monthly_limit_usd"] == 5
    assert {m["operation"]: m["model"] for m in after["models"]}["filter"] == "cheap/text-only"
    assert run(client, stored) is None

    # linked again: the warning once more
    link(owner, monkeypatch)
    assert owner.get("/api/ai").json()["warning_accepted"] is False


def test_requests_are_paid_by_the_owner_written_down_and_capped(ctx, monkeypatch, openrouter):
    calls, state = openrouter
    app, client = ctx
    owner = sign_in(app, client, 9602)
    run(client, give_plan, 9602, "dev")  # the owner's own OpenRouter
    uid = run(client, _user_id, 9602)

    async def ask():
        return await ai.complete(user_id=uid, operation="filter", messages=[{"role": "user", "content": "hi"}])

    with pytest.raises(ai.AiPaused):
        run(client, ask)  # nothing linked yet
    link(owner, monkeypatch)
    run(client, ask)
    sent = calls[-1]
    assert sent.headers["authorization"] == "Bearer sk-or-test"
    assert json.loads(sent.content)["model"] == ai.OPERATIONS["filter"]["default"]  # the operation's default

    usage = owner.get("/api/ai/usage", params={"period": "24h", "tz": 180}).json()
    assert usage["requests"] == 1 and usage["cost"] == 0.0012 and len(usage["bars"]) == 24
    assert usage["tokens"] == {"prompt": 100, "completion": 20, "cached": 40}
    assert usage["totals"]["operation"][0]["title"] == "Фильтрация в умной ленте"
    assert usage["totals"]["person"][0]["title"] == "Вы"
    assert usage["balance"]["credits_left"] == 7.5

    # our monthly cap: reached, nothing more is sent
    owner.put("/api/ai", json={"monthly_limit_usd": 0.001})
    before = len(calls)
    with pytest.raises(ai.AiPaused, match="лимит"):
        run(client, ask)
    assert len(calls) == before
    owner.put("/api/ai", json={"clear_monthly_limit": True})

    # OpenRouter says the credits are gone: the AI stands still until a new link
    state["status"] = 402
    with pytest.raises(ai.AiPaused, match="закончились"):
        run(client, ask)
    assert owner.get("/api/ai").json()["paused"]
    state["status"] = 200
    with pytest.raises(ai.AiPaused):
        run(client, ask)
    link(owner, monkeypatch)
    run(client, ask)

    async def old_spending():  # last month's spending does not count towards this month's cap
        async with SessionLocal() as s:
            s.add(AiUsage(user_id=uid, operation="chat", model="m", cost=50, at=ai.month_start() - timedelta(days=1)))
            await s.commit()
            return await ai.spent_this_month(s, uid)

    assert run(client, old_spending) == pytest.approx(0.0024)


def test_staff_get_ai_switches_and_see_whether_the_owner_linked(ctx, monkeypatch, openrouter):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9603)
    run(client, give_plan, 9603, "dev")  # the owner's own OpenRouter
    created = owner.post("/api/members", json={"login": "ai.staff", "ai_configure": True}).json()
    member = created["member"]
    assert (member["ai_configure"], member["ai_chat"]) == (True, False) and "ai_personal_feed" not in member
    patched = owner.patch(f"/api/members/{member['id']}", json={"ai_chat": True}).json()
    assert (patched["ai_configure"], patched["ai_chat"], patched["can_edit_channels"]) == (True, True, False)

    from test_members import member_login
    staff, r = member_login(app, "ai.staff", created["password"])
    assert r.status_code == 200
    me = staff.get("/api/auth/me").json()
    assert me["ai_linked"] is False and me["member"]["ai_chat"] is True
    assert staff.get("/api/ai").status_code == 403  # the settings are the owner's
    link(owner, monkeypatch)
    assert staff.get("/api/auth/me").json()["ai_linked"] is True
