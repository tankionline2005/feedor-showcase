"""«MCP-коннектор»: the site's methods as an AI app's tools, by the API key or by the connector's own sign-in."""

import base64
import hashlib
import re
from functools import partial
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app.web import mcp
from app.web import members as members_api

from test_chat_posts import _setup, model  # noqa: F401
from test_members import member_login
from test_user_channels import ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def rpc(client, method: str, params: dict | None = None, id_=1, **headers):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
                       headers=headers)


def call(client, name: str, args: dict | None = None, **headers) -> dict:
    r = rpc(client, "tools/call", {"name": name, "arguments": args or {}}, **headers)
    assert r.status_code == 200, r.text
    return r.json()


def tool_names(client, **headers) -> set[str]:
    return {t["name"] for t in rpc(client, "tools/list", **headers).json()["result"]["tools"]}


def test_the_key_s_holder_gets_the_site_as_tools(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9961)
    run(client, partial(_setup, 9961))
    key = owner.get("/api/api-key").json()["key"]
    ai = TestClient(app, headers={"X-API-Key": key})

    # no key: where to sign in
    r = TestClient(app).post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 401 and 'resource_metadata="' in r.headers["www-authenticate"]

    init = rpc(ai, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "1"}}).json()["result"]
    assert init["protocolVersion"] == "2025-03-26" and init["capabilities"]["tools"] == {"listChanged": False}
    assert ai.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    names = tool_names(ai)
    # every tool the instructions name is there
    assert set(re.findall(r"\b(?:get|post|put|patch|delete)_[a-z_]+\b", init["instructions"])) <= names
    assert {"get_data_project", "post_rubrics", "put_publications_settings"} <= names
    assert not names & {"get_live", "post_api_key_reset", "post_covers_logo", "get_accounts"}

    assert '"role": "user"' in call(ai, "get_auth_me")["result"]["content"][0]["text"]
    done = call(ai, "put_publications_settings", {"daily_limit": 10})["result"]
    assert done["isError"] is False and owner.get("/api/publications").json()["settings"]["daily_limit"] == 10
    rubric = call(ai, "post_rubrics", {"preset": "day"})["result"]
    assert not rubric["isError"] and '"Итоги дня"' in rubric["content"][0]["text"]
    refused = call(ai, "put_rubrics_rubric_id", {"rubric_id": 999999, "name": "x"})["result"]
    assert refused["isError"] and "404" in refused["content"][0]["text"]
    picture = call(ai, "get_covers_preview_jpg", {"title": "Проба"})["result"]["content"][0]
    assert picture["type"] == "image" and picture["mimeType"] == "image/jpeg" and base64.b64decode(picture["data"])
    assert rpc(ai, "tools/call", {"name": "get_accounts"}).json()["error"]["code"] == -32602
    assert rpc(ai, "resources/list").json()["error"]["code"] == -32601
    assert TestClient(app).get("/mcp").status_code == 405


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(hashlib.sha256(b"probe").digest()).rstrip(b"=").decode() * 2
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _connect(app, browser: TestClient, name: str = "Claude") -> dict:
    """What an AI app does to be let in: register, send the person to allow it, trade the code for tokens."""
    app_side = TestClient(app)
    meta = app_side.get("/.well-known/oauth-authorization-server").json()
    assert app_side.get("/.well-known/oauth-protected-resource").json()["resource"].endswith("/mcp")
    redirect = "https://claude.ai/api/mcp/auth_callback"
    client_id = app_side.post(urlsplit(meta["registration_endpoint"]).path,
                              json={"client_name": name, "redirect_uris": [redirect]}).json()["client_id"]
    verifier, challenge = _pkce()
    r = app_side.get("/oauth/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": redirect,
                                                 "code_challenge": challenge, "code_challenge_method": "S256",
                                                 "state": "st"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/#/connect/")
    request = r.headers["location"].rsplit("/", 1)[1]
    asked = browser.post("/api/mcp/request", json={"request": request})
    if asked.status_code != 200:
        return {"refused": asked}
    back = browser.post("/api/mcp/approve", json={"request": request, "allow": True}).json()["redirect"]
    query = parse_qs(urlsplit(back).query)
    assert back.startswith(redirect) and query["state"] == ["st"]
    token = app_side.post("/oauth/token", data={"grant_type": "authorization_code", "code": query["code"][0],
                                                "redirect_uri": redirect, "client_id": client_id,
                                                "code_verifier": verifier})
    assert token.status_code == 200, token.text
    again = app_side.post("/oauth/token", data={"grant_type": "authorization_code", "code": query["code"][0],
                                                "redirect_uri": redirect, "client_id": client_id,
                                                "code_verifier": verifier})
    assert again.json()["error"] == "invalid_grant"  # a code is good once
    return {"asked": asked.json(), **token.json(), "client_id": client_id}


def test_an_ai_app_signs_in_and_acts_as_the_person(ctx, model):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9962)
    run(client, partial(_setup, 9962))

    got = _connect(app, owner)
    assert got["asked"]["client_name"] == "Claude" and got["asked"]["role"] == "user"
    bearer = {"Authorization": f"Bearer {got['access_token']}"}
    ai = TestClient(app, headers=bearer)
    assert '"role": "user"' in call(ai, "get_auth_me")["result"]["content"][0]["text"]
    assert "post_rubrics" in tool_names(ai)
    [conn] = owner.get("/api/mcp/connections").json()["items"]
    assert conn["client_name"] == "Claude" and conn["redirect_host"] == "claude.ai" and conn["last_used_at"]

    # a refresh: new tokens, the old ones gone
    fresh = TestClient(app).post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": got["refresh_token"],
                                                       "client_id": got["client_id"]}).json()
    assert fresh["access_token"] != got["access_token"]
    assert rpc(ai, "tools/list").status_code == 401
    ai = TestClient(app, headers={"Authorization": f"Bearer {fresh['access_token']}"})
    assert rpc(ai, "tools/list").status_code == 200
    # a wrong PKCE verifier, a redirect it did not register
    assert TestClient(app).get("/oauth/authorize", params={"client_id": got["client_id"], "redirect_uri": "https://evil.example/cb",
                                                           "response_type": "code", "code_challenge": "x",
                                                           "code_challenge_method": "S256"}).status_code == 400

    # a staff member's app: their rights, not the owner's
    password = owner.post("/api/members", json={"login": "mcp.staff", "ai_teach": True}).json()["password"]
    staff, _ = member_login(app, "mcp.staff", password)
    theirs = _connect(app, staff, "ChatGPT")
    assert theirs["asked"]["role"] == "member" and theirs["asked"]["login"] == "mcp.staff"
    staff_ai = TestClient(app, headers={"Authorization": f"Bearer {theirs['access_token']}"})
    names = tool_names(staff_ai)
    assert "post_topics_topic_id_hide" in names and not names & {"post_rubrics", "put_publications_settings",
                                                                 "post_chat_posts_post_id_publish", "get_chats"}
    assert [c["client_name"] for c in staff.get("/api/mcp/connections").json()["items"]] == ["ChatGPT"]
    assert [c["client_name"] for c in owner.get("/api/mcp/connections").json()["items"]] == ["Claude"]
    # signed out everywhere by a new password: their apps too
    [member] = owner.get("/api/members").json()["items"]
    owner.post(f"/api/members/{member['id']}/password")
    assert rpc(staff_ai, "tools/list").status_code == 401

    # «Отключить»: at once
    assert owner.delete(f"/api/mcp/connections/{conn['id']}").json() == {"ok": True}
    assert rpc(ai, "tools/list").status_code == 401

    # the admin connects by the key, not by signing an app in
    admin = TestClient(app)
    admin.post("/api/auth/login", json={"username": "admin", "password": "secret"})
    assert _connect(app, admin)["refused"].status_code == 403


def test_tool_names_are_what_apps_take(ctx):
    app, _ = ctx
    names = [t.name for t in mcp.all_tools(app)]
    assert len(names) == len(set(names)) and all(re.fullmatch(r"[a-z0-9_]{1,64}", n) for n in names)


def test_the_ai_app_asks_the_service_s_ai_for_a_post(ctx, model):
    """A chat message as a tool: the answer is waited for and given whole — the post the service's AI wrote."""
    _, script = model
    app, client = ctx
    owner = sign_in(app, client, 9963)
    ids = run(client, partial(_setup, 9963))
    ai = TestClient(app, headers={"X-API-Key": owner.get("/api/api-key").json()["key"]})
    chat = call(ai, "post_chats")["result"]["content"][0]["text"]
    thread = int(re.search(r'"id": (\d+)', chat).group(1))
    script.append(("suggest_post", {"text": "<b>Ракета</b> стартовала", "source_post_ids": [ids["other"]]}))
    answer = call(ai, "post_chats_thread_id_messages", {"thread_id": thread, "text": "Напиши пост"})["result"]
    assert not answer["isError"] and "Ракета" in answer["content"][0]["text"] and '"post"' in answer["content"][0]["text"]
