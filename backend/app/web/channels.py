from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_, select

from ..channel_stats import channel_stats as project_channel_stats
from .. import notify, settings_store
from ..models import Account, Channel, ChannelReserve, Member, Post, Project, ProjectChannel, User, utcnow
from ..orphans import mark_if_orphaned
from ..plans import plan_of
from ..projects import project_title, user_projects
from ..scope import can_see, linked_channel_ids, project_channel_ids, tracked_channel_ids, user_status
from ..tg.links import ChannelLink, parse_channel_link, split_links
from .deps import DB, Admin, Principal, Viewer
from .posts import avatar_url, iso

router = APIRouter(prefix="/api/channels", tags=["channels"])

NO_PLAN_TEXT = "Нет действующего тарифа — выберите тариф в настройках, чтобы добавлять каналы"
NO_EDIT_TEXT = "Добавлять и удалять каналы может владелец проекта или сотрудник с таким правом"

STATS_WINDOW = timedelta(days=7)


def channel_out(ch: Channel, stats: dict | None = None, *, viewer: Principal | None = None,
                added_at=None, users: int | None = None, accounts: dict | None = None, own: bool = False) -> dict:
    stats = stats or {}
    for_user = viewer is not None and not viewer.is_admin
    status, detail = user_status(ch) if for_user else (ch.status, ch.status_detail)
    out = {
        "id": ch.id,
        "link": ch.link,
        "title": ch.title,
        "username": ch.username,
        # every public @username it has now, the main one first (none: it is private)
        "usernames": ch.usernames if ch.usernames is not None else ([ch.username] if ch.username else []),
        "subscribers": ch.subscribers,
        "status": status,
        "status_detail": detail,
        "account_id": None if for_user else ch.account_id,
        "avatar_url": avatar_url(ch),
        "last_post_at": iso(ch.last_post_at),
        # a user sees when they added the channel, not when anyone did
        "created_at": iso(added_at or ch.created_at),
        "posts_per_day": round(stats.get("posts", 0) / STATS_WINDOW.days, 1),
        "avg_er": round(stats.get("er") or 0, 2),
        "avg_fr": round(stats.get("fr") or 0, 2),
        "avg_views": int(stats.get("views") or 0),
    }
    if for_user:
        out["own"] = own  # the project's own channel: not counted towards the limit, cannot be removed here
    else:
        out["users"] = users or 0  # how many users track it
        out["held"] = ch.held  # added by the admin: kept even when no user tracks it
        if accounts is not None:
            names, backups = accounts["names"], accounts["backups"].get(ch.id, [])
            out["account_name"] = names.get(ch.account_id)
            # backup accounts of a private channel (see worker/reserves.py)
            out["backups"] = [
                {"name": names.get(r.account_id) or f"#{r.account_id}", "status": r.status, "due_at": iso(r.due_at)}
                for r in backups
            ]
    return out


async def channel_stats(db, channel_ids: list[int] | None = None) -> dict[int, dict]:
    """7-day post count and averages per channel; shared by the web UI and the external API."""
    stmt = (
        select(Post.channel_id, func.count(), func.avg(Post.er), func.avg(Post.views), func.avg(Post.fr))
        .where(Post.posted_at >= utcnow() - STATS_WINDOW)
        .group_by(Post.channel_id)
    )
    if channel_ids is not None:
        stmt = stmt.where(Post.channel_id.in_(channel_ids))
    rows = await db.execute(stmt)
    return {cid: {"posts": n, "er": er, "views": views, "fr": fr} for cid, n, er, views, fr in rows}


@router.get("")
async def list_channels(viewer: Viewer, db: DB) -> dict:
    stats = await channel_stats(db)
    if viewer.is_admin:
        channels = (
            await db.execute(select(Channel).where(Channel.status != "deleting").order_by(Channel.created_at.desc()))
        ).scalars().all()
        # how many people track it, in any of their projects
        users = dict((await db.execute(
            select(ProjectChannel.channel_id, func.count(func.distinct(Project.owner_id)))
            .join(Project, Project.id == ProjectChannel.project_id)
            .group_by(ProjectChannel.channel_id)
        )).all())
        backups: dict[int, list[ChannelReserve]] = defaultdict(list)
        for r in (await db.execute(select(ChannelReserve).order_by(ChannelReserve.created_at))).scalars():
            backups[r.channel_id].append(r)
        names = {a.id: a.name or a.phone for a in (await db.execute(select(Account))).scalars()}
        accounts = {"names": names, "backups": backups}
        return {"items": [channel_out(c, stats.get(c.id), viewer=viewer, users=users.get(c.id), accounts=accounts)
                          for c in channels]}
    rows = (await db.execute(
        select(Channel, ProjectChannel.created_at, ProjectChannel.own, ProjectChannel.paused_at)
        .join(ProjectChannel, ProjectChannel.channel_id == Channel.id)
        .where(ProjectChannel.project_id == viewer.project_id, Channel.status != "deleting")
        .order_by(ProjectChannel.own.desc(), ProjectChannel.created_at.desc(), Channel.id.desc())
    )).all()
    plan = plan_of(viewer.plan)
    return {
        # over the plan's limit: not read till a plan with room is bought, or another channel is paused for it
        "items": [{**channel_out(c, stats.get(c.id), viewer=viewer, added_at=added, own=own),
                   "paused": paused is not None} for c, added, own, paused in rows],
        "limit": plan.channels if plan else 0,  # None: no limit
    }


class AddIn(BaseModel):
    links: str = Field(max_length=100_000)


async def channel_for(db, link: ChannelLink) -> Channel:
    """The channel behind a link: the one already in the system (wanted again if it was being left),
    or a new one for an account to join."""
    if link.username:
        # any of its @usernames, not only the main one
        name = link.username.lower()
        # in the JSON list as "name" ("_" is a wildcard of LIKE: taken literally)
        alias = '%"' + name.replace("_", r"\_") + '"%'
        found = or_(func.lower(Channel.username) == name,
                    func.lower(cast(Channel.usernames, String)).like(alias, escape="\\"))
    else:
        found = func.lower(Channel.invite_hash) == link.invite_hash.lower()
    ch = await db.scalar(select(Channel).where(found).order_by(Channel.id).limit(1))
    if ch is None:
        ch = Channel(
            link=f"@{link.username}" if link.username else f"t.me/+{link.invite_hash}",
            username=link.username,
            invite_hash=link.invite_hash,
            status="pending",
        )
        db.add(ch)
        await db.flush()
    else:
        _revive(ch)
    return ch


def _revive(ch: Channel) -> None:
    """Someone wants a channel again: stop leaving it, and retry it if it failed."""
    ch.orphaned_at = None
    if ch.status == "deleting":
        ch.status = "pending"  # the worker joins it again if it already left
    elif ch.status == "error":
        ch.status, ch.status_detail = "pending", None


@router.post("")
async def add_channels(body: AddIn, viewer: Viewer, db: DB) -> dict:
    """The admin adds channels to the system (held); a user subscribes their project to them — to an
    existing channel instantly, a new one is joined by an account first."""
    by_key: dict[str, Channel] = {}
    for ch in (await db.execute(select(Channel))).scalars():
        # a channel answers to every @username it has
        for name in [*(ch.usernames or []), *([ch.username] if ch.username else [])]:
            by_key.setdefault(f"@{name.lower()}", ch)
        if ch.invite_hash:
            by_key[f"+{ch.invite_hash}"] = ch
    if not viewer.can_edit_channels:
        raise HTTPException(status_code=403, detail=NO_EDIT_TEXT)
    mine: set[int] = set()
    own: set[int] = set()  # the project's own channel does not count towards the limit
    limit = None
    working: set[int] = set()  # the channels the project reads: a paused one does not count
    if not viewer.is_admin:
        mine = set((await db.execute(linked_channel_ids(viewer.project_id))).scalars())
        working = set((await db.execute(project_channel_ids(viewer.project_id))).scalars())
        own = set((await db.execute(select(ProjectChannel.channel_id).where(
            ProjectChannel.project_id == viewer.project_id, ProjectChannel.own
        ))).scalars())
        plan = plan_of(viewer.plan)
        if plan is None:
            raise HTTPException(status_code=403, detail=NO_PLAN_TEXT)
        limit = plan.channels  # None: no limit

    added: list[Channel] = []
    duplicates, invalid, over_limit = [], [], []
    seen: set[str] = set()
    for raw in split_links(body.links):
        try:
            link = parse_channel_link(raw)
        except ValueError:
            invalid.append(raw)
            continue
        ch = by_key.get(link.key)
        if link.key in seen or (ch is not None and (ch.id in mine if not viewer.is_admin else ch.status != "deleting")):
            if viewer.is_admin and ch is not None:
                ch.held = True
            duplicates.append(raw)
            continue
        seen.add(link.key)
        if limit is not None and len(working - own) >= limit:
            over_limit.append(raw)
            continue
        if ch is None:
            ch = Channel(
                link=f"@{link.username}" if link.username else f"t.me/+{link.invite_hash}",
                username=link.username,
                invite_hash=link.invite_hash,
                status="pending",
            )
            db.add(ch)
            await db.flush()
            by_key[link.key] = ch
        else:
            _revive(ch)
        if viewer.is_admin:
            ch.held = True
        else:
            db.add(ProjectChannel(project_id=viewer.project_id, channel_id=ch.id))
            mine.add(ch.id)
            working.add(ch.id)
        added.append(ch)
    await db.commit()
    return {
        "added": [channel_out(c, viewer=viewer) for c in added],
        "duplicates": duplicates,
        "invalid": invalid,
        "over_limit": over_limit,
        "limit": limit,
    }


class RemoveIn(BaseModel):
    links: str = Field(max_length=100_000)
    # only tell what would go (the page asks «Перестать отслеживать 12 каналов?» first)
    dry: bool = False


@router.post("/remove")
async def remove_channels(body: RemoveIn, viewer: Viewer, db: DB) -> dict:
    """«Удалить списком»: the project stops tracking the channels of the links, as one by one (a channel goes
    once nobody needs it). The project's own channel stays; a link not among the project's channels is told."""
    if viewer.is_admin or viewer.project_id is None:
        raise HTTPException(status_code=400, detail="Списком удаляются каналы проекта")
    if not viewer.can_edit_channels:
        raise HTTPException(status_code=403, detail=NO_EDIT_TEXT)
    links = {pc.channel_id: pc for pc in (await db.execute(
        select(ProjectChannel).where(ProjectChannel.project_id == viewer.project_id))).scalars()}
    by_key: dict[str, Channel] = {}
    for ch in (await db.execute(select(Channel).where(Channel.id.in_(links)))).scalars():
        for name in [*(ch.usernames or []), *([ch.username] if ch.username else [])]:
            by_key.setdefault(f"@{name.lower()}", ch)
        if ch.invite_hash:
            by_key[f"+{ch.invite_hash}"] = ch
    found: dict[int, Channel] = {}
    not_found, invalid, own = [], [], []
    for raw in split_links(body.links):
        try:
            link = parse_channel_link(raw)
        except ValueError:
            invalid.append(raw)
            continue
        ch = by_key.get(link.key)
        if ch is None:
            not_found.append(raw)
        elif links[ch.id].own:
            own.append(raw)
        else:
            found[ch.id] = ch
    if not body.dry:
        for ch in found.values():
            await db.delete(links[ch.id])
        await db.flush()
        for ch in found.values():
            await mark_if_orphaned(db, ch)
        await db.commit()
    return {"removed": len(found), "not_found": not_found, "invalid": invalid, "own": own}


class PatchIn(BaseModel):
    action: str = Field(pattern="^(pause|resume|retry)$")


def tme_link(ch: Channel) -> str:
    """A channel's t.me link: its @username, else its invite link."""
    if ch.username:
        return f"https://t.me/{ch.username}"
    if ch.invite_hash:
        return f"https://t.me/+{ch.invite_hash}"
    return ch.link if ch.link.startswith("http") else f"https://{ch.link.lstrip('@')}"


def list_file(project_title: str, channels: list[Channel]) -> bytes:
    lines = [f"# Отслеживаемые каналы проекта «{project_title}»", "", f"Каналов: {len(channels)}", ""]
    lines += [f"- {tme_link(c)}" for c in channels]
    return ("\n".join(lines) + "\n").encode("utf-8")


@router.post("/list-file")
async def send_list_file(viewer: Viewer, db: DB) -> dict:
    """«Файл-список отслеживаемых каналов»: the bot sends the project's channels to the person's own
    Telegram as channel-list.md, one t.me link a line. A staff member needs their Telegram linked."""
    if viewer.is_admin or viewer.project_id is None:
        raise HTTPException(status_code=400, detail="Список каналов — у проекта")
    if viewer.role == "member":
        member = await db.get(Member, viewer.member_id)
        chat_id = member.tg_user_id if member else None
        if chat_id is None:
            raise HTTPException(status_code=409, detail="Привяжите Telegram в настройках, чтобы получить файл")
    else:
        chat_id = (await db.get(User, viewer.user_id)).tg_user_id
    cfg = await settings_store.get_all(db)
    if not cfg.get("notify_bot_token"):
        raise HTTPException(status_code=409, detail="Бот сейчас не подключён — попробуйте позже")
    owned = await user_projects(db, viewer.user_id)
    places = [p.id for p in owned]
    project = next((p for p in owned if p.id == viewer.project_id), None)
    title = project_title(project, places.index(project.id)) if project else "Проект"
    rows = (await db.execute(select(Channel).where(Channel.id.in_(tracked_channel_ids(viewer.project_id)))
                             .order_by(func.lower(func.coalesce(Channel.title, Channel.username, Channel.link)))
                             )).scalars().all()
    count = len(rows)
    caption = f"Отслеживаемые каналы проекта «{notify.esc(title)}»: {count}"
    try:
        await notify.bot_document(str(cfg["notify_bot_token"]), chat_id, "channel-list.md",
                                  list_file(title, list(rows)), caption)
    except notify.BotError as exc:
        if "blocked" in str(exc).lower() or "chat not found" in str(exc).lower():
            raise HTTPException(status_code=409,
                                detail=f"Откройте бота @{cfg.get('notify_bot_username')} в Telegram и нажмите «Старт»") from exc
        raise HTTPException(status_code=502, detail="Не удалось отправить файл, попробуйте позже") from exc
    return {"ok": True, "count": count}


@router.patch("/{channel_id}")
async def patch_channel(channel_id: int, body: PatchIn, _: Admin, db: DB) -> dict:
    ch = await db.get(Channel, channel_id)
    if ch is None or ch.status == "deleting":
        raise HTTPException(status_code=404)
    if body.action == "pause" and ch.status in ("active", "pending", "restricted"):
        ch.status = "paused"
    elif body.action == "resume" and ch.status == "paused":
        ch.status = "active" if ch.account_id and ch.tg_id else "pending"
    elif body.action == "retry" and ch.status == "error":
        ch.status, ch.status_detail = "pending", None
    else:
        raise HTTPException(status_code=409, detail="Действие недоступно в текущем статусе")
    await db.commit()
    return channel_out(ch)


class PausedIn(BaseModel):
    paused: bool


@router.put("/{channel_id}/paused")
async def set_paused(channel_id: int, body: PausedIn, viewer: Viewer, db: DB) -> dict:
    """«Выберите, какие каналы оставить»: over the plan's limit, a channel is paused for another to be read."""
    if viewer.is_admin or viewer.project_id is None:
        raise HTTPException(status_code=400, detail="Каналы ставятся на паузу в проекте")
    if not viewer.can_edit_channels:
        raise HTTPException(status_code=403, detail=NO_EDIT_TEXT)
    link = await db.get(ProjectChannel, (viewer.project_id, channel_id))
    if link is None:
        raise HTTPException(status_code=404)
    if link.own:
        raise HTTPException(status_code=409, detail="Канал проекта не ставится на паузу")
    plan = plan_of(viewer.plan)
    if plan is None:
        raise HTTPException(status_code=403, detail=NO_PLAN_TEXT)
    if not body.paused and link.paused_at is not None and plan.channels is not None:
        working = await db.scalar(select(func.count()).where(
            ProjectChannel.project_id == viewer.project_id, ~ProjectChannel.own, ProjectChannel.paused_at.is_(None)))
        if working >= plan.channels:
            raise HTTPException(status_code=409, detail=f"По тарифу {plan.title} читаются {plan.channels} каналов — "
                                                        "сначала поставьте на паузу другой")
    link.paused_at = utcnow() if body.paused else None
    await db.commit()
    return {"paused": link.paused_at is not None}


@router.delete("/{channel_id}")
async def delete_channel(channel_id: int, viewer: Viewer, db: DB) -> dict:
    ch = await db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404)
    if not viewer.can_edit_channels:
        raise HTTPException(status_code=403, detail=NO_EDIT_TEXT)
    if not viewer.is_admin:
        # a project only stops tracking it; the channel goes once nobody needs it (see orphans.py)
        link = await db.get(ProjectChannel, (viewer.project_id, ch.id))
        if link is None:
            raise HTTPException(status_code=404)
        if link.own:
            raise HTTPException(status_code=409, detail="Это канал проекта — его можно сменить в настройках")
        await db.delete(link)
        await db.flush()
        await mark_if_orphaned(db, ch)
        await db.commit()
        return {"ok": True}
    # the admin removes it from the system, for every user who tracks it
    if ch.tg_id is None:
        # never joined: nothing to leave
        await db.delete(ch)
    else:
        # the worker leaves the channel, then deletes it with its posts
        ch.status = "deleting"
    await db.commit()
    return {"ok": True}


@router.get("/{channel_id}/stats")
async def stats(
    channel_id: int, viewer: Viewer, db: DB,
    period: str = Query("30d", pattern="^(7d|30d|90d)$"), tz: int = Query(0, ge=-900, le=900),
) -> dict:
    """«Статистика» of the project's own channel (the admin: of any channel)."""
    channel = await db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Канал не найден")
    others: list[int] = []
    if not viewer.is_admin:
        own = await db.scalar(select(ProjectChannel.own).where(
            ProjectChannel.project_id == viewer.project_id, ProjectChannel.channel_id == channel_id))
        if not own:
            raise HTTPException(status_code=403, detail="Статистика есть только у канала проекта")
        others = list((await db.execute(select(ProjectChannel.channel_id).where(
            ProjectChannel.project_id == viewer.project_id, ~ProjectChannel.own))).scalars())
    return await project_channel_stats(db, channel_id, others, period, tz, channel.subscribers)


@router.get("/{channel_id}/followers")
async def followers(channel_id: int, _: Admin, db: DB) -> dict:
    """Who tracks the channel: every project with it, and the person whose project it is."""
    rows = (await db.execute(
        select(ProjectChannel, Project, User)
        .join(Project, Project.id == ProjectChannel.project_id)
        .join(User, User.id == Project.owner_id)
        .where(ProjectChannel.channel_id == channel_id)
        .order_by(ProjectChannel.created_at)
    )).all()
    owners = {u.id for _, _, u in rows}
    # a project is named by its place among its owner's projects until it has a channel
    places = {p.id: i for owner in owners for i, p in enumerate(await user_projects(db, owner))}
    return {"items": [
        {
            "user": {"id": u.id, "name": " ".join(x for x in (u.first_name, u.last_name) if x) or None,
                     "username": u.username},
            "project": {"id": p.id, "title": project_title(p, places.get(p.id, 0))},
            "own": link.own,  # the project's own channel
            "added_at": iso(link.created_at),
        }
        for link, p, u in rows
    ]}


@router.get("/{channel_id}/avatar")
async def avatar(channel_id: int, viewer: Viewer, db: DB) -> Response:
    data = await db.scalar(select(Channel.avatar).where(Channel.id == channel_id)) if await can_see(
        db, viewer, channel_id) else None
    if not data:
        raise HTTPException(status_code=404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})
