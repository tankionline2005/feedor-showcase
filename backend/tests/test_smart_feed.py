import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import ai, smart_feed
from app.db import SessionLocal
from app.models import AiAccount, AiUsage, Channel, Post, Project, ProjectChannel, User, utcnow
from app.services import preview_path
from app.config import settings
from app.web import members as members_api

from test_members import member_login
from test_user_channels import own_ai, _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


@pytest.fixture
def model(monkeypatch):
    """A fake OpenRouter model: a post matches when its text has "AI" in it."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404)
        body = json.loads(request.content)
        sent.append(body)
        user = body["messages"][-1]["content"]
        posts = json.loads(user[0]["text"])["posts"]
        results = [{"id": p["id"], "match": "AI" in p["text"] + p["ocr_text"], "score": 90 if "AI" in p["text"] else 5,
                    "reason": "про ИИ" if "AI" in p["text"] + p["ocr_text"] else "не по теме"} for p in posts]
        return httpx.Response(200, json={"id": "gen", "model": body["model"],
                                         "choices": [{"message": {"content": json.dumps({"results": results})}}],
                                         "usage": {"prompt_tokens": 500, "completion_tokens": 40, "cost": 0.0003}})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    ai._balance_cache.clear()
    return sent


async def _link(tg_id: int) -> None:
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        s.add(AiAccount(user_id=user.id, key_enc=ai.encrypt_key("sk-or-x"), connected_at=utcnow()))
        await own_ai(s, user.id)
        await s.commit()


async def _add_posts(channel_id: int, texts: list[str], ocr_status: str | None = None, ocr: str | None = None) -> list[int]:
    async with SessionLocal() as s:
        top = max([p.msg_id for p in (await s.execute(select(Post).where(Post.channel_id == channel_id))).scalars()] or [0])
        posts = [Post(channel_id=channel_id, msg_id=top + i + 1, posted_at=utcnow(), text=t, ocr_status=ocr_status,
                      extracted_text_from_media=ocr) for i, t in enumerate(texts)]
        s.add_all(posts)
        await s.commit()
        return [p.id for p in posts]


async def _own_channel(tg_id: int) -> int:
    """The project's own channel."""
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        ch = Channel(link=f"@uc_own_{tg_id}", status="active", title="Мой канал")
        s.add(ch)
        await s.flush()
        s.add(ProjectChannel(project_id=project.id, channel_id=ch.id, own=True))
        await s.commit()
        return ch.id


def smart_texts(browser) -> list[str]:
    body = browser.get("/api/posts", params={"smart": "true"}).json()
    return [p["text"] for p in body["items"]]


def test_the_smart_feed_keeps_what_matches_new_posts_only(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9701)
    assert owner.post("/api/channels", json={"links": "@uc_smart"}).json()["added"]
    channel_id = run(client, _activate, "@uc_smart", 97011, 2)  # two posts from before

    state = owner.get("/api/smart").json()
    assert state["editable"] and not state["enabled"] and state["prompt"] == smart_feed.DEFAULT_PROMPT
    assert owner.put("/api/smart", json={"enabled": True}).status_code == 409  # OpenRouter is not linked
    run(client, _link, 9701)
    owner.put("/api/smart", json={"enabled": True, "prompt": "Only posts about AI."})

    run(client, _add_posts, channel_id, ["AI startup raised a round", "weather today", "new AI model"])
    waiting = run(client, _add_posts, channel_id, ["picture of an announcement"], "pending")
    own_id = run(client, _own_channel, 9701)
    run(client, _add_posts, own_id, ["my own AI post"])
    run(client, smart_feed.smart_feed_step)

    assert len(model) == 1
    sent = model[0]
    assert sent["model"] == ai.OPERATIONS["filter"]["default"] and sent["response_format"]["type"] == "json_schema"
    assert "Only posts about AI." in sent["messages"][1]["content"]
    payload = json.loads(sent["messages"][-1]["content"][0]["text"])
    # not the old ones, not the waiting one, not the project's own: those come apart, as what the owner published
    assert [p["text"] for p in payload["posts"]] == ["AI startup raised a round", "weather today", "new AI model"]
    assert [p["text"] for p in payload["my_channel"]] == ["my own AI post"]

    # what the AI kept, and the posts from before it was turned on: those stay, unread; the one still
    # waiting for its pictures' text is not shown until it is read
    body = owner.get("/api/posts", params={"smart": "true"}).json()
    assert body["smart"] and [p["text"] for p in body["items"]] == [
        "new AI model", "AI startup raised a round", "@uc_smart 0", "@uc_smart 1"]
    assert body["items"][0]["smart"] == {"score": 90, "reason": "про ИИ", "match": True, "mine": None,
                                         "mine_reason": None, "mine_by": None}
    assert "smart" not in body["items"][2]  # never read: no verdict to show
    feed = [p["text"] for p in owner.get("/api/posts").json()["items"]]
    assert len(feed) == 6 and "my own AI post" not in feed  # everything but the project's own posts
    assert owner.get("/api/posts", params={"channel_id": own_id}).json()["items"][0]["text"] == "my own AI post"
    # what the AI turned down, with why
    rejected = owner.get("/api/posts", params={"smart": "rejected"}).json()["items"]
    assert [(p["text"], p["smart"]["reason"]) for p in rejected] == [("weather today", "не по теме")]

    # the text on its pictures came: it is read now
    async def ocr_done():
        async with SessionLocal() as s:
            post = await s.get(Post, waiting[0])
            post.ocr_status, post.extracted_text_from_media = "done", "AI conference"
            await s.commit()

    run(client, ocr_done)
    run(client, smart_feed.smart_feed_step)
    assert smart_texts(owner)[0] == "picture of an announcement"

    state = owner.get("/api/smart").json()
    assert state["day"]["judged"] == 4 and state["day"]["matched"] == 3 and state["day"]["cost"] == pytest.approx(0.0006)
    assert state["model"] == ai.OPERATIONS["filter"]["default"]

    # turned off: nothing is read, the smart filter rests
    owner.put("/api/smart", json={"enabled": False})
    run(client, _add_posts, channel_id, ["AI again"])
    run(client, smart_feed.smart_feed_step)
    assert len(model) == 2 and not owner.get("/api/posts", params={"smart": "true"}).json()["smart"]

    # reset to the default prompt
    assert owner.put("/api/smart", json={"reset_prompt": True}).json()["prompt"] == smart_feed.DEFAULT_PROMPT
    assert owner.put("/api/smart", json={"prompt": "  "}).status_code == 422


def test_staff_see_the_project_s_feed_as_it_is(ctx, model):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9702)
    assert owner.post("/api/channels", json={"links": "@uc_smart_team"}).json()["added"]
    channel_id = run(client, _activate, "@uc_smart_team", 97021, 0)
    run(client, _link, 9702)
    owner.put("/api/smart", json={"enabled": True, "prompt": "Only posts about AI."})
    created = owner.post("/api/members", json={"login": "smart.staff"}).json()
    staff, _ = member_login(app, "smart.staff", created["password"])

    state = staff.get("/api/smart").json()
    assert not state["editable"] and state["enabled"] and state["model"] is None and "personal" not in state
    assert staff.put("/api/smart", json={"prompt": "mine"}).status_code == 403
    assert staff.post("/api/smart/mode", json={"personal": True}).status_code in (404, 405)  # no feeds of their own

    run(client, _add_posts, channel_id, ["AI news", "football"])
    run(client, smart_feed.smart_feed_step)
    assert smart_texts(staff) == ["AI news"]  # the project's feed

    # allowed to set up the project's AI: they set up the project's feed, for everyone
    owner.patch(f"/api/members/{created['member']['id']}", json={"ai_configure": True})
    assert staff.get("/api/smart").json()["editable"]
    staff.put("/api/smart", json={"prompt": "Football only"})
    assert owner.get("/api/smart").json()["prompt"] == "Football only"
    # off in the project: off for everyone
    staff.put("/api/smart", json={"enabled": False})
    assert not owner.get("/api/smart").json()["enabled"]
    assert len(model) == 1  # one feed read the posts, once


def test_the_ai_reads_the_text_on_the_pictures_not_the_pictures(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9703)
    assert owner.post("/api/channels", json={"links": "@uc_smart_vision"}).json()["added"]
    channel_id = run(client, _activate, "@uc_smart_vision", 97031, 0)
    run(client, _link, 9703)
    # a feed that once sent the pictures themselves asks for it no more
    owner.put("/api/smart", json={"enabled": True, "vision": True})
    assert "vision" not in owner.get("/api/smart").json()
    ids = run(client, _add_posts, channel_id, ["photo post"], "done", "Текст на фото")

    async def with_picture():
        async with SessionLocal() as s:
            post = await s.get(Post, ids[0])
            post.has_preview, post.previews = True, [{"k": "photo", "p": 0}]
            await s.commit()
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        preview_path(ids[0], 0).write_bytes(b"jpeg")

    run(client, with_picture)
    run(client, smart_feed.smart_feed_step)
    assert model[-1]["model"] == ai.OPERATIONS["filter"]["default"]
    content = model[-1]["messages"][-1]["content"]
    assert [c["type"] for c in content] == ["text"]
    assert json.loads(content[0]["text"])["posts"][0]["ocr_text"] == "Текст на фото"


def test_a_rate_limit_keeps_the_posts_waiting(ctx, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, json={"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day"}})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    app, client = ctx
    owner = sign_in(app, client, 9704)
    assert owner.post("/api/channels", json={"links": "@uc_smart_limit"}).json()["added"]
    channel_id = run(client, _activate, "@uc_smart_limit", 97041, 0)
    run(client, _link, 9704)
    owner.put("/api/smart", json={"enabled": True})

    async def cursor():
        async with SessionLocal() as s:
            feed = await smart_feed.owner_feed(s, (await s.scalar(select(Project).where(
                Project.owner_id == (await s.scalar(select(User.id).where(User.tg_user_id == 9704)))))).id, create=False)
            return feed.cursor_post_id

    run(client, _add_posts, channel_id, ["AI post"])
    before = run(client, cursor)
    for _ in range(5):  # more than the failures that would let a batch go
        run(client, smart_feed.smart_feed_step)
    assert len(calls) == 1  # asked to wait: OpenRouter is not asked again every few seconds
    assert run(client, cursor) == before  # nothing was let go: the post is still to be read
    assert owner.get("/api/posts", params={"smart": "true"}).json()["items"] == []


def test_the_owner_corrects_the_feed_and_the_ai_learns(ctx, model):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9705)
    assert owner.post("/api/channels", json={"links": "@uc_smart_fix"}).json()["added"]
    channel_id = run(client, _activate, "@uc_smart_fix", 97051, 1)  # one post from before
    run(client, _link, 9705)
    owner.put("/api/smart", json={"enabled": True})
    kept, turned_down = run(client, _add_posts, channel_id, ["AI ad: buy our course", "weather today"])
    run(client, smart_feed.smart_feed_step)
    [old] = [p["id"] for p in owner.get("/api/posts", params={"smart": "true"}).json()["items"] if "smart" not in p]

    url = "/api/smart/posts/{}/correct"
    assert owner.post(url.format(kept), json={"show": False, "reason": " "}).status_code == 422  # why is a must
    assert owner.post(url.format(kept), json={"show": False, "reason": "Реклама курсов нам не нужна"}).json()
    assert owner.post(url.format(turned_down), json={"show": True, "reason": "Погоду  мы  публикуем"}).json()
    assert owner.post(url.format(old), json={"show": False, "reason": "Старьё"}).json()  # never read by the AI

    smart = owner.get("/api/posts", params={"smart": "true"}).json()["items"]
    assert [(p["text"], p["smart"]["mine"], p["smart"]["mine_reason"]) for p in smart] == [
        ("weather today", "show", "Погоду мы публикуем")]
    rejected = owner.get("/api/posts", params={"smart": "rejected"}).json()["items"]
    assert sorted(p["smart"]["mine_reason"] for p in rejected) == ["Реклама курсов нам не нужна", "Старьё"]
    assert owner.get("/api/smart").json()["day"] == {**owner.get("/api/smart").json()["day"], "judged": 2,
                                                     "matched": 1}  # the hidden old post was never judged

    # the next batch: the corrections go with it, the newest first
    run(client, _add_posts, channel_id, ["AI news"])
    run(client, smart_feed.smart_feed_step)
    payload = json.loads(model[-1]["messages"][-1]["content"][0]["text"])
    assert [(c["text"], c["user_wants"], c["reason"]) for c in payload["corrections"]] == [
        ("@uc_smart_fix 0", "hide", "Старьё"), ("weather today", "show", "Погоду мы публикуем"),
        ("AI ad: buy our course", "hide", "Реклама курсов нам не нужна")]

    # undone: the AI's word again, and no lesson any more
    assert owner.delete(url.format(kept)).json() == {"ok": True}
    assert owner.delete(url.format(old)).json() == {"ok": True}
    assert {"AI ad: buy our course", "@uc_smart_fix 0"} <= set(smart_texts(owner))
    run(client, _add_posts, channel_id, ["AI again"])
    run(client, smart_feed.smart_feed_step)
    payload = json.loads(model[-1]["messages"][-1]["content"][0]["text"])
    assert [c["text"] for c in payload["corrections"]] == ["weather today"]

    # someone else's post, or the feed off: no
    assert owner.post(url.format(10**9), json={"show": False, "reason": "чужой"}).status_code == 404
    owner.put("/api/smart", json={"enabled": False})
    assert owner.post(url.format(kept), json={"show": False, "reason": "выключена"}).status_code == 409
