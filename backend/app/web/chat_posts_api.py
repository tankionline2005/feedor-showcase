"""The posts the AI wrote for the project's channel (chat_posts.py) — in «Чат» and in «Публикации» (publications.py):
shown as posts, made from an answer by hand («Оформить как пост»), edited, sent to the person's own Telegram,
published in the project's channel. A post of one's own chat is one's own; one of «Публикации» everyone of the
project sees, and the owner and staff allowed to publish edit and publish it."""

import logging
import zlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from .. import chat_posts, covers, post_style, publications, settings_store, tg_html
from ..models import (Channel, ChatMessage, ChatPost, ChatPostSource, ChatThread, CoverSettings, Member, Post, Project,
                      ProjectGroup, Rubric, Topic, User, utcnow)
from ..notify import BotError
from ..scope import own_channel_ids
from .chat_api import _allowed, _mine, _thread
from .deps import DB, Principal, Viewer
from .posts import GROUP_GONE, avatar_url, favorite_ids, iso, post_out

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat-posts", tags=["chat posts"])


async def channel_header(db, project_id: int) -> dict:
    """Whose post it is: the project's own channel, as the feed shows a channel."""
    ch = await db.scalar(select(Channel).where(Channel.id.in_(own_channel_ids(project_id))))
    if ch is None:
        return {"title": "Ваш канал", "avatar_url": None, "seed": project_id}
    return {"title": ch.title or (f"@{ch.username}" if ch.username else "Ваш канал"), "avatar_url": avatar_url(ch),
            "seed": ch.id}


async def _channel_of(db, post: ChatPost) -> int | str | None:
    """The project's channel, for a post published before its messages were kept (chat_posts.published_of)."""
    if post.published_at is None or post.published_chat:
        return None
    project = await db.get(Project, post.project_id) if post.project_id else None
    target = await chat_posts.publish_target(db, project) if project else None
    return target.chat_id if target else None


async def card(db, post: ChatPost, header: dict, editable: bool = True) -> dict:
    sources = await chat_posts.sources_of(db, post.id)
    cands = chat_posts.candidates(sources)
    media = chat_posts.chosen(post, cands)
    return {
        "id": post.id, "text": post.text, "ai_text": post.ai_text, "media": media, "candidates": cands, "channel": header,
        "edited": post.text != post.ai_text, "sent_at": iso(post.sent_at),
        # written by its author themselves (the API), not the AI: who
        "written_by": post.written_by,
        # «N источников», a page of its own
        "sources": len(sources),
        "published_at": iso(post.published_at), "published_url": post.published_url,
        # «Отложенная публикация»; and why a scheduled one did not happen
        "scheduled_at": iso(post.scheduled_at),
        # «Публикации»: what it came from, who published or scheduled it; may this person edit and publish it
        "topic": await _topic_of(db, post), "published_by": post.published_by, "scheduled_by": post.scheduled_by,
        "editable": editable,
        "publish_error": post.publish_error, "publish_error_at": iso(post.publish_error_at),
        # an issue of a rubric: which
        "rubric": await _rubric_of(db, post),
        # why «Автопостинг» left it a suggestion — while it still is one
        "autopost_note": post.autopost_note if not (post.published_at or post.scheduled_at) else None,
        # «Изменить в канале» / «Удалить из канала»: may each be done, why not; «изменён»; «Удалён из канала»
        "channel_actions": chat_posts.channel_actions(post, await _channel_of(db, post)),
        "published_edited_at": iso(post.published_edited_at),
        "withdrawn_at": iso(post.withdrawn_at), "withdrawn_by": post.withdrawn_by,
        # «Обложка»: its picture (made when shown), its title
        "cover": post.cover,
        "cover_url": f"/api/chat-posts/{post.id}/cover.jpg?v={await _cover_version(db, post)}" if post.cover else None,
        "cover_title": post.cover_title or covers.first_line(post.text),
        # Telegram's limit for its text: a caption under pictures, or a message
        "limit": tg_html.limit(bool(media) or post.cover), "length": tg_html.length(post.text),
        "max_media": chat_posts.MAX_MEDIA,
    }


async def cards_of(db, thread: ChatThread, viewer: Principal) -> list[tuple[ChatPost, dict]]:
    """The posts of the viewer's own chat: theirs to edit — but one scheduled or out, only if they may publish."""
    posts = (await db.execute(select(ChatPost).where(ChatPost.thread_id == thread.id).order_by(ChatPost.id))).scalars().all()
    if not posts:
        return []
    header = await channel_header(db, thread.project_id)
    return [(p, await card(db, p, header, editable=not _going(p) or viewer.may("ai_publish"))) for p in posts]


def _going(post: ChatPost) -> bool:
    """Scheduled or out in the channel: what changes it changes the channel — for those who may publish."""
    return bool(post.scheduled_at or post.published_at)


async def _cover_version(db, post: ChatPost) -> int:
    """Changes when what the cover shows may: the title, the text, the project's cover style — the browser takes a
    new picture then."""
    style = await db.get(CoverSettings, post.project_id) if post.project_id else None
    stamp = style.updated_at.isoformat() if style else ""
    return zlib.crc32(f"{post.cover_title}|{post.text}|{stamp}".encode())


async def _rubric_of(db, post: ChatPost) -> dict | None:
    if not post.rubric_id:
        return None
    rubric = await db.get(Rubric, post.rubric_id)
    return {"id": rubric.id, "name": rubric.name} if rubric else None


async def _topic_of(db, post: ChatPost) -> dict | None:
    if not post.topic_id:
        return None
    topic = await db.get(Topic, post.topic_id)
    if topic is None:
        return None
    return {"id": topic.id, "title": topic.title, "importance": topic.importance}


async def may_edit(db, viewer: Principal, post: ChatPost) -> bool:
    """Its author (their own chat), or — a post of «Публикации» — the owner and staff allowed to publish."""
    if post.thread_id and await db.scalar(select(ChatThread.id).where(ChatThread.id == post.thread_id, _mine(viewer))):
        return not _going(post) or viewer.may("ai_publish")
    return publications.in_list(post) and viewer.may("ai_publish")


async def _own(db, viewer: Principal, post_id: int, *, write: bool = False) -> ChatPost:
    """A post this person may see: one of their own chat, or one of «Публикации» (anyone of the project).
    `write` — editing, publishing: the author, or for «Публикации» the owner and staff allowed to publish."""
    if viewer.is_admin:
        raise HTTPException(status_code=403, detail="Посты ИИ — для владельцев проектов и их сотрудников")
    post = await db.get(ChatPost, post_id)
    if post is None or post.project_id != viewer.project_id:
        raise HTTPException(status_code=404, detail="Пост не найден")
    mine = bool(post.thread_id) and bool(await db.scalar(
        select(ChatThread.id).where(ChatThread.id == post.thread_id, _mine(viewer))))
    if mine:
        if not publications.in_list(post):
            await _allowed(db, viewer)  # a draft of one's chat: as the chat is
        if write and _going(post) and not viewer.may("ai_publish"):
            # theirs, scheduled or out while they still could publish: no longer theirs to change
            raise HTTPException(status_code=403, detail="Владелец проекта не разрешил вам публиковать посты в канал")
        return post
    if not publications.in_list(post):
        raise HTTPException(status_code=404, detail="Пост не найден")
    if write and not viewer.may("ai_publish"):
        raise HTTPException(status_code=403, detail="Владелец проекта не разрешил вам публиковать посты в канал")
    return post


class WrittenIn(BaseModel):
    text: str = Field(max_length=40000)
    source_post_ids: list[int] = Field(default_factory=list, max_length=chat_posts.MAX_SOURCES)
    cover: bool = False
    cover_title: str | None = Field(default=None, max_length=200)


@router.post("")
async def write(body: WrittenIn, viewer: Viewer, db: DB) -> dict:
    """A post of one's own text (the API, the MCP connector): to «Публикации», as those who may publish see it —
    to edit, publish or schedule like any other. The pictures: of the project's posts it names."""
    if viewer.is_admin:
        raise HTTPException(status_code=403, detail="Посты — для владельцев проектов и их сотрудников")
    if not viewer.may("ai_publish"):
        raise HTTPException(status_code=403, detail="Владелец проекта не разрешил вам публиковать посты в канал")
    text = tg_html.clean(body.text)
    if not tg_html.plain(text).strip():
        raise HTTPException(status_code=422, detail="Пост пустой — напишите текст")
    if (size := tg_html.length(text)) > tg_html.TEXT_LIMIT:
        raise HTTPException(status_code=422, detail=f"Текст длиннее, чем Telegram разрешает в посте: {size} из "
                                                    f"{tg_html.TEXT_LIMIT} символов")
    made = await chat_posts.create_written(viewer.project_id, text, body.source_post_ids, viewer.who)
    if isinstance(made, str):
        raise HTTPException(status_code=422, detail=made)
    post = await db.get(ChatPost, made.id)
    if body.cover:
        post.cover = True
        post.cover_title = " ".join((body.cover_title or "").split()) or None
        await db.commit()
    return await card(db, post, await channel_header(db, post.project_id), editable=True)


@router.post("/from-message/{message_id}")
async def from_message(message_id: int, viewer: Viewer, db: DB) -> dict:
    """«Оформить как пост»: an answer of the AI becomes a post (the model wrote it as text). Its pictures come
    from the posts it links to, and the topic the chat was begun from."""
    await _allowed(db, viewer)
    msg = await db.get(ChatMessage, message_id)
    if msg is None or msg.role != "assistant":
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    thread = await _thread(db, viewer, msg.thread_id)
    made = await db.scalar(select(ChatPost).where(ChatPost.message_id == msg.id))
    if made is None:
        # the answer as the page shows it: its parts around the tool calls, up to the next question
        parts = (await db.execute(select(ChatMessage).where(ChatMessage.thread_id == thread.id,
                                                            ChatMessage.id >= msg.id).order_by(ChatMessage.id))).scalars()
        text = []
        for m in parts:
            if m.role not in ("assistant", "tool"):
                break
            if m.role == "assistant" and (m.content or "").strip():
                text.append(m.content)
        answer = "\n\n".join(text)
        made = await chat_posts.create(thread, tg_html.from_markdown(answer),
                                       await chat_posts.linked_posts(thread.project_id, answer),
                                       message_id=msg.id, strict=False)
        if isinstance(made, str):
            raise HTTPException(status_code=422, detail="Этот ответ слишком длинный для поста Telegram — попросите ИИ "
                                                        "сократить его" if "too long" in made else "В ответе нет текста")
        made = await db.get(ChatPost, made.id)
    return await card(db, made, await channel_header(db, thread.project_id))


class EditIn(BaseModel):
    text: str | None = Field(default=None, max_length=60_000)  # Telegram's HTML, from the editor
    media: list[dict] | None = None  # [{"post", "slot"}], in order


@router.patch("/{post_id}")
async def edit(post_id: int, body: EditIn, viewer: Viewer, db: DB) -> dict:
    """The person's edit: the text (in Telegram's formatting) and the pictures chosen among the sources'. Telegram's
    limit holds: 1024 characters under pictures, 4096 without."""
    post = await _own(db, viewer, post_id, write=True)
    cands = chat_posts.candidates(await chat_posts.sources_of(db, post.id))
    media = post.media or []
    if body.media is not None:
        checked = chat_posts.check_media(body.media, cands)
        if isinstance(checked, str):
            raise HTTPException(status_code=422, detail=checked)
        media = checked
    text = tg_html.clean(body.text) if body.text is not None else post.text
    if not tg_html.plain(text).strip():
        raise HTTPException(status_code=422, detail="Пост пустой — напишите текст")
    size, limit = tg_html.length(text), tg_html.limit(bool(media) or post.cover)
    if size > limit:
        raise HTTPException(status_code=422, detail=f"Текст длиннее, чем Telegram разрешает {'под фото и видео' if media else 'в посте'}: "
                                                    f"{size} из {limit} символов")
    rewritten = text != post.text
    if rewritten or media != (post.media or []):
        post.text, post.media = text, media
        if rewritten:  # the text the person made: what the AI learns their way from
            post.edited_at = utcnow()
        await db.commit()
    if rewritten and text != post.ai_text:
        post_style.learn_later(post.project_id, viewer.member_id)
    return await card(db, post, await channel_header(db, post.project_id))


class SendIn(BaseModel):
    chat_id: int | None = None  # one of the project's groups («Группы проекта»); none: the person's own Telegram


@router.post("/{post_id}/send")
async def send(post_id: int, viewer: Viewer, db: DB, body: SendIn | None = None) -> dict:
    """The bot sends the post, with its pictures in their original quality, to the person's own Telegram — or, as
    a feed's post, to one of the owner's groups (a staff member: if allowed to send to groups)."""
    post = await _own(db, viewer, post_id)
    group = None
    if body is not None and body.chat_id is not None:
        group = await db.get(ProjectGroup, (viewer.project_id, body.chat_id))
        if group is None:
            raise HTTPException(status_code=404, detail="Этой группы нет в группах проекта")
        chat_id = group.chat_id
    elif viewer.member_id:
        chat_id = (await db.get(Member, viewer.member_id)).tg_user_id
        if chat_id is None:
            raise HTTPException(status_code=409, detail="Привяжите Telegram в настройках, чтобы получать посты")
    else:
        chat_id = (await db.get(User, viewer.user_id)).tg_user_id
    cfg = await settings_store.get_all(db)
    try:
        try:
            skipped = await chat_posts.send(post.id, chat_id)
        except BotError as exc:
            new_id = exc.parameters.get("migrate_to_chat_id")
            if group is None or not new_id:
                raise
            # the group became a supergroup and got a new id: follow it
            await db.execute(update(ProjectGroup).where(ProjectGroup.chat_id == group.chat_id).values(chat_id=new_id))
            await db.commit()
            group = await db.get(ProjectGroup, (viewer.project_id, new_id))
            skipped = await chat_posts.send(post.id, new_id)
    except BotError as exc:
        text = str(exc).lower()
        if group is not None and any(x in text for x in GROUP_GONE):
            raise HTTPException(
                status_code=409,
                detail=f"Бот не может писать в группу «{group.title or group.username}»: проверьте, что "
                       f"@{cfg['notify_bot_username']} в ней состоит и может отправлять сообщения",
            ) from exc
        if group is None and "blocked" in text:
            raise HTTPException(status_code=409, detail=f"Разблокируйте бота @{cfg['notify_bot_username']} в Telegram") from exc
        log.warning("chat post %s to %s failed: %s", post.id, chat_id, exc)
        raise HTTPException(status_code=502, detail="Не удалось отправить пост, попробуйте позже") from exc
    return {"ok": True, "skipped": skipped, "to": (group.title or group.username) if group else None}


@router.get("/{post_id}/sources")
async def sources(post_id: int, viewer: Viewer, db: DB) -> dict:
    """«N источников»: the posts the post was written from, as the feed shows them, the most important first."""
    post = await _own(db, viewer, post_id)
    rows = (await db.execute(
        select(Post, Channel).join(ChatPostSource, ChatPostSource.post_id == Post.id)
        .join(Channel, Channel.id == Post.channel_id)
        .where(ChatPostSource.chat_post_id == post.id).order_by(ChatPostSource.rank, Post.posted_at.desc())
    )).all()
    starred = await favorite_ids(db, viewer, [p.id for p, _ in rows])
    return {"items": [{**post_out(p, ch, for_user=True), "favorite": p.id in starred} for p, ch in rows]}


class PublishIn(BaseModel):
    # publish without what of the pictures cannot go (asked first)
    partial: bool = False


@router.post("/{post_id}/publish")
async def publish(post_id: int, viewer: Viewer, db: DB, body: PublishIn | None = None) -> dict:
    """The bot publishes the post in the project's channel at once, with its pictures and videos. The owner always
    may; a staff member, if allowed. When some of the pictures cannot go, nothing is published:
    {published: False, skipped} — and with `partial` it goes without them."""
    post = await _own(db, viewer, post_id, write=True)
    project, target = await _project_channel(db, viewer)
    try:
        done = await chat_posts.publish(post.id, target.chat_id, target.username, partial=bool(body and body.partial),
                                        by=viewer.who)
    except chat_posts.ChannelNotReady as exc:
        raise await _not_ready(db, viewer, project, exc) from exc
    except BotError as exc:
        log.warning("chat post %s to the channel %s failed: %s", post.id, target.chat_id, exc)
        raise HTTPException(status_code=502, detail="Не удалось опубликовать пост, попробуйте позже") from exc
    if done["published"]:  # «Опубликовать сейчас» of a scheduled one: the schedule is done with
        await db.execute(update(ChatPost).where(ChatPost.id == post.id).values(
            scheduled_at=None, publish_error=None, publish_error_at=None))
        await db.commit()
    return done


class CoverIn(BaseModel):
    on: bool
    title: str | None = Field(default=None, max_length=200)


@router.put("/{post_id}/cover")
async def set_cover(post_id: int, body: CoverIn, viewer: Viewer, db: DB) -> dict:
    """«Обложка» on the post or off; its title (empty: the post's first line). With it the post is a post with a
    picture: its text keeps to a caption's 1024 characters, or goes after it as a message of its own."""
    post = await _own(db, viewer, post_id, write=True)
    post.cover = body.on
    if body.title is not None:
        title = " ".join(body.title.split())
        post.cover_title = title or None
    await db.commit()
    return await card(db, post, await channel_header(db, post.project_id))


@router.get("/{post_id}/cover.jpg")
async def cover_picture(post_id: int, viewer: Viewer, db: DB) -> Response:
    """The post's cover as it will go out."""
    post = await _own(db, viewer, post_id)
    return Response(await covers.for_post(db, post), media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=60"})


class ChannelEditIn(BaseModel):
    text: str = Field(max_length=40000)


@router.put("/{post_id}/channel")
async def edit_in_channel(post_id: int, body: ChannelEditIn, viewer: Viewer, db: DB) -> dict:
    """«Изменить в канале»: the published post's text changed in the channel by the bot (its pictures stay)."""
    post = await _own(db, viewer, post_id, write=True)
    _, target = await _project_channel(db, viewer)
    text = tg_html.clean(body.text)
    if not tg_html.plain(text).strip():
        raise HTTPException(status_code=422, detail="Пост пустой — напишите текст")
    try:
        await chat_posts.edit_published(post.id, text, target.chat_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BotError as exc:
        log.warning("chat post %s: editing in the channel failed: %s", post.id, exc)
        raise HTTPException(status_code=502, detail=f"Telegram не дал изменить пост: {exc}") from exc
    await db.refresh(post)
    if post.text != post.ai_text:
        post_style.learn_later(post.project_id, viewer.member_id)  # the person's edit, as any other
    return await card(db, post, await channel_header(db, post.project_id))


@router.delete("/{post_id}/channel")
async def withdraw(post_id: int, viewer: Viewer, db: DB) -> dict:
    """«Удалить из канала»: the bot deletes the published post from the channel; here it stays, and may go again."""
    post = await _own(db, viewer, post_id, write=True)
    _, target = await _project_channel(db, viewer)
    try:
        await chat_posts.withdraw(post.id, viewer.who, target.chat_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BotError as exc:
        log.warning("chat post %s: deleting from the channel failed: %s", post.id, exc)
        raise HTTPException(status_code=502, detail=f"Telegram не дал удалить пост: {exc}. Удалите его в канале "
                                                    "вручную.") from exc
    await db.refresh(post)
    return await card(db, post, await channel_header(db, post.project_id))


async def _project_channel(db, viewer: Principal) -> tuple[Project, chat_posts.Target]:
    """The project's channel the bot publishes in; or why there is none, for people."""
    if viewer.member_id and not viewer.may("ai_publish"):
        raise HTTPException(status_code=403, detail="Владелец проекта не разрешил вам публиковать посты в канал")
    project = await db.get(Project, viewer.project_id)
    target = await chat_posts.publish_target(db, project) if project else None
    if target is None:
        raise HTTPException(status_code=409, detail="Канал проекта не выбран. Выберите его в настройках: «Канал проекта»."
                            if not viewer.member_id else "Владелец проекта ещё не выбрал канал проекта.")
    return project, target


async def _not_ready(db, viewer: Principal, project: Project, exc: chat_posts.ChannelNotReady) -> HTTPException:
    """What the bot lacks in the channel, for people — and, for the page, why (missing | rights) and the bot's
    @username: the owner gets «Добавить бота», Telegram's own way to add it to a channel."""
    bot = str((await settings_store.get_all(db))["notify_bot_username"])
    text = chat_posts.not_ready_text(exc.reason, bot, chat_posts.channel_title(project), bool(viewer.member_id))
    return HTTPException(status_code=409, detail={"message": text, "reason": exc.reason, "bot": bot})


MAX_AHEAD = timedelta(days=365)
MIN_AHEAD = timedelta(minutes=2)


class ScheduleIn(BaseModel):
    at: datetime  # with its time zone, as the person's device has it
    partial: bool = False


@router.put("/{post_id}/schedule")
async def schedule(post_id: int, body: ScheduleIn, viewer: Viewer, db: DB) -> dict:
    """«Отложенная публикация»: the bot publishes the post in the channel then — the site keeps the time, as
    Telegram lets no bot schedule. Checked now as publishing is; what of the pictures cannot go is asked about:
    {scheduled: False, skipped}, and with `partial` it is scheduled without them. The post may still be edited:
    it goes as it is then. A new time takes the place of the one before."""
    post = await _own(db, viewer, post_id, write=True)
    project, target = await _project_channel(db, viewer)
    at = (body.at.astimezone(timezone.utc) if body.at.tzinfo else body.at).replace(tzinfo=None)
    now = utcnow()
    if at < now + MIN_AHEAD - timedelta(seconds=30):
        raise HTTPException(status_code=422, detail="Выберите время хотя бы на пару минут позже, чем сейчас")
    if at > now + MAX_AHEAD:
        raise HTTPException(status_code=422, detail="Запланировать можно не больше чем на год вперёд")
    try:
        skipped = await chat_posts.can_publish(post.id, target.chat_id, partial=body.partial)
    except chat_posts.ChannelNotReady as exc:
        raise await _not_ready(db, viewer, project, exc) from exc
    except BotError as exc:
        raise HTTPException(status_code=502, detail="Не удалось проверить канал, попробуйте позже") from exc
    if skipped:
        return {"scheduled": False, "skipped": skipped}
    post.scheduled_at, post.scheduled_partial, post.scheduled_by = at, body.partial, viewer.who
    post.publish_error = post.publish_error_at = None
    await db.commit()
    return {"scheduled": True, "scheduled_at": iso(at)}


@router.delete("/{post_id}/schedule")
async def unschedule(post_id: int, viewer: Viewer, db: DB) -> dict:
    """«Отменить публикацию» of a scheduled post (and a failed one's note goes with it)."""
    post = await _own(db, viewer, post_id, write=True)
    post.scheduled_at = post.publish_error = post.publish_error_at = None
    await db.commit()
    return {"ok": True}


@router.delete("/{post_id}")
async def delete_post(post_id: int, viewer: Viewer, db: DB) -> dict:
    """A post the AI wrote for a topic, or a rubric's issue, that nobody wants: gone from «Публикации». One published
    or scheduled stays."""
    post = await _own(db, viewer, post_id, write=True)
    if not (post.topic_id or post.rubric_id or post.written_by) or post.thread_id or post.published_at \
            or post.scheduled_at:
        raise HTTPException(status_code=409, detail="Удалить можно только пост, который ещё не публиковали")
    await db.delete(post)
    await db.commit()
    return {"ok": True}
