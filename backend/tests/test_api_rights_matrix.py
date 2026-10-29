"""Every right of a staff member against every method a key may call: what the reference does not show them is
refused by the server too — called with real ids and a body the method accepts, so the refusal is the right's."""

import re
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ChatMessage, ChatPost, ChatThread, Member, ProjectChannel, Rubric, Trigger, User
from app.web import api_routes, deps
from app.web import members as members_api

from test_chat_posts import _setup, model  # noqa: F401
from test_publications import _topics
from test_user_channels import ctx, run, sign_in  # noqa: F401  (ctx is a fixture)

CONFIGS = {
    "none": {},
    "channels": {"can_edit_channels": True},
    "chat": {"ai_chat": True},
    "teach": {"ai_teach": True},
    "configure": {"ai_configure": True},
    "publish": {"ai_publish": True},
    "all": {r: True for r in deps.MEMBER_RIGHTS},
}


async def _world(tg_id: int, project_id: int, topic_id: int) -> dict:
    """The owner's things a staff member might reach for."""
    async with SessionLocal() as s:
        owner = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        thread = ChatThread(project_id=project_id, user_id=owner.id, model="m")
        s.add(thread)
        await s.flush()
        message = ChatMessage(thread_id=thread.id, role="assistant", content="Ответ")
        post = ChatPost(project_id=project_id, topic_id=topic_id, text="Предложение", ai_text="Предложение")
        trigger = Trigger(project_id=project_id, person=f"u{owner.id}", name="Ракета", words=["ракета"])
        s.add_all([message, post, trigger])
        await s.commit()
        rubric = await s.scalar(select(Rubric.id).where(Rubric.project_id == project_id))
        own = await s.scalar(select(ProjectChannel.channel_id).where(ProjectChannel.project_id == project_id,
                                                                     ProjectChannel.own))
        return {"channel_id": own, "thread_id": thread.id, "message_id": message.id, "post_id": post.id,
                "trigger_id": trigger.id, "rubric_id": rubric, "user_id": owner.id}


async def _keys(project_id: int) -> dict[str, str]:
    async with SessionLocal() as s:
        rows = (await s.execute(select(Member.login, Member.api_key).where(Member.project_id == project_id))).all()
        return dict(rows)


def _sample(schema: dict, components: dict):
    if "anyOf" in schema:
        schema = next(x for x in schema["anyOf"] if x.get("type") != "null")
    if "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "string":
        if pattern := schema.get("pattern"):
            if found := re.match(r"^\^\(?([\w-]+)", pattern):
                return found.group(1)
        return "2030-01-01T10:00:00+03:00" if schema.get("format") == "date-time" else "проба"
    if kind == "integer":
        return schema.get("minimum", 1)
    if kind == "number":
        return schema.get("minimum", 1)
    if kind == "boolean":
        return True
    if kind == "array":
        return [_sample(schema.get("items", {"type": "string"}), components)]
    if kind == "object" and "properties" in schema:
        return {k: _sample(v, components) for k, v in schema["properties"].items() if k in schema.get("required", [])}
    return {}


def _request(method: str, path: str, op: dict, components: dict, ids: dict) -> dict:
    url = re.sub(r"\{(\w+)\}", lambda m: str(ids.get(m.group(1), 1)), path)
    query = {p["name"]: _sample(p["schema"], components) for p in op.get("parameters", [])
             if p["in"] == "query" and p.get("required")}
    out: dict = {"url": url, "params": query}
    for content_type, content in op.get("requestBody", {}).get("content", {}).items():
        body = _sample(content["schema"], components)
        if "form-data" in content_type:
            out["files"] = {k: ("x.png", b"\x89PNG", "image/png") for k in (body or {"file": 0})}
        else:
            out["json"] = body
    return out


@pytest.mark.parametrize("config", list(CONFIGS))
def test_what_the_reference_hides_the_server_refuses(ctx, model, config):
    app, client = ctx
    members_api._failed.clear()
    tg = 9900 + list(CONFIGS).index(config)
    owner = sign_in(app, client, tg)
    ids = run(client, partial(_setup, tg))
    [topic_id] = run(client, partial(_topics, ids["project"], ids["other"], 1))
    owner.post("/api/rubrics", json={"preset": "day"})
    other = owner.post("/api/members", json={"login": f"m.other.{config}"}).json()["member"]["id"]
    owner.post("/api/members", json={"login": f"m.{config}", **CONFIGS[config]})
    world = run(client, partial(_world, tg, ids["project"], topic_id))
    keys = run(client, partial(_keys, ids["project"]))
    api = TestClient(app, headers={"X-API-Key": keys[f"m.{config}"]})
    viewer = deps.Principal("member", project_id=ids["project"], member_id=1,
                            can_edit_channels=bool(CONFIGS[config].get("can_edit_channels")),
                            rights=frozenset(r for r, on in CONFIGS[config].items() if on))
    values = {**world, "project_id": ids["project"], "member_id": other, "topic_id": topic_id,
              "chat_id": -1001, "comment_id": 1, "account_id": 1, "proxy_id": 1,
              "code": "PROBE"}
    schema = app.openapi()
    components = schema["components"]["schemas"]

    deps._calls.clear()
    let_through = []
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            entry = api_routes.BY_KEY.get(f"{method.upper()} {path}")
            if entry is None or api_routes.may(entry.who, viewer):
                continue
            req = _request(method, path, op, components, values)
            r = api.request(method.upper(), req.pop("url"), **req)
            if r.status_code < 400 or r.status_code == 422:
                let_through.append((method.upper(), path, r.status_code, r.text[:120]))
    assert let_through == []
    # and what it shows, it lets them read (the streams aside)
    refused = []
    for path, ops in schema["paths"].items():
        entry = api_routes.BY_KEY.get(f"GET {path}")
        if "get" not in ops or entry is None or not api_routes.may(entry.who, viewer) or path == "/api/live":
            continue
        req = _request("get", path, ops["get"], components, values)
        r = api.get(req.pop("url"), params=req["params"])
        if r.status_code == 403:
            refused.append((path, r.text[:120]))
    assert refused == []

    # nothing of the owner's changed: the rubric, the owner's trigger, the chat, the suggestion, the other member
    assert len(owner.get("/api/rubrics").json()["items"]) == 1
    assert owner.get(f"/api/chats/{world['thread_id']}").status_code == 200
    assert [t["id"] for t in owner.get("/api/triggers").json()["items"]] == [world["trigger_id"]]
    assert any(m["id"] == other for m in owner.get("/api/members").json()["items"])
    assert any(p["id"] == world["post_id"] for p in owner.get("/api/publications").json()["items"])

