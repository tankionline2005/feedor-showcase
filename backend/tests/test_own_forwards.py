import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine
from telethon.tl import types

from app.db import SessionLocal, _add_missing_columns, _discount_own_forwards, _has_column, _recount_fr
from app.models import Base, Channel, Post, utcnow
from app.tg.parse import compute_er, compute_fr
from app.worker.collector import Collector
from app.worker.pool import AccountPool, AccountWorker

from test_metrics_refresh import TG_ID, _setup


class ForwardedClient:
    """In Telegram every post has 100 views, no reactions or comments, and 5 forwards."""

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(peer.channel_id, 0)

    async def get_messages(self, peer, ids):
        return [types.Message(id=i, peer_id=types.PeerChannel(TG_ID), date=datetime.now(timezone.utc), message="",
                              views=100, forwards=5) for i in ids]


async def test_our_repost_is_not_counted_as_a_forward(monkeypatch):
    acc, channel_id = await _setup(monkeypatch)
    async with SessionLocal() as s:
        # msg 1: our account forwarded it to the group; msg 2: a text copy (no forward); msg 3: not reposted
        await s.execute(update(Post).where(Post.channel_id == channel_id, Post.msg_id == 1)
                        .values(repost_status="done", repost_copy=False))
        await s.execute(update(Post).where(Post.channel_id == channel_id, Post.msg_id == 2)
                        .values(repost_status="done", repost_copy=True))
        await s.execute(update(Post).where(Post.channel_id == channel_id)
                        .values(metrics_updated_at=utcnow() - timedelta(hours=2)))
        await s.commit()
    pool = AccountPool()
    pool.workers[acc.id] = AccountWorker(acc, ForwardedClient())
    await Collector(pool).metrics_step()

    async with SessionLocal() as s:
        rows = {m: (f, own, er, fr) for m, f, own, er, fr in (await s.execute(
            select(Post.msg_id, Post.forwards, Post.forwards_own, Post.er, Post.fr).where(Post.channel_id == channel_id)
        )).all()}
    assert rows[1] == (4, 1, compute_er(100, 0, 0, 4), 4.0)  # 5 in Telegram, one of them ours
    assert rows[2] == (5, 0, compute_er(100, 0, 0, 5), 5.0)
    assert rows[3] == (5, 0, compute_er(100, 0, 0, 5), 5.0)


def test_posts_counted_before_are_corrected_once(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'old.db').as_posix()}")
        now = utcnow()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE posts DROP COLUMN forwards_own"))  # the schema as it was
            await conn.execute(Channel.__table__.insert().values(id=1, link="@old", status="active"))
            later, earlier = now - timedelta(hours=1), now - timedelta(hours=3)
            for mid, status, copy, reposted, read, forwards in (
                (1, "done", False, earlier, later, 5),  # forwarded, read after: ours is in the 5
                (2, "done", False, later, earlier, 5),  # forwarded, but last read before that
                (3, "done", True, earlier, later, 5),   # a text copy
                (4, "skipped", None, None, later, 5),   # history, never reposted
                (5, "done", False, earlier, later, 0),  # forwarded, the count hidden: stays 0
            ):
                await conn.execute(text(
                    "INSERT INTO posts (channel_id, msg_id, posted_at, text, html, media_count, has_preview, views, "
                    "forwards, reactions, comments, er, fr, metrics_updated_at, repost_status, reposted_at, "
                    "repost_copy, created_at) VALUES (1, :mid, :posted, '', '', 0, 0, 200, :forwards, 6, 2, :er, 37.5, "
                    ":read, :status, :reposted, :copy, :posted)"
                ), {"mid": mid, "posted": earlier, "forwards": forwards, "er": compute_er(200, 6, 2, forwards),
                    "status": status, "copy": copy, "reposted": reposted, "read": read})
        for _ in range(2):  # what init_db does; the second start changes nothing
            async with engine.begin() as conn:
                counted_own = await conn.run_sync(lambda c: not _has_column(c, "posts", "forwards_own"))
                await conn.run_sync(_add_missing_columns)
                if counted_own:
                    await conn.run_sync(_discount_own_forwards)
                await conn.run_sync(_recount_fr)
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT msg_id, forwards, forwards_own, er FROM posts ORDER BY msg_id"))).all()
            frs = (await conn.execute(text("SELECT fr FROM posts ORDER BY msg_id"))).scalars().all()
            # recounted once: a post's FR written later by the collector is not touched again
            await conn.execute(text("UPDATE posts SET fr = 99 WHERE msg_id = 1"))
            await conn.commit()
        async with engine.begin() as conn:
            await conn.run_sync(_recount_fr)
            assert (await conn.execute(text("SELECT fr FROM posts WHERE msg_id = 1"))).scalar() == 99
        await engine.dispose()
        return rows, frs

    rows, frs = asyncio.run(scenario())
    assert frs == [compute_fr(200, 4), compute_fr(200, 5), compute_fr(200, 5), compute_fr(200, 5), 0.0]
    assert [(m, f, own) for m, f, own, _ in rows] == [(1, 4, 1), (2, 5, 0), (3, 5, 0), (4, 5, 0), (5, 0, 1)]
    assert rows[0][3] == compute_er(200, 6, 2, 4) and rows[1][3] == compute_er(200, 6, 2, 5)
