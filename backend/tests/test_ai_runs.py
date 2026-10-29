import time
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import ai, ai_runs, topics
from app.db import SessionLocal
from app.models import AiAccount, Channel, Post, Project, TopicState, User, utcnow

from test_topics import model  # noqa: F401  (a fixture)
from test_user_channels import own_ai, _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def _iso(at):
    return at.isoformat(timespec="seconds")


def test_how_far_a_run_is_and_when_it_is_gone():
    now = utcnow()
    run_ = {"started": _iso(now - timedelta(seconds=40)), "beat": _iso(now - timedelta(seconds=5)),
            "done": 0.04, "share": 0.96, "asked": _iso(now - timedelta(seconds=30)), "expect": 60.0}
    assert ai_runs.going(run_, now)
    halfway = ai_runs.percent(run_, now)
    assert 40 < halfway < 50
    # past the usual time it slows down, and is never full before the answer
    late = {**run_, "asked": _iso(now - timedelta(seconds=600))}
    assert halfway < ai_runs.percent(late, now) == 99

    # a run that stopped beating (its process died) or has gone on far too long is not going: nothing stays
    # locked. One that beats goes on, slow as it is: a free model takes a quarter of an hour a request
    assert not ai_runs.going({**run_, "beat": _iso(now - timedelta(minutes=2))}, now)
    assert ai_runs.going({**run_, "started": _iso(now - timedelta(minutes=50))}, now)
    assert not ai_runs.going({**run_, "started": _iso(now - timedelta(hours=3))}, now)
    gone = ai_runs.shown({**run_, "beat": _iso(now - timedelta(minutes=2))})
    assert not gone["going"] and gone["error"]["text"].startswith("Запуск прервался")
    assert not ai_runs.going({**run_, "finished": _iso(now)}, now)
    assert ai_runs.shown(None) is None


def _owner(app, client, tg_id):
    """Signed in, its requests made in the app's own loop: a run started by one goes on after it, as
    it does on the server (a client of its own would end the loop, and the run, with each request)."""
    owner = sign_in(app, client, tg_id)
    owner.portal = client.portal
    return owner


def _wait(get, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        out = get()
        if out and not out["going"]:
            return out
        time.sleep(0.1)
    raise AssertionError("the run never ended")


async def _topics_setup(tg_id: int, link: str) -> tuple[Project, AiAccount]:
    """The owner's OpenRouter linked, topics on, a launch post waiting to be read."""
    async with SessionLocal() as s:
        user = await s.scalar(select(User).where(User.tg_user_id == tg_id))
        account = AiAccount(user_id=user.id, key_enc=ai.encrypt_key("sk-or-x"), connected_at=utcnow())
        await own_ai(s, user.id)
        s.add(account)
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        state = await topics.state_of(s, project.id)
        state.enabled, state.last_run_at = True, utcnow() - timedelta(days=1)
        channel = await s.scalar(select(Channel).where(Channel.link == link))
        s.add(Post(channel_id=channel.id, msg_id=7, posted_at=utcnow(), text="launch of the year", views=100,
                   created_at=utcnow() - timedelta(minutes=5)))
        await s.commit()
        await s.refresh(account)
        await s.refresh(project)
        return project, account


def _with_channel(app, client, tg_id: int):
    owner = _owner(app, client, tg_id)
    link = f"@uc_runs_{tg_id}"
    assert owner.post("/api/channels", json={"links": link}).json()["added"]
    run(client, _activate, link, tg_id * 10, 0)
    return owner, run(client, _topics_setup, tg_id, link)


def test_a_run_now_is_seen_going_then_its_result(ctx, model):  # noqa: F811
    app, client = ctx
    owner, _ = _with_channel(app, client, 9911)
    assert owner.get("/api/topics/settings").json()["run"] is None
    started = owner.post("/api/topics/run").json()
    assert started["going"] and 1 <= started["percent"] < 100
    ended = _wait(lambda: owner.get("/api/topics/run").json())
    assert ended["result"]["new"] == 1 and ended["error"] is None
    assert owner.get("/api/topics/settings").json()["run"]["going"] is False


def test_a_run_going_is_not_started_twice_and_a_dead_one_frees_the_button(ctx, model):  # noqa: F811
    app, client = ctx
    owner, (project, account) = _with_channel(app, client, 9912)

    async def claim():
        return await ai_runs.Run.claim(TopicState, project.id, "topics")

    async def scenario():
        first = await claim()
        assert first is not None and await claim() is None  # the schedule's run would wait too
        assert await topics.run_project(project, account, force=True) == []
        # its process died: no beat for a while
        async with SessionLocal() as s:
            state = await s.get(TopicState, project.id)
            state.run = {**state.run, "beat": _iso(utcnow() - timedelta(minutes=5))}
            await s.commit()
        first._beating.cancel()

    run(client, scenario)
    shown = owner.get("/api/topics/run").json()
    assert not shown["going"] and shown["error"]["text"].startswith("Запуск прервался")
    assert owner.post("/api/topics/run").json()["going"]  # free again
    assert _wait(lambda: owner.get("/api/topics/run").json())["result"]["new"] == 1


@pytest.fixture
def limited(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "Rate limit exceeded: free-models-per-day."}})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))


def test_a_failed_run_tells_why(ctx, limited):
    app, client = ctx
    owner, _ = _with_channel(app, client, 9913)
    assert owner.post("/api/topics/run").json()["going"]
    ended = _wait(lambda: owner.get("/api/topics/run").json())
    assert ended["result"]["new"] == 0
    assert "дневной лимит" in ended["error"]["text"] and "free-models-per-day" in ended["error"]["detail"]
