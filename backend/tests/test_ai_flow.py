"""How the AI's background work goes together: projects side by side, a rest after failures, OpenRouter's "wait"
and time limit, channels' histories left out, «Собирать темы» from now on, one run's answer never written twice."""

import asyncio
import json
from datetime import timedelta
from functools import partial

import httpx
import pytest
from sqlalchemy import func, select

from app import ai, ai_runs, covered, smart_feed, topics
from app.db import SessionLocal
from app.lanes import Lanes
from app.models import Post, Project, Topic, TopicPost, TopicState, utcnow

from test_topics import _own_channel, _setup
from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def go(client, fn, *args, **kwargs):
    """run() with keyword arguments too."""
    return run(client, partial(fn, **kwargs), *args)


@pytest.fixture
def model(monkeypatch):
    """A fake model that answers each kind of request emptily, and keeps what it was sent; `fail` makes it
    answer with an error, `during` runs before it answers."""
    sent: list[dict] = []
    state = {"fail": False, "during": None, "tells": []}

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        content = body["messages"][-1]["content"]
        payload = json.loads(content if isinstance(content, str) else content[0]["text"])
        sent.append(payload)
        if state["during"]:
            await state["during"](payload)
        if state["fail"]:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        if "my_channel" in payload and "hidden" in payload:  # topics
            answer = {"new_topics": [{"title": "Тема", "summary": "Что-то случилось.", "update": "Началось",
                                      "post_ids": [p["id"] for p in payload["posts"]], "covered": False,
                                      "emoji": "🚀", "importance": 5, "old_news": False}],
                      "updates": [], "attach": []}
        elif "corrections" in payload:  # the smart feed
            answer = {"results": [{"id": p["id"], "match": True, "score": 90, "reason": "ок"}
                                  for p in payload["posts"]]}
        else:  # «Есть в вашем канале»
            answer = {"results": [{"post_id": p["id"], "topic_ids": state["tells"]} for p in payload["posts"]]}
        return httpx.Response(200, json={"id": "g", "model": body["model"],
                                         "choices": [{"message": {"content": json.dumps(answer)}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0}})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    return sent, state


async def _post(channel_id: int, msg_id: int, text: str, *, came=timedelta(minutes=5), history=False) -> int:
    async with SessionLocal() as s:
        p = Post(channel_id=channel_id, msg_id=msg_id, posted_at=utcnow() - came, text=text,
                 created_at=utcnow() - came, history=history)
        s.add(p)
        await s.commit()
        return p.id


async def _project(project_id: int) -> Project:
    async with SessionLocal() as s:
        return await s.get(Project, project_id)


async def _state(project_id: int, **changes) -> TopicState:
    async with SessionLocal() as s:
        state = await s.get(TopicState, project_id)
        for k, v in changes.items():
            setattr(state, k, v)
        await s.commit()
        return state


def _project_with_ai(app, client, tg_id: int, link: str):
    owner = sign_in(app, client, tg_id)
    assert owner.post("/api/channels", json={"links": link}).json()["added"]
    run(client, _activate, link, tg_id * 10 + 1, 0)
    project_id, channel_id, account = run(client, _setup, tg_id, link)
    return owner, project_id, channel_id, account


# --- projects side by side -------------------------------------------------------------------------


async def test_a_slow_project_does_not_hold_the_others():
    lanes, gate, done = Lanes("test"), asyncio.Event(), []

    async def slow():
        await gate.wait()
        done.append("slow")

    async def fast():
        done.append("fast")

    async def broken():
        raise RuntimeError("boom")

    await lanes.run({1: slow, 2: fast, 3: broken}, wait=False)
    await asyncio.sleep(0.01)
    assert done == ["fast"] and lanes.busy(1) and not lanes.busy(3)  # a failure is logged, the lane is free
    await lanes.run({1: slow, 2: fast}, wait=False)  # the slow one still goes: not started twice
    await asyncio.sleep(0.01)
    assert done == ["fast", "fast"]
    gate.set()
    await asyncio.sleep(0.01)
    assert done.count("slow") == 1 and not lanes.busy(1)


# --- a rest after failures -------------------------------------------------------------------------


def test_a_failing_schedule_rests_longer_and_longer():
    now = utcnow()

    def ended(ago: timedelta, fails: int, error=True) -> dict:
        return {"started": "x", "finished": (now - ago).isoformat(timespec="seconds"), "fails": fails,
                "error": {"text": "boom"} if error else None}

    assert ai_runs.resting(ended(timedelta(seconds=30), 1), now)
    assert not ai_runs.resting(ended(timedelta(minutes=2), 1), now)
    assert ai_runs.resting(ended(timedelta(minutes=3), 3), now)  # 4 minutes after the third failure
    assert not ai_runs.resting(ended(timedelta(minutes=5), 3), now)
    assert ai_runs.resting(ended(timedelta(minutes=29), 20), now)  # half an hour at most
    assert not ai_runs.resting(ended(timedelta(minutes=31), 20), now)
    assert not ai_runs.resting(ended(timedelta(seconds=5), 0, error=False), now)
    assert not ai_runs.resting(None, now)


def test_a_failed_topics_run_is_not_tried_again_every_minute(ctx, model):
    sent, behaviour = model
    app, client = ctx
    _, project_id, channel_id, account = _project_with_ai(app, client, 9851, "@uc_flow_rest")
    run(client, _post, channel_id, 1, "news")
    project = run(client, _project, project_id)
    behaviour["fail"] = True

    def scheduled():
        go(client, _state, project_id, last_run_at=utcnow() - timedelta(hours=2))  # its time has come
        run(client, topics.run_project, project, account)

    scheduled()
    assert len(sent) == 1
    scheduled()  # a minute later: it rests
    assert len(sent) == 1
    state = run(client, _state, project_id)
    assert state.run["fails"] == 1
    finished = (utcnow() - timedelta(minutes=2)).isoformat(timespec="seconds")
    go(client, _state, project_id, run={**state.run, "finished": finished})
    scheduled()  # the rest is over: tried again, and fails a second time — it rests longer now
    assert len(sent) == 2 and run(client, _state, project_id).run["fails"] == 2

    behaviour["fail"] = False
    go(client, _state, project_id, run={**go(client, _state, project_id).run, "finished": "2000-01-01T00:00:00"})
    scheduled()
    assert len(sent) == 3 and run(client, _state, project_id).run["fails"] == 0  # it went: the count starts anew


# --- OpenRouter's "wait" and the time limit ----------------------------------------------------------


def test_openrouter_s_wait_and_the_time_limit(ctx, monkeypatch):
    app, client = ctx
    _, _, _, account = _project_with_ai(app, client, 9852, "@uc_flow_limit")
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "slow/model":
            await asyncio.sleep(1)
        return httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))

    async def ask(model: str):
        try:
            await ai.complete(user_id=account.user_id, operation="topics", model=model,
                              messages=[{"role": "user", "content": "hi"}])
        except ai.AiError as exc:
            return exc

    first = run(client, ask, "some/model")
    assert isinstance(first, ai.AiRateLimited) and calls == ["some/model"]
    again = run(client, ask, "some/model")  # asked to wait: not asked again for a minute
    assert isinstance(again, ai.AiRateLimited) and calls == ["some/model"]
    run(client, ask, "other/model")  # another model is free to go
    assert calls == ["some/model", "other/model"]

    # a model that keeps the request open is given up on after the limit
    monkeypatch.setattr(ai, "REQUEST_LIMIT", 0.1)
    slow = run(client, ask, "slow/model")
    assert type(slow) is ai.AiError and "не ответила" in str(slow)


# --- channels' histories --------------------------------------------------------------------------


def test_a_channel_s_history_is_not_news_for_the_ai(ctx, model):
    sent, _ = model
    app, client = ctx
    owner, project_id, channel_id, account = _project_with_ai(app, client, 9853, "@uc_flow_history")
    own = run(client, _own_channel, 9853)
    run(client, covered.covered_step)  # «Есть в вашем канале» begins from now
    assert owner.put("/api/smart", json={"enabled": True}).status_code == 200
    go(client, _post, channel_id, 1, "a week-old post brought with the channel", history=True)
    fresh = run(client, _post, channel_id, 2, "fresh news")
    go(client, _post, own, 3, "our post from last month", history=True)
    run(client, smart_feed.smart_feed_step)
    run(client, covered.covered_step)
    run(client, topics.run_project, run(client, _project, project_id), account, True)

    feed = [p for p in sent if "corrections" in p]
    assert [[x["id"] for x in p["posts"]] for p in feed] == [[fresh]]
    assert not [p for p in sent if "topics" in p and "hidden" not in p]  # the channel's old post: not checked
    [topics_request] = [p for p in sent if "hidden" in p]
    assert [x["id"] for x in topics_request["posts"]] == [fresh]


# --- «Собирать темы» from now on --------------------------------------------------------------------


def test_topics_begin_from_now_not_from_when_the_state_was_made(ctx, model):
    sent, _ = model
    app, client = ctx
    owner, project_id, channel_id, account = _project_with_ai(app, client, 9854, "@uc_flow_from_now")
    old = go(client, _post, channel_id, 1, "came a week ago", came=timedelta(days=7))
    recent = go(client, _post, channel_id, 2, "came 20 minutes ago", came=timedelta(minutes=20))
    # made when the owner linked OpenRouter, and never run: it would read everything since then
    go(client, _state, project_id, cursor_post_id=0, last_run_at=None, enabled=False)

    assert owner.put("/api/topics/settings", json={"enabled": True}).status_code == 200
    assert run(client, _state, project_id).cursor_post_id >= old  # the last hour only
    go(client, _state, project_id, cursor_post_id=0)  # and if it was on already: the first run does the same
    run(client, topics.run_project, run(client, _project, project_id), account, True)
    [request] = [p for p in sent if "hidden" in p]
    assert [x["id"] for x in request["posts"]] == [recent]

    # off and on again later: from then on, never back over what was read
    go(client, _state, project_id, enabled=False)
    late = go(client, _post, channel_id, 3, "came while off", came=timedelta(hours=5))
    assert owner.put("/api/topics/settings", json={"enabled": True}).status_code == 200
    assert run(client, _state, project_id).cursor_post_id >= late


# --- one run's answer is written once ---------------------------------------------------------------


def test_an_answer_to_posts_read_past_meanwhile_is_dropped(ctx, model):
    sent, behaviour = model
    app, client = ctx
    _, project_id, channel_id, account = _project_with_ai(app, client, 9855, "@uc_flow_twice")
    run(client, _post, channel_id, 1, "news")

    async def moved(_payload):
        # while the model thinks, the posts are read past («Собирать темы» off and on, or another run)
        async with SessionLocal() as s:
            (await s.get(TopicState, project_id)).cursor_post_id += 1000
            await s.commit()

    behaviour["during"] = moved
    run(client, topics.run_project, run(client, _project, project_id), account, True)
    assert len(sent) == 1

    async def written():
        async with SessionLocal() as s:
            return (await s.scalar(select(func.count()).select_from(Topic).where(Topic.project_id == project_id)),
                    await s.scalar(select(func.count()).select_from(TopicPost).join(Topic).where(
                        Topic.project_id == project_id)))

    assert run(client, written) == (0, 0)


# --- «Есть в вашем канале» finds older topics by their words ------------------------------------------


def test_the_check_finds_a_topic_of_days_ago_by_its_words(ctx, model):
    sent, _ = model
    app, client = ctx
    _, project_id, channel_id, account = _project_with_ai(app, client, 9856, "@uc_flow_words")
    own = run(client, _own_channel, 9856)

    async def make_topics():
        async with SessionLocal() as s:
            for i in range(30):  # a busy day: more fresh topics than are sent whatever their words
                s.add(Topic(project_id=project_id, title=f"Новость номер {i} про погоду",
                            summary="Синоптики обещают дожди."))
            s.add(Topic(project_id=project_id, title="Ракета Ангара стартовала с Плесецка",
                        summary="Тяжёлая ракета Ангара вывела спутник на орбиту.",
                        updated_at=utcnow() - timedelta(days=3)))
            await s.commit()

    run(client, make_topics)
    run(client, covered.covered_step)  # begins from now
    run(client, _post, own, 1, "Как прошёл запуск ракеты Ангара с космодрома Плесецк")
    run(client, covered.covered_step)
    [check] = [p for p in sent if "hidden" not in p]
    titles = [t["title"] for t in check["topics"]]
    assert "Ракета Ангара стартовала с Плесецка" in titles
    assert len(titles) <= covered.FRESHEST + covered.MATCHED


# --- the smart feed: batches, pictures, histories -----------------------------------------------------


def test_the_smart_feed_collects_posts_into_one_request(ctx, model, monkeypatch):
    sent, _ = model
    monkeypatch.setattr(smart_feed, "COLLECT", timedelta(minutes=5))
    app, client = ctx
    owner, project_id, channel_id, account = _project_with_ai(app, client, 9857, "@uc_flow_collect")
    assert owner.put("/api/smart", json={"enabled": True}).status_code == 200

    def feed_requests():
        return [[x["text"] for x in p["posts"]] for p in sent if "corrections" in p]

    go(client, _post, channel_id, 1, "one", came=timedelta(minutes=1))
    go(client, _post, channel_id, 2, "two", came=timedelta(seconds=30))
    run(client, smart_feed.smart_feed_step)
    assert feed_requests() == []  # two posts only: they wait for more

    async def older():
        async with SessionLocal() as s:
            for p in (await s.execute(select(Post).where(Post.channel_id == channel_id))).scalars():
                p.created_at -= timedelta(minutes=5)
            await s.commit()

    run(client, older)
    run(client, smart_feed.smart_feed_step)
    assert feed_requests() == [["one", "two"]]  # the oldest has waited 5 minutes: they go together

    for i in range(smart_feed.BATCH):
        go(client, _post, channel_id, 10 + i, f"busy {i}", came=timedelta(seconds=10))
    run(client, smart_feed.smart_feed_step)
    assert len(feed_requests()) == 2 and len(feed_requests()[1]) == smart_feed.BATCH  # a full batch goes at once


def test_a_post_waits_for_the_text_on_its_pictures_and_histories_pass_unread(ctx, model):
    sent, _ = model
    app, client = ctx
    owner, project_id, channel_id, account = _project_with_ai(app, client, 9858, "@uc_flow_vision")
    assert owner.put("/api/smart", json={"enabled": True}).status_code == 200
    history = go(client, _post, channel_id, 1, "a week-old post", history=True)
    photo = run(client, _post, channel_id, 2, "photo post")

    async def pictures(read: bool):
        async with SessionLocal() as s:
            p = await s.get(Post, photo)
            p.ocr_status = "done" if read else "pending"  # pending: the text on its pictures is being read
            p.extracted_text_from_media = "text on the photo" if read else None
            p.created_at = utcnow()
            p.has_preview, p.previews = True, [{"k": "photo", "p": 0}]
            await s.commit()

    run(client, pictures, False)
    run(client, smart_feed.smart_feed_step)
    assert [p for p in sent if "corrections" in p] == []
    # the history before it was passed unread, and is shown
    shown = [p["id"] for p in owner.get("/api/posts", params={"smart": "true"}).json()["items"]]
    assert history in shown and photo not in shown

    run(client, pictures, True)  # the text on them is read: it goes to the AI with the post, the pictures do not
    run(client, smart_feed.smart_feed_step)
    feed = [p for p in sent if "corrections" in p]
    assert [[x["id"] for x in p["posts"]] for p in feed] == [[photo]]
    assert feed[0]["posts"][0]["ocr_text"] == "text on the photo"


# --- the AI goes on after the balance is topped up ------------------------------------------------


def test_the_ai_goes_on_once_the_balance_is_topped_up(ctx, monkeypatch):
    app, client = ctx
    _, _, _, account = _project_with_ai(app, client, 9859, "@uc_flow_topup")
    money = {"total": 5.0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/credits"):
            return httpx.Response(200, json={"data": {"total_credits": money["total"], "total_usage": 5.0}})
        return httpx.Response(200, json={"data": {"limit": None, "limit_remaining": None, "label": "Feedor"}})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))

    async def paused():
        async with SessionLocal() as s:
            return (await s.get(type(account), account.user_id)).paused_reason

    async def pause():
        async with SessionLocal() as s:
            (await s.get(type(account), account.user_id)).paused_reason = "no_credits"
            await s.commit()

    run(client, pause)
    run(client, ai.resume_paid)
    assert run(client, paused) == "no_credits"  # still nothing on the balance
    money["total"] = 15.0
    run(client, ai.resume_paid)
    assert run(client, paused) is None


# --- «Есть продолжение» the channel told already -------------------------------------------------------


def test_a_continuation_the_channel_told_first_is_found(ctx, model):
    sent, behaviour = model
    app, client = ctx
    _, project_id, channel_id, account = _project_with_ai(app, client, 9860, "@uc_flow_followup")
    own = run(client, _own_channel, 9860)

    async def told_topic():
        async with SessionLocal() as s:
            t = Topic(project_id=project_id, title="Запуск ракеты", summary="Ракету запустили.", covered=True,
                      covered_at=utcnow() - timedelta(hours=1))
            s.add(t)
            await s.commit()
            return t.id

    topic_id = run(client, told_topic)
    run(client, covered.covered_step)  # begins from now
    # the channel is first: it tells the continuation before the others write about it
    run(client, _post, own, 1, "Спутник отделился от ракеты и вышел на орбиту")
    run(client, covered.covered_step)
    assert [p for p in sent if "hidden" not in p] == []  # the topic has the mark: nothing to look for

    news = run(client, _post, channel_id, 2, "Спутник вышел на орбиту")

    async def big_news():
        async with SessionLocal() as s:
            await topics.apply(s, project_id, {"new_topics": [], "attach": [], "updates": [{
                "topic_id": topic_id, "title": "", "summary": "Ракету запустили, спутник на орбите.",
                "update": "Спутник отделился и вышел на орбиту", "post_ids": [news], "covered": False,
                "importance": 8, "novelty": 8}]}, {news: await s.get(Post, news)}, {topic_id: await s.get(Topic, topic_id)})
            await s.commit()

    async def marks():
        async with SessionLocal() as s:
            t = await s.get(Topic, topic_id)
            return t.covered, t.followup_at is not None

    run(client, big_news)
    assert run(client, marks) == (True, True)  # «Есть продолжение»
    behaviour["tells"] = [topic_id]
    run(client, covered.covered_step)  # the channel's post since the mark is read again, for the continuation
    [check] = [p for p in sent if "hidden" not in p]
    assert check["topics"][0]["continuation"] == "Спутник отделился и вышел на орбиту"
    assert run(client, marks) == (True, False)


# --- topics catch up -------------------------------------------------------------------------------------


def test_topics_that_could_not_read_everything_catch_up_at_once(ctx, model, monkeypatch):
    sent, _ = model
    monkeypatch.setattr(topics, "BATCH", 1)
    monkeypatch.setattr(topics, "RUN_BATCHES", 1)
    app, client = ctx
    _, project_id, channel_id, account = _project_with_ai(app, client, 9861, "@uc_flow_catch_up")
    for i in range(3):
        go(client, _post, channel_id, i + 1, f"news {i}", came=timedelta(minutes=10))
    project = run(client, _project, project_id)
    go(client, _state, project_id, last_run_at=utcnow() - timedelta(hours=2))

    def runs() -> list[list[str]]:
        return [[x["text"] for x in p["posts"]] for p in sent if "hidden" in p]

    run(client, topics.run_project, project, account)
    assert runs() == [["news 0"]]
    run(client, topics.run_project, project, account)  # a minute later, not an hour: the rest is waiting
    run(client, topics.run_project, project, account)
    assert runs() == [["news 0"], ["news 1"], ["news 2"]]
    run(client, topics.run_project, project, account)  # caught up: back to the schedule
    assert len(runs()) == 3


def test_posts_that_came_during_a_run_wait_for_the_schedule(ctx, model):
    sent, behaviour = model
    app, client = ctx
    _, project_id, channel_id, account = _project_with_ai(app, client, 9863, "@uc_flow_no_extra")
    run(client, _post, channel_id, 1, "news")
    project = run(client, _project, project_id)
    go(client, _state, project_id, last_run_at=utcnow() - timedelta(hours=2))

    async def came_meanwhile(_payload):
        # a free model thinks for minutes: posts keep coming meanwhile
        await _post(channel_id, 2, "came while the model was thinking", came=timedelta(minutes=5))

    behaviour["during"] = came_meanwhile
    run(client, topics.run_project, project, account)
    behaviour["during"] = None
    run(client, topics.run_project, project, account)  # a minute later: not a run that stopped short
    assert len([p for p in sent if "hidden" in p]) == 1


# --- the chat's answer has a time limit too ---------------------------------------------------------------


def test_a_chat_answer_that_never_comes_is_given_up(ctx, monkeypatch):
    app, client = ctx
    _, project_id, _, account = _project_with_ai(app, client, 9862, "@uc_flow_chat_limit")

    async def keep_alive():
        for _ in range(50):
            await asyncio.sleep(0.05)
            yield b": OPENROUTER PROCESSING\n\n"

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(lambda request: httpx.Response(200, content=keep_alive())))
    monkeypatch.setattr(ai, "REQUEST_LIMIT", 0.2)

    async def ask():
        try:
            async for _event in ai.stream(user_id=account.user_id, operation="chat", model="some/model",
                                          messages=[{"role": "user", "content": "hi"}]):
                pass
        except ai.AiError as exc:
            return exc

    error = run(client, ask)
    assert error is not None and "не ответила" in str(error)


# --- «Собирать темы» turned off during a run --------------------------------------------------------------


def test_a_run_stops_when_topics_are_turned_off(ctx, model, monkeypatch):
    sent, behaviour = model
    monkeypatch.setattr(topics, "BATCH", 1)
    app, client = ctx
    owner, project_id, channel_id, account = _project_with_ai(app, client, 9864, "@uc_flow_off")
    for i in range(3):
        go(client, _post, channel_id, i + 1, f"news {i}", came=timedelta(minutes=10))

    async def turned_off(_payload):
        async with SessionLocal() as s:
            (await s.get(TopicState, project_id)).enabled = False
            await s.commit()

    behaviour["during"] = turned_off
    run(client, topics.run_project, run(client, _project, project_id), account, True)
    # the answer that came after is not written, and no more requests go
    assert len([p for p in sent if "hidden" in p]) == 1

    async def made():
        async with SessionLocal() as s:
            return await s.scalar(select(func.count()).select_from(Topic).where(Topic.project_id == project_id))

    assert run(client, made) == 0

