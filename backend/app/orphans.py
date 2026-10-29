"""Channels nobody needs any more are left — but not at once.

When the last user stops tracking a channel the admin does not hold, it is marked orphaned. If
nobody adds it back within a grace period, the worker leaves it and deletes its posts. The wait
spares a re-join (the riskiest thing for an account) when someone removes a channel and soon
adds it again; a private channel waits much longer, since getting back in may need an invite
or an approval that will not come again.
"""

import logging
from datetime import timedelta

from sqlalchemy import func, select

from .db import SessionLocal
from .models import Channel, ProjectChannel, utcnow

log = logging.getLogger(__name__)

GRACE = timedelta(days=1)
PRIVATE_GRACE = timedelta(days=30)


async def subscribers(db, channel_id: int) -> int:
    return await db.scalar(select(func.count()).where(ProjectChannel.channel_id == channel_id)) or 0


async def mark_if_orphaned(db, ch: Channel) -> None:
    """Called when a project stopped tracking `ch` (its row is already gone from `db`)."""
    if ch.held or await subscribers(db, ch.id):
        return
    if ch.tg_id is None:
        await db.delete(ch)  # never joined: nothing to leave, nothing to keep
        return
    ch.orphaned_at = utcnow()


async def orphans_step() -> None:
    now = utcnow()
    async with SessionLocal() as s:
        channels = (await s.execute(
            select(Channel).where(Channel.orphaned_at.is_not(None), Channel.status != "deleting")
        )).scalars().all()
        for ch in channels:
            if ch.held or await subscribers(s, ch.id):
                ch.orphaned_at = None  # wanted again
                continue
            if now - ch.orphaned_at < (PRIVATE_GRACE if ch.invite_hash else GRACE):
                continue
            log.info("channel %s (%s): nobody tracks it, leaving", ch.id, ch.link)
            if ch.tg_id is None:
                await s.delete(ch)
            else:
                ch.status = "deleting"  # the collector leaves it and deletes it with its posts
        await s.commit()
