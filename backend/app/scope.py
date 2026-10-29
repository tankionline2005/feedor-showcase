"""What a viewer may see: the admin sees every channel, a user only the channels their current
project tracks.

Channels are shared — one account serves every project that tracks a channel — but every user sees
it as their own: the internals (which account, bans, proxies, reassignments) stay hidden and
a channel shows up as connecting, working or failed for a reason of its own.
"""

from sqlalchemy import exists, select

from .models import Channel, ProjectChannel

# what a user is told about a channel that failed for a reason of the channel itself;
# any other failure (an account's, a network's) looks like "still connecting" to them
CHANNEL_ERRORS = (
    "Канал не найден",
    "Ссылка-приглашение недействительна",
    "Это не канал (группа или пользователь)",
    "Канал приватный или аккаунт в нём заблокирован",
    "Канал сменил адрес или стал закрытым — добавьте его заново по актуальной ссылке",
)
PRIVATE_FOR_USERS = "Канал приватный — нужна ссылка-приглашение"
RESTRICTED_FOR_USERS = "Telegram ограничил доступ к этому каналу"
JOIN_REQUEST_PREFIX = "Отправлена заявка на вступление"


def linked_channel_ids(project_id: int):
    """Every channel of the project, the paused ones too (over its plan's limit, billing.py)."""
    return select(ProjectChannel.channel_id).where(ProjectChannel.project_id == project_id)


def project_channel_ids(project_id: int):
    """The channels the project reads: a paused one (over its plan's limit) is not read till it is back."""
    return linked_channel_ids(project_id).where(ProjectChannel.paused_at.is_(None))


def tracked_channel_ids(project_id: int):
    """The channels the project watches, without its own: the "outside" the feeds and the AI read."""
    return project_channel_ids(project_id).where(~ProjectChannel.own)


def own_channel_ids(project_id: int):
    return project_channel_ids(project_id).where(ProjectChannel.own)


async def my_channel_posts(session, project_id: int, since, limit: int = 20) -> list[dict]:
    """The project channel's latest posts, briefly: what the AI is told the owner already published."""
    from .models import Post

    posts = (await session.execute(
        select(Post).where(Post.channel_id.in_(own_channel_ids(project_id)), Post.posted_at >= since,
                           Post.deleted_at.is_(None)).order_by(Post.posted_at.desc()).limit(limit)
    )).scalars().all()
    return [{"posted_at": p.posted_at.isoformat(timespec="minutes") + "Z",
             "text": ((p.text or "") + (" " + p.extracted_text_from_media if p.extracted_text_from_media else ""))[:400]}
            for p in posts]


def visible_channels(viewer):
    """A subquery of the channel ids the viewer may see, or None for the admin (all of them)."""
    return None if viewer.is_admin else project_channel_ids(viewer.project_id)


def scoped(stmt, viewer, column=Channel.id):
    ids = visible_channels(viewer)
    return stmt if ids is None else stmt.where(column.in_(ids))


async def can_see(db, viewer, channel_id: int) -> bool:
    if viewer.is_admin:
        return True
    return bool(await db.scalar(select(
        exists().where(ProjectChannel.project_id == viewer.project_id, ProjectChannel.channel_id == channel_id)
    )))


def user_status(ch: Channel) -> tuple[str, str | None]:
    """(status, detail) of a channel as a user sees it: active | pending | error."""
    if ch.status == "active":
        return "active", None
    if ch.status == "pending":
        detail = ch.status_detail if (ch.status_detail or "").startswith(JOIN_REQUEST_PREFIX) else None
        return "pending", detail
    if ch.status == "restricted":
        return "error", RESTRICTED_FOR_USERS
    if ch.status == "error" and ch.status_detail in CHANNEL_ERRORS:
        if ch.status_detail == "Канал приватный или аккаунт в нём заблокирован":
            return "error", PRIVATE_FOR_USERS
        return "error", ch.status_detail
    return "pending", None  # paused by the admin, or failing for reasons that are ours to fix
