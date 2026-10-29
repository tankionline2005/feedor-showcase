"""FR+: a post's FR relative to what is usual for its channel.

Channel.fr_base is the average FR of the channel's last 100 posts older than a day (younger
ones are still gaining views and would drag the average down). FR+ = post FR / fr_base is
computed at read time, so it stays consistent when the base moves. ×1 is a typical post for
that channel, ×3 a post that did three times better than usual — comparable across channels
of any size.
"""

from datetime import timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Channel, Post, utcnow

BASE_POSTS = 100
SETTLED_AGE = timedelta(hours=24)


def fr_plus(fr: float, base: float | None) -> float:
    return round(fr / base, 2) if base else 0.0


def fr_plus_sql():
    """FR+ as an SQL expression; needs Channel joined to Post."""
    return case((Channel.fr_base > 0, Post.fr / Channel.fr_base), else_=0.0)


async def refresh_fr_bases(session: AsyncSession) -> None:
    cutoff = utcnow() - SETTLED_AGE
    channel_ids = (await session.execute(select(Channel.id).where(Channel.status != "deleting"))).scalars().all()
    for channel_id in channel_ids:
        recent = (
            select(Post.fr)
            .where(Post.channel_id == channel_id, Post.posted_at < cutoff)
            .order_by(Post.posted_at.desc())
            .limit(BASE_POSTS)
            .subquery()
        )
        avg = await session.scalar(select(func.avg(recent.c.fr)))
        base = round(float(avg), 4) if avg else None
        await session.execute(update(Channel).where(Channel.id == channel_id).values(fr_base=base))
    await session.commit()
