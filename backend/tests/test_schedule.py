from datetime import datetime, timedelta

import pytest

from app import schedule, topics
from app.db import SessionLocal
from app.models import Project, TopicState

from test_topics import _post, _setup
from test_topics import bot, model  # noqa: F401  (fixtures)
from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)

# 2026-10-14 is a Wednesday; Moscow is UTC+3 all year
WED = datetime(2026, 10, 14)


def s(*rules, posts=None, tz="Europe/Moscow"):
    return schedule.normalize("topics", {"rules": list(rules), "posts": posts, "tz": tz})


def every(minutes, start=None, end=None, days=None):
    return {"kind": "every", "days": days or schedule.ALL_DAYS, "from": start, "to": end, "every_min": minutes}


def at(*times, days=None):
    return {"kind": "times", "days": days or schedule.ALL_DAYS, "times": list(times)}


def msk(hour, minute=0):
    """Wednesday's Moscow time, as our UTC (hours past 24 run into Thursday)."""
    return WED + timedelta(hours=hour - 3, minutes=minute)


def test_every_with_hours_and_days():
    night = s(every(60, "08:00", "23:00", [0, 1, 2, 3, 4]))
    at_six = WED.replace(hour=3)  # 6:00 in Moscow
    assert not schedule.due(night, at_six, WED - timedelta(days=1), WED)
    assert schedule.next_run(night, at_six, WED - timedelta(days=1), WED) == WED.replace(hour=5)  # 8:00
    assert schedule.due(night, WED.replace(hour=5), WED - timedelta(days=1), WED)
    # an hour after the last run, inside the hours: due; sooner: not
    assert not schedule.due(night, WED.replace(hour=9, minute=30), WED.replace(hour=9), WED)
    assert schedule.due(night, WED.replace(hour=10), WED.replace(hour=9), WED)
    # Friday 23:30 in Moscow: the next run is on Monday at 8:00
    friday_late = datetime(2026, 10, 16, 20, 30)
    assert schedule.next_run(night, friday_late, friday_late - timedelta(minutes=40), WED) == datetime(2026, 10, 19, 5)
    assert schedule.per_day(night) == pytest.approx(15 * 5 / 7)
    assert schedule.summary(night) == "каждый час с 8:00 до 23:00 по будням"

    # hours over midnight: from 22:00 to 6:00 — the night belongs to the day it begins
    owl = s(every(180, "22:00", "06:00"))
    assert schedule.allowed(owl, WED.replace(hour=0))  # 3:00 in Moscow
    assert not schedule.allowed(owl, WED.replace(hour=12))
    assert schedule.next_run(owl, WED.replace(hour=12), None, WED) == WED.replace(hour=19)  # 22:00
    monday_night = s(every(60, "23:00", "02:00", [0]))
    assert schedule.allowed(monday_night, datetime(2026, 10, 19, 22))  # Tuesday 1:00 is still Monday's night
    assert not schedule.allowed(monday_night, datetime(2026, 10, 20, 22))  # Wednesday 1:00


def test_rules_make_one_day_together():
    # hourly from 10:00 to 23:00, every two hours through the night
    day_and_night = s(every(60, "10:00", "23:00"), every(120, "23:00", "10:00"))
    # by the clock: 22:00 is the day's last, then the night's 23:00, 1:00, 3:00 …
    assert schedule.next_run(day_and_night, msk(22, 30), msk(22), WED) == msk(23)
    assert schedule.next_run(day_and_night, msk(23, 30), msk(23), WED) == msk(25)  # 1:00
    # 9:00 was the night's last: 10:00 is the day's
    assert schedule.next_run(day_and_night, msk(9, 10), msk(9), WED) == msk(10)
    ahead = schedule.upcoming(day_and_night, msk(21, 10), msk(21), WED)
    assert ahead == [msk(h) for h in (22, 23, 25, 27, 29)]
    # «Собрать сейчас» at 10:40 does not move the day's hours
    assert schedule.next_run(day_and_night, msk(10, 41), msk(10, 40), WED) == msk(11)
    # the server was down through the night: the missed run goes at once — but not the night's in the day
    assert schedule.next_run(day_and_night, msk(4, 20), msk(1), WED - timedelta(days=1)) == msk(4, 20)
    hourly_day = s(every(60, "10:00", "23:00"))
    assert schedule.next_run(hourly_day, msk(24 + 8), msk(18), WED - timedelta(days=1)) == msk(24 + 10)
    assert schedule.per_day(day_and_night) == pytest.approx(13 + 6)
    assert schedule.summary(day_and_night) == "каждый час с 10:00 до 23:00; каждые 2 часа с 23:00 до 10:00"
    assert schedule.allowed(day_and_night, msk(4)) and schedule.allowed(day_and_night, msk(15))

    # a rule at times alongside a window
    with_morning = s(every(180, "12:00", "18:00"), at("09:00"))
    assert schedule.next_run(with_morning, msk(7), None, msk(6)) == msk(9)
    assert schedule.next_run(with_morning, msk(9, 5), msk(9), WED) == msk(12)


def test_at_times():
    twice = s(at("19:00", "09:00", days=[1, 2]))
    assert twice["rules"][0]["times"] == ["09:00", "19:00"]
    morning = WED.replace(hour=6)  # 9:00 in Moscow
    assert schedule.due(twice, morning, WED - timedelta(days=1), WED - timedelta(days=30))
    assert not schedule.due(twice, morning + timedelta(hours=1), morning, WED)
    assert schedule.next_run(twice, morning + timedelta(hours=1), morning, WED) == WED.replace(hour=16)
    # Wednesday's last run done: next Tuesday at 9:00
    assert schedule.next_run(twice, WED.replace(hour=17), WED.replace(hour=16), WED) == datetime(2026, 10, 20, 6)
    # missed while the server was down: once, as soon as it is back
    assert schedule.due(twice, WED.replace(hour=12), datetime(2026, 10, 7), WED - timedelta(days=30))
    # a time already past when the schedule was saved is not a run missed
    saved = schedule.stamped(twice, WED.replace(hour=12))
    assert not schedule.due(saved, WED.replace(hour=12, minute=1), None, WED - timedelta(days=30))
    assert schedule.next_run(saved, WED.replace(hour=12, minute=1), None, WED) == WED.replace(hour=16)
    assert schedule.per_day(twice) == pytest.approx(4 / 7)
    assert schedule.summary(twice) == "в 9:00 и 19:00 по вт, ср"

    # the zone's clock changes: 9:00 in Berlin is 7:00 UTC in summer, 8:00 after 25 October
    berlin = s(at("09:00"), tz="Europe/Berlin")
    rule = berlin["rules"][0]
    assert schedule._next_time(berlin, rule, datetime(2026, 10, 24, 12)) == datetime(2026, 10, 25, 8)
    assert schedule._next_time(berlin, rule, datetime(2026, 10, 23, 12)) == datetime(2026, 10, 24, 7)


def test_by_posts_alone_and_besides_the_rules():
    posts = s(posts={"count": 50, "gap_min": 30})
    now = WED.replace(hour=12)
    assert not schedule.due(posts, now, None, WED, waiting=49)
    assert schedule.due(posts, now, None, WED, waiting=50)
    assert not schedule.due(posts, now, now - timedelta(minutes=10), WED, waiting=80)
    assert schedule.next_run(posts, now, now - timedelta(minutes=10), WED, waiting=80) == now + timedelta(minutes=20)
    assert schedule.next_run(posts, now, None, WED, waiting=3) is None  # it waits for the posts
    assert schedule.upcoming(posts, now, None, WED) == []
    assert schedule.per_day(posts, posts_per_day=500) == pytest.approx(10)
    assert schedule.per_day(posts) is None
    assert schedule.summary(posts) == "когда наберётся 50 новых постов, не чаще раза в 30 минут"

    # besides a rule every 3 hours (0:00, 3:00 … by the clock): a flood of posts brings a run sooner
    both = s(every(180), posts={"count": 30, "gap_min": 15})
    four = msk(16)
    assert schedule.next_run(both, four, four - timedelta(minutes=20), WED, waiting=40) == four
    assert schedule.next_run(both, four, four - timedelta(minutes=20), WED, waiting=5) == msk(18)
    assert schedule.summary(both) == ("каждые 3 часа; и внеочередной сбор, когда наберётся 30 новых постов, "
                                      "не чаще раза в 15 минут")
    assert schedule.per_day(both) == pytest.approx(8)  # without the flow of posts: the rule's runs


def test_old_schedules_are_read_as_rules():
    old = {"mode": "every", "every_min": 60, "hours": {"from": "10:00", "to": "23:00"}, "days": [0, 1, 2, 3, 4, 5, 6],
           "times": ["09:00", "19:00"], "posts": 50, "gap_min": 30, "tz": "Europe/Berlin",
           "saved_at": "2026-10-19T07:36:00"}
    now = schedule.of("topics", old)
    assert now["rules"] == [every(60, "10:00", "23:00")] and now["posts"] is None and now["tz"] == "Europe/Berlin"
    assert now["saved_at"] == "2026-10-19T07:36:00"
    assert schedule.of("topics", {"mode": "every", "every_min": 360, "hours": None})["rules"] == [every(360)]
    assert schedule.of("topics", {"mode": "times", "times": ["08:00"], "days": [1]})["rules"] == [at("08:00", days=[1])]
    by_posts = schedule.of("topics", {"mode": "posts", "posts": 100, "gap_min": 60, "days": [0]})
    assert by_posts["rules"] == [] and by_posts["posts"] == {"count": 100, "gap_min": 60}
    # the owner's old interval is where a project's schedule starts
    assert schedule.of("topics", None, 180)["rules"][0]["every_min"] == 180
    assert schedule.of("topics", None)["rules"][0]["every_min"] == 60  # the default is never changed by that
    assert schedule.shortest_every(s(every(120), every(30, "10:00", "12:00"), at("09:00"))) == 30
    assert schedule.shortest_every(s(at("09:00"))) == 60


def test_what_is_not_a_schedule():
    bad_ones = [
        {"rules": []},
        {"rules": "каждый час"},
        {"rules": [{"kind": "after_post"}]},
        {"rules": [every(7)]},
        {"rules": [{**every(60), "days": []}]},
        {"rules": [{**at("09:00"), "times": []}]},
        {"rules": [every(60, "08:00", "08:00")]},
        {"rules": [at("25:00")]},
        {"rules": [every(60)] * 9},
        {"rules": [], "posts": {"count": 7, "gap_min": 30}},
    ]
    for bad in bad_ones:
        with pytest.raises(ValueError):
            schedule.normalize("topics", bad)
    assert schedule.normalize("topics", {"rules": [every(60)], "tz": "Mars/Olympus"})["tz"] == schedule.DEFAULT_TZ


def test_topics_run_on_their_schedule(ctx, model, bot):  # noqa: F811
    app, client = ctx
    owner = sign_in(app, client, 9921)
    assert owner.post("/api/channels", json={"links": "@uc_sched"}).json()["added"]
    run(client, _activate, "@uc_sched", 99211, 0)
    project_id, channel_id, account = run(client, _setup, 9921, "@uc_sched")

    bad = owner.put("/api/topics/settings", json={"schedule": {"rules": [], "posts": {"count": 7, "gap_min": 15}}})
    assert bad.status_code == 422
    saved = owner.put("/api/topics/settings", json={"schedule": {"rules": [], "posts": {"count": 10, "gap_min": 15},
                                                                  "tz": "Europe/Moscow"}}).json()
    assert saved["schedule_summary"] == "когда наберётся 10 новых постов, не чаще раза в 15 минут"
    assert saved["next_run_at"] is None and saved["waiting_posts"] == 0 and saved["upcoming"] == []
    assert saved["schedule_options"]["kinds"] == ["every", "times"]

    async def scheduled():
        async with SessionLocal() as s_:
            project = await s_.get(Project, project_id)
        return await topics.run_project(project, account)

    for i in range(9):
        run(client, _post, channel_id, i + 1, f"launch {i}")
    assert run(client, scheduled) == [] and model == []  # 9 of 10: not yet
    run(client, _post, channel_id, 10, "launch 10")
    assert owner.get("/api/topics/settings").json()["waiting_posts"] == 10
    assert len(run(client, scheduled)) == 1 and len(model) == 1

    async def last_run():
        async with SessionLocal() as s_:
            return (await s_.get(TopicState, project_id)).last_run_at

    assert run(client, last_run) is not None

    # two rules: what each one says, and the next runs by them
    saved = owner.put("/api/topics/settings", json={"schedule": {
        "rules": [every(60, "10:00", "23:00"), every(120, "23:00", "10:00")], "posts": None, "tz": "Europe/Moscow",
    }}).json()
    assert saved["rule_summaries"] == ["каждый час с 10:00 до 23:00", "каждые 2 часа с 23:00 до 10:00"]
    assert len(saved["upcoming"]) == schedule.UPCOMING and saved["per_day"] == 19
