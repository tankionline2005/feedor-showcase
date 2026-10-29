"""AI → «Чат»: a person's chats with the AI in the current project, and talking in one (see chat.py).

An answer comes as it is written: the reply is a stream of JSON lines — what the AI is looking at
("status"), the pieces of its text ("delta"), and "done" or "error" at the end.

The answer is written by a task of its own, not by the request: the person may leave the chat (or
lose the connection) and the answer still comes and is saved. Opening the chat meanwhile shows what
is written so far ("pending"), and the page follows it until it is done.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update

from .. import ai, chat, tg_html
from ..db import SessionLocal
from ..models import AiUsage, ChatMessage, ChatPost, ChatThread, Member, Project, Topic, utcnow
from .deps import DB, Principal, Viewer
from .posts import iso

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["chat"])


@dataclass
class Job:
    """An answer being written: what there is of it so far, and the pages following it."""

    text: str = ""
    status: str | None = None
    started: float = field(default_factory=time.monotonic)  # the question came: the answer's time runs from here
    listeners: list[asyncio.Queue] = field(default_factory=list)

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def publish(self, event: dict) -> None:
        if event["type"] == "delta":
            # a piece after a tool call starts a new paragraph, as the page shows it
            glue = "\n\n" if self.status and self.text and not self.text.endswith("\n") else ""
            self.text += glue + event["text"]
            self.status = None
        elif event["type"] == "status":
            self.status = event["text"]
        for q in self.listeners:
            q.put_nowait(event)


_jobs: dict[int, Job] = {}  # chats the AI is answering in right now
_tasks: set[asyncio.Task] = set()  # kept referenced, or a running answer could be garbage-collected


async def _allowed(db, viewer: Principal) -> None:
    if viewer.is_admin:
        raise HTTPException(status_code=403, detail="Чат с ИИ — для владельцев проектов и их сотрудников")
    if viewer.member_id and not (await db.get(Member, viewer.member_id)).ai_chat:
        raise HTTPException(status_code=403, detail="Владелец проекта не разрешил вам общаться с ИИ")


async def over_limit(db, viewer: Principal) -> str | None:
    """A staff member whose chats this month cost what the owner allowed them: why they cannot go on."""
    if not viewer.member_id:
        return None
    member = await db.get(Member, viewer.member_id)
    if member.ai_chat_limit_usd is None:
        return None
    spent = await db.scalar(select(func.coalesce(func.sum(ai.PAID), 0.0)).where(
        AiUsage.member_id == member.id, AiUsage.operation == "chat", AiUsage.at >= ai.month_start()))
    if float(spent or 0) < member.ai_chat_limit_usd:
        return None
    return (f"Ваш лимит на чат с ИИ в этом месяце (${member.ai_chat_limit_usd:g}) исчерпан — "
            "поднять его может владелец проекта")


def _mine(viewer: Principal):
    """A person's own chats only: the owner does not see their staff's."""
    who = ChatThread.member_id == viewer.member_id if viewer.member_id else (
        (ChatThread.user_id == viewer.user_id) & ChatThread.member_id.is_(None))
    return (ChatThread.project_id == viewer.project_id) & who


async def _thread(db, viewer: Principal, thread_id: int) -> ChatThread:
    thread = await db.scalar(select(ChatThread).where(ChatThread.id == thread_id, _mine(viewer)))
    if thread is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return thread


def answered():
    """The AI's messages that are answers: with text, or that gave a post (they have none); and failures."""
    return (ChatMessage.role.in_(("assistant", "error"))
            & ((ChatMessage.content != "") | ChatMessage.id.in_(select(ChatPost.message_id).where(
                ChatPost.message_id.is_not(None)))))


async def _post_texts(db, messages: list[ChatMessage]) -> dict[int, str]:
    """An answer that is a post, in a word: «Предложение поста: …»."""
    ids = [m.id for m in messages if not (m.content or "").strip()]
    if not ids:
        return {}
    rows = (await db.execute(select(ChatPost.message_id, ChatPost.text).where(ChatPost.message_id.in_(ids)))).all()
    return {mid: "Предложение поста: " + tg_html.plain(text)[:200] for mid, text in rows}


def _shown(messages: list[ChatMessage], cards: list[tuple[ChatPost, dict]] | None = None) -> list[dict]:
    """What the page shows: the person's messages and the AI's answers, an answer's parts (around its
    tool calls) as one; our notes and the tools' answers stay hidden. A post the AI gave shows as a post where it
    gave it; an answer made a post by hand, as that post."""
    by_call = {p.tool_call_id: c for p, c in cards or [] if p.tool_call_id}
    by_message = {p.message_id: c for p, c in cards or [] if not p.tool_call_id and p.message_id}
    out: list[dict] = []
    for m in messages:
        if m.role == "user":
            out.append({"id": m.id, "role": "user", "text": m.content, "at": iso(m.created_at)})
        elif m.role == "error":
            # a failed answer: «Отправить заново» under it while it is the chat's last word
            try:
                err = json.loads(m.content)
            except ValueError:
                err = {"text": m.content}
            out.append({"id": m.id, "role": "error", "text": err.get("text") or "", "detail": err.get("detail"),
                        "at": iso(m.created_at)})
        elif m.role == "assistant" and (m.content or "").strip():
            if out and out[-1]["role"] == "assistant":
                out[-1]["text"] += ("\n\n" if out[-1]["text"] else "") + m.content
            else:
                out.append({"id": m.id, "role": "assistant", "text": m.content})
            # the answer is ready when its last part is: its time, and how long the whole answer took
            out[-1]["at"], out[-1]["took_ms"] = iso(m.created_at), m.took_ms
        if m.role == "assistant":
            for call in m.tool_calls or []:
                if call.get("id") in by_call:
                    out.append({"id": m.id, "role": "post", "post": by_call[call["id"]], "text": "",
                                "at": iso(m.created_at), "took_ms": m.took_ms})
    for row in out:
        if row["role"] == "assistant" and row["id"] in by_message:
            row["role"], row["post"] = "post", by_message[row["id"]]
    return out


@router.get("")
async def list_chats(viewer: Viewer, db: DB) -> dict:
    await _allowed(db, viewer)
    # chats opened and left without a word are not kept
    await db.execute(delete(ChatThread).where(_mine(viewer), ChatThread.created_at < utcnow() - timedelta(hours=6),
                                              ~ChatThread.id.in_(select(ChatMessage.thread_id))))
    await db.commit()
    threads = (await db.execute(select(ChatThread).where(_mine(viewer)).order_by(ChatThread.updated_at.desc()))).scalars().all()
    last, answered_at = {}, {}
    if threads:
        newest = (select(ChatMessage.thread_id, func.max(ChatMessage.id).label("mid"))
                  .where(ChatMessage.thread_id.in_([t.id for t in threads]), answered(), ChatMessage.role == "assistant")
                  .group_by(ChatMessage.thread_id).subquery())
        newest_rows = (await db.execute(select(ChatMessage).join(newest, ChatMessage.id == newest.c.mid))).scalars().all()
        posts = await _post_texts(db, newest_rows)
        last = {m.thread_id: m.content or posts.get(m.id, "") for m in newest_rows}
        answered_at = {m.thread_id: m.created_at for m in newest_rows}
    eng = await ai.engine(db, viewer.project_id, viewer.user_id)
    return {
        "linked": eng.linked,
        "paused": (eng.stop if eng.linked else None) or await over_limit(db, viewer),
        "owner": viewer.is_owner,
        # the model new chats start with: the owner's to see and change, on their own OpenRouter only
        "model": eng.model("chat") if viewer.is_owner and eng.own and eng.linked else None,
        "items": [{"id": t.id, "title": t.title, "updated_at": iso(t.updated_at),
                   "last": (last.get(t.id) or "")[:160],
                   # an answer came after the person last had it open
                   "unread": bool(t.read_at and t.id in answered_at and answered_at[t.id] > t.read_at)}
                  for t in threads if t.id in last or t.title != "Новый чат"],
    }


class NewChatIn(BaseModel):
    # «Создать об этом пост для моего канала»: the topic; the chat's first answer is a post on it
    topic_id: int | None = None


@router.post("")
async def new_chat(viewer: Viewer, db: DB, body: NewChatIn | None = None) -> dict:
    await _allowed(db, viewer)
    eng = await ai.engine(db, viewer.project_id, viewer.user_id)
    if not eng.linked:
        raise HTTPException(status_code=409, detail=eng.stop or ai.SHARED_DOWN)
    topic = await db.get(Topic, body.topic_id) if body and body.topic_id else None
    topic_id = topic.id if topic is not None and topic.project_id == viewer.project_id else None
    thread = ChatThread(project_id=viewer.project_id, user_id=None if viewer.member_id else viewer.user_id,
                        member_id=viewer.member_id, model=eng.model("chat"), topic_id=topic_id)
    db.add(thread)
    await db.commit()
    return {"id": thread.id}


# «Создать об этом пост для моего канала»: what the AI is asked; {тема} and {номер} are the topic's
POST_PROMPT = (
    "Изучи стиль написания постов моего канала и напиши мне пост на эту тему: «{тема}», не указывай источники. "
    "Подробности в теме №{номер}: пересказ, хронология и посты, из которых она собрана."
)
POST_PROMPT_LIMIT = 4000


class PostPromptIn(BaseModel):
    # None or empty: back to the default
    template: str | None = Field(default=None, max_length=POST_PROMPT_LIMIT)


async def _prompt_owner(db, viewer: Principal) -> Project | Member:
    """Where the template is kept: the owner's in the project they work in, a staff member's in their account
    (they are in one project)."""
    if viewer.is_admin or viewer.project_id is None:
        raise HTTPException(status_code=400, detail="Шаблон — у проекта")
    person = await db.get(Member, viewer.member_id) if viewer.member_id else await db.get(Project, viewer.project_id)
    if person is None:
        raise HTTPException(status_code=404)
    return person


def _prompt_json(person: Project | Member) -> dict:
    return {"template": person.post_prompt or POST_PROMPT, "default": POST_PROMPT, "custom": bool(person.post_prompt)}


@router.get("/post-prompt")
async def get_post_prompt(viewer: Viewer, db: DB) -> dict:
    """The person's template for «Создать об этом пост для моего канала» (their own, else the default)."""
    return _prompt_json(await _prompt_owner(db, viewer))


@router.put("/post-prompt")
async def put_post_prompt(body: PostPromptIn, viewer: Viewer, db: DB) -> dict:
    person = await _prompt_owner(db, viewer)
    text = (body.template or "").strip().replace("\r\n", "\n")
    person.post_prompt = text if text and text != POST_PROMPT else None
    await db.commit()
    return _prompt_json(person)


@router.get("/status")
async def chats_status(viewer: Viewer, db: DB, ids: str = "") -> dict:
    """How the chats the browser waits on stand (see the page's chat notifier): whether the AI is still
    answering, and once it is done the chat's title and the start of the answer, or that it failed."""
    await _allowed(db, viewer)
    wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()][:20]
    threads = (await db.execute(select(ChatThread).where(ChatThread.id.in_(wanted), _mine(viewer)))).scalars().all()
    items = []
    for t in threads:
        last = await db.scalar(select(ChatMessage).where(
            ChatMessage.thread_id == t.id, (ChatMessage.role == "user") | answered()
        ).order_by(ChatMessage.id.desc()).limit(1))
        failed = last is not None and last.role == "error"
        text = ((last.content or (await _post_texts(db, [last])).get(last.id, ""))[:200]
                if last is not None and last.role == "assistant" else "")
        items.append({"id": t.id, "title": t.title, "busy": t.id in _jobs, "failed": failed, "last": text})
    return {"items": items}


@router.get("/{thread_id}")
async def get_chat(thread_id: int, viewer: Viewer, db: DB) -> dict:
    await _allowed(db, viewer)
    thread = await _thread(db, viewer, thread_id)
    messages = (await db.execute(select(ChatMessage).where(ChatMessage.thread_id == thread.id)
                                 .order_by(ChatMessage.id))).scalars().all()
    thread.read_at = utcnow()  # opened: its answers are read (the blue dot goes)
    await db.commit()
    from .chat_posts_api import cards_of  # it builds on this module

    cards = await cards_of(db, thread, viewer)
    return {
        "id": thread.id, "title": thread.title, "created_at": iso(thread.created_at),
        # the chat keeps the model it began with; only the owner sees which
        "model": thread.model if viewer.is_owner else None,
        "busy": thread.id in _jobs,
        # the answer being written right now: the page shows it and keeps asking until it is done
        "pending": {"text": _jobs[thread.id].text, "status": _jobs[thread.id].status,
                    "elapsed": round(_jobs[thread.id].elapsed(), 1)} if thread.id in _jobs else None,
        "messages": _shown(messages, cards),
    }


class TitleIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)


@router.patch("/{thread_id}")
async def rename_chat(thread_id: int, body: TitleIn, viewer: Viewer, db: DB) -> dict:
    await _allowed(db, viewer)
    thread = await _thread(db, viewer, thread_id)
    thread.title = body.title.strip()[:80] or thread.title
    await db.commit()
    return {"id": thread.id, "title": thread.title}


@router.delete("/{thread_id}")
async def delete_chat(thread_id: int, viewer: Viewer, db: DB) -> dict:
    await _allowed(db, viewer)
    thread = await _thread(db, viewer, thread_id)
    from ..publications import keep_out_of_chats  # it builds on the chat

    await keep_out_of_chats(db, select(ChatThread.id).where(ChatThread.id == thread.id))
    await db.delete(thread)
    await db.commit()
    return {"ok": True}


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    tz: int = Field(default=180, ge=-900, le=900)  # the person's time zone, minutes east of UTC


class RetryIn(BaseModel):
    tz: int = Field(default=180, ge=-900, le=900)


@router.post("/{thread_id}/retry")
async def retry(thread_id: int, body: RetryIn, viewer: Viewer, db: DB) -> StreamingResponse:
    """«Отправить заново»: the last question once more, after an answer that failed."""
    return await _answer(thread_id, None, body.tz, viewer, db)


@router.post("/{thread_id}/messages")
async def send(thread_id: int, body: MessageIn, viewer: Viewer, db: DB) -> StreamingResponse:
    return await _answer(thread_id, body.text.strip(), body.tz, viewer, db)


async def _answer(thread_id: int, text: str | None, tz: int, viewer: Principal, db) -> StreamingResponse:
    await _allowed(db, viewer)
    thread = await _thread(db, viewer, thread_id)
    eng = await ai.engine(db, viewer.project_id, viewer.user_id)
    if eng.stop:
        raise HTTPException(status_code=409, detail=eng.stop)
    if thread.id in _jobs:
        raise HTTPException(status_code=409, detail="ИИ ещё отвечает на прошлое сообщение")
    if limit := await over_limit(db, viewer):
        raise HTTPException(status_code=409, detail=limit)
    job = _jobs[thread.id] = Job()
    listener: asyncio.Queue = asyncio.Queue()
    job.listeners.append(listener)

    async def write(thread_id: int, owner_id: int, text: str, tz: int) -> None:
        try:
            async for event in chat.reply(thread_id, owner_id, text, tz):
                if event["type"] == "done":
                    # saved before the page hears «done»: it reloads the chat and shows how long it took
                    async with SessionLocal() as s:
                        (await s.get(ChatMessage, event["message_id"])).took_ms = round(job.elapsed() * 1000)
                        await s.commit()
                job.publish(event)
        except Exception:  # noqa: BLE001
            log.exception("chat %s: the answer failed", thread_id)
            job.publish({"type": "error", "text": "Не получилось ответить — попробуйте ещё раз", "detail": None})
        finally:
            _jobs.pop(thread_id, None)
            job.publish({"type": "end"})

    # a task of its own: it goes on, and saves the answer, when the page is closed
    _tasks.add(task := asyncio.create_task(write(thread.id, viewer.user_id, text, tz)))
    task.add_done_callback(_tasks.discard)

    async def lines():
        try:
            while (event := await listener.get())["type"] != "end":
                yield json.dumps(event, ensure_ascii=False) + "\n"
        finally:
            if listener in job.listeners:
                job.listeners.remove(listener)

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
