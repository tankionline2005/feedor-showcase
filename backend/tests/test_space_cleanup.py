from datetime import timedelta

from sqlalchemy import delete, select

from app import settings_store
from app.db import SessionLocal, engine, init_db
from app.models import Channel, Favorite, Post, Project, User, utcnow
from app.worker import maintenance

MB = 1024 * 1024


async def _seed():
    """Four posts older than anything else in the shared test database, plus one fresh."""
    await engine.dispose()
    await init_db()
    async with SessionLocal() as s:
        old = await s.scalar(select(Channel.id).where(Channel.username == "space_chan"))
        if old:
            await s.execute(delete(Post).where(Post.channel_id == old))
            await s.execute(delete(Channel).where(Channel.id == old))
        ch = Channel(link="@space_chan", username="space_chan", status="active")
        s.add(ch)
        await s.flush()
        ancient = utcnow() - timedelta(days=5000)
        s.add_all([Post(channel_id=ch.id, msg_id=i, posted_at=ancient + timedelta(days=i)) for i in range(1, 5)])
        s.add(Post(channel_id=ch.id, msg_id=99, posted_at=utcnow() - timedelta(hours=2)))
        await s.commit()
        await settings_store.update(s, {"cleanup_free_mb": 1024})
        return ch.id


async def _left(channel_id):
    async with SessionLocal() as s:
        return sorted((await s.execute(select(Post.msg_id).where(Post.channel_id == channel_id))).scalars())


def _fake_disk(monkeypatch, free_mb: int, freed_per_post_mb: int):
    state = {"free": free_mb * MB}
    monkeypatch.setattr(maintenance.storage, "disk", lambda: (40_000 * MB, 0, state["free"]))

    def delete_previews(ids):
        state["free"] += len(ids) * freed_per_post_mb * MB

    monkeypatch.setattr(maintenance, "delete_previews", delete_previews)
    notes: list[str] = []
    monkeypatch.setattr(maintenance.notifier, "emit", lambda key, text, **kw: notes.append(key))
    monkeypatch.setattr(maintenance, "_cleanup_paused_until", 0.0)
    monkeypatch.setattr(maintenance, "CLEANUP_BATCH", 2)
    return notes


async def test_oldest_posts_go_until_enough_space(monkeypatch):
    channel_id = await _seed()
    # 1000 MB free, threshold 1024 MB, each deleted post frees 20 MB of previews: one batch of two is enough
    notes = _fake_disk(monkeypatch, free_mb=1000, freed_per_post_mb=20)

    await maintenance.space_step()

    assert await _left(channel_id) == [3, 4, 99]  # the two oldest are gone
    assert notes == ["disk-cleanup"]


async def test_futile_cleanup_pauses_and_keeps_the_last_day(monkeypatch):
    channel_id = await _seed()
    notes = _fake_disk(monkeypatch, free_mb=100, freed_per_post_mb=0)  # the space is taken by something else
    monkeypatch.setattr(maintenance, "CLEANUP_MAX_PER_RUN", 4)

    await maintenance.space_step()
    assert await _left(channel_id) == [99]  # the fresh post stays even though space is still short
    assert notes == ["disk-cleanup-futile"]

    # paused: nothing more is deleted for a while
    async with SessionLocal() as s:
        s.add(Post(channel_id=channel_id, msg_id=5, posted_at=utcnow() - timedelta(days=4000)))
        await s.commit()
    await maintenance.space_step()
    assert await _left(channel_id) == [5, 99]


async def _star(channel_id: int, msg_id: int) -> None:
    async with SessionLocal() as s:
        post = await s.scalar(select(Post).where(Post.channel_id == channel_id, Post.msg_id == msg_id))
        user = await s.scalar(select(User).where(User.tg_user_id == 990001))
        if user is None:
            user = User(tg_user_id=990001)
            s.add(user)
            await s.flush()
            s.add(Project(owner_id=user.id))
            await s.flush()
        project = await s.scalar(select(Project).where(Project.owner_id == user.id))
        s.add(Favorite(project_id=project.id, person=f"u{user.id}", post_id=post.id))
        await s.commit()


async def test_starred_posts_stay_when_space_runs_low(monkeypatch):
    channel_id = await _seed()
    await _star(channel_id, 1)
    notes = _fake_disk(monkeypatch, free_mb=1000, freed_per_post_mb=20)

    await maintenance.space_step()

    assert await _left(channel_id) == [1, 4, 99]  # the oldest is starred: the next two go instead
    assert notes == ["disk-cleanup"]
