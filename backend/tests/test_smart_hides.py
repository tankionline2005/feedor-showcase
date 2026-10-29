"""What the project's smart feed hid is not there for the topics — the feed on or off now."""

from datetime import timedelta

from sqlalchemy import select

from app import topics
from app.db import SessionLocal
from app.models import Channel, Post, Project, SmartFeed, SmartVerdict, utcnow

from test_topics import _post, _setup, bot, model  # noqa: F401  (fixtures)
from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)


async def _feed(project_id: int, verdicts: dict[int, tuple[bool, bool | None]], enabled: bool, cursor: int) -> None:
    """The project's smart feed with its words on posts: post id → (the AI kept it, the person's override)."""
    async with SessionLocal() as s:
        feed = SmartFeed(project_id=project_id, member_id=None, prompt="p", enabled=enabled, cursor_post_id=cursor)
        s.add(feed)
        await s.flush()
        for post_id, (match, override) in verdicts.items():
            s.add(SmartVerdict(feed_id=feed.id, post_id=post_id, match=match, override=override, score=50))
        await s.commit()


async def _run_topics(project_id: int, account) -> None:
    async with SessionLocal() as s:
        project = await s.get(Project, project_id)
    await topics.run_project(project, account, force=True)


def test_the_topics_leave_out_what_the_smart_feed_hid(ctx, model, bot):
    app, client = ctx
    owner = sign_in(app, client, 9831)
    assert owner.post("/api/channels", json={"links": "@uc_smart_hides"}).json()["added"]
    run(client, _activate, "@uc_smart_hides", 98311, 0)
    project_id, channel_id, account = run(client, _setup, 9831, "@uc_smart_hides")
    kept, hidden, shown_by_hand = (
        run(client, _post, channel_id, i, text) for i, text in enumerate(
            ["kept news", "an ad", "an ad the owner showed"], 1))
    # the feed is off now: what it hid then stays hidden
    run(client, _feed, project_id, {kept: (True, None), hidden: (False, None), shown_by_hand: (False, True)},
        False, 0)

    run(client, _run_topics, project_id, account)
    assert [p["text"] for p in model[0]["posts"]] == ["kept news", "an ad the owner showed"]


def test_while_the_feed_is_on_the_topics_wait_for_its_word(ctx, model, bot):
    app, client = ctx
    owner = sign_in(app, client, 9832)
    assert owner.post("/api/channels", json={"links": "@uc_smart_waits"}).json()["added"]
    run(client, _activate, "@uc_smart_waits", 98321, 0)
    project_id, channel_id, account = run(client, _setup, 9832, "@uc_smart_waits")
    read = run(client, _post, channel_id, 1, "read by the feed")
    unread = run(client, _post, channel_id, 2, "not read by the feed yet")
    run(client, _feed, project_id, {read: (True, None)}, True, read)

    run(client, _run_topics, project_id, account)
    assert [p["text"] for p in model[0]["posts"]] == ["read by the feed"]

    # the feed stands still too long: the post goes on without its word
    async def age(post_id: int) -> None:
        async with SessionLocal() as s:
            (await s.get(Post, post_id)).created_at = utcnow() - topics.SMART_WAIT - timedelta(minutes=1)
            await s.commit()

    run(client, age, unread)
    run(client, _run_topics, project_id, account)
    assert [p["text"] for p in model[1]["posts"]] == ["not read by the feed yet"]


def test_the_personal_feeds_staff_had_are_removed(ctx):
    from app.db import _drop_personal_feeds, engine
    from app.models import Member

    app, client = ctx
    owner = sign_in(app, client, 9834)
    project_id = owner.get("/api/auth/me").json()["project"]["id"]

    async def made() -> tuple[int, int]:
        async with SessionLocal() as s:
            member = Member(project_id=project_id, login="old.personal", password_hash="x")
            s.add(member)
            await s.flush()
            project_feed = SmartFeed(project_id=project_id, member_id=None, prompt="p")
            personal = SmartFeed(project_id=project_id, member_id=member.id, prompt="mine")
            s.add_all([project_feed, personal])
            await s.commit()
            return project_feed.id, personal.id

    async def upgrade() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_personal_feeds)

    async def left(ids) -> list[int]:
        async with SessionLocal() as s:
            return list((await s.execute(select(SmartFeed.id).where(SmartFeed.id.in_(ids)))).scalars())

    ids = run(client, made)
    run(client, upgrade)
    assert run(client, left, ids) == [ids[0]]  # the project's stays
