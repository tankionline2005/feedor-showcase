"""When topics run: each project's own schedule, set in its settings.

A schedule is a list of rules, each on its own days of the week:
- "every": every so often by the clock between two hours, or all day long — «каждый час с 10:00 до 23:00» runs at
  10:00, 11:00 … 22:00 (a span past midnight belongs to the day it begins: «пн 23:00–10:00» is Monday night till
  Tuesday morning);
- "times": at the times listed.
Runs go by all the rules together — «каждый час с 10:00 до 23:00» and «каждые 2 часа с 23:00 до 10:00» make one
day. Besides them, `posts`: an extra run once that many new posts wait, not more often than every so often; with
no rules it is the only one. Times are the owner's local ones: the zone comes from their browser with each change.

Schedules saved before the rules had one "mode" with one window of hours: of() reads them as rules.
"""

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Europe/Moscow"
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]

OPTIONS = {
    "topics": {
        "kinds": ["every", "times"],
        "every_min": [15, 30, 60, 120, 180, 360, 720, 1440],
        "posts": [10, 20, 30, 50, 100, 200],
        "gap_min": [15, 30, 60, 180],
        "max_rules": 8,
        "max_times": 12,
    },
}
OPTIONS["rubrics"] = OPTIONS["topics"]  # a rubric's issues: the same rules (no extra run by posts)

DEFAULT_RULE = {"kind": "every", "days": ALL_DAYS, "from": None, "to": None, "every_min": 60}
DEFAULT_POSTS = {"count": 50, "gap_min": 30}
DEFAULTS = {"topics": {"rules": [DEFAULT_RULE], "posts": None, "tz": DEFAULT_TZ},
            "rubrics": {"rules": [{"kind": "times", "days": ALL_DAYS, "times": ["21:00"]}], "posts": None,
                        "tz": DEFAULT_TZ}}
UPCOMING = 5  # the next runs shown under the schedule

EVERY_TEXT = {15: "каждые 15 минут", 30: "каждые 30 минут", 60: "каждый час", 120: "каждые 2 часа",
              180: "каждые 3 часа", 360: "каждые 6 часов", 720: "каждые 12 часов", 1440: "раз в сутки"}
GAP_TEXT = {15: "15 минут", 30: "30 минут", 60: "час", 180: "3 часа"}
DAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def _copy(s: dict) -> dict:
    return {**s, "rules": [dict(r) for r in s["rules"]], "posts": dict(s["posts"]) if s.get("posts") else None}


def _from_mode(old: dict) -> dict:
    """A schedule saved before the rules: its one mode as rules."""
    days = old.get("days") or ALL_DAYS
    mode = old.get("mode", "every")
    out = {"rules": [], "posts": None, "tz": old.get("tz") or DEFAULT_TZ}
    if old.get("saved_at"):
        out["saved_at"] = old["saved_at"]
    if mode == "times":
        out["rules"] = [{"kind": "times", "days": days, "times": old.get("times") or ["09:00", "19:00"]}]
    elif mode == "posts":
        out["posts"] = {"count": old.get("posts") or 50, "gap_min": old.get("gap_min") or 30}
    else:
        hours = old.get("hours") or {}
        out["rules"] = [{"kind": "every", "days": days, "from": hours.get("from"), "to": hours.get("to"),
                         "every_min": old.get("every_min") or 60}]
    return out


def of(op: str, saved: dict | None, every_min: int | None = None) -> dict:
    """The schedule in use: the saved one (an old one read as rules), or the default — with the interval the owner
    had before schedules were per project."""
    if saved:
        return {**_copy(DEFAULTS[op]), **(saved if "rules" in saved else _from_mode(saved))}
    out = _copy(DEFAULTS[op])
    if every_min in OPTIONS[op]["every_min"]:
        out["rules"][0]["every_min"] = every_min
    return out


def _hhmm(value) -> str:
    if not isinstance(value, str):
        raise ValueError("Время указано неверно")
    try:
        t = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Время указано неверно") from exc
    return f"{t.hour:02d}:{t.minute:02d}"


def _rule(raw, opts: dict) -> dict:
    if not isinstance(raw, dict) or raw.get("kind") not in opts["kinds"]:
        raise ValueError("Такого правила расписания нет")
    days = raw.get("days", ALL_DAYS)
    if not isinstance(days, list) or not days or any(d not in ALL_DAYS for d in days):
        raise ValueError("Выберите хотя бы один день недели")
    days = sorted(set(days))
    if raw["kind"] == "times":
        times = raw.get("times")
        if not isinstance(times, list) or not times:
            raise ValueError("Добавьте хотя бы одно время")
        times = sorted({_hhmm(t) for t in times})
        if len(times) > opts["max_times"]:
            raise ValueError(f"Не больше {opts['max_times']} запусков в одном правиле")
        return {"kind": "times", "days": days, "times": times}
    if raw.get("every_min") not in opts["every_min"]:
        raise ValueError("Такого варианта расписания нет")
    start, end = raw.get("from"), raw.get("to")
    if start is None and end is None:
        return {"kind": "every", "days": days, "from": None, "to": None, "every_min": raw["every_min"]}
    start, end = _hhmm(start), _hhmm(end)
    if start == end:
        raise ValueError("Начало и конец периода совпадают")
    return {"kind": "every", "days": days, "from": start, "to": end, "every_min": raw["every_min"]}


def normalize(op: str, raw: dict) -> dict:
    """A schedule from the page, checked; ValueError says what is wrong, for people."""
    opts = OPTIONS[op]
    rules = raw.get("rules")
    if not isinstance(rules, list):
        raise ValueError("Расписание указано неверно")
    if len(rules) > opts["max_rules"]:
        raise ValueError(f"Не больше {opts['max_rules']} правил")
    out_rules = [_rule(r, opts) for r in rules]
    posts = raw.get("posts")
    if posts:
        if not isinstance(posts, dict) or posts.get("count") not in opts["posts"] \
                or posts.get("gap_min") not in opts["gap_min"]:
            raise ValueError("Такого варианта расписания нет")
        posts = {"count": posts["count"], "gap_min": posts["gap_min"]}
    else:
        posts = None
    if not out_rules and not posts:
        raise ValueError("Добавьте хотя бы одно правило расписания")
    tz = raw.get("tz") or DEFAULT_TZ
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        tz = DEFAULT_TZ
    return {"rules": out_rules, "posts": posts, "tz": tz}


def shortest_every(s: dict) -> int:
    """The shortest interval of the rules, in minutes (an hour when there is none): how far back a first run reads."""
    return min((r["every_min"] for r in s["rules"] if r["kind"] == "every"), default=60)


# --- moments ---------------------------------------------------------------------------------------

def _zone(s: dict) -> ZoneInfo:
    try:
        return ZoneInfo(s.get("tz") or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def _utc(local: datetime) -> datetime:
    """Our times are naive UTC."""
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _local_date(s: dict, at: datetime) -> date:
    return at.replace(tzinfo=ZoneInfo("UTC")).astimezone(_zone(s)).date()


def _at(s: dict, day: date, hhmm: str) -> datetime:
    return _utc(datetime.combine(day, time.fromisoformat(hhmm), tzinfo=_zone(s)))


def _span(s: dict, rule: dict, day: date) -> tuple[datetime, datetime] | None:
    """When an "every" rule's runs may go on the local day it begins on, as a UTC interval; times rules: all of
    their day."""
    if day.weekday() not in rule["days"]:
        return None
    if rule["kind"] == "times" or rule.get("from") is None:
        return _at(s, day, "00:00"), _at(s, day + timedelta(days=1), "00:00")
    end_day = day if rule["from"] < rule["to"] else day + timedelta(days=1)
    return _at(s, day, rule["from"]), _at(s, end_day, rule["to"])


def _spans(s: dict, rule: dict, around: datetime):
    first = _local_date(s, around)
    for k in range(-1, 9):
        if (span := _span(s, rule, first + timedelta(days=k))) is not None:
            yield span


def allowed(s: dict, at: datetime) -> bool:
    """Is `at` within one of the rules' days and hours (always, for a schedule by posts alone)."""
    if not s["rules"]:
        return True
    return any(a <= at < b for rule in s["rules"] for a, b in _spans(s, rule, at))


def _base(s: dict, last: datetime | None, since: datetime) -> datetime:
    """Times rules: the listed times after this are still to come. Not those before the schedule was saved: a time
    already past today is not a run missed."""
    saved = datetime.fromisoformat(s["saved_at"]) if s.get("saved_at") else None
    return max(x for x in (last, since, saved) if x is not None)


def _next_time(s: dict, rule: dict, after: datetime) -> datetime | None:
    """A times rule: its first listed time after `after`, on its days."""
    first = _local_date(s, after)
    found = [m for k in range(-1, 9) for day in [first + timedelta(days=k)] if day.weekday() in rule["days"]
             for m in (_at(s, day, t) for t in rule["times"]) if m > after]
    return min(found, default=None)


def _grid(s: dict, rule: dict, around: datetime):
    """An "every" rule's runs, by the clock: from its start every so often, before its end — «каждый час с 10:00»
    is 10:00, 11:00, 12:00 (all day: from midnight). Wall-clock steps, so a change of the zone's clock keeps them."""
    zone, step = _zone(s), timedelta(minutes=rule["every_min"])
    first = _local_date(s, around)
    for k in range(-1, 9):
        day = first + timedelta(days=k)
        if (span := _span(s, rule, day)) is None:
            continue
        moment = datetime.combine(day, time.fromisoformat(rule.get("from") or "00:00"), tzinfo=zone)
        while (utc := _utc(moment)) < span[1]:
            yield utc
            moment += step


def _rule_next(s: dict, rule: dict, now: datetime, last: datetime | None, since: datetime) -> datetime | None:
    """A rule's next run after the last one; one missed (the server was down) goes now — for an "every" rule only
    while its hours last: the night's run is not made up in the morning."""
    if rule["kind"] == "times":
        moment = _next_time(s, rule, _base(s, last, since))
        return None if moment is None else max(moment, now)
    # by the last run only: the first one goes as soon as the hours allow
    moment = None if last is None else next((m for m in _grid(s, rule, last) if m > last), None)
    if moment is not None and moment > now:
        return moment
    if any(a <= now < b for a, b in _spans(s, rule, now)):
        return now
    return next((m for m in _grid(s, rule, now) if m >= now), None)


def stamped(s: dict, now: datetime) -> dict:
    """The schedule to keep, with when it was saved."""
    return {**s, "saved_at": now.isoformat(timespec="seconds")}


def next_run(s: dict, now: datetime, last: datetime | None, since: datetime, *,
             waiting: int = 0) -> datetime | None:
    """When the next run goes, as far as can be known now: the soonest of the rules' runs and of the extra one by
    posts (None: none of them comes — it waits for posts). `since`: when the schedule began (the state's creation),
    for times rules; `waiting`: new posts, for `posts`."""
    moments = [m for rule in s["rules"] if (m := _rule_next(s, rule, now, last, since)) is not None]
    posts = s.get("posts")
    if posts and waiting >= posts["count"]:
        moments.append(now if last is None else max(now, last + timedelta(minutes=posts["gap_min"])))
    return min(moments, default=None)


def due(s: dict, now: datetime, last: datetime | None, since: datetime, *, waiting: int = 0) -> bool:
    """Is it time for a run?"""
    moment = next_run(s, now, last, since, waiting=waiting)
    return moment is not None and moment <= now


def upcoming(s: dict, now: datetime, last: datetime | None, since: datetime, n: int = UPCOMING) -> list[datetime]:
    """The next runs by the rules, each going on time (the extra runs by posts cannot be known ahead)."""
    out: list[datetime] = []
    for _ in range(n):
        moment = next_run(s, now, last, since)
        if moment is None:
            break
        out.append(moment)
        now = last = moment
    return out


def _span_minutes(rule: dict) -> int:
    if rule.get("from") is None:
        return 1440
    a = int(rule["from"][:2]) * 60 + int(rule["from"][3:])
    b = int(rule["to"][:2]) * 60 + int(rule["to"][3:])
    return b - a if a < b else 1440 - a + b


def per_day(s: dict, *, posts_per_day: float | None = None) -> float | None:
    """About how many runs a day: what the schedule means for the daily limit of requests. None: it depends on posts
    that cannot be counted now."""
    regular = 0.0
    for rule in s["rules"]:
        share = len(rule["days"]) / 7
        if rule["kind"] == "times":
            regular += len(rule["times"]) * share
        else:
            regular += math.ceil(_span_minutes(rule) / rule["every_min"]) * share
    posts = s.get("posts")
    if not posts:
        return regular
    if posts_per_day is None:
        return regular if s["rules"] else None
    by_posts = min(posts_per_day / posts["count"], 1440 / posts["gap_min"])
    # with rules, the posts are read by the regular runs too: an extra run only for what they would not catch
    return regular + max(0.0, by_posts - regular) if s["rules"] else by_posts


# --- for people ------------------------------------------------------------------------------------

def _hm(hhmm: str) -> str:
    return hhmm.lstrip("0") if not hhmm.startswith("00") else "0" + hhmm[2:]


def _days_text(days: list[int]) -> str:
    if len(days) == 7:
        return ""
    if days == [0, 1, 2, 3, 4]:
        return "по будням"
    if days == [5, 6]:
        return "по выходным"
    return "по " + ", ".join(DAY_SHORT[d] for d in days)


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " и " + items[-1]


def rule_summary(rule: dict) -> str:
    """One rule in a few words: «каждый час с 8:00 до 23:00 по будням», «в 9:00 и 19:00»."""
    if rule["kind"] == "times":
        parts = ["в " + _join([_hm(t) for t in rule["times"]])]
    else:
        parts = [EVERY_TEXT.get(rule["every_min"], f"каждые {rule['every_min']} мин")]
        if rule.get("from"):
            parts.append(f"с {_hm(rule['from'])} до {_hm(rule['to'])}")
    if days := _days_text(rule["days"]):
        parts.append(days)
    return " ".join(parts)


def summary(s: dict) -> str:
    """The whole schedule in a few words."""
    text = "; ".join(rule_summary(r) for r in s["rules"])
    posts = s.get("posts")
    if posts:
        by_posts = (f"когда наберётся {posts['count']} новых постов, не чаще раза в "
                    f"{GAP_TEXT.get(posts['gap_min'], posts['gap_min'])}")
        text = f"{text}; и внеочередной сбор, {by_posts}" if text else by_posts
    return text
