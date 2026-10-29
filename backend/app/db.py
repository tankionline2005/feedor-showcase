import asyncio
import logging
from pathlib import Path

from sqlalchemy import DateTime, bindparam, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import settings
from .models import Base, Project, utcnow

log = logging.getLogger(__name__)

_url = make_url(settings.database_url)
_is_sqlite = _url.get_backend_name() == "sqlite"

if _is_sqlite and _url.database:
    Path(_url.database).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=not _is_sqlite,
    connect_args={"timeout": 30} if _is_sqlite else {},
)

if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def _add_missing_columns(conn) -> None:
    """Minimal schema upgrade: adds columns that exist in the models but not in the database.

    create_all() only creates missing tables, so without this a new model field would break
    an already deployed database. Foreign keys are not added this way; code nulls such
    references itself.
    """
    insp = inspect(conn)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(dialect=conn.dialect)}"
            default = col.default.arg if col.default is not None and col.default.is_scalar else None
            if default is not None:
                literal = ("TRUE" if default else "FALSE") if isinstance(default, bool) else repr(default)
                ddl += f" DEFAULT {literal} NOT NULL"
            elif not col.nullable:
                log.error("cannot add NOT NULL column %s.%s without a default", table.name, col.name)
                continue
            log.warning("schema upgrade: %s", ddl)
            conn.execute(text(ddl))


def _move_to_projects(conn) -> None:
    """One-time upgrade: a user's plan, API key and channels become their first project."""
    insp = inspect(conn)
    if "plan" not in {c["name"] for c in insp.get_columns("users")}:
        return
    log.warning("schema upgrade: users' plans, API keys and channels move to their first projects")
    old_links = insp.has_table("user_channels")
    users = conn.execute(
        text("SELECT id, plan, plan_expires_at, api_key, created_at FROM users ORDER BY id")
        .columns(plan_expires_at=DateTime, created_at=DateTime)  # SQLite hands dates back as text otherwise
    ).all()
    for user_id, plan, expires_at, api_key, created_at in users:
        if conn.execute(text("SELECT 1 FROM projects WHERE owner_id = :u"), {"u": user_id}).first():
            continue
        conn.execute(text("UPDATE users SET api_key = NULL WHERE id = :u"), {"u": user_id})
        project_id = conn.execute(Project.__table__.insert().values(
            owner_id=user_id, plan=plan or "", plan_expires_at=expires_at, api_key=api_key,
            created_at=created_at or utcnow(),
        )).inserted_primary_key[0]
        ids = {"p": project_id, "u": user_id}
        if old_links:
            conn.execute(text(
                "INSERT INTO project_channels (project_id, channel_id, own, created_at) "
                "SELECT :p, channel_id, FALSE, created_at FROM user_channels WHERE user_id = :u"
            ), ids)
        conn.execute(text("UPDATE users SET project_id = :p WHERE id = :u"), ids)
        conn.execute(text("UPDATE promo_codes SET project_id = :p WHERE used_by = :u"), ids)
    if old_links:
        conn.execute(text("DROP TABLE user_channels"))
    conn.execute(text("ALTER TABLE users DROP COLUMN plan"))
    conn.execute(text("ALTER TABLE users DROP COLUMN plan_expires_at"))
    if not _is_sqlite:  # SQLite cannot drop a UNIQUE column; it stays there empty
        conn.execute(text("ALTER TABLE users DROP COLUMN api_key"))


def _has_column(conn, table: str, column: str) -> bool:
    insp = inspect(conn)
    return insp.has_table(table) and column in {c["name"] for c in insp.get_columns(table)}


def _discount_own_forwards(conn) -> None:
    """One-time upgrade: posts our account forwarded to the aggregator group had that forward in
    their count (and ER) once their numbers were read after it; take it off."""
    log.warning("schema upgrade: our own reposts are taken off the forwards of the posts")
    less = "CASE WHEN forwards > 0 THEN forwards - 1 ELSE 0 END"
    conn.execute(text(
        f"UPDATE posts SET forwards_own = 1, forwards = {less}, "
        f"er = CASE WHEN views > 0 THEN ROUND(CAST((reactions + comments + {less}) * 100.0 / views AS NUMERIC), 3) "
        "ELSE er END "
        "WHERE repost_status = 'done' AND COALESCE(repost_copy, FALSE) = FALSE "
        "AND reposted_at IS NOT NULL AND metrics_updated_at > reposted_at"
    ))


FR_IS_FORWARDS = "schema_fr_is_forwards"  # a settings row: FR was recounted as forwards / views


def _recount_fr(conn) -> None:
    """One-time upgrade: FR was views / subscribers; it is forwards / views now.
    Every post is recounted; channels' FR+ bases go empty until the worker counts them anew."""
    if conn.execute(text("SELECT 1 FROM settings WHERE key = :k"), {"k": FR_IS_FORWARDS}).first():
        return
    log.warning("schema upgrade: FR of every post is recounted as forwards / views")
    conn.execute(text(
        "UPDATE posts SET fr = CASE WHEN views > 0 "
        "THEN ROUND(CAST(forwards * 100.0 / views AS NUMERIC), 3) ELSE 0 END"
    ))
    conn.execute(text("UPDATE channels SET fr_base = NULL"))
    conn.execute(text("INSERT INTO settings (key, value) VALUES (:k, 'true')"), {"k": FR_IS_FORWARDS})


# the default criteria before they were written in Russian: whoever kept one unchanged gets the new one
OLD_DEFAULT_PROMPTS = {
    "smart_feeds": (
        "Show posts with real news or useful information on the topics of my channels: announcements, launches, "
        "deals, numbers, research, notable opinions and analysis.\n"
        "Hide ads and sponsored posts, giveaways and contests, promotion of other channels, greetings, polls "
        "without substance and posts that say nothing new."
    ),
    "topic_states": (
        "A topic is worth it when it is news: an event, a launch, a deal, a decision, an incident, a number "
        "that changes something, or a topic several channels discuss at once.\n"
        "Not topics: ads and sponsored posts, giveaways, channel self-promotion, greetings, memes, polls and "
        "posts with no facts."
    ),
}


def _new_default_prompts(conn) -> None:
    """The AI's default criteria changed: a project (or a staff member's feed) that kept the old default as it
    was gets the new one; a prompt the person wrote stays theirs."""
    from . import smart_feed, topics

    new = {"smart_feeds": smart_feed.DEFAULT_PROMPT, "topic_states": topics.DEFAULT_PROMPT}
    for table, old in OLD_DEFAULT_PROMPTS.items():
        if table not in new or not _has_column(conn, table, "prompt"):
            continue
        done = conn.execute(text(f"UPDATE {table} SET prompt = :new WHERE prompt = :old"),
                            {"new": new[table], "old": old}).rowcount
        if done:
            log.warning("schema upgrade: %s %s got the new default criteria", done, table)


def _groups_to_projects(conn) -> None:
    """One-time upgrade: the groups («Группы проекта») were the owner's, for all their projects; now each project has its own. Every
    group an owner had goes into each of their projects, so nothing they did stops working."""
    if not inspect(conn).has_table("user_groups"):
        return
    copied = conn.execute(text(
        "INSERT INTO project_groups (project_id, chat_id, title, username, created_at) "
        "SELECT p.id, g.chat_id, g.title, g.username, g.created_at FROM user_groups g "
        "JOIN projects p ON p.owner_id = g.user_id"
    )).rowcount
    if copied:
        log.warning("schema upgrade: owners' groups copied into their projects (%s)", copied)


def _drop_personal_feeds(conn) -> None:
    """Staff no longer have smart feeds of their own (they see the project's, as it is): the ones there were go,
    with their verdicts."""
    if not _has_column(conn, "smart_feeds", "member_id"):
        return
    gone = conn.execute(text(
        "DELETE FROM smart_verdicts WHERE feed_id IN (SELECT id FROM smart_feeds WHERE member_id IS NOT NULL)"
    )).rowcount
    feeds = conn.execute(text("DELETE FROM smart_feeds WHERE member_id IS NOT NULL")).rowcount
    if feeds:
        log.warning("schema upgrade: %s personal smart feeds of staff removed (%s verdicts)", feeds, gone)


def _drop_advice(conn) -> None:
    """«Советы» are gone from the site («Публикации» in their place): their tables, and the marks of what was
    seen of them, go."""
    if not inspect(conn).has_table("advice"):
        return
    conn.execute(text("DROP TABLE advice"))
    if inspect(conn).has_table("advice_states"):
        conn.execute(text("DROP TABLE advice_states"))
    conn.execute(text("DELETE FROM seen_marks WHERE kind = 'advice'"))
    log.warning("schema upgrade: the advice tables dropped")


def _drop_gone_columns(conn) -> None:
    """Settings the site no longer has: «Надёжность» (the AI's marks of how sure a topic is, «Только надёжные
    новости») and autoposting's own «Минимальная важность» (the topics' posts have theirs) — their columns go."""
    for table, column in (("topics", "trust"), ("topics", "trust_note"),
                          ("publication_settings", "autopost_trusted_only"),
                          ("publication_settings", "min_importance_autopost")):
        if _has_column(conn, table, column):
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
            log.warning("schema upgrade: %s.%s dropped", table, column)


def _chat_posts_in_projects(conn) -> None:
    """One-time upgrade with «Публикации»: a chat post knows its project (a post written for a topic has no chat),
    and who published or scheduled the ones that went before."""
    done = conn.execute(text(
        "UPDATE chat_posts SET project_id = (SELECT t.project_id FROM chat_threads t WHERE t.id = chat_posts.thread_id)"
        " WHERE project_id IS NULL")).rowcount
    for column, when in (("published_by", "published_at"), ("scheduled_by", "scheduled_at")):
        conn.execute(text(
            f"UPDATE chat_posts SET {column} = COALESCE((SELECT m.login FROM chat_threads t JOIN members m "
            f"ON m.id = t.member_id WHERE t.id = chat_posts.thread_id), 'владелец') "
            f"WHERE {when} IS NOT NULL AND {column} IS NULL"))
    if not _is_sqlite:  # SQLite cannot; a new database has it right from the model
        conn.execute(text("ALTER TABLE chat_posts ALTER COLUMN thread_id DROP NOT NULL"))
    log.warning("schema upgrade: chat posts belong to their projects (%s)", done)


UNSUPPORTED_MARKED = "schema_unsupported_marked"  # a settings row: empty posts were marked once


def _mark_unsupported(conn) -> None:
    """One-time upgrade: posts that came empty (no text, no media) are Telegram's newer kinds our
    protocol layer cannot read; they are marked so (see tg/parse.py), and the site says so."""
    if conn.execute(text("SELECT 1 FROM settings WHERE key = :k"), {"k": UNSUPPORTED_MARKED}).first():
        return
    log.warning("schema upgrade: empty posts are marked as a kind Telegram sends us empty")
    conn.execute(text(
        "UPDATE posts SET media_type = 'unsupported' "
        "WHERE media_type IS NULL AND COALESCE(media_count, 0) = 0 AND COALESCE(text, '') = ''"
    ))
    conn.execute(text("INSERT INTO settings (key, value) VALUES (:k, 'true')"), {"k": UNSUPPORTED_MARKED})


def _mark_history(conn) -> None:
    """One-time upgrade with Post.history: the posts brought a day and more after they came out (channels'
    histories) are marked, so the AI does not take them for news."""
    late = ("created_at > datetime(posted_at, '+1 day')" if _is_sqlite
            else "created_at > posted_at + interval '1 day'")
    log.warning("schema upgrade: channels' histories are marked")
    conn.execute(text(f"UPDATE posts SET history = TRUE WHERE {late}"))


PLANS_MOVED = "schema_plans_moved"  # a settings row: the projects moved to the paid plans once (billing.py)
# dev:service:plan from the move on: these owners' projects of these channels (ILIKE patterns)
DEV_PROJECTS = (("tankionline2005", "%Шифровальн%"), ("kindergarten4631", "%Фактур%"))


def _move_to_plans(conn) -> None:
    """One-time upgrade to the paid plans: every project gets «Free Trial» (no end till the payments go live) but
    the two of dev:service:plan; everyone has had their trial; only dev:service:plan keeps its own OpenRouter.
    The channels, staff and trigger words over the trial's limits are paused by the worker (billing.apply_limits)."""
    if conn.execute(text("SELECT 1 FROM settings WHERE key = :k"), {"k": PLANS_MOVED}).first():
        return
    now = utcnow()
    log.warning("schema upgrade: every project moves to Free Trial; dev:service:plan for its own")
    conn.execute(text(
        "UPDATE projects SET plan = 'trial', plan_expires_at = NULL, plan_started_at = :now, plan_paid = 0, "
        "next_plan = NULL, ai_period_start = :now, ai_used = 0, ai_extra_used = 0, reminded_days = NULL"), {"now": now})
    for username, channel in DEV_PROJECTS:
        moved = conn.execute(text(
            "UPDATE projects SET plan = 'dev' WHERE channel_title ILIKE :channel AND owner_id IN "
            "(SELECT id FROM users WHERE lower(username) = :username)" if not _is_sqlite else
            "UPDATE projects SET plan = 'dev' WHERE channel_title LIKE :channel AND owner_id IN "
            "(SELECT id FROM users WHERE lower(username) = :username)"),
            {"channel": channel, "username": username}).rowcount
        log.warning("schema upgrade: dev:service:plan for @%s %s: %s project(s)", username, channel, moved)
    conn.execute(text("UPDATE users SET trial_used_at = :now"), {"now": now})
    conn.execute(text(
        "UPDATE ai_accounts SET key_enc = NULL, paused_reason = NULL, paused_at = NULL "
        "WHERE user_id NOT IN (SELECT owner_id FROM projects WHERE plan = 'dev')"))
    conn.execute(text("INSERT INTO settings (key, value) VALUES (:k, 'true')"), {"k": PLANS_MOVED})


DEV_FIXED = "schema_dev_plans_fixed"  # a settings row: the second, wider look for the dev:service:plan projects
DEV_OWNERS = ("tankionline2005", "kindergarten4631")
DEV_TITLES = ("%шифровальн%", "%фактур%")


def _fix_dev_plans(conn) -> None:
    """One-time upgrade after _move_to_plans found neither project (each owner looked only for their own title):
    either title among either owner's projects, letters in any case. What each owner has is logged."""
    if conn.execute(text("SELECT 1 FROM settings WHERE key = :k"), {"k": DEV_FIXED}).first():
        return
    owners = conn.execute(text("SELECT id, username FROM users WHERE lower(username) IN :names")
                          .bindparams(bindparam("names", expanding=True)), {"names": list(DEV_OWNERS)}).all()
    for owner_id, username in owners:
        rows = conn.execute(text("SELECT id, channel_title, plan FROM projects WHERE owner_id = :o"), {"o": owner_id}).all()
        log.warning("schema upgrade: @%s has projects %s", username, [(r[0], r[1], r[2]) for r in rows])
        for project_id, title, _ in rows:
            if title and any(pattern.strip("%") in title.lower() for pattern in DEV_TITLES):
                conn.execute(text("UPDATE projects SET plan = 'dev', plan_expires_at = NULL, next_plan = NULL "
                                  "WHERE id = :p"), {"p": project_id})
                log.warning("schema upgrade: dev:service:plan for @%s project %s «%s»", username, project_id, title)
    if not owners:
        log.warning("schema upgrade: no user is @%s", ", @".join(DEV_OWNERS))
    conn.execute(text("INSERT INTO settings (key, value) VALUES (:k, 'true')"), {"k": DEV_FIXED})


async def init_db() -> None:
    # web and worker start together; retry while the database container boots
    for attempt in range(30):
        try:
            async with engine.begin() as conn:
                if not _is_sqlite:  # web and worker must not upgrade the schema at the same time
                    await conn.execute(text("SELECT pg_advisory_xact_lock(7311)"))
                no_project_groups = await conn.run_sync(lambda c: not inspect(c).has_table("project_groups"))
                await conn.run_sync(Base.metadata.create_all)
                if no_project_groups:
                    await conn.run_sync(_groups_to_projects)
                # a database from before forwards_own: its reposted posts still count our forward
                counted_own = await conn.run_sync(lambda c: not _has_column(c, "posts", "forwards_own"))
                no_history = await conn.run_sync(lambda c: not _has_column(c, "posts", "history"))
                posts_without_projects = await conn.run_sync(
                    lambda c: c.dialect.has_table(c, "chat_posts") and not _has_column(c, "chat_posts", "project_id"))
                await conn.run_sync(_add_missing_columns)
                await conn.run_sync(_move_to_projects)
                if counted_own:
                    await conn.run_sync(_discount_own_forwards)
                if no_history:
                    await conn.run_sync(_mark_history)
                await conn.run_sync(_recount_fr)
                await conn.run_sync(_mark_unsupported)
                await conn.run_sync(_new_default_prompts)
                await conn.run_sync(_drop_personal_feeds)
                await conn.run_sync(_drop_advice)
                await conn.run_sync(_drop_gone_columns)
                if posts_without_projects:
                    await conn.run_sync(_chat_posts_in_projects)
                await conn.run_sync(_move_to_plans)
                await conn.run_sync(_fix_dev_plans)
            return
        except Exception as exc:  # noqa: BLE001
            if attempt == 29:
                raise
            log.warning("database not ready (%s), retrying", exc)
            await asyncio.sleep(2)
