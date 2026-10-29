"""Posts the AI writes in a chat for the project's channel: shown as posts with their pictures, made from an answer
by hand, sent to the person's Telegram in their original quality (chat_posts.py)."""

import asyncio
import io
import json
from datetime import timedelta
from functools import partial

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app import ai, chat_posts, post_style, settings_store, tg_html
from app.config import settings
from app.db import SessionLocal
from app.models import (
    AiAccount, BotFile, Channel, ChatPost, ChatThread, Member, Post, Project, ProjectChannel, Topic, TopicPost, User,
    ProjectGroup, utcnow,
)
from app.services import preview_path
from app.worker import maintenance

from test_chat import sse
from test_user_channels import own_ai, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def picture(pattern: str) -> bytes:
    """Pictures that look alike or not: a diagonal, its mirror, stripes."""
    img = Image.new("L", (64, 64), 30)
    for i in range(64):
        if pattern == "diagonal":
            img.putpixel((i, i), 230)
        elif pattern == "mirror":
            img.putpixel((63 - i, i), 230)
        elif pattern == "columns":
            for y in range(64):
                img.putpixel((i, y), 230 if (i // 8) % 2 else 30)
        else:
            for x in range(64):
                img.putpixel((x, i), 230 if (i // 8) % 2 else 30)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG")
    return buf.getvalue()


def test_telegram_s_html():
    assert tg_html.clean("<p>Привет <strong>мир</strong></p><p>2<br>3</p><script>x()</script>") == \
        "Привет <b>мир</b>\n\n2\n3"
    assert tg_html.clean('<a href="javascript:alert(1)">тут</a> <a href="https://x.ru">там</a>') == \
        'тут <a href="https://x.ru">там</a>'
    assert tg_html.clean("<code><b>npm</b> i</code>") == "<code>npm i</code>"  # nothing inside code
    assert tg_html.clean('<span class="tg-spoiler">s</span><blockquote expandable>q</blockquote>') == \
        "<tg-spoiler>s</tg-spoiler>\n<blockquote expandable>q</blockquote>"
    assert tg_html.length("<b>Привет</b> 👍 &amp;") == 11  # an emoji is two, as Telegram counts
    md = tg_html.from_markdown("# Итоги\n**Жирно**, *курсив*, ~~нет~~, ||тайна||, `код`, [ссылка](https://x.ru)\n"
                               "> цитата\n- пункт")
    assert md == ('<b>Итоги</b>\n<b>Жирно</b>, <i>курсив</i>, <s>нет</s>, <tg-spoiler>тайна</tg-spoiler>, '
                  '<code>код</code>, <a href="https://x.ru">ссылка</a>\n<blockquote>цитата</blockquote>\n• пункт')


@pytest.fixture
def model(monkeypatch):
    """A fake streaming model that answers from a script: each request takes the next answer."""
    sent: list[dict] = []
    script: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        answer = script.pop(0)
        if isinstance(answer, httpx.Response):
            return answer
        usage = {"usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0}, "choices": []}
        if not body.get("stream"):  # a request that is not the chat's (the notes on the person's way)
            return httpx.Response(200, json={"id": "g", "model": body["model"], "usage": usage["usage"],
                                             "choices": [{"message": {"content": answer}}]})
        if isinstance(answer, str):
            return httpx.Response(200, content=sse({"id": "g", "choices": [{"delta": {"content": answer}}]}, usage))
        name, args = answer
        return httpx.Response(200, content=sse({"id": "g", "choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": f"call_{len(sent)}", "function": {"name": name, "arguments": json.dumps(args)}}]}}]},
            usage))

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    return sent, script


async def _setup(tg_id: int) -> dict:
    """A project with OpenRouter, a watched channel whose posts have pictures, and its own channel."""
    now = utcnow()
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        s.add(AiAccount(user_id=user.id, key_enc=ai.encrypt_key("sk-or-x"), connected_at=now))
        await own_ai(s, user.id)
        watched = Channel(link=f"@cp_w_{tg_id}", username=f"cp_w_{tg_id}", title="Новости", status="active")
        mine = Channel(link=f"@cp_m_{tg_id}", username=f"cp_m_{tg_id}", title="Мой канал", status="active")
        s.add_all([watched, mine])
        await s.flush()
        s.add_all([ProjectChannel(project_id=project.id, channel_id=watched.id),
                   ProjectChannel(project_id=project.id, channel_id=mine.id, own=True)])
        photo = [{"k": "photo", "p": 0}]
        posts = {
            # in our group: the bot takes its photo from there
            "grouped": Post(channel_id=watched.id, msg_id=1, posted_at=now, text="Запуск ракеты", media_type="photo",
                            has_preview=True, previews=photo, repost_status="done",
                            repost_url="https://t.me/c/1234567/10", repost_copy=False),
            # the same photo from another post: once
            "same": Post(channel_id=watched.id, msg_id=2, posted_at=now, text="Ракета", media_type="photo",
                         has_preview=True, previews=photo, repost_status="skipped"),
            # not in the group: the worker reads it in full size
            "other": Post(channel_id=watched.id, msg_id=3, posted_at=now, text="Старт", media_type="photo",
                          has_preview=True, previews=photo, repost_status="skipped"),
            # a channel that forbids copying: shown, not attached
            "protected": Post(channel_id=watched.id, msg_id=4, posted_at=now, text="Закрыто", media_type="photo",
                              has_preview=True, previews=photo, repost_status="done",
                              repost_url="https://t.me/c/1234567/20", repost_copy=True),
            # a GIF: alone, or not at all (Telegram puts none in an album)
            "gif": Post(channel_id=watched.id, msg_id=5, posted_at=now, text="Гифка", media_type="gif",
                        has_preview=True, previews=[{"k": "video", "p": 0}], repost_status="skipped"),
        }
        s.add_all(posts.values())
        await s.commit()
    pictures = {"grouped": "diagonal", "same": "diagonal", "other": "mirror", "protected": "stripes", "gif": "columns"}
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    for key, p in posts.items():
        preview_path(p.id, 0).write_bytes(picture(pictures[key]))
    return {"project": project.id, **{k: p.id for k, p in posts.items()}, "watched": watched.id}


def talk(browser, chat_id: int, text: str) -> list[dict]:
    r = browser.post(f"/api/chats/{chat_id}/messages", json={"text": text, "tz": 180})
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line]


def test_the_ai_gives_a_post_and_the_chat_shows_it_as_one(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9921)
    ids = run(client, _setup, 9921)
    chat_id = owner.post("/api/chats").json()["id"]

    script.append(("suggest_post", {"text": "<p><strong>Ракета</strong> стартовала</p>",
                                    "source_post_ids": [ids["grouped"], ids["same"], ids["other"], ids["protected"],
                                                        999999]}))
    events = talk(owner, chat_id, "Напиши пост про запуск")
    assert [e["type"] for e in events] == ["status", "done"] and events[0]["text"] == "Оформляю пост"
    assert len(sent) == 1  # the post is the answer: no second request
    assert "suggest_post" in {t["function"]["name"] for t in sent[0]["tools"]} and "tool_choice" not in sent[0]

    shown = owner.get(f"/api/chats/{chat_id}").json()["messages"]
    assert [m["role"] for m in shown] == ["user", "post"]
    post = shown[1]["post"]
    assert post["text"] == "<b>Ракета</b> стартовала" and post["channel"]["title"] == "Мой канал"
    # the same photo once; the protected one shown with why, not picked
    assert [(c["post_id"], bool(c["why"])) for c in post["candidates"]] == [
        (ids["grouped"], False), (ids["other"], False), (ids["protected"], True)]
    assert [m["post_id"] for m in post["media"]] == [ids["grouped"], ids["other"]]
    assert post["limit"] == 1024 and post["length"] == len("Ракета стартовала")
    # an answer that is a post shows as one in the list of chats and in the notice that it came
    listed = [c for c in owner.get("/api/chats").json()["items"] if c["id"] == chat_id][0]
    assert listed["last"] == "Предложение поста: Ракета стартовала"
    status = owner.get("/api/chats/status", params={"ids": str(chat_id)}).json()["items"][0]
    assert status["last"] == "Предложение поста: Ракета стартовала" and not status["failed"]

    # the next turn: the model sees the post as it is now
    script.append("Готово")
    talk(owner, chat_id, "Спасибо")
    tool_answer = [m for m in sent[-1]["messages"] if m["role"] == "tool"][-1]
    assert json.loads(tool_answer["content"])["text_now"] == "<b>Ракета</b> стартовала"


async def _topic(project_id: int, post_ids: list[int]) -> int:
    async with SessionLocal() as s:
        t = Topic(project_id=project_id, title="Запуск ракеты", summary="Ракета стартовала.")
        s.add(t)
        await s.flush()
        s.add_all(TopicPost(topic_id=t.id, post_id=i) for i in post_ids)
        await s.commit()
        return t.id


def test_a_chat_begun_from_a_topic_answers_with_a_post(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9922)
    ids = run(client, _setup, 9922)
    topic = run(client, _topic, ids["project"], [ids["other"]])
    chat_id = owner.post("/api/chats", json={"topic_id": topic}).json()["id"]

    # the model reads the topic first, then must give a post; it names no sources: the topic's posts are
    script += [("get_topic", {"id": topic}), ("suggest_post", {"text": "Пост о запуске", "source_post_ids": []})]
    talk(owner, chat_id, "Напиши пост на тему")
    assert [b.get("tool_choice") for b in sent] == ["required", "required"]
    post = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]
    assert [m["post_id"] for m in post["media"]] == [ids["other"]]

    # the next message is a conversation as usual
    script.append("Пожалуйста")
    talk(owner, chat_id, "Спасибо")
    assert "tool_choice" not in sent[-1]

    # a model that cannot be made to call a tool is asked as usual
    other = owner.post("/api/chats", json={"topic_id": topic}).json()["id"]
    script += [httpx.Response(400, json={"error": {"message": "tool_choice is not supported"}}),
               ("suggest_post", {"text": "Пост", "source_post_ids": []})]
    events = talk(owner, other, "Напиши пост")
    assert events[-1]["type"] == "done" and "tool_choice" not in sent[-1]


def test_a_post_too_long_for_its_pictures_is_asked_shorter_once(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9923)
    ids = run(client, _setup, 9923)
    chat_id = owner.post("/api/chats").json()["id"]

    long = "Очень длинный пост. " * 70  # 1400 characters: more than a caption takes
    script += [("suggest_post", {"text": long, "source_post_ids": [ids["other"]]}),
               ("suggest_post", {"text": long, "source_post_ids": [ids["other"]]})]
    talk(owner, chat_id, "Напиши пост")
    asked_back = json.loads([m for m in sent[1]["messages"] if m["role"] == "tool"][-1]["content"])
    assert asked_back["error"].startswith("too long: 1399 characters")
    # the second one goes as it is: without pictures, which fit once it is shorter
    post = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]
    assert post["media"] == [] and post["limit"] == 4096 and len(post["candidates"]) == 1


def test_an_answer_is_made_a_post_by_hand(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9924)
    ids = run(client, _setup, 9924)
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(f"**Ракета** стартовала, [подробнее](https://t.me/cp_w_9924/3)")
    talk(owner, chat_id, "Напиши пост")
    answer = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]
    assert answer["role"] == "assistant"

    made = owner.post(f"/api/chat-posts/from-message/{answer['id']}").json()
    assert made["text"] == '<b>Ракета</b> стартовала, <a href="https://t.me/cp_w_9924/3">подробнее</a>'
    assert [m["post_id"] for m in made["media"]] == [ids["other"]]  # from the post it links to
    shown = owner.get(f"/api/chats/{chat_id}").json()["messages"]
    assert [m["role"] for m in shown] == ["user", "post"] and shown[1]["post"]["id"] == made["id"]
    assert owner.post(f"/api/chat-posts/from-message/{answer['id']}").json()["id"] == made["id"]  # once

    stranger = sign_in(app, client, 9925)
    assert stranger.post(f"/api/chat-posts/from-message/{answer['id']}").status_code == 404


@pytest.fixture
def bot(monkeypatch):
    calls: list[tuple[str, dict, dict | None]] = []

    async def fake_call(token, method, **params):
        params.pop("http_timeout", None)
        calls.append((method, params, None))
        if method == "forwardMessage":
            return {"message_id": 777, "photo": [{"file_id": "small"}, {"file_id": "BIG"}]}
        return {"message_id": 1}

    async def fake_upload(token, method, params, files):
        calls.append((method, params, files))
        return [{"message_id": 2}] if method == "sendMediaGroup" else {"message_id": 2}

    async def fake_photo(post_id, slot):
        return b"full size jpeg"

    monkeypatch.setattr(chat_posts, "bot_call", fake_call)
    monkeypatch.setattr(chat_posts, "bot_upload", fake_upload)
    monkeypatch.setattr(chat_posts, "full_photo", fake_photo)
    return calls


async def _bot_token() -> None:
    async with SessionLocal() as s:
        await settings_store.update(s, {"notify_bot_token": "123:test"})


def test_the_bot_sends_the_post_with_its_pictures_in_full(ctx, model, bot):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9926)
    ids = run(client, _setup, 9926)
    run(client, _bot_token)
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "<b>Ракета</b> стартовала",
                                    "source_post_ids": [ids["grouped"], ids["other"]]}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]

    r = owner.post(f"/api/chat-posts/{post_id}/send")
    assert r.status_code == 200 and r.json() == {"ok": True, "skipped": [], "to": None}
    methods = [c[0] for c in bot]
    # the grouped photo: forwarded inside the group for a moment, its file_id taken, the copy deleted
    assert methods == ["forwardMessage", "deleteMessage", "sendMediaGroup"]
    assert bot[0][1] == {"chat_id": -1001234567, "from_chat_id": -1001234567, "message_id": 10,
                         "disable_notification": True}
    album, files = bot[2][1], bot[2][2]
    assert album["chat_id"] == 9926
    assert album["media"] == [
        {"type": "photo", "media": "BIG", "caption": "<b>Ракета</b> стартовала", "parse_mode": "HTML"},
        {"type": "photo", "media": "attach://p1"}]
    assert files == {"p1": ("photo1.jpg", b"full size jpeg", "image/jpeg")}

    async def known():
        async with SessionLocal() as s:
            return (await s.get(BotFile, (ids["grouped"], 0))).file_id

    assert run(client, known) == "BIG"
    bot.clear()
    owner.post(f"/api/chat-posts/{post_id}/send")
    assert [c[0] for c in bot] == ["sendMediaGroup"]  # the file_id is kept: no forward again

    async def sent_at():
        async with SessionLocal() as s:
            return (await s.get(ChatPost, post_id)).sent_at

    assert run(client, sent_at) is not None


async def _protected_only(post_id: int, protected: int) -> None:
    async with SessionLocal() as s:
        (await s.get(ChatPost, post_id)).media = [{"post": protected, "slot": 0}]
        await s.commit()


def test_what_cannot_go_is_left_out_and_said(ctx, model, bot):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9927)
    ids = run(client, _setup, 9927)
    run(client, _bot_token)
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "Пост", "source_post_ids": [ids["protected"]]}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]
    run(client, partial(_protected_only, post_id, ids["protected"]))

    r = owner.post(f"/api/chat-posts/{post_id}/send").json()
    assert r["skipped"] == ["канал запретил копирование"]
    assert [c[0] for c in bot] == ["sendMessage"] and bot[0][1]["text"] == "Пост"


def test_the_sources_outlive_the_retention_period(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9928)
    ids = run(client, _setup, 9928)
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "Пост", "source_post_ids": [ids["other"]]}))
    talk(owner, chat_id, "Напиши пост")

    async def age_and_clean() -> tuple[bool, bool]:
        async with SessionLocal() as s:
            for key in ("other", "same"):
                (await s.get(Post, ids[key])).posted_at = utcnow() - timedelta(days=400)
            await settings_store.update(s, {"retention_days": 30})
            await s.commit()
        await maintenance.retention_step()
        async with SessionLocal() as s:
            return await s.get(Post, ids["other"]) is not None, await s.get(Post, ids["same"]) is not None

    assert run(client, age_and_clean) == (True, False)


def test_the_person_edits_the_text_and_the_pictures(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9929)
    ids = run(client, _setup, 9929)
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "Пост ИИ", "source_post_ids": [ids["grouped"], ids["other"],
                                                                          ids["protected"], ids["gif"]]}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]
    url = f"/api/chat-posts/{post_id}"

    edited = owner.patch(url, json={"text": "<b>Мой</b> пост<br><span class=\"tg-spoiler\">тайна</span>",
                                    "media": [{"post": ids["other"], "slot": 0}]}).json()
    assert edited["text"] == "<b>Мой</b> пост\n<tg-spoiler>тайна</tg-spoiler>" and edited["ai_text"] == "Пост ИИ"
    assert edited["edited"] and [m["post_id"] for m in edited["media"]] == [ids["other"]]

    def refused(body: dict) -> str:
        r = owner.patch(url, json=body)
        assert r.status_code == 422
        return r.json()["detail"]

    assert "запретил копировать" in refused({"media": [{"post": ids["protected"], "slot": 0}]})
    assert "GIF" in refused({"media": [{"post": ids["gif"], "slot": 0}, {"post": ids["other"], "slot": 0}]})
    assert "нет среди источников" in refused({"media": [{"post": 123456789, "slot": 0}]})
    assert "1100 из 1024" in refused({"text": "я" * 1100})  # under pictures
    assert "пустой" in refused({"text": "<b> </b>"})
    # without pictures it takes 4096; a GIF alone is fine
    assert owner.patch(url, json={"text": "я" * 1100, "media": []}).json()["limit"] == 4096
    assert len(owner.patch(url, json={"text": "Гиф", "media": [{"post": ids["gif"], "slot": 0}]}).json()["media"]) == 1

    stranger = sign_in(app, client, 9930)
    assert stranger.patch(url, json={"text": "чужое"}).status_code == 404



async def _notes(project_id: int) -> str | None:
    async with SessionLocal() as s:
        style = await s.get(post_style.PostStyle, post_style.key(project_id))
        return style.notes if style else None


async def _staff_edit(project_id: int, ai_text: str, text: str) -> None:
    """A staff member of the project edited a post the AI wrote in their own chat."""
    async with SessionLocal() as s:
        member = Member(project_id=project_id, login=f"staff{project_id}-{len(ai_text)}", password_hash="x")
        s.add(member)
        await s.flush()
        thread = ChatThread(project_id=project_id, member_id=member.id, model="m")
        s.add(thread)
        await s.flush()
        s.add(ChatPost(project_id=project_id, thread_id=thread.id, ai_text=ai_text, text=text, media=[],
                       edited_at=utcnow()))
        await s.commit()


def test_the_ai_learns_the_project_s_way_from_everyone_s_edits(ctx, model, monkeypatch):
    sent, script = model
    # an edit asks for the notes to be made anew in the background; here they are made when the test says
    asked: list[tuple[int, int | None]] = []
    monkeypatch.setattr(post_style, "learn_later", lambda project_id, member_id: asked.append((project_id, member_id)))
    app, client = ctx
    owner = sign_in(app, client, 9931)
    ids = run(client, _setup, 9931)
    chat_id = owner.post("/api/chats").json()["id"]

    def new_post(text: str) -> int:
        script.append(("suggest_post", {"text": text, "source_post_ids": []}))
        talk(owner, chat_id, "Напиши пост")
        return owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]

    first = new_post("Длинный пост ИИ с воды и пафосом")
    script.append("• Убирает пафос, пишет коротко")
    owner.patch(f"/api/chat-posts/{first}", json={"text": "Коротко и по делу"})
    assert asked == [(ids["project"], None)]
    assert run(client, partial(post_style.learn, ids["project"], None)) is True
    learning = sent[-1]
    assert not learning.get("stream") and "edit the posts an AI writes" in learning["messages"][0]["content"]
    assert json.loads(learning["messages"][1]["content"])["edits"] == [
        {"ai": "Длинный пост ИИ с воды и пафосом", "final": "Коротко и по делу"}]
    assert run(client, partial(_notes, ids["project"])) == "• Убирает пафос, пишет коротко"

    # the chat's AI gets the notes and the edit itself as an example
    script.append("Хорошо")
    talk(owner, chat_id, "Ещё идея?")
    system = sent[-1]["messages"][0]["content"]
    assert "• Убирает пафос, пишет коротко" in system and "They made it:\nКоротко и по делу" in system

    # one more edit is not enough to learn anew; the second is
    second = new_post("Второй пост ИИ")
    before = len(sent)
    owner.patch(f"/api/chat-posts/{second}", json={"text": "Второй, мой"})
    assert run(client, partial(post_style.learn, ids["project"], None)) is False
    assert len(sent) == before
    third = new_post("Третий пост ИИ")
    owner.patch(f"/api/chat-posts/{third}", json={"text": "Третий, мой"})
    script.append("• Пишет «мой»")
    assert run(client, partial(post_style.learn, ids["project"], None)) is True
    assert len(json.loads(sent[-1]["messages"][1]["content"])["edits"]) == 3
    assert run(client, partial(_notes, ids["project"])) == "• Пишет «мой»"

    # pictures changed only teach nothing; an edit back to the AI's text is no lesson either
    asked.clear()
    owner.patch(f"/api/chat-posts/{third}", json={"media": []})
    owner.patch(f"/api/chat-posts/{third}", json={"text": "Третий пост ИИ"})
    assert asked == []

    # one way for the project: a staff member's edits teach it too, and the owner's chat gets them
    run(client, partial(_staff_edit, ids["project"], "Пост ИИ сотруднику", "Пост, поправленный сотрудником"))
    script.append("• И сотрудник пишет коротко")
    assert run(client, partial(post_style.learn, ids["project"])) is False  # one new edit: not yet
    run(client, partial(_staff_edit, ids["project"], "Ещё пост ИИ", "Ещё пост сотрудника"))
    assert run(client, partial(post_style.learn, ids["project"])) is True
    edits = json.loads(sent[-1]["messages"][1]["content"])["edits"]
    assert {"ai": "Пост ИИ сотруднику", "final": "Пост, поправленный сотрудником"} in edits
    assert "• И сотрудник пишет коротко" in run(client, partial(post_style.prompt_part, ids["project"]))
    # another project has a way of its own
    assert run(client, partial(post_style.prompt_part, ids["project"] + 1000)) == ""


def test_tool_calls_written_as_text_are_made(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9932)
    ids = run(client, _setup, 9932)
    topic = run(client, _topic, ids["project"], [ids["other"]])
    chat_id = owner.post("/api/chats", json={"topic_id": topic}).json()["id"]

    # the model writes its calls as text, with a bracket off, as some free models do
    script += [f'[[{{ "name": "get_topic", "parameters": {{ "id": {topic} }} }}]',
               ("suggest_post", {"text": "Пост о запуске", "source_post_ids": []})]
    events = talk(owner, chat_id, "Напиши пост")
    assert [e["type"] for e in events] == ["status", "status", "done"]  # the text was never shown
    assert [e["text"] for e in events if e["type"] == "status"] == ["Читаю тему", "Оформляю пост"]
    tool = [m for m in sent[1]["messages"] if m["role"] == "tool"][0]
    assert json.loads(tool["content"])["title"] == "Запуск ракеты"  # the topic was read
    assert [m["role"] for m in owner.get(f"/api/chats/{chat_id}").json()["messages"]] == ["user", "post"]


def test_a_post_asked_for_and_written_as_text_becomes_a_post(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9933)
    ids = run(client, _setup, 9933)
    topic = run(client, _topic, ids["project"], [ids["other"]])
    chat_id = owner.post("/api/chats", json={"topic_id": topic}).json()["id"]
    text = "**Ракета стартовала.** " + "Подробности запуска и что будет дальше. " * 6
    script.append(text)
    events = talk(owner, chat_id, "Напиши пост")
    assert "".join(e["text"] for e in events if e["type"] == "delta") == text
    shown = owner.get(f"/api/chats/{chat_id}").json()["messages"]
    assert [m["role"] for m in shown] == ["user", "post"] and shown[1]["post"]["text"].startswith("<b>Ракета стартовала.</b>")
    assert [m["post_id"] for m in shown[1]["post"]["media"]] == [ids["other"]]  # the topic's pictures

    # an answer that starts with a link is an answer as usual, shown as it is written
    script.append("[Пост](https://t.me/x/1) хороший")
    events = talk(owner, chat_id, "Что скажешь?")
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "[Пост](https://t.me/x/1) хороший"
    assert owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["role"] == "assistant"


async def _group(tg_id: int) -> None:
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        s.add(ProjectGroup(project_id=user.project_id, chat_id=-100555, title="Моя группа"))
        await s.commit()


def test_the_post_goes_to_one_of_my_groups_as_a_feed_post_does(ctx, model, bot):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9934)
    ids = run(client, _setup, 9934)
    run(client, _bot_token)
    run(client, partial(_group, 9934))
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "Пост в группу", "source_post_ids": []}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]

    r = owner.post(f"/api/chat-posts/{post_id}/send", json={"chat_id": -100555}).json()
    assert r == {"ok": True, "skipped": [], "to": "Моя группа"}
    assert bot[-1][0] == "sendMessage" and bot[-1][1]["chat_id"] == -100555
    assert owner.post(f"/api/chat-posts/{post_id}/send", json={"chat_id": -100777}).status_code == 404
    assert owner.post(f"/api/chat-posts/{post_id}/send").json()["to"] is None  # the owner's own Telegram
    assert bot[-1][1]["chat_id"] == 9934



@pytest.fixture
def channel_bot(bot, monkeypatch):
    """The bot as the project's channel sees it: `member` is what getChatMember answers."""
    member = {"status": "administrator", "can_post_messages": True}
    real = chat_posts.bot_call

    async def call(token, method, **params):
        if method == "getChatMember":
            bot.append((method, params, None))
            if member.get("status") == "gone":
                raise chat_posts.BotError("Bad Request: chat not found")
            return member
        return await real(token, method, **params)

    monkeypatch.setattr(chat_posts, "bot_call", call)
    return member


async def _project_channel(project_id: int) -> None:
    async with SessionLocal() as s:
        p = await s.get(Project, project_id)
        p.channel_chat_id, p.channel_title, p.channel_username = -1009988, "Мой канал", "my_chan"
        await s.commit()




def test_the_bot_publishes_the_post_in_the_project_s_channel(ctx, model, bot, channel_bot):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9941)
    ids = run(client, _setup, 9941)
    run(client, _bot_token)
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "<b>Ракета</b> стартовала", "source_post_ids": [ids["other"], ids["grouped"]]}))
    talk(owner, chat_id, "Напиши пост")
    card = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]
    post_id = card["id"]
    assert card["sources"] == 2 and card["published_at"] is None and card["published_url"] is None

    # «N источников»: the posts it was written from, as the feed shows them
    sources = owner.get(f"/api/chat-posts/{post_id}/sources").json()["items"]
    assert sorted(p["id"] for p in sources) == sorted([ids["other"], ids["grouped"]])
    assert {"views", "comments", "media"} <= set(sources[0])

    # the project's channel chosen by its link (any channel, no bot needed to choose it): addressed by its @username
    bot.clear()
    r = owner.post(f"/api/chat-posts/{post_id}/publish").json()
    assert r["published"] and r["url"] == "https://t.me/cp_m_9941/2"
    assert bot[0] == ("getChatMember", {"chat_id": "@cp_m_9941", "user_id": 123}, None)
    # once our account has read it, by its id
    run(client, partial(_own_channel_read, ids["project"]))
    bot.clear()
    owner.post(f"/api/chat-posts/{post_id}/publish")
    assert bot[0][1]["chat_id"] == -1005550001

    # no channel of the project at all: said so
    run(client, partial(_no_own_channel, ids["project"]))
    r = owner.post(f"/api/chat-posts/{post_id}/publish")
    assert r.status_code == 409 and "Канал проекта не выбран" in r.json()["detail"]

    run(client, partial(_project_channel, ids["project"]))
    bot.clear()
    r = owner.post(f"/api/chat-posts/{post_id}/publish").json()
    assert r == {"published": True, "url": "https://t.me/my_chan/2", "skipped": []}
    # asked about its rights first, then the album into the channel (the photo's file known from before)
    assert [c[0] for c in bot] == ["getChatMember", "sendMediaGroup"]
    assert bot[0][1] == {"chat_id": -1009988, "user_id": 123}
    assert bot[1][1]["chat_id"] == -1009988
    card = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]
    assert card["published_at"] and card["published_url"] == "https://t.me/my_chan/2"


def test_publishing_says_what_the_bot_lacks_and_asks_before_leaving_pictures_out(ctx, model, bot, channel_bot):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9942)
    ids = run(client, _setup, 9942)
    run(client, _bot_token)
    run(client, partial(_project_channel, ids["project"]))
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "Пост", "source_post_ids": [ids["protected"]]}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]

    # not in the channel; an admin that may not post
    channel_bot["status"] = "gone"
    r = owner.post(f"/api/chat-posts/{post_id}/publish")
    assert r.status_code == 409 and "не добавлен в канал «Мой канал»" in r.json()["detail"]["message"]
    assert r.json()["detail"]["reason"] == "missing" and r.json()["detail"]["bot"]  # for «Добавить бота»
    channel_bot.update(status="administrator", can_post_messages=False)
    r = owner.post(f"/api/chat-posts/{post_id}/publish")
    assert r.status_code == 409 and "нет права публиковать сообщения" in r.json()["detail"]["message"]

    # a picture that cannot go: nothing published until the person agrees to go without it
    channel_bot["can_post_messages"] = True
    run(client, partial(_protected_only, post_id, ids["protected"]))
    bot.clear()
    r = owner.post(f"/api/chat-posts/{post_id}/publish").json()
    assert r == {"published": False, "skipped": ["канал запретил копирование"]}
    assert [c[0] for c in bot] == ["getChatMember"]
    r = owner.post(f"/api/chat-posts/{post_id}/publish", json={"partial": True}).json()
    assert r["published"] and r["skipped"] == ["канал запретил копирование"] and r["url"] == "https://t.me/my_chan/1"
    assert [c[0] for c in bot][-1] == "sendMessage"


def test_staff_publish_only_with_the_switch(ctx, model, bot, channel_bot):
    from test_members import member_login

    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9943)
    ids = run(client, _setup, 9943)
    run(client, _bot_token)
    run(client, partial(_project_channel, ids["project"]))
    made = owner.post("/api/members", json={"login": "publish.staff", "ai_chat": True}).json()
    assert made["member"]["ai_publish"] is False
    staff, _ = member_login(app, "publish.staff", made["password"])
    chat_id = staff.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "Пост сотрудника", "source_post_ids": []}))
    talk(staff, chat_id, "Напиши пост")
    post_id = staff.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]

    r = staff.post(f"/api/chat-posts/{post_id}/publish")
    assert r.status_code == 403
    patched = owner.patch(f"/api/members/{made['member']['id']}", json={"ai_publish": True}).json()
    assert patched["ai_publish"] is True
    channel_bot["status"] = "gone"
    r = staff.post(f"/api/chat-posts/{post_id}/publish")
    assert r.status_code == 409 and r.json()["detail"]["message"].endswith("Попросите об этом владельца проекта.")
    channel_bot["status"] = "administrator"
    assert staff.post(f"/api/chat-posts/{post_id}/publish").json()["published"] is True


async def _due_in_the_past(post_id: int, minutes: int) -> None:
    async with SessionLocal() as s:
        (await s.get(ChatPost, post_id)).scheduled_at = utcnow() - timedelta(minutes=minutes)
        await s.commit()


async def _row(post_id: int) -> ChatPost:
    async with SessionLocal() as s:
        return await s.get(ChatPost, post_id)


def test_a_scheduled_post_is_published_by_the_bot_at_its_time(ctx, model, bot, channel_bot):
    from datetime import datetime, timezone

    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9944)
    ids = run(client, _setup, 9944)
    run(client, _bot_token)
    run(client, partial(_project_channel, ids["project"]))
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "Пост на завтра", "source_post_ids": [ids["protected"]]}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]
    run(client, partial(_protected_only, post_id, ids["protected"]))

    soon = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert owner.put(f"/api/chat-posts/{post_id}/schedule", json={"at": soon.isoformat()}).status_code == 422
    far = datetime.now(timezone.utc) + timedelta(days=400)
    assert owner.put(f"/api/chat-posts/{post_id}/schedule", json={"at": far.isoformat()}).status_code == 422

    # checked as publishing is: what cannot go is asked about, nothing is downloaded and nothing sent
    at = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(microsecond=0)
    bot.clear()
    r = owner.put(f"/api/chat-posts/{post_id}/schedule", json={"at": at.isoformat()}).json()
    assert r == {"scheduled": False, "skipped": ["канал запретил копирование"]}
    assert [c[0] for c in bot] == ["getChatMember"]
    r = owner.put(f"/api/chat-posts/{post_id}/schedule", json={"at": at.isoformat(), "partial": True}).json()
    assert r["scheduled"] and r["scheduled_at"] == at.replace(tzinfo=None).isoformat() + "Z"
    card = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]
    assert card["scheduled_at"] == r["scheduled_at"] and card["publish_error"] is None

    # its time: the bot publishes it, as it is then, without what cannot go
    run(client, partial(_due_in_the_past, post_id, 1))
    bot.clear()
    run(client, chat_posts.publish_due)
    assert [c[0] for c in bot] == ["getChatMember", "sendMessage"] and bot[1][1]["chat_id"] == -1009988
    row = run(client, partial(_row, post_id))
    assert row.scheduled_at is None and row.published_url == "https://t.me/my_chan/1" and row.publish_error is None
    bot.clear()
    run(client, chat_posts.publish_due)
    assert bot == []  # once

    # cancelled: nothing goes
    owner.put(f"/api/chat-posts/{post_id}/schedule", json={"at": at.isoformat(), "partial": True})
    assert owner.delete(f"/api/chat-posts/{post_id}/schedule").json() == {"ok": True}
    assert run(client, partial(_row, post_id)).scheduled_at is None


def test_a_scheduled_post_that_cannot_go_is_not_published_and_its_author_is_told(ctx, model, bot, channel_bot):
    from datetime import datetime, timezone

    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9945)
    ids = run(client, _setup, 9945)
    run(client, _bot_token)
    run(client, partial(_project_channel, ids["project"]))
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "Пост", "source_post_ids": []}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]
    at = datetime.now(timezone.utc) + timedelta(hours=1)
    assert owner.put(f"/api/chat-posts/{post_id}/schedule", json={"at": at.isoformat()}).json()["scheduled"]

    # the bot lost its right to post meanwhile
    channel_bot["can_post_messages"] = False
    run(client, partial(_due_in_the_past, post_id, 2))
    bot.clear()
    run(client, chat_posts.publish_due)
    row = run(client, partial(_row, post_id))
    assert row.scheduled_at is None and row.published_at is None and "нет права публиковать" in row.publish_error
    told = [c for c in bot if c[0] == "sendMessage"]
    assert len(told) == 1 and told[0][1]["chat_id"] == 9945 and "Не удалось опубликовать запланированный" in told[0][1]["text"]
    card = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]
    assert card["publish_error"] == row.publish_error and card["publish_error_at"]

    # the server was down past its time by more than an hour: not published, said why
    channel_bot["can_post_messages"] = True
    owner.put(f"/api/chat-posts/{post_id}/schedule", json={"at": at.isoformat()})
    assert run(client, partial(_row, post_id)).publish_error is None  # a new time clears the old failure
    run(client, partial(_due_in_the_past, post_id, 90))
    bot.clear()
    run(client, chat_posts.publish_due)
    assert run(client, partial(_row, post_id)).publish_error == chat_posts.LATE_TEXT
    assert [c[0] for c in bot] == ["sendMessage"] and bot[0][1]["chat_id"] == 9945


async def _own_channel_read(project_id: int) -> None:
    async with SessionLocal() as s:
        ch = await s.scalar(select(Channel).join(ProjectChannel, ProjectChannel.channel_id == Channel.id).where(
            ProjectChannel.project_id == project_id, ProjectChannel.own))
        ch.tg_id = 5550001
        await s.commit()


async def _no_own_channel(project_id: int) -> None:
    async with SessionLocal() as s:
        row = await s.scalar(select(ProjectChannel).where(ProjectChannel.project_id == project_id, ProjectChannel.own))
        await s.delete(row)
        await s.commit()


async def _published(post_id: int, **values) -> None:
    async with SessionLocal() as s:
        post = await s.get(ChatPost, post_id)
        for k, v in values.items():
            setattr(post, k, v)
        await s.commit()


def test_a_published_post_is_changed_in_the_channel_or_taken_back(ctx, model, bot, channel_bot):
    _, script = model
    app, client = ctx
    owner = sign_in(app, client, 9947)
    ids = run(client, _setup, 9947)
    run(client, _bot_token)
    run(client, partial(_project_channel, ids["project"]))
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "<b>Ракета</b> стартовала", "source_post_ids": [ids["other"]]}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]
    assert owner.post(f"/api/chat-posts/{post_id}/publish").json()["published"]
    card = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]
    # its photo carries the text: a caption, 1024 characters at most
    assert card["channel_actions"] == {"edit": True, "why_edit": None, "delete": True, "why_delete": None,
                                       "limit": 1024}

    bot.clear()
    changed = owner.put(f"/api/chat-posts/{post_id}/channel", json={"text": "<b>Ракета</b> вышла на орбиту"}).json()
    assert bot == [("editMessageCaption", {"chat_id": -1009988, "message_id": 2,
                                           "caption": "<b>Ракета</b> вышла на орбиту", "parse_mode": "HTML"}, None)]
    assert changed["text"] == "<b>Ракета</b> вышла на орбиту" and changed["published_edited_at"] and changed["edited"]
    r = owner.put(f"/api/chat-posts/{post_id}/channel", json={"text": "а" * 1100})
    assert r.status_code == 422 and "1024" in r.json()["detail"]

    # taken back: deleted from the channel, it stays here and may go again
    bot.clear()
    gone = owner.delete(f"/api/chat-posts/{post_id}/channel").json()
    assert bot == [("deleteMessages", {"chat_id": -1009988, "message_ids": [2]}, None)]
    assert gone["published_at"] is None and gone["withdrawn_at"] and gone["withdrawn_by"] == "владелец"
    assert gone["channel_actions"] is None
    listed = owner.get("/api/publications").json()["items"]
    assert [p["id"] for p in listed] == [post_id]
    assert owner.delete(f"/api/chat-posts/{post_id}/channel").status_code == 422  # nothing out to take back

    # Telegram lets a bot delete its message in the first 48 hours only: said before anything is asked
    owner.post(f"/api/chat-posts/{post_id}/publish")
    run(client, partial(_published, post_id, published_at=utcnow() - timedelta(days=3)))
    card = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]
    assert card["channel_actions"]["delete"] is False and "48 часов" in card["channel_actions"]["why_delete"]
    assert owner.delete(f"/api/chat-posts/{post_id}/channel").status_code == 422

    # one published before its messages were kept: its first message from the link, in the project's channel — a
    # text-only one may be changed and taken back
    run(client, partial(_published, post_id, published_at=utcnow(), published_url="https://t.me/my_chan/55",
                        published_chat=None, published_ids=None, published_text_id=None, published_text_kind=None,
                        media=[]))
    card = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]
    assert card["channel_actions"]["edit"] and card["channel_actions"]["delete"] and card["channel_actions"]["limit"] == 4096
    bot.clear()
    owner.put(f"/api/chat-posts/{post_id}/channel", json={"text": "Старый пост"})
    assert bot[0][0] == "editMessageText" and bot[0][1]["message_id"] == 55 and bot[0][1]["chat_id"] == -1009988
