from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column


def utcnow() -> datetime:
    """Naive UTC: SQLite has no timezone support, so the whole app stores naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(64))
    session: Mapped[str] = mapped_column(Text)
    api_id: Mapped[int | None]
    api_hash: Mapped[str | None] = mapped_column(String(64))
    # device the session was created with (from a seller's .json); None = our defaults.
    # Reconnecting a session under a different device looks suspicious to Telegram.
    device_model: Mapped[str | None] = mapped_column(String(128))
    system_version: Mapped[str | None] = mapped_column(String(128))
    app_version: Mapped[str | None] = mapped_column(String(64))
    lang_code: Mapped[str | None] = mapped_column(String(16))
    system_lang_code: Mapped[str | None] = mapped_column(String(16))
    # assigned automatically from the proxy pool by the worker
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id", ondelete="SET NULL"), index=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    # new | active | flood | waiting (no free working proxy) | error (retried)
    # | dead (session revoked / banned, needs a human)
    status: Mapped[str] = mapped_column(String(16), default="new")
    status_detail: Mapped[str | None] = mapped_column(Text)
    flood_until: Mapped[datetime | None]
    max_channels: Mapped[int] = mapped_column(default=300)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Proxy(Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # socks5 | mtproto
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int]
    username: Mapped[str | None] = mapped_column(String(255))
    password: Mapped[str | None] = mapped_column(String(255))
    secret: Mapped[str | None] = mapped_column(String(512))
    # normalized identity used to skip duplicates when a list is pasted again
    key: Mapped[str] = mapped_column(String(1024), unique=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    # unknown (not checked yet) | ok | dead
    status: Mapped[str] = mapped_column(String(16), default="unknown")
    fails: Mapped[int] = mapped_column(default=0)  # consecutive failed checks
    latency_ms: Mapped[int | None]
    checked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # where an automatically added proxy came from (e.g. "@ProxyMTProto"); None = added by hand
    source: Mapped[str | None] = mapped_column(String(64))


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    link: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    # every public @username it has now, the main one (`username`) first; empty: it is private.
    # Kept up to date by the collector (channel info, polling, Telegram's "channel changed")
    usernames: Mapped[list[str] | None] = mapped_column(JSON)
    invite_hash: Mapped[str | None] = mapped_column(String(64))
    tg_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    title: Mapped[str | None] = mapped_column(String(255))
    subscribers: Mapped[int | None]
    avatar: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    has_avatar: Mapped[bool] = mapped_column(default=False)
    # pending | active | paused | error | deleting
    # | restricted (blocked in the countries of all accounts; retried when a new account appears)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # accounts that see this channel blocked by Telegram (local laws) — never assigned again
    restricted_for: Mapped[list[int] | None] = mapped_column(JSON)
    # accounts a public channel shut out (banned or removed by its admins) — not assigned again
    banned_for: Mapped[list[int] | None] = mapped_column(JSON)
    # average FR of the last 100 settled posts — the baseline for FR+ (see fr_plus.py)
    fr_base: Mapped[float | None]
    status_detail: Mapped[str | None] = mapped_column(Text)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True
    )
    last_msg_id: Mapped[int | None]
    last_post_at: Mapped[datetime | None]
    last_polled_at: Mapped[datetime | None]
    info_updated_at: Mapped[datetime | None]
    # Channels are shared: projects only subscribe to them (ProjectChannel). The admin's own additions
    # are held — kept even when no project tracks them.
    held: Mapped[bool] = mapped_column(default=False)
    # its posts of the last backfill_days are in: done on joining; channels added before that
    # are caught up in the background (collector.history_step)
    history_done: Mapped[bool] = mapped_column(default=False)
    # when the last subscriber left a channel nobody holds; it is left after a grace period
    # (see orphans.py) unless someone adds it again
    orphaned_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ChannelReserve(Base):
    """A backup account inside a private channel. It only stays a member — no requests while the
    main account is fine — and takes over collecting at once if the main one loses the channel:
    a private channel cannot be re-joined by just anyone, the way a public one can."""

    __tablename__ = "channel_reserves"

    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # planned (joins at due_at) | requested (a join request waits for the channel admin)
    # | member | failed (the invite link no longer works; tried again at due_at)
    status: Mapped[str] = mapped_column(String(16), default="planned")
    due_at: Mapped[datetime | None]  # next thing to do: join, recheck the request, check membership
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ProjectChannel(Base):
    """A project tracks a channel. Every project sees the channel as its own; one account serves them all."""

    __tablename__ = "project_channels"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # the project's own channel: tracked automatically, not counted towards the plan's limit
    own: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # over the plan's limit (billing.py): the project does not read it till a plan with room is bought
    paused_at: Mapped[datetime | None]


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("channel_id", "msg_id"),
        Index("ix_posts_posted_at", "posted_at"),
        Index("ix_posts_repost", "repost_status", "posted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    msg_id: Mapped[int]
    # All message ids of an album (None for a single message)
    album_ids: Mapped[list[int] | None] = mapped_column(JSON)
    posted_at: Mapped[datetime]
    text: Mapped[str] = mapped_column(Text, default="")
    html: Mapped[str] = mapped_column(Text, default="")
    media_type: Mapped[str | None] = mapped_column(String(16))
    media_count: Mapped[int] = mapped_column(default=0)
    has_preview: Mapped[bool] = mapped_column(default=False)
    # one picture per album item: [{"k": photo|video|other, "p": position in the album}, …], files
    # <id>.jpg, <id>_1.jpg, … (services.preview_path); None for posts saved before (one picture)
    previews: Mapped[list[dict] | None] = mapped_column(JSON)

    views: Mapped[int] = mapped_column(default=0)
    forwards: Mapped[int] = mapped_column(default=0)
    # forwards of ours that Telegram counts in: our repost to the aggregator group is one. Already
    # taken off `forwards` (and ER), so the numbers are the channel's audience alone
    forwards_own: Mapped[int] = mapped_column(default=0)
    reactions: Mapped[int] = mapped_column(default=0)
    comments: Mapped[int] = mapped_column(default=0)
    er: Mapped[float] = mapped_column(default=0.0)
    # FR = views / channel subscribers, in percent
    fr: Mapped[float] = mapped_column(default=0.0)
    # (deployed databases still have unused `velocity` and `views_history` columns: 0.0 / NULL by default)
    # [{"e": "👍", "c": 10}, ...] — top reactions, "custom"/"paid" for non-standard ones
    reactions_top: Mapped[list[dict] | None] = mapped_column(JSON)
    metrics_updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    # pending | done | skipped | failed
    repost_status: Mapped[str] = mapped_column(String(16), default="pending")
    repost_error: Mapped[str | None] = mapped_column(Text)
    reposted_at: Mapped[datetime | None]  # when the repost succeeded, failed or was skipped
    # link to the forwarded copy in the aggregator group (the web UI opens it instead of the original)
    repost_url: Mapped[str | None] = mapped_column(String(255))
    # the copy in the group is our own text with a link (the channel forbids forwarding), not a
    # forward; None for posts reposted before this was recorded (see originals.py)
    repost_copy: Mapped[bool | None]
    # text read off the post's pictures (see ocr.py), every picture's lines in order; "" when they
    # have none. Only for posts collected after OCR came: ocr_status pending -> done | failed
    extracted_text_from_media: Mapped[str | None] = mapped_column(Text)
    ocr_status: Mapped[str | None] = mapped_column(String(8))
    # when the post turned out to be deleted in its channel (its numbers stop changing then)
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # a channel's history: brought when the channel was added, or a day and more after it came out. Not news:
    # the AI (smart feed, topics, «Есть в вашем канале») does not read it as new posts
    history: Mapped[bool] = mapped_column(default=False, server_default=false())


class User(Base):
    """A person who signs in to the web UI through the bot (the admin is not one: see config)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(64))
    blocked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime | None]
    # the sections whose welcome cards this person has seen (Intro on the site), shown once each
    intros_seen: Mapped[list | None] = mapped_column(JSON)
    # the project opened last, so another device opens the same one (not a foreign key: projects
    # point back at users, and a deleted project just falls back to the first one)
    project_id: Mapped[int | None]
    # the account's money (billing.py), in millionths of a dollar: an AI request costs a fraction of a cent.
    # It may go a little below zero — a request already sent is paid for whatever is left
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    # when «Free Trial» was given: once a Telegram account, to its first project
    trial_used_at: Mapped[datetime | None]


class Project(Base):
    """What a user works on: its own plan, channels and API key. A user has one or more."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # see plans.py; "" = no plan (a new project has none until it is bought, or the admin or a promo code gives one)
    plan: Mapped[str] = mapped_column(String(32), default="")
    # None: the plan never ends (and «Free Trial» before the payments go live: its 30 days start then)
    plan_expires_at: Mapped[datetime | None]
    # billing.py: when the plan's current period began, and what was paid for it (millionths of a dollar; 0: a
    # promo code, the admin, the trial) — a better plan bought before the end gives the unused part back
    plan_started_at: Mapped[datetime | None]
    plan_paid: Mapped[int] = mapped_column(BigInteger, default=0)
    # «Автопродление»: at the end the plan is bought again from the owner's balance
    auto_renew: Mapped[bool] = mapped_column(default=True)
    # a cheaper plan chosen: it comes at the end of the current period
    next_plan: Mapped[str | None] = mapped_column(String(32))
    # the AI included in the plan, spent in the current AI period (a month; the trial's: once), and what was
    # paid for from the balance above it — millionths of a dollar, at the service's prices
    ai_period_start: Mapped[datetime | None]
    ai_used: Mapped[int] = mapped_column(BigInteger, default=0)
    ai_extra_used: Mapped[int] = mapped_column(BigInteger, default=0)
    # «Докупать ИИ с баланса», and at most how much a month (None: no cap)
    ai_extra: Mapped[bool] = mapped_column(default=True)
    ai_extra_cap: Mapped[int | None] = mapped_column(BigInteger)
    # the latest «осталось N дней» the owner was told (the bot, once a day), so it is told once
    reminded_days: Mapped[int | None]
    # key of the external API
    api_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    # the project's own Telegram channel, chosen among those the bot was added to (None: not chosen)
    channel_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    channel_title: Mapped[str | None] = mapped_column(String(255))
    channel_username: Mapped[str | None] = mapped_column(String(64))
    channel_photo: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    # «Создать об этом пост для моего канала»: the owner's wording of the request in this project;
    # None: the default (a staff member has their own, see Member)
    post_prompt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Member(Base):
    """A staff account of a project ("Совместный мониторинг"): signs in with a login and password,
    sees the project's posts and channels, may edit its channels if allowed, uses its own API key."""

    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    login: Mapped[str] = mapped_column(String(32), unique=True)  # lowercase
    password_hash: Mapped[str] = mapped_column(String(255))
    can_edit_channels: Mapped[bool] = mapped_column(default=False)
    api_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    # a new password signs out every session that has an older version
    session_version: Mapped[int] = mapped_column(default=0)
    # their own Telegram, linked in the settings: the bot sends them original posts
    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_username: Mapped[str | None] = mapped_column(String(64))
    tg_name: Mapped[str | None] = mapped_column(String(255))
    # AI (the owner's OpenRouter): talking to the AI
    ai_chat: Mapped[bool] = mapped_column(default=False)
    # what talking to the AI may cost the owner in a month (USD); None: no limit of their own
    ai_chat_limit_usd: Mapped[float | None]
    # teaching the project's AI: hiding topics, «Это не так», correcting the owner's smart feed
    ai_teach: Mapped[bool] = mapped_column(default=False)
    # the settings of the smart feed and topics (on/off, prompt, schedule)
    ai_configure: Mapped[bool] = mapped_column(default=False)
    # publishing the posts the AI wrote in the project's channel, through the bot
    ai_publish: Mapped[bool] = mapped_column(default=False)
    # once: running the AI by hand, and seeing what they spent on it — the owner's only now, never read
    ai_run: Mapped[bool] = mapped_column(default=False)
    ai_see_spend: Mapped[bool] = mapped_column(default=False)
    # once: sending posts to the project's groups — everyone of the project may now, never read
    send_to_groups: Mapped[bool] = mapped_column(default=False)
    # the bot sends them each new topic of the project (needs their Telegram linked)
    ai_topic_notify: Mapped[bool] = mapped_column(default=False)
    # /start staff_<token> in the bot links the Telegram of whoever opens it
    link_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    link_token_at: Mapped[datetime | None]
    # «Создать об этом пост для моего канала»: the staff member's wording of the request; None: the default
    post_prompt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime | None]
    intros_seen: Mapped[list | None] = mapped_column(JSON)  # as a User's
    # over the plan's limit (billing.py): cannot sign in till a plan with room is bought
    paused_at: Mapped[datetime | None]


class BotChat(Base):
    """A group the bot was added to (Telegram tells it, with who did it). A private group cannot be
    found by its invite link, so this is how a user's own group shows up in "Мои группы"."""

    __tablename__ = "bot_chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    type: Mapped[str] = mapped_column(String(16))  # group | supergroup | channel
    title: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(64))
    # member | administrator | left | kicked — the bot's own status there
    status: Mapped[str] = mapped_column(String(16))
    added_by: Mapped[int | None] = mapped_column(BigInteger)  # Telegram id of who added the bot
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class ProjectGroup(Base):
    """A group of the project's own that the bot sends posts to ("Мои группы"): each project has its own, as many
    as its plan allows (plans.Plan.groups). (Groups were the owner's, for all their projects: table user_groups,
    copied into each of their projects once, db._groups_to_projects.)"""

    __tablename__ = "project_groups"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PromoCode(Base):
    """A one-time code that gives a plan for some days (or forever), see web/promo.py."""

    __tablename__ = "promo_codes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan: Mapped[str] = mapped_column(String(32))
    days: Mapped[int | None]  # None: the plan never ends
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    used_at: Mapped[datetime | None]


class LoginRequest(Base):
    """One "Войти через Telegram" — the browser waits while the person confirms it in the bot — or one QR code of
    the login page, which a phone signed in scans (Settings → «Устройства»)."""

    __tablename__ = "login_requests"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    # bot | qr
    kind: Mapped[str] = mapped_column(String(8), default="bot", server_default="bot")
    # pending | confirmed (in the bot, or on the phone) | declined | used (the browser got its session)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    device: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # a QR code a staff member's phone confirmed: the browser signs in as them
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Device(Base):
    """A browser someone is signed in on (Settings → «Устройства»): its session cookie carries the id ("did"), and
    a device signed out from another is gone from here — its cookie then signs in nobody."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # whose: a user's own sign-in, or a staff member's
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    # what the browser itself tells (devices.py): an Android phone's model, the version of Windows, an app on the
    # home screen
    model: Mapped[str | None] = mapped_column(String(64))
    os_version: Mapped[str | None] = mapped_column(String(32))
    standalone: Mapped[bool] = mapped_column(default=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    # signed out from another device: its cookie is refused (kept until such a cookie would have expired anyway)
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class NotifyChat(Base):
    """Telegram chats that receive alerts from the notification bot."""

    __tablename__ = "notify_chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)


class PerfSample(Base):
    """The server's load, every 30 seconds (see perf.py): Settings → System Performance."""

    __tablename__ = "perf_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    cpu: Mapped[float]  # % of all cores busy
    mem_used: Mapped[int] = mapped_column(BigInteger)  # bytes: total minus what is available
    mem_total: Mapped[int] = mapped_column(BigInteger)
    # bytes per second, averaged since the sample before
    disk_read: Mapped[float]
    disk_write: Mapped[float]
    net_in: Mapped[float]
    net_out: Mapped[float]


class AiAccount(Base):
    """A project owner's OpenRouter (see ai.py): one for all their projects, their staff use it too.
    Unlinking drops only the key; the models, schedule and limit stay for the next link."""

    __tablename__ = "ai_accounts"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    key_enc: Mapped[str | None] = mapped_column(Text)  # the OpenRouter key, encrypted with SECRET_KEY
    connected_at: Mapped[datetime | None]
    # when the owner accepted, after linking, that tokens are paid by them and models are their choice
    warning_accepted_at: Mapped[datetime | None]
    # {operation: model id}; an operation not in it uses ai.OPERATIONS' default
    models: Mapped[dict | None] = mapped_column(JSON)
    # the intervals set here before schedules were per project: what a project's default starts from
    topics_every_min: Mapped[int] = mapped_column(default=60)
    advice_every_hours: Mapped[int] = mapped_column(default=24)  # once: «Советы», gone; never read
    monthly_limit_usd: Mapped[float | None]  # our own cap on spending; None: none
    # why the AI stands still: no_credits | invalid_key (None: it works); cleared by a new link
    paused_reason: Mapped[str | None] = mapped_column(String(32))
    paused_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AiUsage(Base):
    """Every request to OpenRouter: what it cost and for what. Settings → «Расход и остаток»."""

    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)  # who pays
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    operation: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    cached_tokens: Mapped[int] = mapped_column(default=0)
    cost: Mapped[float] = mapped_column(default=0.0)  # USD, as OpenRouter charged it
    generation_id: Mapped[str | None] = mapped_column(String(128))
    # with the service's OpenRouter (billing.py): what the owner paid for it, the markup in — millionths of a
    # dollar; None: their own OpenRouter paid (dev:service:plan, and everyone before)
    charged: Mapped[int | None] = mapped_column(BigInteger)


class BalanceOp(Base):
    """Money in and out of an owner's balance (billing.py): «История операций». The AI's requests are one row a day
    for each project, added up as they go."""

    __tablename__ = "balance_ops"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    # topup | plan (bought or renewed) | refund (a plan's unused part) | ai | admin (by hand, with a note)
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(BigInteger)  # millionths of a dollar; spent ones are below zero
    plan: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(String(255))
    day: Mapped[str | None] = mapped_column(String(10))  # an AI row's day (UTC), YYYY-MM-DD
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class ChannelSnapshot(Base):
    """Subscribers of a channel over time: hourly for project channels, daily for the rest."""

    __tablename__ = "channel_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(default=utcnow)
    subscribers: Mapped[int]


class PostCheckpoint(Base):
    """The numbers of a project channel's post 1, 6, 24 and 48 hours after it came out (see
    channel_stats.py). Not tied to the post: they outlive it, the channel's history stays."""

    __tablename__ = "post_checkpoints"
    __table_args__ = (UniqueConstraint("channel_id", "msg_id", "hours"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    msg_id: Mapped[int]
    posted_at: Mapped[datetime]
    hours: Mapped[int]
    views: Mapped[int]
    forwards: Mapped[int]
    reactions: Mapped[int]
    comments: Mapped[int]
    at: Mapped[datetime] = mapped_column(default=utcnow)


class SmartFeed(Base):
    """«Умная лента» (see smart_feed.py): the AI keeps only the posts that match a prompt. One per
    project (member_id None): staff see it as it is. (member_id: personal feeds of staff, gone.)"""

    __tablename__ = "smart_feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    prompt: Mapped[str] = mapped_column(Text, default="")
    # once: pictures went to the AI itself instead of the text OCR read on them — no longer done, never read
    vision: Mapped[bool] = mapped_column(default=False)
    # posts up to this id are judged (or were there before the feed was turned on)
    cursor_post_id: Mapped[int | None] = mapped_column(BigInteger)
    enabled_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SmartVerdict(Base):
    """What the AI said of a post for a smart feed."""

    __tablename__ = "smart_verdicts"
    __table_args__ = (UniqueConstraint("feed_id", "post_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("smart_feeds.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    match: Mapped[bool]
    # 0–100: how well it matches; -1: the AI never read it (a post from before the feed was on, which
    # the person hid themselves)
    score: Mapped[int] = mapped_column(default=0)
    reason: Mapped[str | None] = mapped_column(String(300))  # a few words, for the post's card
    # the person overruled the AI (True: show it, False: hide it), with why: the AI learns from it
    override: Mapped[bool | None]
    override_reason: Mapped[str | None] = mapped_column(Text)
    override_at: Mapped[datetime | None]
    override_by: Mapped[str | None] = mapped_column(String(64))  # «владелец» or a staff member's login
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TopicState(Base):
    """A project's topics (see topics.py): on or off, the prompt, how far the posts were read."""

    __tablename__ = "topic_states"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    # off until the owner turns it on: linking OpenRouter alone spends nothing
    enabled: Mapped[bool] = mapped_column(default=False)
    prompt: Mapped[str] = mapped_column(Text, default="")
    cursor_post_id: Mapped[int | None] = mapped_column(BigInteger)
    # how far the project channel's posts were checked for «Есть в вашем канале» (covered.py); None: from now
    covered_cursor_post_id: Mapped[int | None] = mapped_column(BigInteger)
    last_run_at: Mapped[datetime | None]
    # the owner gets each new topic from the bot
    notify_owner: Mapped[bool] = mapped_column(default=False)
    run: Mapped[dict | None] = mapped_column(JSON)  # the run going, or how the last one ended (ai_runs.py)
    schedule: Mapped[dict | None] = mapped_column(JSON)  # when it runs (schedule.py); None: the default
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Topic(Base):
    """What several posts of a project's channels tell about one event; it grows as more come."""

    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    # when something new was added to it («Обновлена»); sources that only repeat it do not count
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    last_post_at: Mapped[datetime] = mapped_column(default=utcnow)
    # the project's own channel already told it («Есть в вашем канале»)
    covered: Mapped[bool] = mapped_column(default=False)
    # someone in the project said the mark was wrong («Это не так»): the model does not put it back
    covered_dismissed: Mapped[bool] = mapped_column(default=False)
    # someone in the project put the mark themselves: «владелец» or a staff member's login; None: the AI did
    covered_by: Mapped[str | None] = mapped_column(String(64))
    # when the mark was put; None on topics from before this was kept (taken as long ago)
    covered_at: Mapped[datetime | None]
    # big news came after the channel told it («Есть продолжение»): a reason for one more post; it goes once the
    # channel tells the continuation, or the mark is taken off
    followup_at: Mapped[datetime | None]
    # what the model chose to stand for it in the bot's messages (the site shows none)
    emoji: Mapped[str | None] = mapped_column(String(16))
    # how hot and in demand the model found it, 1-10: a run's topics are listed hottest first
    importance: Mapped[int | None] = mapped_column(Integer)
    # the run that brought it to the top of the list: the one it began in, or a later one whose
    # news the model found big enough to raise it again (None: older topics, listed by updated_at)
    ranked_at: Mapped[datetime | None]
    # hidden by someone in the project, with why: off the list, and the model learns from the reason
    hidden_at: Mapped[datetime | None]
    hide_reason: Mapped[str | None] = mapped_column(Text)
    # who hid it: «владелец» or the staff member's login
    hidden_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TopicPost(Base):
    """A post a topic is made of (its sources, and the pictures of its gallery)."""

    __tablename__ = "topic_posts"

    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True, index=True)
    added_at: Mapped[datetime] = mapped_column(default=utcnow)


class TopicUpdate(Base):
    """A line of a topic's timeline: how it began, and each thing that was added later."""

    __tablename__ = "topic_updates"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(default=utcnow)
    text: Mapped[str] = mapped_column(Text)


class TopicSeen(Base):
    """When a person last opened a topic: «Обновлена» goes once they have seen the update."""

    __tablename__ = "topic_seen"
    __table_args__ = (UniqueConstraint("topic_id", "user_id", "member_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    seen_at: Mapped[datetime] = mapped_column(default=utcnow)


class ChatThread(Base):
    """A chat with the AI (see chat.py): a person's own (the owner does not see staff chats), in one
    project, with the model it was started with."""

    __tablename__ = "chat_threads"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))  # the owner's
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="Новый чат")
    model: Mapped[str] = mapped_column(String(128))
    # the newest post the AI has been told about: the next message brings what came after it
    seen_post_id: Mapped[int | None] = mapped_column(BigInteger)
    seen_at: Mapped[datetime | None]
    # what was said before the messages still sent in full, in short (long chats)
    summary: Mapped[str | None] = mapped_column(Text)
    # when its person last had it open: an answer after that is unread (a blue dot); None: chats from
    # before this was kept count as read
    read_at: Mapped[datetime | None]
    # a chat begun from a topic's «Создать об этом пост для моего канала»: its first answer is a post, and the
    # topic's posts are where the post's pictures come from
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class Trigger(Base):
    """«Триггер-слова» (see triggers.py): a person's words to watch for in the project's channels, without
    the AI — a feed of their own, and the bot telling at once when a new post has one. The owner's and
    each staff member's are their own ("u<user id>" / "m<member id>", as in SeenMark)."""

    __tablename__ = "triggers"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    person: Mapped[str] = mapped_column(String(24), index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))  # gone with them
    name: Mapped[str] = mapped_column(String(64))
    # the words and phrases, any of which is a match («БПЛА», «беспилотник», «сбит дрон»)
    words: Mapped[list[str]] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(8), default="forms")  # forms | exact
    # all: every channel the project tracks, now and later, but `channel_ids`; only: just `channel_ids`
    channels_mode: Mapped[str] = mapped_column(String(8), default="all")
    channel_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    notify: Mapped[bool] = mapped_column(default=False)
    # where the bot tells: their own Telegram, and/or one of the owner's groups («Мои группы»)
    notify_dm: Mapped[bool] = mapped_column(default=True)
    notify_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    # the newest hit the person has seen: the ones after it are counted on the blue badges
    seen_hit_id: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # over the plan's limit (billing.py): not watched till a plan with room is bought
    paused_at: Mapped[datetime | None]


class Favorite(Base):
    """«Избранное»: a post a person starred, theirs alone (each staff member has their own), in the project they
    starred it in. Such posts outlive the retention period, as a topic's do."""

    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    person: Mapped[str] = mapped_column(String(24), index=True)  # as Trigger.person: "u<user id>", "m<member id>"
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))  # gone with them
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TriggerHit(Base):
    """A post that has one of a trigger's words: the trigger's feed, and what the bot tells."""

    __tablename__ = "trigger_hits"
    __table_args__ = (UniqueConstraint("trigger_id", "post_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger_id: Mapped[int] = mapped_column(ForeignKey("triggers.id", ondelete="CASCADE"), index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    # the words as the post has them («взрывы», «БПЛА»): highlighted in the feed, named by the bot
    found: Mapped[list[str]] = mapped_column(JSON, default=list)
    # told by the bot already, or not to be (posts that were there before the trigger was made)
    notified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TriggerScan(Base):
    """How far the worker has read the posts for the triggers: their text at once, the text on their
    pictures once it is read (a single row)."""

    __tablename__ = "trigger_scan"

    id: Mapped[int] = mapped_column(primary_key=True)
    text_post_id: Mapped[int] = mapped_column(BigInteger, default=0)
    ocr_post_id: Mapped[int] = mapped_column(BigInteger, default=0)


class SeenMark(Base):
    """How far a person has looked at the feed, the topics or the publications of a project: what came after
    is counted on the blue badges (see web/badges_api.py)."""

    __tablename__ = "seen_marks"
    # one per person: "u<user id>" for the owner, "m<member id>" for staff (no NULL in it, so the
    # database keeps it unique even when two pages ask at once)
    __table_args__ = (UniqueConstraint("project_id", "person", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    person: Mapped[str] = mapped_column(String(24))
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))  # gone with them
    kind: Mapped[str] = mapped_column(String(8))  # feed | topics | posts (the AI's new posts in «Публикации»)
    at: Mapped[datetime] = mapped_column(default=utcnow)
    post_id: Mapped[int | None] = mapped_column(BigInteger)  # the feed: the newest post seen


class ChatMessage(Base):
    """A message of a chat: the person's, the AI's, a tool's answer, or the «what is new» note we add."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"), index=True)
    # user | assistant | tool | context (our note, sent as the user's; never shown)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list | None] = mapped_column(JSON)  # the assistant's requests to our tools
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    # gone into the thread's summary: not sent any more
    summarized: Mapped[bool] = mapped_column(default=False)
    # the answer's last part: how long the whole answer took, from the question to the last letter
    took_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ChatPost(Base):
    """A post the AI wrote for the project's channel (see chat_posts.py) — in a chat, or by itself for a new topic
    («Публикации», publications.py): shown as a post, edited, sent to the person's Telegram, published. What the
    AI wrote and what the people made of it teach the AI their way."""

    __tablename__ = "chat_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # the chat it was written in; None: written for a topic (`topic_id`), or its chat was deleted after it went out
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"), index=True)
    # the AI's call that gave it (suggest_post); or None: an answer of the AI made a post by hand
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    # the AI's message that gave it (the call's), or the answer made a post by hand: its first message. A message
    # that gave a post is an answer, though it has no text
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id", ondelete="SET NULL"))
    ai_text: Mapped[str] = mapped_column(Text)  # Telegram's HTML (tg_html.py), as the AI wrote it
    # a post its author wrote themselves, not the AI — through the API or the MCP connector: who («владелец», a
    # staff member's login). It goes to «Публикации» as is; nothing of it teaches the AI
    written_by: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)  # and as it is now
    # the pictures and videos that go with it, in order: [{"post": post id, "slot": picture of that post}]
    media: Mapped[list] = mapped_column(JSON, default=list)
    edited_at: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]  # last sent to the person's Telegram
    # last published in the project's channel by the bot, and the link to it there
    published_at: Mapped[datetime | None]
    published_url: Mapped[str | None] = mapped_column(String(255))
    # who published it last, and who scheduled it: «владелец», a staff member's login, «автопостинг»
    published_by: Mapped[str | None] = mapped_column(String(64))
    scheduled_by: Mapped[str | None] = mapped_column(String(64))
    # «Отложенная публикация»: when the bot publishes it (the site keeps the time — Telegram lets no bot schedule),
    # and whether without what of the pictures cannot go, as the person agreed
    scheduled_at: Mapped[datetime | None] = mapped_column(index=True)
    scheduled_partial: Mapped[bool] = mapped_column(default=False)
    # a scheduled publishing that did not happen: why, for people, and when it was to be
    publish_error: Mapped[str | None] = mapped_column(Text)
    publish_error_at: Mapped[datetime | None]
    # why «Автопостинг» left it a suggestion (the day's limit, the importance, the sources), for the card
    autopost_note: Mapped[str | None] = mapped_column(String(200))
    # where it went out, to change it there or take it back: the chat (its id, or @username), all its messages, the
    # one with the text and how it carries it (text | caption)
    published_chat: Mapped[str | None] = mapped_column(String(64))
    published_ids: Mapped[list | None] = mapped_column(JSON)
    published_text_id: Mapped[int | None] = mapped_column(BigInteger)
    published_text_kind: Mapped[str | None] = mapped_column(String(8))
    published_edited_at: Mapped[datetime | None]  # «изменён»: its text changed in the channel
    # «Обложка»: a cover in the project's style goes as its first picture; its title, when a person wrote one
    cover: Mapped[bool] = mapped_column(default=False)
    cover_title: Mapped[str | None] = mapped_column(String(200))
    # an issue of a rubric («Рубрики»): its rubric
    rubric_id: Mapped[int | None] = mapped_column(ForeignKey("rubrics.id", ondelete="SET NULL"), index=True)
    # «Удалён из канала»: taken back from the channel, when and by whom
    withdrawn_at: Mapped[datetime | None]
    withdrawn_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PublicationSettings(Base):
    """«Публикации» of a project, set by its owner only (publications.py)."""

    __tablename__ = "publication_settings"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    # «Создавать тематические посты»: after a topics run, a post for each of its largest new topics
    topic_posts: Mapped[bool] = mapped_column(default=False)
    posts_limit: Mapped[int] = mapped_column(default=3)  # at most this many a run
    prompt: Mapped[str] = mapped_column(Text, default="")
    # «Автопостинг»: off | now | delay (by `autopost_hours`)
    autopost: Mapped[str] = mapped_column(String(8), default="off")
    autopost_hours: Mapped[int] = mapped_column(default=1)
    # «Время между публикациями»: minutes between two autoposts
    autopost_gap_min: Mapped[int] = mapped_column(default=15)
    last_autopost_at: Mapped[datetime | None]  # the latest autopost's time: the next goes `autopost_gap_min` after
    # topic posts only for topics this important (1-10; 1: all); autoposting only these (the rest stay suggestions)
    min_importance_posts: Mapped[int] = mapped_column(default=1)
    daily_limit: Mapped[int] = mapped_column(default=0)  # autoposts a day (the owner's local day) at most; 0: any
    # autoposting only topics this many channels told (1: any)
    autopost_min_sources: Mapped[int] = mapped_column(default=1)


class CoverSettings(Base):
    """«Обложки»: the project's one cover style (covers.py), the owner's."""

    __tablename__ = "cover_settings"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(10), default="off")  # autoposts and rubrics: off | always | no_photo
    template: Mapped[str] = mapped_column(String(16), default="gradient")
    size: Mapped[str] = mapped_column(String(6), default="1:1")
    accent: Mapped[str] = mapped_column(String(7), default="#0a84ff")
    font: Mapped[str] = mapped_column(String(12), default="sans")
    position: Mapped[str] = mapped_column(String(8), default="bottom")
    show_channel: Mapped[bool] = mapped_column(default=True)
    has_logo: Mapped[bool] = mapped_column(default=False)  # its file: covers.logo_path
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Rubric(Base):
    """«Рубрики» (rubrics.py): a post the AI writes by itself on a schedule — «Итоги дня», «Утренний дайджест» — from
    the project's topics of a period or its channels' most popular posts, the owner's way."""

    __tablename__ = "rubrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(default=False)
    schedule: Mapped[dict | None] = mapped_column(JSON)  # when issues go (schedule.py, its rules)
    source: Mapped[str] = mapped_column(String(12), default="topics")  # topics | top_posts
    period_hours: Mapped[int] = mapped_column(default=0)  # what it takes: this many hours back; 0: since the last issue
    max_items: Mapped[int] = mapped_column(default=5)
    min_importance: Mapped[int] = mapped_column(default=1)  # topics this important (1: any)
    channel_ids: Mapped[list | None] = mapped_column(JSON)  # only what these channels told; None: all
    words: Mapped[list | None] = mapped_column(JSON)  # only topics or posts with one of these words; None: any
    prompt: Mapped[str] = mapped_column(Text, default="")  # how to write it, in the owner's words
    header: Mapped[str] = mapped_column(Text, default="")  # the same first line of every issue
    footer: Mapped[str] = mapped_column(Text, default="")  # and its last
    picture: Mapped[str] = mapped_column(String(12), default="none")  # none | source (the material's photos)
    mode: Mapped[str] = mapped_column(String(8), default="suggest")  # suggest | auto (straight to the channel)
    last_run_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)  # why the last issue did not come out
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TopicPostRetry(Base):
    """A topic whose AI post did not come out — the model failed, or gave no post: tried again in the next topics
    runs (publications.py)."""

    __tablename__ = "topic_post_retries"

    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    followup: Mapped[bool] = mapped_column(default=False)  # a follow-up of the post published on it
    runs: Mapped[int] = mapped_column(default=0)  # the topics runs it has waited through
    error: Mapped[str | None] = mapped_column(Text)  # why it failed last
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ChatPostSource(Base):
    """A post a chat post's facts come from: its pictures and videos are the ones to choose from. Such posts
    outlive the retention period, as a topic's do."""

    __tablename__ = "chat_post_sources"

    chat_post_id: Mapped[int] = mapped_column(ForeignKey("chat_posts.id", ondelete="CASCADE"), primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True, index=True)
    rank: Mapped[int] = mapped_column(default=0)  # the most important first


class BotFile(Base):
    """A picture or video of a post as our bot knows it in Telegram (its file_id), taken from the post's copy in
    the aggregator group: the bot sends it in its original quality, without downloading it anywhere."""

    __tablename__ = "bot_files"

    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    slot: Mapped[int] = mapped_column(primary_key=True)
    # photo | video | animation (a GIF) | video_note (a «кружок») | other
    kind: Mapped[str] = mapped_column(String(16))
    file_id: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)



class PostStyle(Base):
    """How a project's people edit the posts the AI writes for its channel (post_style.py): the notes the AI keeps
    from their edits, one for the project."""

    __tablename__ = "post_styles"

    # "<project id>:0" (post_style.key)
    person: Mapped[str] = mapped_column(String(32), primary_key=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # the edits the notes were made from, up to this one (ChatPost.edited_at)
    learned_until: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class McpConnection(Base):
    """«MCP-коннектор»: an AI app (Claude, ChatGPT…) a person let in through OAuth (web/mcp_oauth.py) — it acts as
    they do, by its tokens: the project's owner, or a staff member with the rights the owner gave them."""

    __tablename__ = "mcp_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    client_name: Mapped[str] = mapped_column(String(120))
    redirect_host: Mapped[str] = mapped_column(String(255))
    # sha256 of the tokens: they are shown to the app once
    access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # the authorization code it was made from: once only
    code_id: Mapped[str] = mapped_column(String(32), unique=True)
    access_expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_used_at: Mapped[datetime | None]
