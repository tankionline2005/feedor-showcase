"""«Рубрики»: issues the AI writes on a schedule from the project's topics or popular posts (rubrics.py)."""

from datetime import timedelta
from functools import partial
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app import publications, rubrics, schedule
from app.db import SessionLocal
from app.models import ChatPost, Post, Rubric, utcnow

from test_chat_posts import _setup, model  # noqa: F401
from test_publications import _tool_answer, _topics
from test_user_channels import ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


async def _issues(project_id: int) -> list[ChatPost]:
    async with SessionLocal() as s:
        return (await s.execute(select(ChatPost).where(ChatPost.project_id == project_id, ChatPost.rubric_id.is_not(None))
                                .order_by(ChatPost.id))).scalars().all()


async def _rubric(rubric_id: int) -> Rubric:
    async with SessionLocal() as s:
        return await s.get(Rubric, rubric_id)


def test_a_rubric_writes_its_issues_the_owner_s_way(ctx, model):
    sent, script = model
    app, client = ctx
    owner = sign_in(app, client, 9971)
    ids = run(client, _setup, 9971)
    run(client, partial(_topics, ids["project"], ids["other"], 4))  # importance 10, 9, 8, 7, all fresh

    listed = owner.get("/api/rubrics").json()
    assert listed["items"] == [] and [p["key"] for p in listed["presets"]][:3] == ["day", "morning", "week"]
    day = owner.post("/api/rubrics", json={"preset": "day"}).json()
    assert day["name"] == "Итоги дня" and day["enabled"] is False and day["rule_summaries"] == ["в 21:00"]
    assert owner.post("/api/rubrics", json={"preset": "nope"}).status_code == 422
    rid = day["id"]
    assert owner.put(f"/api/rubrics/{rid}", json={"period_hours": 5}).status_code == 422
    saved = owner.put(f"/api/rubrics/{rid}", json={"min_importance": 8, "period_hours": 24, "words": "Тема, тема 1",
                                                     "footer": "@my_chan", "max_items": 3}).json()
    assert saved["words"] == ["Тема", "тема 1"] and saved["max_items"] == 3

    # «Сделать пример сейчас»: written at once, saved nowhere — the header and the footer round it
    script.append(_tool_answer("Главное за день: тема 0, тема 1 и тема 2", [ids["other"]]))
    text = owner.post(f"/api/rubrics/{rid}/example").json()["text"]
    assert text == "<b>Итоги дня</b>\n\nГлавное за день: тема 0, тема 1 и тема 2\n\n@my_chan"
    material = __import__("json").loads(sent[-1]["messages"][1]["content"])["material"]
    assert [m["importance"] for m in material] == [10, 9, 8]  # the least importance kept to, the hottest first
    assert "«Итоги дня»" in sent[-1]["messages"][0]["content"]
    assert run(client, partial(_issues, ids["project"])) == []

    # an issue: a suggestion first
    script.append(_tool_answer("Выпуск", [ids["other"]]))
    run(client, partial(rubrics.issue, rid))
    [first] = run(client, partial(_issues, ids["project"]))
    assert first.media == [] and first.scheduled_at is None and first.text.startswith("<b>Итоги дня</b>")
    card = [p for p in owner.get("/api/publications", params={"filter": "suggested"}).json()["items"]
            if p["id"] == first.id][0]
    assert card["rubric"] == {"id": rid, "name": "Итоги дня"}
    # one nobody wants goes from «Предложения ИИ» like a topic's post
    script.append(_tool_answer("Лишний выпуск", [ids["other"]]))
    run(client, partial(rubrics.issue, rid))
    extra = run(client, partial(_issues, ids["project"]))[-1]
    assert owner.delete(f"/api/chat-posts/{extra.id}").json() == {"ok": True}
    assert [p.id for p in run(client, partial(_issues, ids["project"]))] == [first.id]

    # straight to the channel: scheduled now, in the rubric's name
    owner.put(f"/api/rubrics/{rid}", json={"mode": "auto"})
    script.append(_tool_answer("Ещё выпуск", []))
    run(client, partial(rubrics.issue, rid))
    second = run(client, partial(_issues, ids["project"]))[-1]
    assert second.scheduled_at is not None and second.scheduled_by == "рубрика «Итоги дня»"

    # nothing fits: no issue, and the rubric says why
    owner.put(f"/api/rubrics/{rid}", json={"words": "ничего такого"})
    assert run(client, partial(rubrics.issue, rid)) is None
    assert "не нашлось тем" in run(client, partial(_rubric, rid)).last_error
    assert owner.post(f"/api/rubrics/{rid}/example").status_code == 422

    # on its schedule: an hourly rule, never run yet — due now
    owner.put(f"/api/rubrics/{rid}", json={"words": [], "enabled": True, "mode": "suggest", "schedule": {
        "rules": [{"kind": "every", "every_min": 60, "days": [0, 1, 2, 3, 4, 5, 6], "from": None, "to": None}],
        "tz": "Europe/Moscow"}})
    async def never_run():
        async with SessionLocal() as s:
            (await s.get(Rubric, rid)).last_run_at = None
            await s.commit()
    run(client, never_run)
    script.append(_tool_answer("По расписанию", []))
    run(client, rubrics.rubrics_step)
    assert run(client, partial(_issues, ids["project"]))[-1].text.endswith("По расписанию\n\n@my_chan")
    run(client, rubrics.rubrics_step)  # just ran: not again
    assert len(run(client, partial(_issues, ids["project"]))) == 3

    # gone: its issue scheduled stays in «Публикации», its suggestions nobody took go with it
    assert owner.delete(f"/api/rubrics/{rid}").json() == {"ok": True}
    assert owner.get("/api/rubrics").json()["items"] == []
    assert [p["id"] for p in owner.get("/api/publications").json()["items"]] == [second.id]


async def _backdate(rubric_id: int, days: int) -> None:
    """Made `days` ago and left as it was since — as one created earlier and turned on only now."""
    async with SessionLocal() as s:
        r = await s.get(Rubric, rubric_id)
        r.created_at = utcnow() - timedelta(days=days)
        r.schedule = schedule.stamped(schedule.of("rubrics", r.schedule), r.created_at)
        await s.commit()


async def _due(rubric_id: int) -> bool:
    r = await _rubric(rubric_id)
    return schedule.due(schedule.of("rubrics", r.schedule), utcnow(), r.last_run_at, r.created_at)


def test_a_rubric_turned_on_waits_for_its_time(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9972)
    run(client, partial(_setup, 9972))
    rid = owner.post("/api/rubrics", json={"preset": "day"}).json()["id"]
    later = (utcnow().replace(tzinfo=ZoneInfo("UTC")) + timedelta(hours=6)).astimezone(ZoneInfo("Europe/Moscow"))
    owner.put(f"/api/rubrics/{rid}", json={"schedule": {
        "rules": [{"kind": "times", "days": [0, 1, 2, 3, 4, 5, 6], "times": [later.strftime("%H:%M")]}],
        "tz": "Europe/Moscow"}})
    run(client, partial(_backdate, rid, 2))
    assert run(client, partial(_due, rid))  # its times of the two days passed while it was off
    assert owner.put(f"/api/rubrics/{rid}", json={"enabled": True}).json()["enabled"]
    assert not run(client, partial(_due, rid))  # turned on: the next one is in six hours, not now

    # off for days and on again: the same
    owner.put(f"/api/rubrics/{rid}", json={"enabled": False})
    run(client, partial(_backdate, rid, 3))
    owner.put(f"/api/rubrics/{rid}", json={"enabled": True})
    assert not run(client, partial(_due, rid))


async def _old_issues(project_id: int, rubric_id: int) -> dict:
    long_ago = utcnow() - timedelta(days=400)
    async with SessionLocal() as s:
        plain = ChatPost(project_id=project_id, rubric_id=rubric_id, text="Никто не взял", ai_text="", created_at=long_ago)
        withdrawn = ChatPost(project_id=project_id, rubric_id=rubric_id, text="Удалён из канала", ai_text="",
                             created_at=long_ago, withdrawn_at=long_ago, withdrawn_by="владелец")
        s.add_all([plain, withdrawn])
        await s.commit()
        return {"plain": plain.id, "withdrawn": withdrawn.id}


async def _forget(cutoff) -> None:
    async with SessionLocal() as s:
        await publications.forget_stale(s, cutoff)
        await s.commit()


async def _exist(ids: list[int]) -> list[int]:
    async with SessionLocal() as s:
        return sorted((await s.execute(select(ChatPost.id).where(ChatPost.id.in_(ids)))).scalars())


def test_a_post_taken_back_from_the_channel_stays(ctx, model):
    app, client = ctx
    owner = sign_in(app, client, 9973)
    ids = run(client, partial(_setup, 9973))
    rid = owner.post("/api/rubrics", json={"preset": "day"}).json()["id"]
    posts = run(client, partial(_old_issues, ids["project"], rid))
    run(client, partial(_forget, utcnow() - timedelta(days=30)))  # the retention period
    assert run(client, partial(_exist, list(posts.values()))) == [posts["withdrawn"]]
    # nor does it go with its rubric
    owner.delete(f"/api/rubrics/{rid}")
    assert run(client, partial(_exist, list(posts.values()))) == [posts["withdrawn"]]


async def _popular(project_id: int, watched: int) -> list[dict]:
    async with SessionLocal() as s:
        s.add(Post(channel_id=watched, msg_id=77, posted_at=utcnow(), text="", media_type="photo", views=10 ** 9))
        s.add(Post(channel_id=watched, msg_id=78, posted_at=utcnow(), text="  ", media_type="video", views=10 ** 9))
        await s.commit()
        r = Rubric(project_id=project_id, name="Популярное", source="top_posts", max_items=3, period_hours=24)
        items, _ = await rubrics.material(s, r, utcnow())
        return items


def test_popular_posts_without_text_are_not_taken(ctx, model):
    app, client = ctx
    sign_in(app, client, 9974)
    ids = run(client, partial(_setup, 9974))
    items = run(client, partial(_popular, ids["project"], ids["watched"]))
    assert items and all(i["text"].strip() for i in items)
