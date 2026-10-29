from datetime import timedelta

from sqlalchemy import delete, select

from app.channel_stats import checkpoints_step
from app.db import SessionLocal, engine, init_db
from app.models import Channel, Post, PostCheckpoint, Project, ProjectChannel, User, utcnow


async def test_project_channel_posts_get_checkpoints_that_outlive_them():
    await engine.dispose()
    await init_db()
    now = utcnow()
    async with SessionLocal() as s:
        await s.execute(delete(Channel).where(Channel.link.in_(("@cs_own", "@cs_other"))))
        user = User(tg_user_id=97001, first_name="cs")
        s.add(user)
        await s.flush()
        project = Project(owner_id=user.id, plan="pro")
        own, other = Channel(link="@cs_own", status="active"), Channel(link="@cs_other", status="active")
        s.add_all([project, own, other])
        await s.flush()
        s.add_all([ProjectChannel(project_id=project.id, channel_id=own.id, own=True),
                   ProjectChannel(project_id=project.id, channel_id=other.id)])
        for ch, mid, age, views in [(own, 1, timedelta(minutes=70), 100), (own, 2, timedelta(minutes=20), 5),
                                    (own, 3, timedelta(hours=6, minutes=30), 900), (own, 4, timedelta(hours=10), 1),
                                    (other, 1, timedelta(minutes=70), 100)]:
            s.add(Post(channel_id=ch.id, msg_id=mid, posted_at=now - age, views=views, forwards=1))
        await s.commit()
        own_id = own.id

    await checkpoints_step()
    await checkpoints_step()  # once per post and hour

    async with SessionLocal() as s:
        rows = (await s.execute(select(PostCheckpoint.channel_id, PostCheckpoint.msg_id, PostCheckpoint.hours,
                                       PostCheckpoint.views).where(PostCheckpoint.channel_id == own_id)
                                .order_by(PostCheckpoint.msg_id, PostCheckpoint.hours))).all()
        # message 3 is past 1 hour too, but far too late for that one; message 4 missed its 6-hour window
        assert rows == [(own_id, 1, 1, 100), (own_id, 3, 6, 900)]
        await s.execute(delete(Post).where(Post.channel_id == own_id))
        await s.commit()
        assert await s.scalar(select(PostCheckpoint.id).where(PostCheckpoint.channel_id == own_id).limit(1))
