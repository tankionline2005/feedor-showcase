"""A staff member does what the owner allowed and nothing else: every change refused without its right, and nothing
of the owner's own (their OpenRouter, their other groups) shown."""

from datetime import timedelta
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import ai
from app.db import SessionLocal
from app.models import AiUsage, BotChat, ChatPost, ChatThread, Member, Project, Topic, utcnow
from app.web import members as members_api

from test_chat_posts import _setup, model  # noqa: F401
from test_members import member_login
from test_publications import _topics
from test_user_channels import ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


async def _seed(project_id: int, topic_id: int, owner_tg: int) -> dict:
    """A suggestion by a topic, one scheduled, one published; and a group the owner added the bot to elsewhere."""
    async with SessionLocal() as s:
        common = {"project_id": project_id, "topic_id": topic_id, "ai_text": "Текст"}
        suggestion = ChatPost(text="Предложение", **common)
        scheduled = ChatPost(text="Запланирован", scheduled_at=utcnow() + timedelta(hours=3), scheduled_by="владелец",
                             **common)
        published = ChatPost(text="Вышел", published_at=utcnow(), published_by="владелец", published_chat="-1001",
                             published_ids=[5], published_text_id=5, published_text_kind="text", **common)
        s.add_all([suggestion, scheduled, published])
        s.add(BotChat(chat_id=-(10**9 + owner_tg), type="supergroup", title="Личная группа владельца", status="member",
                      added_by=owner_tg))
        await s.commit()
        return {"suggestion": suggestion.id, "scheduled": scheduled.id, "published": published.id}


async def _state(ids: dict, topic_id: int) -> tuple:
    async with SessionLocal() as s:
        posts = [await s.get(ChatPost, i) for i in ids.values()]
        topic = await s.get(Topic, topic_id)
        return ([(p.text, p.scheduled_at, p.published_at, p.cover) for p in posts],
                (topic.hidden_at, topic.covered))


@pytest.mark.parametrize("by", ["site", "key"])
def test_staff_without_rights_change_nothing(ctx, model, by):
    """On the site, and with their API key: the same refusals."""
    app, client = ctx
    members_api._failed.clear()
    tg = 9981 if by == "site" else 9984
    owner = sign_in(app, client, tg)
    ids = run(client, partial(_setup, tg))
    [topic_id] = run(client, partial(_topics, ids["project"], ids["other"], 1))
    posts = run(client, partial(_seed, ids["project"], topic_id, tg))
    rubric = owner.post("/api/rubrics", json={"preset": "day"}).json()["id"]
    password = owner.post("/api/members", json={"login": f"probe.{by}"}).json()["password"]
    staff, r = member_login(app, f"probe.{by}", password)
    assert r.json() == {"ok": True}
    if by == "key":
        staff = TestClient(app, headers={"X-API-Key": staff.get("/api/api-key").json()["key"]})
        assert staff.get("/api/auth/me").json()["role"] == "member"
    before = run(client, partial(_state, posts, topic_id))

    refused = [
        # the owner's alone
        ("get", "/api/members", None), ("post", "/api/members", {"login": "x.y.z"}),
        ("get", "/api/projects", None), ("post", "/api/projects", None),
        ("get", "/api/ai", None), ("put", "/api/ai", {}), ("delete", "/api/ai", None),
        ("get", "/api/ai/usage", None), ("get", "/api/ai/models?operation=chat", None),
        ("get", "/api/rubrics", None), ("post", "/api/rubrics", {"preset": "day"}),
        ("put", f"/api/rubrics/{rubric}", {"enabled": True}), ("post", f"/api/rubrics/{rubric}/example", None),
        ("delete", f"/api/rubrics/{rubric}", None),
        ("get", "/api/covers", None), ("put", "/api/covers", {}), ("delete", "/api/covers/logo", None),
        ("put", "/api/publications/settings", {"topic_posts": False}),
        ("post", "/api/groups", {"link": "@somegroup"}), ("delete", "/api/groups/-100777", None),
        ("post", "/api/topics/run", None),
        # the admin's
        ("get", "/api/settings", None), ("get", "/api/users", None), ("get", "/api/accounts", None),
        ("get", "/api/proxies", None), ("get", "/api/notify", None), ("get", "/api/promo", None),
        # rights not given: channels, the AI's settings, teaching it, the chat, publishing
        ("post", "/api/channels", {"links": "@probe_new"}), ("post", "/api/channels/remove", {"links": "@x"}),
        ("put", "/api/topics/settings", {"enabled": False}), ("put", "/api/smart", {"enabled": True}),
        ("post", f"/api/topics/{topic_id}/hide", {"reason": "не наша тема"}),
        ("post", f"/api/topics/{topic_id}/covered", None), ("post", f"/api/topics/{topic_id}/not-covered", None),
        ("post", f"/api/smart/posts/{ids['other']}/correct", {"show": False, "reason": "не то"}),
        ("get", "/api/chats", None), ("post", "/api/chats", None),
        ("patch", f"/api/chat-posts/{posts['suggestion']}", {"text": "Чужой текст"}),
        ("post", f"/api/chat-posts/{posts['suggestion']}/publish", None),
        ("put", f"/api/chat-posts/{posts['suggestion']}/schedule", {"at": "2030-01-01T10:00:00+03:00"}),
        ("put", f"/api/chat-posts/{posts['suggestion']}/cover", {"on": True}),
        ("delete", f"/api/chat-posts/{posts['suggestion']}", None),
        ("patch", f"/api/chat-posts/{posts['scheduled']}", {"text": "Чужой текст"}),
        ("delete", f"/api/chat-posts/{posts['scheduled']}/schedule", None),
        ("put", f"/api/chat-posts/{posts['published']}/channel", {"text": "Чужой текст"}),
        ("delete", f"/api/chat-posts/{posts['published']}/channel", None),
    ]
    for method, url, body in refused:
        r = getattr(staff, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code in (400, 401, 403), (method, url, r.status_code, r.text[:200])
    assert run(client, partial(_state, posts, topic_id)) == before

    # what they may: read the project's publications, topics and feed
    assert owner.get("/api/publications").status_code == 200 and staff.get("/api/publications").status_code == 200
    assert staff.get(f"/api/topics/{topic_id}").status_code == 200

    # nothing of the owner's own: their OpenRouter balance and spending, the groups they keep elsewhere
    smart = staff.get("/api/smart").json()
    assert smart["balance_left"] is None and smart["day"]["cost"] is None and smart["model"] is None
    assert staff.get("/api/groups").json()["found"] == []
    assert [g["title"] for g in owner.get("/api/groups").json()["found"]] == ["Личная группа владельца"]


async def _owner_spent(project_id: int) -> None:
    async with SessionLocal() as s:
        project = await s.get(Project, project_id)
        s.add(AiUsage(user_id=project.owner_id, project_id=project_id, operation="filter", model="m", cost=0.25))
        await s.commit()


def test_staff_see_nothing_of_the_owner_s_money(ctx, model, monkeypatch):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9982)
    ids = run(client, partial(_setup, 9982))
    run(client, partial(_owner_spent, ids["project"]))

    async def balance(user_id, key):
        return {"credits_left": 7.5}
    monkeypatch.setattr(ai, "balance", balance)
    password = owner.post("/api/members", json={"login": "probe.money", "ai_configure": True}).json()["password"]
    staff, _ = member_login(app, "probe.money", password)

    mine = owner.get("/api/smart").json()
    assert mine["balance_left"] == 7.5 and mine["day"]["cost"] == 0.25
    theirs = staff.get("/api/smart").json()
    assert theirs["balance_left"] is None and theirs["day"]["cost"] is None and theirs["editable"]


async def _their_scheduled(project_id: int, login: str) -> tuple[int, int]:
    """A post of the staff member's chat, scheduled while they still could publish."""
    async with SessionLocal() as s:
        member = await s.scalar(select(Member).where(Member.login == login))
        thread = ChatThread(project_id=project_id, member_id=member.id, model="m")
        s.add(thread)
        await s.flush()
        post = ChatPost(project_id=project_id, thread_id=thread.id, text="Мой пост", ai_text="Мой пост",
                        scheduled_at=utcnow() + timedelta(hours=2), scheduled_by=login)
        s.add(post)
        await s.commit()
        return thread.id, post.id


def test_a_post_scheduled_before_the_right_was_taken_is_not_theirs_to_change(ctx, model):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9983)
    ids = run(client, partial(_setup, 9983))
    created = owner.post("/api/members", json={"login": "probe.author", "ai_chat": True, "ai_publish": True}).json()
    thread_id, post_id = run(client, partial(_their_scheduled, ids["project"], "probe.author"))
    staff, _ = member_login(app, "probe.author", created["password"])
    assert staff.get(f"/api/chats/{thread_id}").status_code == 200
    assert staff.patch(f"/api/chat-posts/{post_id}", json={"text": "Правка"}).status_code == 200

    owner.patch(f"/api/members/{created['member']['id']}", json={"ai_publish": False})
    for method, url, body in (("patch", f"/api/chat-posts/{post_id}", {"text": "Ещё правка"}),
                              ("put", f"/api/chat-posts/{post_id}/cover", {"on": True}),
                              ("delete", f"/api/chat-posts/{post_id}/schedule", None)):
        r = getattr(staff, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, url, r.status_code)
    card = [p for p in staff.get("/api/publications").json()["items"] if p["id"] == post_id][0]
    assert card["editable"] is False and card["text"] == "Правка" and card["scheduled_at"]
