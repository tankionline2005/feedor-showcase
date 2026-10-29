import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import ai, chat
from app.db import SessionLocal
from app.models import AiAccount, AiUsage, Channel, ChatMessage, Post, Project, ProjectChannel, User, utcnow
from app.web import members as members_api

from test_members import member_login
from test_user_channels import own_ai, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def sse(*chunks: dict) -> bytes:
    return "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"


@pytest.fixture
def model(monkeypatch):
    """A fake streaming model: asked about "запуск", it first searches posts, then answers with a link."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        last = body["messages"][-1]
        usage = {"usage": {"prompt_tokens": 1000, "completion_tokens": 50, "cost": 0.001}, "choices": []}
        if last["role"] == "user" and "запуск" in last["content"]:
            return httpx.Response(200, content=sse(
                {"id": "g1", "choices": [{"delta": {"tool_calls": [
                    {"index": 0, "id": "call_1", "function": {"name": "search_posts", "arguments": '{"query": "'}}]}}]},
                {"id": "g1", "choices": [{"delta": {"tool_calls": [
                    {"index": 0, "function": {"arguments": 'launch"}'}}]}}]},
                usage,
            ))
        if last["role"] == "tool":
            found = json.loads(last["content"])["posts"]
            return httpx.Response(200, content=sse(
                {"id": "g2", "choices": [{"delta": {"content": "Нашёл: "}}]},
                {"id": "g2", "choices": [{"delta": {"content": f"[пост]({found[0]['url']})"}}]},
                usage,
            ))
        return httpx.Response(200, content=sse({"id": "g3", "choices": [{"delta": {"content": "Привет!"}}]}, usage))

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    return sent


async def _setup(tg_id: int) -> int:
    now = utcnow()
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        s.add(AiAccount(user_id=user.id, key_enc=ai.encrypt_key("sk-or-x"), connected_at=now))
        await own_ai(s, user.id)
        watched = Channel(link=f"@chat_w_{tg_id}", username=f"chat_w_{tg_id}", title="Новости", status="active")
        mine = Channel(link=f"@chat_m_{tg_id}", username=f"chat_m_{tg_id}", title="Мой", status="active")
        s.add_all([watched, mine])
        await s.flush()
        s.add_all([ProjectChannel(project_id=project.id, channel_id=watched.id),
                   ProjectChannel(project_id=project.id, channel_id=mine.id, own=True)])
        s.add_all([Post(channel_id=watched.id, msg_id=1, posted_at=now - timedelta(hours=2), text="big launch today"),
                   Post(channel_id=watched.id, msg_id=2, posted_at=now - timedelta(days=3), text="old launch"),
                   Post(channel_id=mine.id, msg_id=1, posted_at=now - timedelta(hours=1), text="my own post")])
        await s.commit()
        return watched.id


def talk(browser, chat_id: int, text: str) -> list[dict]:
    r = browser.post(f"/api/chats/{chat_id}/messages", json={"text": text, "tz": 180})
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line]


def test_a_chat_knows_whats_new_and_looks_things_up(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9911)
    watched_id = run(client, _setup, 9911)

    listed = owner.get("/api/chats").json()
    assert listed["items"] == [] and listed["model"] == ai.OPERATIONS["chat"]["default"]
    chat_id = owner.post("/api/chats").json()["id"]

    events = talk(owner, chat_id, "Что писали про запуск?")
    assert [e["type"] for e in events] == ["status", "delta", "delta", "done"]
    assert events[0]["text"] == "Ищу посты: «launch»"
    assert "".join(e["text"] for e in events if e["type"] == "delta") == f"Нашёл: [пост](https://t.me/chat_w_9911/1)"

    first = model[0]
    assert first["model"] == ai.OPERATIONS["chat"]["default"] and first["stream"] is True
    assert {t["function"]["name"] for t in first["tools"]} >= {"search_posts", "my_channel_stats", "get_topic"}
    assert "Новости" in first["messages"][0]["content"] and "own channel: Мой" in first["messages"][0]["content"]
    note = first["messages"][1]["content"]
    # the first note: the last 24 hours; the watched channel's post, the own channel's apart
    assert note.startswith("<update>") and "big launch today" in note and "old launch" not in note
    assert "my_channel_new_posts" in note and "UTC+03:00" in note
    # the tool's answer went back to the model
    tool_answer = json.loads(model[1]["messages"][-1]["content"])
    assert [p["text"] for p in tool_answer["posts"]] == ["big launch today", "old launch"]

    shown = owner.get(f"/api/chats/{chat_id}").json()
    assert [(m["role"], m["text"]) for m in shown["messages"]] == [
        ("user", "Что писали про запуск?"), ("assistant", "Нашёл: [пост](https://t.me/chat_w_9911/1)")]
    assert shown["title"] == "Что писали про запуск?" and shown["model"] == ai.OPERATIONS["chat"]["default"]

    # the next message brings only what came after
    async def new_post():
        async with SessionLocal() as s:
            s.add(Post(channel_id=watched_id, msg_id=3, posted_at=utcnow(), text="fresh news"))
            await s.commit()

    run(client, new_post)
    assert [e["type"] for e in talk(owner, chat_id, "Привет")] == ["delta", "done"]
    notes = [m["content"] for m in model[-1]["messages"] if m["role"] == "user" and m["content"].startswith("<update>")]
    assert len(notes) == 2 and "fresh news" in notes[1] and "big launch today" not in notes[1]

    async def paid():
        async with SessionLocal() as s:
            return [u.operation for u in (await s.execute(select(AiUsage).where(AiUsage.model == ai.OPERATIONS["chat"]["default"]))).scalars()]

    assert run(client, paid).count("chat") >= 3

    renamed = owner.patch(f"/api/chats/{chat_id}", json={"title": "  Запуски  "}).json()
    assert renamed["title"] == "Запуски" and owner.get("/api/chats").json()["items"][0]["title"] == "Запуски"
    assert owner.get(f"/api/chats/{chat_id}").json()["pending"] is None  # nothing being written now
    assert owner.delete(f"/api/chats/{chat_id}").json() == {"ok": True}
    assert owner.get(f"/api/chats/{chat_id}").status_code == 404


def test_staff_chats_are_their_own(ctx, model):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9912)
    run(client, _setup, 9912)
    created = owner.post("/api/members", json={"login": "chat.staff"}).json()
    staff, _ = member_login(app, "chat.staff", created["password"])
    assert staff.get("/api/chats").status_code == 403  # not allowed to talk to the AI
    owner.patch(f"/api/members/{created['member']['id']}", json={"ai_chat": True})

    theirs = staff.post("/api/chats").json()["id"]
    talk(staff, theirs, "Привет")
    assert staff.get("/api/chats").json()["model"] is None  # staff never see the model
    assert [c["id"] for c in staff.get("/api/chats").json()["items"]] == [theirs]
    # the owner does not see it, nor open it
    assert owner.get("/api/chats").json()["items"] == []
    assert owner.get(f"/api/chats/{theirs}").status_code == 404

    async def paid_by_owner():
        async with SessionLocal() as s:
            rows = (await s.execute(select(ChatMessage).where(ChatMessage.thread_id == theirs))).scalars().all()
            return len(rows)

    assert run(client, paid_by_owner) == 3  # the note, the question, the answer


def test_tools_stay_in_the_project():
    async def scenario():
        out = await chat.run_tool(-1, "get_post", {"id": 1})
        return out

    import asyncio
    assert asyncio.run(scenario()) == {"error": "no such post in this project"}


def test_the_answer_comes_even_when_the_page_is_left(ctx, monkeypatch):
    """The page stops reading halfway: the answer is still written to the end and saved, and the chat
    shows what there is of it ("pending") meanwhile."""
    import asyncio

    from app.web import chat_api
    from app.web.deps import Principal

    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, content=sse(
            {"id": "g", "choices": [{"delta": {"content": "Долгий ответ"}}]},
            {"usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0}, "choices": []},
        ))

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(slow))
    app, client = ctx
    owner = sign_in(app, client, 9913)
    run(client, _setup, 9913)
    chat_id = owner.post("/api/chats").json()["id"]

    async def scenario():
        async with SessionLocal() as s:
            user = await s.scalar(select(User).where(User.tg_user_id == 9913))
            project = await s.scalar(select(Project).where(Project.owner_id == user.id))
            viewer = Principal("user", user.id, project_id=project.id, plan="pro")
            response = await chat_api.send(chat_id, chat_api.MessageIn(text="Привет"), viewer, s)
        stream = response.body_iterator
        reading = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.2)
        reading.cancel()  # the page is closed before a word came: the stream is cancelled, as on a disconnect
        try:
            await reading
        except asyncio.CancelledError:
            pass
        pending = chat_api._jobs[chat_id].text if chat_id in chat_api._jobs else None
        assert chat_api._jobs[chat_id].elapsed() >= 0.2  # the answer's time runs from the question
        gate.set()
        for _ in range(100):
            if chat_id not in chat_api._jobs:
                break
            await asyncio.sleep(0.05)
        return pending

    assert run(client, scenario) == ""  # it was still being written after the page left
    shown = owner.get(f"/api/chats/{chat_id}").json()
    assert [(m["role"], m["text"]) for m in shown["messages"]] == [("user", "Привет"), ("assistant", "Долгий ответ")]
    assert shown["pending"] is None and not shown["busy"]
    assert shown["messages"][1]["took_ms"] >= 200 and shown["messages"][1]["at"]  # how long it took, and when ready


LIMIT = {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day"}}


def test_a_failed_answer_stays_with_a_retry(ctx, monkeypatch):
    state = {"limited": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["limited"]:
            return httpx.Response(429, json=LIMIT)
        return httpx.Response(200, content=sse({"id": "g", "choices": [{"delta": {"content": "Теперь отвечаю"}}]},
                                               {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0}, "choices": []}))

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    app, client = ctx
    owner = sign_in(app, client, 9914)
    run(client, _setup, 9914)
    chat_id = owner.post("/api/chats").json()["id"]

    [error] = talk(owner, chat_id, "Что нового?")
    assert error["type"] == "error" and "дневной лимит" in error["text"] and "free-models-per-day" in error["detail"]
    shown = owner.get(f"/api/chats/{chat_id}").json()["messages"]
    assert [m["role"] for m in shown] == ["user", "error"] and "free-models-per-day" in shown[1]["detail"]

    state["limited"] = False
    r = owner.post(f"/api/chats/{chat_id}/retry", json={"tz": 180})
    events = [json.loads(line) for line in r.text.splitlines() if line]
    assert [e["type"] for e in events] == ["delta", "done"]
    shown = owner.get(f"/api/chats/{chat_id}").json()["messages"]
    # the same question once, the failure gone, the answer under it
    assert [(m["role"], m["text"]) for m in shown] == [("user", "Что нового?"), ("assistant", "Теперь отвечаю")]
