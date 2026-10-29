"""«Есть в вашем канале»: a new post of the project's channel marks the topics it tells — while topics are on."""

import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app import ai, covered, topics
from app.db import SessionLocal
from app.models import Post, Project, Topic, TopicState
from app.web import members as members_api

from test_members import member_login
from test_topics import _own_channel, _post, _setup
from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


@pytest.fixture
def model(monkeypatch):
    """A fake model: a post "about the launch" tells the launch topic; anything else tells none."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        payload = json.loads(body["messages"][-1]["content"])
        sent.append(payload)
        launch = [t["id"] for t in payload["topics"] if "Запуск" in t["title"]]
        results = [{"post_id": p["id"], "topic_ids": launch if "about the launch" in p["text"] else []}
                   for p in payload["posts"]]
        return httpx.Response(200, json={"id": "g", "model": body["model"],
                                         "choices": [{"message": {"content": json.dumps({"results": results})}}],
                                         "usage": {"prompt_tokens": 300, "completion_tokens": 20, "cost": 0.0002}})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    return sent


async def _topics(project_id: int) -> dict[str, int]:
    async with SessionLocal() as s:
        made = {
            "launch": Topic(project_id=project_id, title="Запуск ракеты", summary="Ракету запустили."),
            "rates": Topic(project_id=project_id, title="Ставка ЦБ", summary="ЦБ поднял ставку."),
            "unmarked": Topic(project_id=project_id, title="Запуск спутника", summary="Спутник на орбите.",
                              covered_dismissed=True),  # the person said «Это не так»
            "old": Topic(project_id=project_id, title="Запуск завода", summary="Завод открыт.",
                         updated_at=covered.utcnow() - timedelta(days=10)),
        }
        s.add_all(made.values())
        await s.commit()
        return {k: t.id for k, t in made.items()}


async def _marks(ids: dict[str, int]) -> dict[str, bool]:
    async with SessionLocal() as s:
        return {k: (await s.get(Topic, i)).covered for k, i in ids.items()}


async def _topics_on(project_id: int, on: bool) -> None:
    async with SessionLocal() as s:
        (await s.get(TopicState, project_id)).enabled = on
        await s.commit()


def test_a_post_of_the_channel_marks_the_topic_it_tells(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9811)
    assert owner.post("/api/channels", json={"links": "@uc_covered"}).json()["added"]
    run(client, _activate, "@uc_covered", 98111, 0)
    project_id, _, _ = run(client, _setup, 9811, "@uc_covered")
    own = run(client, _own_channel, 9811)
    ids = run(client, _topics, project_id)
    run(client, _post, own, 1, "about the launch, from before")  # before it began: never read

    run(client, covered.covered_step)  # begins from now
    assert model == []
    run(client, _post, own, 2, "Our weekly digest: the launch in one line, mostly the weather")
    run(client, _post, own, 3, "about the launch: how it went")
    run(client, covered.covered_step)

    # one request: the recent topics without the mark, not the unmarked one, not the old one
    assert len(model) == 1
    assert sorted(t["title"] for t in model[0]["topics"]) == ["Запуск ракеты", "Ставка ЦБ"]
    assert [p["text"][:12] for p in model[0]["posts"]] == ["Our weekly d", "about the la"]
    assert run(client, _marks, ids) == {"launch": True, "rates": False, "unmarked": False, "old": False}

    # read once; nothing new, nothing asked
    run(client, covered.covered_step)
    assert len(model) == 1

    # topics off: the channel's posts are not read; on again: from then on
    run(client, _topics_on, project_id, False)
    run(client, _post, own, 4, "about the launch again")
    run(client, covered.covered_step)
    assert len(model) == 1
    assert owner.put("/api/topics/settings", json={"enabled": True}).status_code == 200
    run(client, covered.covered_step)  # begins anew from now: post 4 stays unread
    run(client, covered.covered_step)
    assert len(model) == 1


def test_the_mark_is_put_by_hand_with_the_right_to_teach(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = sign_in(app, client, 9812)
    assert owner.post("/api/channels", json={"links": "@uc_covered_hand"}).json()["added"]
    run(client, _activate, "@uc_covered_hand", 98121, 0)
    project_id, _, _ = run(client, _setup, 9812, "@uc_covered_hand")
    ids = run(client, _topics, project_id)

    # the owner puts it on a topic the AI did not mark, even one unmarked before
    for key in ("rates", "unmarked"):
        assert owner.post(f"/api/topics/{ids[key]}/covered").json() == {"ok": True}
        topic = owner.get(f"/api/topics/{ids[key]}").json()
        assert topic["covered"] and topic["covered_by"] == "владелец"
    assert owner.post(f"/api/topics/{ids['rates']}/not-covered").json() == {"ok": True}
    assert owner.get(f"/api/topics/{ids['rates']}").json()["covered_by"] is None

    # a staff member: only with «Обучать ИИ проекта», the same as taking it off
    created = owner.post("/api/members", json={"login": "covered.staff"}).json()
    staff, _ = member_login(app, "covered.staff", created["password"])
    assert staff.post(f"/api/topics/{ids['launch']}/covered").status_code == 403
    owner.patch(f"/api/members/{created['member']['id']}", json={"ai_teach": True})
    assert staff.post(f"/api/topics/{ids['launch']}/covered").json() == {"ok": True}
    assert owner.get(f"/api/topics/{ids['launch']}").json()["covered_by"] == "covered.staff"
    # someone else's topic is not there
    assert sign_in(app, client, 9813).post(f"/api/topics/{ids['launch']}/covered").status_code == 404


def test_the_topics_and_the_check_take_turns(ctx, model):
    """A topics' run on the schedule waits until the channel's new post is checked; the check waits while a
    topics' run goes."""
    app, client = ctx
    owner = sign_in(app, client, 9813)
    assert owner.post("/api/channels", json={"links": "@uc_turns"}).json()["added"]
    run(client, _activate, "@uc_turns", 98131, 0)
    project_id, channel_id, account = run(client, _setup, 9813, "@uc_turns")
    own = run(client, _own_channel, 9813)
    run(client, _topics, project_id)
    run(client, covered.covered_step)  # begins from now
    run(client, _post, channel_id, 1, "news of the day")
    run(client, _post, own, 2, "about the launch: how it went")

    async def scheduled() -> None:
        async with SessionLocal() as s:
            state = await s.get(TopicState, project_id)
            state.last_run_at = covered.utcnow() - timedelta(hours=2)  # its time has come
            await s.commit()
            project = await s.get(Project, project_id)
        await topics.run_project(project, account)

    def runs() -> int:
        return sum("my_channel" in p for p in model)

    # the channel's post is not checked yet: the run waits
    run(client, scheduled)
    assert model == []
    run(client, covered.covered_step)
    assert len(model) == 1
    run(client, scheduled)
    assert runs() == 1

    # a topics' run goes: the channel's next post waits for it to end
    async def topics_run(going: bool) -> None:
        async with SessionLocal() as s:
            now = covered.utcnow().isoformat(timespec="seconds")
            (await s.get(TopicState, project_id)).run = {"started": now, "beat": now,
                                                         **({} if going else {"finished": now})}
            await s.commit()

    run(client, topics_run, True)
    run(client, _post, own, 3, "about the launch again")
    run(client, covered.covered_step)
    assert len(model) == 2
    run(client, topics_run, False)
    run(client, covered.covered_step)
    assert len(model) == 3 and "my_channel" not in model[-1]

    # a post the check never got to holds the runs no longer than COVERED_WAIT
    async def unchecked(age: timedelta) -> bool:
        async with SessionLocal() as s:
            p = await s.get(Post, run_id[0])
            p.created_at = covered.utcnow() - age
            await s.commit()
            return await topics._channel_unchecked(s, project_id, await s.get(TopicState, project_id))

    run_id = [run(client, _post, own, 4, "not read yet")]
    assert run(client, unchecked, timedelta(minutes=5))
    assert not run(client, unchecked, topics.COVERED_WAIT + timedelta(minutes=1))
