"""A post of one's own text (the API, the MCP connector): to «Публикации», like any other post to publish."""

from datetime import timedelta
from functools import partial

from fastapi.testclient import TestClient

from app import publications
from app.db import SessionLocal
from app.models import ChatPost, utcnow
from app.web import members as members_api

from test_chat_posts import _setup, model  # noqa: F401
from test_mcp import call
from test_members import member_login
from test_user_channels import ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


async def _forget_and_keep(post_id: int) -> bool:
    """The retention period passes: an AI's suggestion would go, one's own post stays."""
    async with SessionLocal() as s:
        await publications.forget_stale(s, utcnow() + timedelta(days=1))
        await s.commit()
        return await s.get(ChatPost, post_id) is not None


def test_an_own_post_goes_to_publications(ctx, model):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9971)
    ids = run(client, partial(_setup, 9971))
    api = TestClient(app, headers={"X-API-Key": owner.get("/api/api-key").json()["key"]})

    made = api.post("/api/chat-posts", json={"text": "<b>Ракета</b> стартовала", "source_post_ids": [ids["other"]],
                                             "cover": True, "cover_title": "Старт"})
    assert made.status_code == 200, made.text
    post = made.json()
    assert post["written_by"] == "владелец" and post["text"] == "<b>Ракета</b> стартовала" and post["editable"]
    assert post["media"] and post["sources"] == 1 and post["cover"] and post["cover_title"] == "Старт"
    listed = {p["id"]: p for p in owner.get("/api/publications").json()["items"]}
    assert post["id"] in listed and listed[post["id"]]["written_by"] == "владелец"
    assert post["id"] not in [p["id"] for p in owner.get("/api/publications", params={"filter": "suggested"}).json()["items"]]
    # edited like any other, and kept past the retention period: it is not the AI's suggestion
    assert api.patch(f"/api/chat-posts/{post['id']}", json={"text": "Ракета вышла на орбиту"}).status_code == 200
    assert run(client, partial(_forget_and_keep, post["id"]))

    assert api.post("/api/chat-posts", json={"text": "  "}).status_code == 422
    r = api.post("/api/chat-posts", json={"text": "а" * 5000})
    assert r.status_code == 422 and "4096" in r.json()["detail"]
    # deleted while it has not gone out
    assert api.delete(f"/api/chat-posts/{post['id']}").json() == {"ok": True}

    # the MCP connector's tool does the same
    text = call(api, "post_chat_posts", {"text": "Пост от ассистента"})["result"]["content"][0]["text"]
    assert '"written_by": "владелец"' in text

    # staff: only those who may publish
    plain = member_login(app, "own.plain", owner.post("/api/members", json={"login": "own.plain"}).json()["password"])[0]
    r = plain.post("/api/chat-posts", json={"text": "Мой пост"})
    assert r.status_code == 403
    publisher = member_login(app, "own.pub", owner.post(
        "/api/members", json={"login": "own.pub", "ai_publish": True}).json()["password"])[0]
    assert publisher.post("/api/chat-posts", json={"text": "Мой пост"}).json()["written_by"] == "own.pub"
    assert "POST /api/chat-posts" not in {f"{m['method']} {m['path']}" for g in plain.get(
        "/api/api-key/reference").json()["groups"] for m in g["methods"]}
