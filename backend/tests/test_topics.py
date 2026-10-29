import io
import json
from datetime import timedelta

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app import ai, settings_store, topics
from app.config import settings, site_url
from app.db import SessionLocal
from app.models import AiAccount, Channel, Post, Project, ProjectChannel, Topic, TopicPost, User, utcnow
from app.services import preview_path
from app.web import members as members_api
from app.worker import maintenance

from test_members import member_login
from test_user_channels import own_ai, _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


@pytest.fixture
def model(monkeypatch):
    """A fake model: "launch" posts start a topic, "more" ones add to the live topic, "again" repeat it."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payload = json.loads(body["messages"][-1]["content"])
        sent.append(payload)
        posts, live = payload["posts"], payload["topics"]
        answer = {"new_topics": [], "updates": [], "attach": []}
        launch = [p["id"] for p in posts if "launch" in p["text"]]
        if launch:
            covered = any("launch" in p["text"] for p in payload["my_channel"])
            answer["new_topics"].append({"title": "Запуск продукта", "summary": "Компания запустила продукт.",
                                          "update": "Компания объявила запуск", "post_ids": launch + [999999],
                                          "covered": covered, "emoji": "🚀", "importance": 7})
        if live:
            more = [p["id"] for p in posts if "more" in p["text"]]
            again = [p["id"] for p in posts if "again" in p["text"]]
            if more:
                answer["updates"].append({"topic_id": live[0]["id"], "title": "", "post_ids": more, "covered": False,
                                          "summary": "Компания запустила продукт, цена 10$.", "update": "Названа цена",
                                          "importance": 6, "novelty": 4})
            if again:
                answer["attach"].append({"topic_id": live[0]["id"], "post_ids": again})
        return httpx.Response(200, json={"id": "g", "model": body["model"],
                                         "choices": [{"message": {"content": json.dumps(answer)}}],
                                         "usage": {"prompt_tokens": 900, "completion_tokens": 120, "cost": 0.0011}})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    return sent


@pytest.fixture
def bot(monkeypatch):
    messages: list[dict] = []

    async def fake_bot_call(token, method, **params):
        messages.append({"method": method, **params})
        return {}

    monkeypatch.setattr(topics, "bot_call", fake_bot_call)
    return messages


async def _setup(tg_id: int, link: str) -> tuple[int, int, AiAccount]:
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        account = AiAccount(user_id=user.id, key_enc=ai.encrypt_key("sk-or-x"), connected_at=utcnow())
        await own_ai(s, user.id)
        s.add(account)
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        channel = await s.scalar(select(Channel).where(Channel.link == link))
        # a post from yesterday: before the first run's window, never read
        s.add(Post(channel_id=channel.id, msg_id=100, posted_at=utcnow() - timedelta(days=1), text="old launch",
                   created_at=utcnow() - timedelta(days=1)))
        # off by default: the owner turns topics on
        (await topics.state_of(s, project.id)).enabled = True
        await s.commit()
        await s.refresh(account)
        return project.id, channel.id, account


async def _post(channel_id: int, msg_id: int, text: str, picture: bytes | None = None) -> int:
    async with SessionLocal() as s:
        p = Post(channel_id=channel_id, msg_id=msg_id, posted_at=utcnow(), text=text, views=100,
                 created_at=utcnow() - timedelta(minutes=5), has_preview=picture is not None,
                 previews=[{"k": "photo", "p": 0}] if picture else None)
        s.add(p)
        await s.commit()
    if picture:
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        preview_path(p.id, 0).write_bytes(picture)
    return p.id


async def _own_channel(tg_id: int) -> int:
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        ch = Channel(link=f"@uc_topic_own_{tg_id}", status="active", title="Мой канал")
        s.add(ch)
        await s.flush()
        s.add(ProjectChannel(project_id=project.id, channel_id=ch.id, own=True))
        await s.commit()
        return ch.id


def picture(shade: int) -> bytes:
    img = Image.new("L", (64, 64), shade)
    for x in range(32):
        img.putpixel((x, x), 255 - shade)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG")
    return buf.getvalue()


def test_posts_become_topics_that_grow(ctx, model, bot):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9801)
    assert owner.post("/api/channels", json={"links": "@uc_topic"}).json()["added"]
    run(client, _activate, "@uc_topic", 98011, 0)
    project_id, channel_id, account = run(client, _setup, 9801, "@uc_topic")

    settings_out = owner.get("/api/topics/settings").json()
    assert settings_out["enabled"] and settings_out["prompt"] == topics.DEFAULT_PROMPT and settings_out["schedule"]["rules"][0]["every_min"] == 60
    owner.put("/api/topics/notify", json={"on": True})

    same = picture(40)
    run(client, _post, channel_id, 1, "big launch today", same)
    run(client, _post, channel_id, 2, "launch details", same)  # the same photo from another channel's post
    run(client, _post, channel_id, 3, "cat picture", picture(200))
    own_id = run(client, _own_channel, 9801)
    run(client, _post, own_id, 1, "our take on the launch")

    async def first_run():
        async with SessionLocal() as s:
            await settings_store.update(s, {"notify_bot_token": "123:test"})
            project = await s.get(Project, project_id)
        return await topics.run_project(project, account, force=True)

    created = run(client, first_run)
    assert len(created) == 1
    # the project's own post is not topic material: it comes apart, to say what the owner already covered
    assert [p["text"] for p in model[0]["posts"]] == ["big launch today", "launch details", "cat picture"]
    assert [p["text"] for p in model[0]["my_channel"]] == ["our take on the launch"]
    assert model[0]["topics"] == []

    listed = owner.get("/api/topics").json()["items"]
    assert [s["title"] for s in listed] == ["Запуск продукта"]
    topic = listed[0]
    # two posts of one channel: one source
    assert topic["sources"] == 1 and topic["posts_count"] == 2 and not topic["updated"] and topic["views"] == 200
    assert topic["covered"] and not topic["hidden"]
    assert len(topic["media"]) == 1  # one photo, twice: shown once

    # the bot told the owner in one message: the emoji, the title linking to the topic, the summary in a quote
    assert len(bot) == 1
    assert bot[0]["text"] == (f'<b>Новая тема</b>\n\n🚀 <a href="{site_url()}/#/ai/topic/{topic["id"]}">'
                              '<b>Запуск продукта</b></a>\n<blockquote expandable>Компания запустила продукт.</blockquote>')

    # the next run: one post adds to the topic, one only repeats it
    run(client, _post, channel_id, 4, "more news: the price")
    run(client, _post, channel_id, 5, "again the same launch")
    run(client, first_run)
    assert model[1]["topics"][0]["title"] == "Запуск продукта"
    detail = owner.get(f"/api/topics/{topic['id']}").json()
    assert detail["summary"] == "Компания запустила продукт, цена 10$." and detail["sources"] == 1
    assert detail["posts_count"] == 4
    assert [t["text"] for t in detail["timeline"]] == ["Компания объявила запуск", "Названа цена"]
    assert [p["text"] for p in detail["posts"]][0] == "again the same launch"
    # and the bot told what is new in it
    assert len(bot) == 2 and bot[1]["text"].startswith("<b>Обновлена тема</b>")
    assert "<i>обновлена</i>" in bot[1]["text"]
    assert "<b>Новое:</b> Названа цена\nКомпания запустила продукт, цена 10$." in bot[1]["text"]

    # opened: «Обновлена» is gone for the owner, still there for the staff member
    assert not owner.get("/api/topics").json()["items"][0]["updated"]
    created_member = owner.post("/api/members", json={"login": "topic.staff"}).json()
    staff, _ = member_login(app, "topic.staff", created_member["password"])
    assert staff.get("/api/topics").json()["items"][0]["updated"]
    assert staff.put("/api/topics/settings", json={"enabled": False}).status_code == 403
    assert staff.put("/api/topics/notify", json={"on": True}).status_code == 409  # no Telegram linked
    assert staff.get("/api/topics/settings").json()["prompt"] is None

    # a run with no new posts asks nothing
    run(client, first_run)
    assert len(model) == 2

    # owner's settings: off, prompt, reset
    assert owner.put("/api/topics/settings", json={"prompt": "Only AI"}).json()["prompt"] == "Only AI"
    assert owner.put("/api/topics/settings", json={"reset_prompt": True}).json()["prompt"] == topics.DEFAULT_PROMPT
    assert not owner.put("/api/topics/settings", json={"enabled": False}).json()["enabled"]


def test_topic_posts_outlive_the_retention_period(ctx, model, bot):
    app, client = ctx
    owner = sign_in(app, client, 9802)
    assert owner.post("/api/channels", json={"links": "@uc_topic_keep"}).json()["added"]
    run(client, _activate, "@uc_topic_keep", 98021, 0)
    project_id, channel_id, _ = run(client, _setup, 9802, "@uc_topic_keep")

    async def scenario():
        async with SessionLocal() as s:
            old = utcnow() - timedelta(days=60)
            kept = Post(channel_id=channel_id, msg_id=50, posted_at=old, text="in a live topic")
            gone = Post(channel_id=channel_id, msg_id=51, posted_at=old, text="nobody needs it")
            dead_post = Post(channel_id=channel_id, msg_id=52, posted_at=old, text="in an old topic")
            live = Topic(project_id=project_id, title="live", summary="s", updated_at=utcnow())
            dead = Topic(project_id=project_id, title="dead", summary="s", updated_at=old, created_at=old)
            # hidden long ago: the topic stays (its reason teaches the model), its post goes
            hid_post = Post(channel_id=channel_id, msg_id=53, posted_at=old, text="in a hidden topic")
            hid = Topic(project_id=project_id, title="hidden", summary="s", updated_at=old, created_at=old,
                        hidden_at=old, hide_reason="не наше")
            s.add_all([kept, gone, dead_post, live, dead, hid_post, hid])
            await s.flush()
            s.add_all([TopicPost(topic_id=live.id, post_id=kept.id), TopicPost(topic_id=dead.id, post_id=dead_post.id),
                       TopicPost(topic_id=hid.id, post_id=hid_post.id)])
            await s.commit()
            ids = kept.id, gone.id, dead_post.id, hid_post.id
        await maintenance.retention_step()
        await maintenance.retention_step()  # the old topic went first, its post on the next run
        async with SessionLocal() as s:
            return [await s.get(Post, i) is not None for i in ids], [
                st.title for st in (await s.execute(select(Topic).where(Topic.project_id == project_id))).scalars()]

    alive, titles = run(client, scenario)
    assert alive == [True, False, False, False] and sorted(titles) == ["hidden", "live"]


def test_the_digest_fits_telegram():
    many = [Topic(id=i, title=f"Тема <{i}>", summary="Длинный пересказ. " * 60, emoji=None) for i in range(1, 30)]
    messages = topics.digest(many[:20], [(t, "Что нового") for t in many[20:]], "Мой канал")
    assert len(messages) > 1 and all(len(m) <= 4096 for m in messages)
    assert messages[0].startswith("<b>Новые и обновлённые темы</b> · Мой канал")
    joined = "".join(messages)
    assert joined.count("<blockquote expandable>") == 29 and "Тема &lt;1&gt;" in joined
    assert topics.DEFAULT_EMOJI in joined and "…</blockquote>" in joined  # no emoji chosen; a long summary cut

    assert topics._emoji(" 🚀 ") == "🚀" and topics._emoji("rocket") is None and topics._emoji("") is None


def test_not_in_your_channel_after_all(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9803)
    assert owner.post("/api/channels", json={"links": "@uc_topic_cov"}).json()["added"]
    run(client, _activate, "@uc_topic_cov", 98031, 0)
    project_id, channel_id, _ = run(client, _setup, 9803, "@uc_topic_cov")
    post_id = run(client, _post, channel_id, 1, "news")

    async def make():
        async with SessionLocal() as s:
            topic = Topic(project_id=project_id, title="t", summary="s", covered=True)
            s.add(topic)
            await s.commit()
            return topic.id

    topic_id = run(client, make)
    assert owner.get(f"/api/topics/{topic_id}").json()["covered"]
    assert owner.post(f"/api/topics/{topic_id}/not-covered").json() == {"ok": True}
    assert not owner.get(f"/api/topics/{topic_id}").json()["covered"]

    async def update_says_covered():
        async with SessionLocal() as s:
            topic, post = await s.get(Topic, topic_id), await s.get(Post, post_id)
            answer = {"new_topics": [], "attach": [], "updates": [
                {"topic_id": topic_id, "post_ids": [post_id], "title": "", "summary": "s2", "update": "u", "covered": True}]}
            await topics.apply(s, project_id, answer, {post_id: post}, {topic_id: topic})
            await s.commit()
            return topic.covered, topic.summary

    assert run(client, update_says_covered) == (False, "s2")  # the model says so again: the mark stays off
    other = sign_in(app, client, 9804)
    assert other.post(f"/api/topics/{topic_id}/not-covered").status_code == 404  # another project's topic


def test_a_run_is_listed_hottest_first_and_big_news_raise_a_topic(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9805)
    assert owner.post("/api/channels", json={"links": "@uc_topic_rank"}).json()["added"]
    run(client, _activate, "@uc_topic_rank", 98051, 0)
    project_id, channel_id, _ = run(client, _setup, 9805, "@uc_topic_rank")
    ids = [run(client, _post, channel_id, i, f"post {i}") for i in range(1, 7)]

    def new(title, post_id, importance):
        return {"title": title, "summary": "s", "update": "u", "post_ids": [post_id], "covered": False, "emoji": "",
                "importance": importance}

    async def scenario():
        async with SessionLocal() as s:
            posts = {p.id: p for p in (await s.execute(select(Post).where(Post.id.in_(ids)))).scalars()}
            first = utcnow() - timedelta(hours=2)
            await topics.apply(s, project_id, {"new_topics": [new("Тихая", ids[0], 2), new("Горячая", ids[1], 9),
                                                              new("Средняя", ids[2], 5)], "updates": [], "attach": []},
                               posts, {}, first)
            await s.commit()
            live = {t.id: t for t in (await s.execute(select(Topic).where(Topic.project_id == project_id))).scalars()}
            by_title = {t.title: t.id for t in live.values()}
            # the next run: a new topic, a small update (stays in its place) and big news (raised)
            update = lambda title, post_id, up: {"topic_id": by_title[title], "title": "", "summary": "s2",  # noqa: E731
                                                 "update": "u2", "post_ids": [post_id], "covered": False,
                                                 "importance": 8, "novelty": 9 if up else 3}
            await topics.apply(s, project_id, {"new_topics": [new("Новая", ids[3], 6)], "attach": [], "updates": [
                update("Средняя", ids[4], False), update("Тихая", ids[5], True)]}, posts, live, utcnow())
            await s.commit()

    run(client, scenario)
    listed = [t["title"] for t in owner.get("/api/topics").json()["items"]]
    assert listed == ["Тихая", "Новая", "Горячая", "Средняя"]  # raised with importance 8 above the new 6


def test_a_hidden_topic_teaches_the_model(ctx, model, bot):
    app, client = ctx
    owner = sign_in(app, client, 9806)
    assert owner.post("/api/channels", json={"links": "@uc_topic_hide"}).json()["added"]
    run(client, _activate, "@uc_topic_hide", 98061, 0)
    project_id, channel_id, account = run(client, _setup, 9806, "@uc_topic_hide")
    run(client, _post, channel_id, 1, "big launch today")

    async def go():
        async with SessionLocal() as s:
            project = await s.get(Project, project_id)
        return await topics.run_project(project, account, force=True)

    [topic_id] = run(client, go)
    assert owner.post(f"/api/topics/{topic_id}/hide", json={"reason": "  "}).status_code == 422  # a reason is a must
    assert owner.post(f"/api/topics/{topic_id}/hide", json={"reason": "Запуски  продуктов\nнам не интересны"}).json()
    assert owner.get("/api/topics").json()["items"] == []
    hidden = owner.get("/api/topics?hidden=true").json()["items"]
    assert [(t["id"], t["hide_reason"], t["hidden"]) for t in hidden] == [
        (topic_id, "Запуски продуктов нам не интересны", True)]
    assert owner.get(f"/api/topics/{topic_id}").json()["hidden"]  # still opens, from the bot's message

    # the next run: the model is told what was hidden and why, and the hidden topic is not one to grow
    run(client, _post, channel_id, 2, "more news: the price")
    run(client, go)
    assert model[-1]["hidden"] == [{"title": "Запуск продукта", "summary": "Компания запустила продукт.",
                                    "reason": "Запуски продуктов нам не интересны"}]
    assert model[-1]["topics"] == []

    # back: on the list, and not a lesson any more
    assert owner.post(f"/api/topics/{topic_id}/unhide").json() == {"ok": True}
    assert [t["id"] for t in owner.get("/api/topics").json()["items"]] == [topic_id]
    run(client, _post, channel_id, 3, "again the same launch")
    run(client, go)
    assert model[-1]["hidden"] == [] and model[-1]["topics"][0]["id"] == topic_id

    other = sign_in(app, client, 9807)
    assert other.post(f"/api/topics/{topic_id}/hide", json={"reason": "чужая"}).status_code == 404


def test_ai_features_are_off_until_turned_on(ctx):
    """Linking OpenRouter spends nothing by itself: topics, the AI's posts and the smart feed wait for the owner."""
    app, client = ctx
    owner = sign_in(app, client, 9808)
    assert not owner.get("/api/topics/settings").json()["enabled"]
    assert not owner.get("/api/publications").json()["settings"]["topic_posts"]


def test_a_run_s_topics_are_timed_when_they_come_not_when_it_began(ctx, model, bot, monkeypatch):
    """Someone looking at the topics while a run waits for the AI must still see its topics as new."""
    app, client = ctx
    owner = sign_in(app, client, 9809)
    assert owner.post("/api/channels", json={"links": "@uc_topic_time"}).json()["added"]
    run(client, _activate, "@uc_topic_time", 98091, 0)
    project_id, channel_id, account = run(client, _setup, 9809, "@uc_topic_time")
    run(client, _post, channel_id, 1, "big launch today")
    asked: list = []
    complete = ai.complete

    async def timed(**kwargs):
        asked.append(utcnow())  # the person looks at the topics now, while the AI thinks
        return await complete(**kwargs)

    monkeypatch.setattr(ai, "complete", timed)

    async def go():
        async with SessionLocal() as s:
            project = await s.get(Project, project_id)
        [topic_id] = await topics.run_project(project, account, force=True)
        async with SessionLocal() as s:
            return (await s.get(Topic, topic_id)).ranked_at

    assert run(client, go) > asked[0]


def test_each_person_chooses_for_themselves_whether_the_bot_sends_topics(ctx, bot):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9802)
    assert owner.post("/api/channels", json={"links": "@uc_topic_notify"}).json()["added"]
    run(client, _activate, "@uc_topic_notify", 98021, 0)
    project_id, _, _ = run(client, _setup, 9802, "@uc_topic_notify")
    created = owner.post("/api/members", json={"login": "notify.staff"}).json()
    staff, _ = member_login(app, "notify.staff", created["password"])

    async def link_staff_telegram() -> None:
        from app.models import Member

        async with SessionLocal() as s:
            member = await s.scalar(select(Member).where(Member.login == "notify.staff"))
            member.tg_user_id = 777002
            await s.commit()

    async def a_topic() -> int:
        async with SessionLocal() as s:
            await settings_store.update(s, {"notify_bot_token": "123:test"})
            t = Topic(project_id=project_id, title="Тема", summary="Что случилось.")
            s.add(t)
            await s.commit()
            return t.id

    run(client, link_staff_telegram)
    topic_id = run(client, a_topic)

    def told() -> list[int]:
        bot.clear()
        run(client, lambda: topics.notify(project_id, [topic_id], []))
        return sorted(m["chat_id"] for m in bot)

    assert told() == []  # nobody asked
    assert staff.put("/api/topics/notify", json={"on": True}).json()["notify"] is True
    assert owner.get("/api/topics/settings").json()["notify"] is False  # the owner's own stays off
    assert told() == [777002]
    assert owner.put("/api/topics/notify", json={"on": True}).json()["notify"] is True
    assert told() == [9802, 777002]
    assert staff.put("/api/topics/notify", json={"on": False}).json()["notify"] is False
    assert owner.get("/api/topics/settings").json()["notify"] is True  # and the staff member's choice is theirs
    assert told() == [9802]


def test_topics_carry_no_marks_of_how_sure_they_are(ctx):
    """«Надёжность» is gone: the model is not asked how sure a topic is, an answer that still says it is fine, and
    neither the topics nor the settings tell it."""
    app, client = ctx
    owner = sign_in(app, client, 9806)
    assert owner.post("/api/channels", json={"links": "@uc_topic_marks"}).json()["added"]
    run(client, _activate, "@uc_topic_marks", 98061, 0)
    project_id, channel_id, _ = run(client, _setup, 9806, "@uc_topic_marks")
    ids = [run(client, _post, channel_id, i, f"post {i}") for i in range(1, 3)]

    schema = topics.SCHEMA["json_schema"]["schema"]["properties"]
    for part in ("new_topics", "updates"):
        assert not {"trust", "trust_note"} & set(schema[part]["items"]["properties"])
    assert "trust" not in topics.SYSTEM_PROMPT and "reliab" not in topics.SYSTEM_PROMPT

    async def scenario():
        async with SessionLocal() as s:
            posts = {p.id: p for p in (await s.execute(select(Post).where(Post.id.in_(ids)))).scalars()}
            # an answer of an old habit with the marks in it: they are left aside
            new = {"title": "Слух", "summary": "s", "update": "u", "post_ids": [ids[0]], "covered": False, "emoji": "",
                   "importance": 5, "old_news": False, "trust": "unconfirmed", "trust_note": "по данным одного СМИ"}
            await topics.apply(s, project_id, {"new_topics": [new], "updates": [], "attach": []}, posts, {},
                               utcnow() - timedelta(hours=1))
            await s.commit()

    run(client, scenario)
    [t] = owner.get("/api/topics").json()["items"]
    assert t["title"] == "Слух" and "trust" not in t and "trust_note" not in t
    assert "autopost_trusted_only" not in owner.get("/api/publications").json()["settings"]
