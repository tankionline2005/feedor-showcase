"""History of project channels, for their «Статистика» and for the AI.

Our accounts are not admins there, so it is what anyone sees, kept over time:
- subscribers: the collector refreshes a project channel's info every hour (others once a day)
  and every refresh leaves a ChannelSnapshot;
- every post's views, forwards, reactions and comments 1, 6, 24 and 48 hours after it came out
  (PostCheckpoint), taken from the metrics the collector keeps fresh. Checkpoints are not tied to
  the post rows, so they stay when old posts are deleted.
"""

from datetime import datetime, timedelta

from sqlalchemy import case, exists, func, select

from .db import SessionLocal
from .models import Channel, ChannelSnapshot, Post, PostCheckpoint, ProjectChannel, utcnow

EPOCH = datetime(1970, 1, 1)  # times are naive UTC (models.utcnow)
CHECKPOINT_HOURS = (1, 6, 24, 48)
# a checkpoint is taken in this window after its hour (the metrics are refreshed by then), not later
LATE = timedelta(hours=3)


def own_channels():
    """Channel ids that are some project's own channel."""
    return select(ProjectChannel.channel_id).where(ProjectChannel.own)


async def record_subscribers(session, channel_id: int, subscribers: int | None) -> None:
    if subscribers is not None:
        session.add(ChannelSnapshot(channel_id=channel_id, subscribers=subscribers))


async def checkpoints_step() -> None:
    now = utcnow()
    async with SessionLocal() as s:
        for hours in CHECKPOINT_HOURS:
            due = (await s.execute(
                select(Post).where(
                    Post.channel_id.in_(own_channels()),
                    Post.deleted_at.is_(None),
                    Post.posted_at <= now - timedelta(hours=hours),
                    Post.posted_at > now - timedelta(hours=hours) - LATE,
                    ~exists().where(
                        PostCheckpoint.channel_id == Post.channel_id,
                        PostCheckpoint.msg_id == Post.msg_id,
                        PostCheckpoint.hours == hours,
                    ),
                )
            )).scalars().all()
            for p in due:
                s.add(PostCheckpoint(channel_id=p.channel_id, msg_id=p.msg_id, posted_at=p.posted_at, hours=hours,
                                     views=p.views, forwards=p.forwards, reactions=p.reactions, comments=p.comments))
        await s.commit()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


# --- «Статистика» of a project channel ---------------------------------------------------------

STATS_PERIODS = {"7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90)}
SETTLED = timedelta(hours=24)  # a post's numbers are compared once it is this old


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 3) if whole else None


async def _post_numbers(session, channel_id: int, since):
    """(posted_at, views, forwards, reactions, comments) of the channel's posts since a moment: from the
    posts while they are kept, from their latest checkpoint once they are deleted."""
    posts = (await session.execute(
        select(Post.msg_id, Post.posted_at, Post.views, Post.forwards, Post.reactions, Post.comments)
        .where(Post.channel_id == channel_id, Post.posted_at >= since)
    )).all()
    have = {p.msg_id for p in posts}
    rows = [(p.posted_at, p.views, p.forwards, p.reactions, p.comments) for p in posts]
    checkpoints = (await session.execute(
        select(PostCheckpoint).where(PostCheckpoint.channel_id == channel_id, PostCheckpoint.posted_at >= since)
        .order_by(PostCheckpoint.hours)
    )).scalars().all()
    latest = {}
    for c in checkpoints:
        if c.msg_id not in have:
            latest[c.msg_id] = c  # the last one wins: the most hours
    rows += [(c.posted_at, c.views, c.forwards, c.reactions, c.comments) for c in latest.values()]
    return rows


async def channel_stats(session, channel_id: int, others: list[int], period: str, tz_minutes: int,
                        subscribers_now: int | None = None) -> dict:
    """Subscribers over time, every day's posts and their numbers, the hours that work best, and the
    same numbers for the project's other channels to compare with."""
    span = STATS_PERIODS[period]
    now = utcnow()
    since = now - span
    shift = timedelta(minutes=tz_minutes)

    snaps = (await session.execute(
        select(ChannelSnapshot.at, ChannelSnapshot.subscribers)
        .where(ChannelSnapshot.channel_id == channel_id, ChannelSnapshot.at >= since).order_by(ChannelSnapshot.at)
    )).all()
    # one point an hour (a week) or a day (longer): the last count in it
    step = 3600 if period == "7d" else 86400
    points: dict[int, tuple] = {}
    for at, subs in snaps:
        points[int((at + shift - EPOCH).total_seconds()) // step] = (at, subs)
    subscribers = [{"t": iso(at), "v": subs} for at, subs in points.values()]

    rows = await _post_numbers(session, channel_id, since - SETTLED)
    days: dict[str, dict] = {}
    for i in range(span.days):
        day = (now + shift - timedelta(days=span.days - 1 - i)).date().isoformat()
        days[day] = {"day": day, "posts": 0, "views": 0, "forwards": 0, "engaged": 0, "settled_posts": 0,
                     "settled_views": 0, "subs_delta": None}
    hours = [{"hour": h, "posts": 0, "views": 0} for h in range(24)]
    totals = {"posts": 0, "views": 0, "forwards": 0, "engaged": 0, "settled_posts": 0}
    for posted_at, views, forwards, reactions, comments in rows:
        if posted_at < since:
            continue
        local = posted_at + shift
        d = days.get(local.date().isoformat())
        if d is None:
            continue
        d["posts"] += 1
        totals["posts"] += 1
        if now - posted_at >= SETTLED:  # a fresh post is still gaining views: not in the averages
            d["settled_posts"] += 1
            d["settled_views"] += views
            d["forwards"] += forwards
            d["engaged"] += reactions + comments + forwards
            totals["settled_posts"] += 1
            totals["views"] += views
            totals["forwards"] += forwards
            totals["engaged"] += reactions + comments + forwards
            hours[local.hour]["posts"] += 1
            hours[local.hour]["views"] += views

    # subscribers gained each day: the last count of the day against the last of the day before
    by_day: dict[str, int] = {}
    for at, subs in snaps:
        by_day[(at + shift).date().isoformat()] = subs
    ordered = sorted(by_day.items())
    for (_, before), (day, after) in zip(ordered, ordered[1:]):
        if day in days:
            days[day]["subs_delta"] = after - before

    subs_now = snaps[-1][1] if snaps else subscribers_now
    out_days = []
    for d in days.values():
        avg = d["settled_views"] / d["settled_posts"] if d["settled_posts"] else None
        out_days.append({
            "day": d["day"], "posts": d["posts"], "avg_views": round(avg) if avg is not None else None,
            "er": _pct(d["engaged"], d["settled_views"]), "fr": _pct(d["forwards"], d["settled_views"]),
            "reach": _pct(avg, subs_now) if avg is not None and subs_now else None,
            "subs_delta": d["subs_delta"],
        })
    avg_views = totals["views"] / totals["settled_posts"] if totals["settled_posts"] else None

    # the project's other channels over the same days, to compare with
    bench = None
    if others:
        b = (await session.execute(
            select(func.count(), func.sum(Post.views), func.sum(Post.forwards),
                   func.sum(Post.reactions + Post.comments + Post.forwards),
                   # reach compares channels of any size: each post's views against its channel's subscribers
                   func.avg(case((Channel.subscribers > 0, Post.views * 100.0 / Channel.subscribers))))
            .join(Channel, Channel.id == Post.channel_id)
            .where(Post.channel_id.in_(others), Post.posted_at >= since, Post.posted_at <= now - SETTLED)
        )).one()
        count, views, forwards, engaged = b[0] or 0, b[1] or 0, b[2] or 0, b[3] or 0
        if count:
            bench = {"avg_views": round(views / count), "er": _pct(engaged, views), "fr": _pct(forwards, views),
                     "reach": round(float(b[4]), 3) if b[4] is not None else None, "channels": len(others)}

    first = await session.scalar(select(func.min(ChannelSnapshot.at)).where(ChannelSnapshot.channel_id == channel_id))
    return {
        "period": period,
        "subscribers": subscribers,
        "days": out_days,
        "hours": [{"hour": h["hour"], "posts": h["posts"],
                   "avg_views": round(h["views"] / h["posts"]) if h["posts"] else None} for h in hours],
        "summary": {
            "subscribers": subs_now,
            "subs_delta": (snaps[-1][1] - snaps[0][1]) if len(snaps) > 1 else None,
            "posts": totals["posts"],
            "posts_per_day": round(totals["posts"] / span.days, 1),
            "avg_views": round(avg_views) if avg_views is not None else None,
            "er": _pct(totals["engaged"], totals["views"]),
            "fr": _pct(totals["forwards"], totals["views"]),
            "reach": _pct(avg_views, subs_now) if avg_views is not None and subs_now else None,
        },
        "benchmark": bench,
        "collecting_since": iso(first),
    }
