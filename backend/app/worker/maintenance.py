"""Deletes posts older than the retention period or, when disk space runs low, the oldest ones."""

import asyncio
import logging
import time
from datetime import timedelta
from functools import partial

from sqlalchemy import delete, select

from .. import ai, billing, chat_posts, geo, logins, publications, rubrics, settings_store, storage
from ..channel_stats import checkpoints_step
from ..covered import covered_step
from ..db import SessionLocal
from ..models import ChatPostSource, Favorite, Post, Topic, TopicPost, utcnow
from ..notify import notifier, plural
from ..ocr import ocr_step
from ..orphans import orphans_step
from ..originals import bot_in_group_step
from ..perf import INTERVAL as PERF_INTERVAL, perf_step
from ..services import delete_previews
from ..smart_feed import smart_feed_step
from ..topics import topics_step
from ..triggers import triggers_step
from .util import forever

log = logging.getLogger(__name__)


async def staff_step() -> None:
    # not the function itself: forever() takes a number a step returns for the delay before the next run
    await billing.limits_step()


async def retention_step() -> None:
    async with SessionLocal() as s:
        cfg = await settings_store.get_all(s)
        cutoff = utcnow() - timedelta(days=int(cfg["retention_days"]))
        # a topic lives as long as posts do, counted from its last update; its posts stay with it.
        # A hidden one stays, for the model to learn from its reason, but lets its posts go
        await s.execute(delete(Topic).where(Topic.updated_at < cutoff, Topic.hidden_at.is_(None)))
        await publications.forget_stale(s, cutoff)
        await s.execute(delete(TopicPost).where(TopicPost.topic_id.in_(
            select(Topic.id).where(Topic.updated_at < cutoff, Topic.hidden_at.is_not(None)))))
        await s.commit()
        # a live topic's posts stay, the sources of the posts the AI wrote in chats (their pictures), and what
        # someone starred
        in_topics = select(TopicPost.post_id).union(select(ChatPostSource.post_id), select(Favorite.post_id))
        total = 0
        while True:
            ids = (await s.execute(
                select(Post.id).where(Post.posted_at < cutoff, Post.id.not_in(in_topics)).limit(2000)
            )).scalars().all()
            if not ids:
                break
            await s.execute(delete(Post).where(Post.id.in_(ids)))
            await s.commit()
            delete_previews(ids)
            total += len(ids)
    if total:
        log.info("retention: removed %d posts older than %s", total, cutoff)


# Never removed for space: if the disk fills up for other reasons, wiping the fresh posts wouldn't help.
KEEP_RECENT = timedelta(hours=24)
CLEANUP_BATCH = 500
CLEANUP_MAX_PER_RUN = 5000
# Deleting posts frees disk mostly through their previews (PostgreSQL keeps freed pages for reuse
# instead of returning them). A run that freed less than this is futile: pause and say so.
FUTILE_FREED = 1024 * 1024
FUTILE_PAUSE = 6 * 3600

_cleanup_paused_until = 0.0


async def space_step() -> None:
    """Removes the oldest posts while free disk space is below the threshold from settings."""
    global _cleanup_paused_until
    if time.monotonic() < _cleanup_paused_until:
        return
    async with SessionLocal() as s:
        need = int((await settings_store.get_all(s))["cleanup_free_mb"]) * 1024 * 1024
        start_free = free = storage.disk()[2]
        if free >= need:
            return
        cutoff = utcnow() - KEEP_RECENT
        # what someone starred and the sources of the posts the AI wrote (their pictures) stay, as with retention
        keep = select(ChatPostSource.post_id).union(select(Favorite.post_id))
        removed = 0
        while free < need and removed < CLEANUP_MAX_PER_RUN:
            ids = (
                await s.execute(select(Post.id).where(Post.posted_at < cutoff, Post.id.not_in(keep))
                                .order_by(Post.posted_at).limit(CLEANUP_BATCH))
            ).scalars().all()
            if not ids:
                break
            await s.execute(delete(Post).where(Post.id.in_(ids)))
            await s.commit()
            delete_previews(ids)
            removed += len(ids)
            free = storage.disk()[2]
    if not removed:
        return
    log.warning("low disk: removed %d oldest posts, free %s -> %s", removed, start_free, free)
    words = plural(removed, "старый пост", "старых поста", "старых постов")
    if free - start_free < FUTILE_FREED:
        _cleanup_paused_until = time.monotonic() + FUTILE_PAUSE
        notifier.emit(
            "disk-cleanup-futile",
            f"<b>Место занимают не посты</b>\nСвободно {storage.human(free)} — меньше порога "
            f"{storage.human(need)}. Удалено {removed} {words}, но место почти не освободилось: "
            "его занимает что-то другое на сервере. Удаление постов приостановлено на 6 часов.",
        )
    else:
        notifier.emit(
            "disk-cleanup",
            f"<b>Удалены старые посты</b>\nСвободного места было меньше {storage.human(need)} — "
            f"удалено {removed} {words}. Сейчас свободно {storage.human(free)}.",
        )


_disk_level = "ok"
_LEVEL_ORDER = {"ok": 0, "low": 1, "critical": 2}


async def disk_step() -> None:
    """Alerts when free disk space drops below 15%, and again below 5%."""
    global _disk_level
    total, used, free = storage.disk()
    current = storage.level(free, total)
    if _LEVEL_ORDER[current] > _LEVEL_ORDER[_disk_level]:
        share = round(free / total * 100) if total else 0
        title = "Место на сервере почти закончилось" if current == "critical" else "На сервере заканчивается место"
        notifier.emit(
            f"disk-{current}",
            f"<b>{title}</b>\nСвободно {storage.human(free)} из {storage.human(total)} ({share}%). "
            "Уменьшите срок хранения постов в настройках или освободите место на сервере.",
        )
        log.warning("disk space %s: %s free of %s", current, free, total)
    _disk_level = current


async def run() -> None:
    await asyncio.gather(
        forever("retention", retention_step, 3600),
        forever("disk space", disk_step, 600),
        forever("space cleanup", space_step, 600),
        forever("orphaned channels", orphans_step, 600),
        forever("plans' limits", staff_step, 300),
        forever("plans' ends and reminders", billing.lifecycle_step, 3600),
        forever("the service's OpenRouter balance", ai.watch_shared, 1800),
        forever("text on pictures", ocr_step, 10),
        forever("bot in the aggregator group", bot_in_group_step, 600),
        forever("server load", perf_step, PERF_INTERVAL),
        forever("project channel checkpoints", checkpoints_step, 300),
        # the AI's steps start each project's work side by side and do not wait for it (lanes.py)
        forever("smart feeds", partial(smart_feed_step, wait=False), 10),
        forever("trigger words", triggers_step, 3),
        forever("topics", partial(topics_step, wait=False), 60),
        forever("covered topics", partial(covered_step, wait=False), 30),
        forever("topped-up OpenRouter balances", ai.resume_paid, 600),
        forever("old sign-ins", logins.forget_old, 3600),
        forever("places of IP addresses", geo.update, 6 * 3600),
        forever("scheduled posts", chat_posts.publish_due, 20),
        forever("rubrics", partial(rubrics.rubrics_step, wait=False), 60),
    )
