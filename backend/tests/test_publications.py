"""«Публикации»: the posts that went out or are to, from anyone's chat, and the posts the AI writes by itself for
the new topics — with autoposting (publications.py, web/publications_api.py)."""

import json
from datetime import timedelta
from functools import partial

import httpx
from sqlalchemy import select

from app import publications
from app.db import SessionLocal
from app.models import Channel, ChatPost, Post, Project, Topic, TopicPost, TopicPostRetry, utcnow

from test_chat_posts import _bot_token, _project_channel, _setup, bot, channel_bot, model, talk  # noqa: F401
from test_members import member_login
from test_user_channels import ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def _tool_answer(text: str, sources: list[int]) -> httpx.Response:
    """The model's answer with a suggest_post call made as a call."""
    args = json.dumps({"text": text, "source_post_ids": sources})
    return httpx.Response(200, json={"id": "g", "model": "m", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                                     "choices": [{"message": {"tool_calls": [{"id": "c1", "type": "function",
                                                                              "function": {"name": "suggest_post",
                                                                                           "arguments": args}}]}}]})


async def _topics(project_id: int, post_id: int, n: int) -> list[int]:
    async with SessionLocal() as s:
        made = [Topic(project_id=project_id, title=f"Тема {i}", summary="Что случилось", importance=10 - i)
                for i in range(n)]
        s.add_all(made)
        await s.flush()
        s.add_all(TopicPost(topic_id=t.id, post_id=post_id) for t in made)
        await s.commit()
        return [t.id for t in made]


async def _project(project_id: int) -> Project:
    async with SessionLocal() as s:
        return await s.get(Project, project_id)


async def _posts(project_id: int) -> list[ChatPost]:
    async with SessionLocal() as s:
        return (await s.execute(select(ChatPost).where(ChatPost.project_id == project_id)
                                .order_by(ChatPost.id))).scalars().all()


def test_the_ai_writes_posts_for_the_largest_new_topics(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9951)
    ids = run(client, _setup, 9951)
    project = run(client, partial(_project, ids["project"]))
    topic_ids = run(client, partial(_topics, ids["project"], ids["other"], 3))

    # off by default: nothing written; the wishes start with the default ones
    assert run(client, partial(publications.after_topics, project, topic_ids)) == []
    assert owner.get("/api/publications").json()["settings"]["prompt"] == publications.DEFAULT_PROMPT
    assert owner.put("/api/publications/settings", json={"autopost": "now"}).status_code == 409  # posts first
    saved = owner.put("/api/publications/settings", json={"topic_posts": True, "posts_limit": 2,
                                                          "prompt": "В конце — @shifr"}).json()
    assert saved["topic_posts"] and saved["posts_limit"] == 2 and saved["autopost"] == "off"

    # the two largest topics: one called the tool, one wrote the call as text
    script += [_tool_answer("<b>Тема 0</b> случилась", [ids["other"]]),
               json.dumps({"name": "suggest_post", "arguments": {"text": "Тема 1 тоже", "source_post_ids": []}})]
    made = run(client, partial(publications.after_topics, project, topic_ids))
    assert len(made) == 2 and len(sent) == 2
    system = sent[0]["messages"][0]["content"]
    assert "В конце — @shifr" in system and sent[0]["model"]  # the owner's wishes; the chat's model
    facts = json.loads(sent[0]["messages"][1]["content"])
    assert facts["topic"]["title"] == "Тема 0"
    assert "my_channel" in facts  # its @username, for wishes like «в конце — @username моего канала»

    listed = owner.get("/api/publications", params={"filter": "suggested"}).json()
    assert [p["topic"]["title"] for p in listed["items"]] == ["Тема 1", "Тема 0"]
    first = [p for p in listed["items"] if p["topic"]["title"] == "Тема 0"][0]
    assert first["text"] == "<b>Тема 0</b> случилась" and first["media"] and first["editable"]
    assert owner.get("/api/badges").json()["posts"] == 0  # the first look: nothing new yet
    assert owner.get("/api/publications", params={"filter": "published"}).json()["items"] == []

    # one nobody wants goes; published or scheduled ones cannot
    assert owner.delete(f"/api/chat-posts/{first['id']}").json() == {"ok": True}
    assert len(owner.get("/api/publications").json()["items"]) == 1


def test_autoposting_schedules_them_apart(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9952)
    ids = run(client, _setup, 9952)
    project = run(client, partial(_project, ids["project"]))
    topic_ids = run(client, partial(_topics, ids["project"], ids["other"], 2))
    saved = owner.put("/api/publications/settings", json={"topic_posts": True, "autopost": "delay", "autopost_hours": 2,
                                                          "autopost_gap_min": 5}).json()
    assert saved["autopost_gap_min"] == 5 and saved["gaps"][0] == 1 and saved["gaps"][-1] == 30
    assert owner.put("/api/publications/settings", json={"autopost_gap_min": 45}).status_code == 422
    _, script = model
    script += [_tool_answer("Первый", []), _tool_answer("Второй", [])]
    run(client, partial(publications.after_topics, project, topic_ids))
    first, second = run(client, partial(_posts, ids["project"]))
    assert first.scheduled_by == second.scheduled_by == publications.AUTOPOST_NAME and first.scheduled_partial
    assert abs((first.scheduled_at - utcnow()) - timedelta(hours=2)) < timedelta(minutes=1)
    assert second.scheduled_at - first.scheduled_at == timedelta(minutes=5)  # the gap set: not both at once
    scheduled = owner.get("/api/publications", params={"filter": "scheduled"}).json()["items"]
    assert [p["text"] for p in scheduled] == ["Первый", "Второй"]  # the soonest first
    assert scheduled[0]["scheduled_by"] == "автопостинг"

    # topic posts turned off: so is autoposting, and what it queued does not go out — suggestions again
    off = owner.put("/api/publications/settings", json={"topic_posts": False}).json()
    assert off["autopost"] == "off"
    assert owner.get("/api/publications", params={"filter": "scheduled"}).json()["items"] == []
    assert len(owner.get("/api/publications", params={"filter": "suggested"}).json()["items"]) == 2


def test_everyone_sees_what_went_out_only_those_allowed_edit_and_publish(ctx, model, bot, channel_bot):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9953)
    ids = run(client, _setup, 9953)
    run(client, _bot_token)
    run(client, partial(_project_channel, ids["project"]))
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "Пост владельца", "source_post_ids": []}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]

    made = owner.post("/api/members", json={"login": "pubs.staff"}).json()
    staff, _ = member_login(app, "pubs.staff", made["password"])
    # a draft of the owner's chat: nobody else sees it
    assert staff.get("/api/publications").json()["items"] == []
    assert staff.get(f"/api/chat-posts/{post_id}/sources").status_code == 404

    assert owner.post(f"/api/chat-posts/{post_id}/publish").json()["published"]
    shown = staff.get("/api/publications").json()["items"]
    assert [p["id"] for p in shown] == [post_id] and shown[0]["published_by"] == "владелец"
    assert shown[0]["editable"] is False and staff.get("/api/publications").json()["settings"] is None
    # without the right: looking only
    assert staff.patch(f"/api/chat-posts/{post_id}", json={"text": "Моё"}).status_code == 403
    assert staff.post(f"/api/chat-posts/{post_id}/publish").status_code == 403
    assert staff.get(f"/api/chat-posts/{post_id}/sources").status_code == 200

    owner.patch(f"/api/members/{made['member']['id']}", json={"ai_publish": True})
    assert staff.get("/api/publications").json()["items"][0]["editable"] is True
    assert staff.patch(f"/api/chat-posts/{post_id}", json={"text": "Поправил сотрудник"}).status_code == 200
    assert staff.post(f"/api/chat-posts/{post_id}/publish").json()["published"]
    assert owner.get("/api/publications").json()["items"][0]["published_by"] == "pubs.staff"

    # the chat deleted: what went out stays
    assert owner.delete(f"/api/chats/{chat_id}").json() == {"ok": True}
    kept = owner.get("/api/publications").json()["items"]
    assert [p["id"] for p in kept] == [post_id] and kept[0]["text"] == "Поправил сотрудник"


def test_a_post_written_as_plain_text_counts_too(ctx, model):
    """Free models often skip the tool and write the post itself: a long enough answer is the post."""
    _, script = model
    app, client = ctx
    owner = sign_in(app, client, 9954)
    ids = run(client, _setup, 9954)
    project = run(client, partial(_project, ids["project"]))
    topic_ids = run(client, partial(_topics, ids["project"], ids["other"], 1))
    owner.put("/api/publications/settings", json={"topic_posts": True})
    script.append("**Ракета стартовала.** " + "Подробности запуска и что это значит для отрасли. " * 5)
    assert len(run(client, partial(publications.after_topics, project, topic_ids))) == 1
    post = owner.get("/api/publications").json()["items"][0]
    assert post["text"].startswith("<b>Ракета стартовала.</b>") and post["topic"]["title"] == "Тема 0"
    # a short answer with no call is no post
    script.append("Не могу")
    assert run(client, partial(publications.after_topics, project, topic_ids)) == []


def test_a_failed_scheduled_post_tells_whoever_scheduled_it(ctx, model, bot, channel_bot):
    from datetime import datetime, timezone

    from app import chat_posts
    from app.models import Member

    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9955)
    ids = run(client, _setup, 9955)
    run(client, _bot_token)
    run(client, partial(_project_channel, ids["project"]))
    chat_id = owner.post("/api/chats").json()["id"]
    script.append(("suggest_post", {"text": "Пост владельца", "source_post_ids": []}))
    talk(owner, chat_id, "Напиши пост")
    post_id = owner.get(f"/api/chats/{chat_id}").json()["messages"][-1]["post"]["id"]
    owner.post(f"/api/chat-posts/{post_id}/publish")  # in «Публикации» now

    made = owner.post("/api/members", json={"login": "sched.staff", "ai_publish": True}).json()
    staff, _ = member_login(app, "sched.staff", made["password"])

    async def link_staff():
        async with SessionLocal() as s:
            (await s.get(Member, made["member"]["id"])).tg_user_id = 99550
            await s.commit()

    run(client, link_staff)
    at = datetime.now(timezone.utc) + timedelta(hours=1)
    assert staff.put(f"/api/chat-posts/{post_id}/schedule", json={"at": at.isoformat()}).json()["scheduled"]

    async def due():
        async with SessionLocal() as s:
            (await s.get(ChatPost, post_id)).scheduled_at = utcnow() - timedelta(minutes=1)
            await s.commit()

    run(client, due)
    channel_bot["can_post_messages"] = False
    bot.clear()
    run(client, chat_posts.publish_due)
    told = [c for c in bot if c[0] == "sendMessage"]
    # the staff member who scheduled it hears of it, not the chat's author; and is told to ask the owner
    assert len(told) == 1 and told[0][1]["chat_id"] == 99550
    assert "Попросите об этом владельца проекта" in told[0][1]["text"]


async def _post_state(post_id: int, **values) -> None:
    async with SessionLocal() as s:
        post = await s.get(ChatPost, post_id)
        for k, v in values.items():
            setattr(post, k, v)
        await s.commit()


async def _row(post_id: int) -> ChatPost:
    async with SessionLocal() as s:
        return await s.get(ChatPost, post_id)


def test_big_news_in_a_topic_rewrites_its_post_or_follows_up_the_one_out(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9956)
    ids = run(client, _setup, 9956)
    project = run(client, partial(_project, ids["project"]))
    t0, t1, t2, t3 = run(client, partial(_topics, ids["project"], ids["other"], 4))
    owner.put("/api/publications/settings", json={"topic_posts": True, "posts_limit": 3})
    script += [_tool_answer(f"Пост {i}", []) for i in range(3)]
    made = run(client, partial(publications.after_topics, project, [t0, t1, t2]))
    assert len(made) == 3
    p0, p1, p2 = made
    script.clear()
    # p0: nobody touched it — written anew; p1: a person edited it — stays; p2: out already — a follow-up
    run(client, partial(_post_state, p1, text="Поправил человек", edited_at=utcnow()))
    run(client, partial(_post_state, p2, published_at=utcnow(), published_url="https://t.me/x/1",
                        published_by="владелец"))
    sent.clear()
    script += [_tool_answer("Продолжение по теме 2", []), _tool_answer("Пост 0, с новыми фактами", [])]
    written = run(client, partial(publications.after_topics, project, [], [t0, t1, t2]))
    assert len(written) == 1 and len(sent) == 2  # one follow-up; one rewrite; the edited one not asked about
    followup = json.loads(sent[0]["messages"][1]["content"])
    assert followup["already_published"] == "Пост 2" and "follow-up" in sent[0]["messages"][0]["content"]
    assert run(client, partial(_row, p0)).text == "Пост 0, с новыми фактами"
    assert run(client, partial(_row, p1)).text == "Поправил человек"
    new_post = run(client, partial(_row, written[0]))
    assert new_post.text == "Продолжение по теме 2" and new_post.topic_id == t2 and new_post.published_at is None

    # one scheduled by a person stays as they saw it
    run(client, partial(_post_state, p0, scheduled_at=utcnow() + timedelta(hours=1), scheduled_by="владелец"))
    sent.clear()
    assert run(client, partial(publications.after_topics, project, [], [t0])) == [] and sent == []

    # the new posts — new topics and follow-ups together — keep to the limit, the most important first: the
    # follow-up of topic 2 (out too now) over the new topic 3
    run(client, partial(_post_state, written[0], published_at=utcnow(), published_by="владелец"))
    owner.put("/api/publications/settings", json={"posts_limit": 1})
    script += [_tool_answer("Только один", [])]
    sent.clear()
    only = run(client, partial(publications.after_topics, project, [t3], [t2]))
    assert len(only) == 1 and len(sent) == 1 and run(client, partial(_row, only[0])).topic_id == t2


def test_what_a_removed_staff_member_published_stays(ctx, model, bot, channel_bot):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9957)
    ids = run(client, _setup, 9957)
    run(client, _bot_token)
    run(client, partial(_project_channel, ids["project"]))
    made = owner.post("/api/members", json={"login": "gone.staff", "ai_chat": True, "ai_publish": True}).json()
    staff, _ = member_login(app, "gone.staff", made["password"])
    chat_id = staff.post("/api/chats").json()["id"]
    script += [("suggest_post", {"text": "Пост сотрудника", "source_post_ids": []}),
               ("suggest_post", {"text": "Черновик сотрудника", "source_post_ids": []})]
    talk(staff, chat_id, "Напиши пост")
    talk(staff, chat_id, "И ещё один")
    posts = [m["post"]["id"] for m in staff.get(f"/api/chats/{chat_id}").json()["messages"] if m["role"] == "post"]
    assert staff.post(f"/api/chat-posts/{posts[0]}/publish").json()["published"]

    assert owner.delete(f"/api/members/{made['member']['id']}").json() == {"ok": True}
    kept = owner.get("/api/publications").json()["items"]
    assert [p["id"] for p in kept] == [posts[0]] and kept[0]["published_by"] == "gone.staff"
    assert run(client, partial(_row, posts[1])) is None  # a draft goes with its chat


def _overloaded() -> httpx.Response:
    """The free model's provider down, as OpenRouter says it: an error in an answer of HTTP 200."""
    return httpx.Response(200, json={"error": {"message": "Provider returned error", "code": 502,
                                               "metadata": {"raw": "upstream overloaded", "provider_name": "Nvidia"}}})


async def _retries(project_id: int) -> dict[int, int]:
    async with SessionLocal() as s:
        rows = (await s.execute(select(TopicPostRetry).where(TopicPostRetry.project_id == project_id))).scalars()
        return {r.topic_id: r.runs for r in rows}


def test_a_post_the_model_failed_is_asked_again_then_waits_for_the_next_runs(ctx, model, monkeypatch, caplog):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9959)
    ids = run(client, _setup, 9959)
    project = run(client, partial(_project, ids["project"]))
    topic_ids = run(client, partial(_topics, ids["project"], ids["other"], 3))
    owner.put("/api/publications/settings", json={"topic_posts": True, "posts_limit": 3})
    waits: list[float] = []

    async def no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(publications.asyncio, "sleep", no_wait)

    # the first fails once and comes out when asked again; the second fails twice: the provider is down, so the
    # third is not asked at all — both wait for the next run
    script += [_overloaded(), _tool_answer("Тема 0", []), _overloaded(), _overloaded()]
    made = run(client, partial(publications.after_topics, project, topic_ids))
    assert len(made) == 1 and len(sent) == 4 and waits == [publications.RETRY_WAIT, publications.RETRY_WAIT]
    assert run(client, partial(_retries, ids["project"])) == {topic_ids[1]: 0, topic_ids[2]: 0}
    # the log keeps what the provider said, not only the short words for people
    assert "upstream overloaded" in caplog.text and "Nvidia" in caplog.text

    # the next run, with nothing new of its own: both are written now
    script += [_tool_answer("Тема 1", []), _tool_answer("Тема 2", [])]
    made = run(client, partial(publications.after_topics, project, []))
    assert len(made) == 2 and run(client, partial(_retries, ids["project"])) == {}
    texts = sorted(p.text for p in run(client, partial(_posts, ids["project"])))
    assert texts == ["Тема 0", "Тема 1", "Тема 2"]


def test_a_failed_post_is_given_up_after_its_runs(ctx, model, monkeypatch):
    _, script = model
    app, client = ctx
    owner = sign_in(app, client, 9961)
    ids = run(client, _setup, 9961)
    project = run(client, partial(_project, ids["project"]))
    [topic_id] = run(client, partial(_topics, ids["project"], ids["other"], 1))
    owner.put("/api/publications/settings", json={"topic_posts": True})

    async def no_wait(seconds):
        pass

    monkeypatch.setattr(publications.asyncio, "sleep", no_wait)
    script += [_overloaded()] * (2 * (publications.RETRY_RUNS + 1))
    run(client, partial(publications.after_topics, project, [topic_id]))
    for _ in range(publications.RETRY_RUNS):  # tried in each of the next runs
        run(client, partial(publications.after_topics, project, []))
    assert script == [] and run(client, partial(_retries, ids["project"])) == {topic_id: publications.RETRY_RUNS}
    # the run after: let go, not asked — news that old is no use
    assert run(client, partial(publications.after_topics, project, [])) == []
    assert run(client, partial(_retries, ids["project"])) == {}


def test_a_provider_asking_to_wait_is_not_asked_again_at_once(ctx, model, monkeypatch):
    # HTTP 200 with the provider's 429 inside: OpenRouter asks to wait — no second try in this run, the next run
    _, script = model
    app, client = ctx
    owner = sign_in(app, client, 9962)
    ids = run(client, _setup, 9962)
    project = run(client, partial(_project, ids["project"]))
    [topic_id] = run(client, partial(_topics, ids["project"], ids["other"], 1))
    owner.put("/api/publications/settings", json={"topic_posts": True})
    waits: list[float] = []

    async def no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(publications.asyncio, "sleep", no_wait)
    script.append(httpx.Response(200, json={"error": {"message": "Provider returned error", "code": 429,
                                                      "metadata": {"raw": "temporarily rate-limited upstream"}}}))
    assert run(client, partial(publications.after_topics, project, [topic_id])) == []
    assert waits == [] and script == [] and topic_id in run(client, partial(_retries, ids["project"]))


async def _topic_state(topic_id: int, **values) -> None:
    async with SessionLocal() as s:
        topic = await s.get(Topic, topic_id)
        for k, v in values.items():
            setattr(topic, k, v)
        await s.commit()


async def _second_source(topic_id: int, tg_id: int) -> None:
    """One more channel tells the topic."""
    async with SessionLocal() as s:
        ch = Channel(link=f"@pub_2_{tg_id}", username=f"pub_2_{tg_id}", title="Ещё канал", status="active")
        s.add(ch)
        await s.flush()
        post = Post(channel_id=ch.id, msg_id=1, posted_at=utcnow(), text="Тоже об этом")
        s.add(post)
        await s.flush()
        s.add(TopicPost(topic_id=topic_id, post_id=post.id))
        await s.commit()


def test_least_importance_for_topic_posts(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9963)
    ids = run(client, _setup, 9963)
    project = run(client, partial(_project, ids["project"]))
    topic_ids = run(client, partial(_topics, ids["project"], ids["other"], 4))  # importance 10, 9, 8, 7
    assert owner.put("/api/publications/settings", json={"min_importance_posts": 11}).status_code == 422
    saved = owner.put("/api/publications/settings", json={"topic_posts": True, "min_importance_posts": 9}).json()
    assert saved["min_importance_posts"] == 9 and saved["daily_limits"][0] == 0
    script += [_tool_answer("Тема 0", []), _tool_answer("Тема 1", [])]
    assert len(run(client, partial(publications.after_topics, project, topic_ids))) == 2 and len(sent) == 2
    assert sorted(p.text for p in run(client, partial(_posts, ids["project"]))) == ["Тема 0", "Тема 1"]


def test_autoposting_keeps_to_the_owner_s_limits(ctx, model):
    _, script = model
    app, client = ctx
    owner = sign_in(app, client, 9964)
    ids = run(client, _setup, 9964)
    project = run(client, partial(_project, ids["project"]))
    t0, t1, t2, t3, t4 = run(client, partial(_topics, ids["project"], ids["other"], 5))  # importance 10 … 6
    owner.put("/api/publications/settings", json={
        "topic_posts": True, "posts_limit": 5, "autopost": "now", "autopost_gap_min": 1,
        "daily_limit": 3, "autopost_min_sources": 2})
    # topics 0, 2, 3: two channels told them, topics 1 and 4 only one
    for t in (t0, t2, t3):
        run(client, partial(_second_source, t, 9964 * 10 + t))
    script += [_tool_answer(f"Пост {i}", []) for i in range(5)]
    made = run(client, partial(publications.after_topics, project, [t0, t1, t2, t3, t4]))
    rows = {p.topic_id: p for p in run(client, partial(_posts, ids["project"]))}
    assert len(made) == 5
    assert rows[t0].scheduled_by == rows[t2].scheduled_by == rows[t3].scheduled_by == publications.AUTOPOST_NAME
    assert rows[t1].scheduled_at is None and "написали 1 из 2" in rows[t1].autopost_note
    assert rows[t4].scheduled_at is None and "написали 1 из 2" in rows[t4].autopost_note
    assert "min_importance_autopost" not in owner.get("/api/publications").json()["settings"]

    # the cards say why
    cards = {c["topic"]["id"]: c for c in owner.get("/api/publications", params={"filter": "suggested"}).json()["items"]}
    assert cards[t1]["autopost_note"].startswith("Не опубликован автоматически")
    assert set(cards[t1]["topic"]) == {"id", "title", "importance"}  # no marks of how sure a topic is

    # the day's limit: three a day — a fourth stays a suggestion
    owner.put("/api/publications/settings", json={"autopost_min_sources": 1})
    [t5] = run(client, partial(_topics, ids["project"], ids["other"], 1))
    script += [_tool_answer("Четвёртый", [])]
    run(client, partial(publications.after_topics, project, [t5]))
    fourth = [p for p in run(client, partial(_posts, ids["project"])) if p.topic_id == t5][0]
    assert fourth.scheduled_at is None and "это лимит" in fourth.autopost_note

    # one only one channel told: kept back when two are asked for
    owner.put("/api/publications/settings", json={"daily_limit": 0, "autopost_min_sources": 2})
    [t6] = run(client, partial(_topics, ids["project"], ids["other"], 1))
    script += [_tool_answer("Один источник", [])]
    run(client, partial(publications.after_topics, project, [t6]))
    lone = [p for p in run(client, partial(_posts, ids["project"])) if p.topic_id == t6][0]
    assert lone.scheduled_at is None and "1 из 2" in lone.autopost_note


def test_the_scale_of_importance_is_shown_on_the_project_s_own_topics(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9965)
    ids = run(client, _setup, 9965)
    run(client, partial(_topics, ids["project"], ids["other"], 8))  # importance 10 … 3
    bands = owner.get("/api/publications/importance").json()["bands"]
    assert [(b["from"], b["to"]) for b in bands] == [(1, 3), (4, 6), (7, 8), (9, 10)]
    assert [e["importance"] for e in bands[3]["examples"]] == [10, 9]
    assert [e["importance"] for e in bands[0]["examples"]] == [3]


def test_the_calendar_shows_what_goes_out_by_the_clock(ctx, model):
    _, script = model
    app, client = ctx
    owner = sign_in(app, client, 9966)
    ids = run(client, _setup, 9966)
    project = run(client, partial(_project, ids["project"]))
    t0, t1, t2 = run(client, partial(_topics, ids["project"], ids["other"], 3))
    owner.put("/api/publications/settings", json={"topic_posts": True})
    script += [_tool_answer("Первый\nвторая строка", []), _tool_answer("Второй", []), _tool_answer("Третий", [])]
    run(client, partial(publications.after_topics, project, [t0, t1, t2]))
    a, b, c = run(client, partial(_posts, ids["project"]))
    day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=2)
    run(client, partial(_post_state, a.id, scheduled_at=day + timedelta(hours=15), scheduled_by="владелец"))
    run(client, partial(_post_state, b.id, published_at=day - timedelta(days=3), published_by="владелец"))
    run(client, partial(_post_state, c.id, publish_error="бот не в канале", publish_error_at=day + timedelta(hours=9)))

    month = owner.get("/api/publications/calendar", params={"start": (day - timedelta(days=10)).isoformat() + "Z",
                                                            "end": (day + timedelta(days=10)).isoformat() + "Z"}).json()
    assert [(i["id"], i["state"]) for i in month["items"]] == [(b.id, "published"), (c.id, "failed"),
                                                              (a.id, "scheduled")]
    assert month["items"][2]["title"] == "Первый"  # its first line
    # one day: the cards, by the clock
    one = owner.get("/api/publications", params={"start": day.isoformat() + "Z",
                                                 "end": (day + timedelta(days=1)).isoformat() + "Z"}).json()
    assert [p["id"] for p in one["items"]] == [c.id, a.id]
    too_long = owner.get("/api/publications/calendar", params={"start": day.isoformat() + "Z",
                                                               "end": (day + timedelta(days=90)).isoformat() + "Z"})
    assert too_long.status_code == 422
