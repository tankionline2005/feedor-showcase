"""Topics as they age: the model sees the topics of two weeks it needs, a quiet topic takes late posts quietly,
an event long over makes no topic, big news raises a topic, and after the channel told it, «Есть продолжение»."""

import json
from datetime import timedelta

import httpx
from sqlalchemy import select

from app import ai, covered, topic_match, topics
from app.db import SessionLocal
from app.models import Post, Topic, TopicPost, TopicState, TopicUpdate, utcnow

from test_topics import _own_channel, _post, _setup
from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


def test_topics_are_picked_by_the_words_they_share():
    pool = [
        (1, "Запуск ракеты «Союз» с Байконура", "Ракета вывела на орбиту спутники связи."),
        (2, "ЦБ повысил ключевую ставку до 18%", "Банк России поднял ставку из-за инфляции."),
        (3, "Хакеры взломали Dodo Pizza", "Утекли данные клиентов сети пиццерий."),
        *[(10 + i, f"Погода на выходные {i}", "Дожди и ветер в центральных регионах.") for i in range(20)],
    ]
    # other forms of the words, and a name
    assert topic_match.pick(pool, ["Ракету «Союз» запустили, спутники на орбите"], limit=5)[0] == 1
    assert topic_match.pick(pool, ["Данные клиентов Dodo утекли в Telegram"], limit=5)[0] == 3
    assert topic_match.pick(pool, ["Ключевая ставка: банк России снова повысил её"], limit=5)[0] == 2
    # nothing in common: nothing picked
    assert topic_match.pick(pool, ["Новый смартфон показали на выставке"], limit=5) == []


async def _topic(project_id: int, title: str, summary: str, age: timedelta, **kw) -> int:
    async with SessionLocal() as s:
        at = utcnow() - age
        t = Topic(project_id=project_id, title=title, summary=summary, created_at=at, updated_at=at,
                  last_post_at=at, ranked_at=at, importance=5, **kw)
        s.add(t)
        await s.flush()
        s.add(TopicUpdate(topic_id=t.id, at=at, text="началось"))
        await s.commit()
        return t.id


async def _told_topic(project_id: int) -> int:
    """A topic the project's channel told two hours ago."""
    return await _topic(project_id, "Запуск ракеты", "пересказ", timedelta(hours=3), covered=True,
                        covered_at=utcnow() - timedelta(hours=2))


async def _candidates(project_id: int, post_ids: list[int]) -> list[int]:
    async with SessionLocal() as s:
        rows = (await s.execute(select(Post, Post.channel_id).where(Post.id.in_(post_ids)))).all()
        return [t.id for t in await topics.candidates(s, project_id, rows)]


def test_the_model_sees_a_quiet_topic_the_posts_are_about(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9821)
    assert owner.post("/api/channels", json={"links": "@uc_life_pick"}).json()["added"]
    run(client, _activate, "@uc_life_pick", 98211, 0)
    project_id, channel_id, _ = run(client, _setup, 9821, "@uc_life_pick")
    quiet_id = run(client, _topic, project_id, "Запуск ракеты «Союз» с Байконура", "Ракета вывела спутники.",
                   timedelta(days=5))
    gone_id = run(client, _topic, project_id, "Запуск ракеты «Протон»", "Ракета стартовала.", timedelta(days=20))
    for i in range(topics.FRESHEST + 5):
        run(client, _topic, project_id, f"Погода, выпуск {i}", "Дожди и ветер.", timedelta(minutes=i + 1))
    post = run(client, _post, channel_id, 1, "Год назад ракету «Союз» запустили с Байконура — вспоминаем")

    picked = run(client, _candidates, project_id, [post])
    assert quiet_id in picked and gone_id not in picked  # five days quiet: in reach; twenty: gone
    assert len(picked) == topics.FRESHEST + 1  # the freshest, and the one it is about


def _update(topic_id: int, post_id: int, novelty: int, covered_: bool = False) -> dict:
    return {"topic_id": topic_id, "title": "", "summary": "новый пересказ", "update": "новости", "post_ids": [post_id],
            "covered": covered_, "importance": 6, "novelty": novelty}


async def _apply(project_id: int, answer: dict, post_ids: list[int], topic_ids: list[int]) -> list[int]:
    async with SessionLocal() as s:
        posts = {p.id: p for p in (await s.execute(select(Post).where(Post.id.in_(post_ids)))).scalars()}
        live = {t.id: t for t in (await s.execute(select(Topic).where(Topic.id.in_(topic_ids)))).scalars()}
        made = await topics.apply(s, project_id, answer, posts, live)
        await s.commit()
        return [t.id for t in made]


async def _get(topic_id: int) -> dict:
    async with SessionLocal() as s:
        t = await s.get(Topic, topic_id)
        sources = await s.scalar(select(TopicPost.post_id).where(TopicPost.topic_id == topic_id).limit(1))
        return {"summary": t.summary, "fresh": not topics.quiet(t), "raised": utcnow() - t.ranked_at < timedelta(minutes=1),
                "covered": t.covered, "followup": t.followup_at is not None, "has_source": sources is not None}


def test_a_quiet_topic_takes_an_echo_quietly_and_wakes_for_big_news(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9822)
    assert owner.post("/api/channels", json={"links": "@uc_life_quiet"}).json()["added"]
    run(client, _activate, "@uc_life_quiet", 98221, 0)
    project_id, channel_id, _ = run(client, _setup, 9822, "@uc_life_quiet")
    quiet_id = run(client, _topic, project_id, "Запуск ракеты", "старый пересказ", timedelta(days=4))
    echo, turn, old = (run(client, _post, channel_id, i, f"post {i}") for i in (1, 2, 3))

    # a small update of a quiet topic: one more source, and it stays as it was
    run(client, _apply, project_id, {"new_topics": [], "attach": [], "updates": [_update(quiet_id, echo, 3)]},
        [echo], [quiet_id])
    assert run(client, _get, quiet_id) == {"summary": "старый пересказ", "fresh": False, "raised": False,
                                          "covered": False, "followup": False, "has_source": True}
    # a turn of events: it is news again, at the top
    run(client, _apply, project_id, {"new_topics": [], "attach": [], "updates": [_update(quiet_id, turn, 8)]},
        [turn], [quiet_id])
    got = run(client, _get, quiet_id)
    assert (got["summary"], got["fresh"], got["raised"]) == ("новый пересказ", True, True)

    # an event long over, retold: no topic
    retold = {"title": "Годовщина запуска", "summary": "Год назад…", "update": "u", "post_ids": [old],
              "covered": False, "emoji": "", "importance": 3, "old_news": True}
    assert run(client, _apply, project_id, {"new_topics": [retold], "attach": [], "updates": []}, [old], []) == []


def test_big_news_after_the_channel_told_it_is_a_continuation(ctx, monkeypatch):
    app, client = ctx
    owner = sign_in(app, client, 9823)
    assert owner.post("/api/channels", json={"links": "@uc_life_follow"}).json()["added"]
    run(client, _activate, "@uc_life_follow", 98231, 0)
    project_id, channel_id, _ = run(client, _setup, 9823, "@uc_life_follow")
    told = run(client, _told_topic, project_id)
    small, big = (run(client, _post, channel_id, i, f"post {i}") for i in (1, 2))

    run(client, _apply, project_id, {"new_topics": [], "attach": [], "updates": [_update(told, small, 4)]},
        [small], [told])
    assert not run(client, _get, told)["followup"]  # details: nothing to write again about
    run(client, _apply, project_id, {"new_topics": [], "attach": [], "updates": [_update(told, big, 9)]},
        [big], [told])
    got = run(client, _get, told)
    assert got["raised"] and got["covered"] and got["followup"]
    topic = owner.get(f"/api/topics/{told}").json()
    assert topic["covered"] and topic["followup"]

    # the channel tells the continuation: the mark is the channel's again
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(json.loads(request.content)["messages"][-1]["content"])
        asked.append(payload)
        results = [{"post_id": p["id"], "topic_ids": [told]} for p in payload["posts"]]
        return httpx.Response(200, json={"id": "g", "model": "m", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                                         "choices": [{"message": {"content": json.dumps({"results": results})}}]})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    own = run(client, _own_channel, 9823)
    run(client, covered.covered_step)  # begins from now
    run(client, _post, own, 1, "Продолжение истории с ракетой")
    run(client, covered.covered_step)
    assert asked[0]["topics"][0]["continuation"] == "новости"  # it is asked whether the post tells the news
    assert not run(client, _get, told)["followup"] and run(client, _get, told)["covered"]
    # taken off by hand: both go
    owner.post(f"/api/topics/{told}/not-covered")
    assert owner.get(f"/api/topics/{told}").json()["followup"] is False


def test_one_topic_s_news_split_by_the_model_is_made_one(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9824)
    assert owner.post("/api/channels", json={"links": "@uc_life_merge"}).json()["added"]
    run(client, _activate, "@uc_life_merge", 98241, 0)
    project_id, channel_id, _ = run(client, _setup, 9824, "@uc_life_merge")
    quiet_id = run(client, _topic, project_id, "Протесты во Франции", "старый пересказ", timedelta(days=3))
    a, b, c = (run(client, _post, channel_id, i, f"post {i}") for i in (1, 2, 3))
    split = [dict(_update(quiet_id, a, 6), update="столкновения в Париже"),
             dict(_update(quiet_id, b, 8), update="атака на телеканал", summary="пересказ с атакой"),
             dict(_update(quiet_id, c, 7), update="захват мэрии Марселя", summary="полный пересказ")]
    run(client, _apply, project_id, {"new_topics": [], "attach": [], "updates": split}, [a, b, c], [quiet_id])

    async def lines():
        async with SessionLocal() as s:
            return list((await s.execute(select(TopicUpdate.text).where(TopicUpdate.topic_id == quiet_id)
                                         .order_by(TopicUpdate.id))).scalars())

    got = run(client, _get, quiet_id)
    assert (got["summary"], got["fresh"], got["raised"]) == ("полный пересказ", True, True)  # the biggest news: 8
    assert run(client, lines) == ["началось", "атака на телеканал"]  # one line a run: the biggest news


def test_a_line_that_retells_the_topic_adds_nothing(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9825)
    assert owner.post("/api/channels", json={"links": "@uc_life_retold"}).json()["added"]
    run(client, _activate, "@uc_life_retold", 98251, 0)
    project_id, channel_id, _ = run(client, _setup, 9825, "@uc_life_retold")
    fresh = run(client, _topic, project_id, "Инженер OpenAI арестован за жучок в переговорной",
                "Инженера OpenAI арестовали за скрытое записывающее устройство в переговорной комнате.",
                timedelta(hours=3))
    again, news = (run(client, _post, channel_id, i, f"post {i}") for i in (1, 2))
    lines = {fresh: ["Инженер OpenAI арестован за записывающее устройство"]}

    async def apply_with_lines(answer, post_ids):
        async with SessionLocal() as s:
            posts = {p.id: p for p in (await s.execute(select(Post).where(Post.id.in_(post_ids)))).scalars()}
            live = {t.id: t for t in (await s.execute(select(Topic).where(Topic.id == fresh))).scalars()}
            await topics.apply(s, project_id, answer, posts, live, None, lines)
            await s.commit()

    retold = dict(_update(fresh, again, 5), update="Сотрудник OpenAI арестован за записывающее устройство в переговорной",
                  summary="переписанный пересказ")
    run(client, apply_with_lines, {"new_topics": [], "attach": [], "updates": [retold]}, [again])
    got = run(client, _get, fresh)
    assert got["summary"].startswith("Инженера OpenAI") and got["has_source"]  # a source, nothing rewritten

    told = dict(_update(fresh, news, 6), update="Абдулрахману Альфозану предъявлено обвинение в федеральном суде")
    run(client, apply_with_lines, {"new_topics": [], "attach": [], "updates": [told]}, [news])
    assert run(client, _get, fresh)["summary"] == "новый пересказ"


def test_what_the_model_rates_as_nothing_new_makes_no_line(ctx):
    app, client = ctx
    owner = sign_in(app, client, 9826)
    assert owner.post("/api/channels", json={"links": "@uc_life_low"}).json()["added"]
    run(client, _activate, "@uc_life_low", 98261, 0)
    project_id, channel_id, _ = run(client, _setup, 9826, "@uc_life_low")
    fresh = run(client, _topic, project_id, "Запуск ракеты", "старый пересказ", timedelta(hours=3))
    post = run(client, _post, channel_id, 1, "post 1")
    run(client, _apply, project_id, {"new_topics": [], "attach": [], "updates": [_update(fresh, post, 2)]},
        [post], [fresh])
    got = run(client, _get, fresh)
    assert got["summary"] == "старый пересказ" and got["has_source"] and not got["raised"]


def test_the_model_sees_the_whole_timeline(ctx, monkeypatch):
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(json.loads(request.content)["messages"][-1]["content"]))
        empty = {"new_topics": [], "updates": [], "attach": []}
        return httpx.Response(200, json={"id": "g", "model": "m", "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                                         "choices": [{"message": {"content": json.dumps(empty)}}]})

    monkeypatch.setattr(ai, "_transport", httpx.MockTransport(handler))
    app, client = ctx
    owner = sign_in(app, client, 9827)
    assert owner.post("/api/channels", json={"links": "@uc_life_lines"}).json()["added"]
    run(client, _activate, "@uc_life_lines", 98271, 0)
    project_id, channel_id, account = run(client, _setup, 9827, "@uc_life_lines")
    tid = run(client, _topic, project_id, "Запуск ракеты", "пересказ", timedelta(hours=3))

    async def grow() -> None:
        async with SessionLocal() as s:
            for i in range(9):
                s.add(TopicUpdate(topic_id=tid, at=utcnow() - timedelta(minutes=60 - i), text=f"строка {i}"))
            await s.commit()

    run(client, grow)
    run(client, _post, channel_id, 1, "Ракета снова на старте")

    async def go() -> None:
        async with SessionLocal() as s:
            project = await s.get(topics.Project, project_id)
        await topics.run_project(project, account, force=True)

    run(client, go)
    timeline = next(t["timeline"] for t in sent[0]["topics"] if t["id"] == tid)
    assert timeline == ["началось"] + [f"строка {i}" for i in range(9)]  # all ten, the oldest first
