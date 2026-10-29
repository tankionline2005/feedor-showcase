"""How a project wants its posts written, learned from how its people edit the posts the AI writes in chats.

Every post the AI gave keeps what it wrote and what was made of it (ChatPost.ai_text / text). For each
project — its channel's way, whoever of its owner and staff edits — the AI keeps short notes on what they
change: length, tone, structure, formatting, emoji, words. The notes are made anew from time to time
(LEARN_EVERY new edits, or the first one), by the chat's model with the owner's key; the chat's AI gets them in
its system prompt, with the latest edits themselves as examples, and writes the project's next post that way.
"""

import asyncio
import json
import logging

from sqlalchemy import select

from . import ai
from .db import SessionLocal
from .models import ChatPost, ChatThread, PostStyle, Project, utcnow

log = logging.getLogger(__name__)

LEARN_EVERY = 2  # new edits before the notes are made anew (the first edit makes the first notes)
LEARN_FROM = 6  # the latest edits read when they are
EXAMPLES = 2  # edits shown to the chat's AI as they are
EXAMPLE_TEXT = 700  # characters of each side of an example
NOTES_LIMIT = 1500

LEARN_PROMPT = """You learn how the people of a Telegram channel edit the posts an AI writes for it, so that the \
next posts come out the way they want them.

You receive JSON: "notes" — what you knew so far (may be empty), and "edits" — the latest posts, each as the AI \
wrote it ("ai") and as they made it ("final"), in Telegram's HTML.

Write the notes anew: concrete rules the AI can follow, from what they change again and again — length, \
tone and voice, structure and paragraphs, the first line, formatting (bold, quotes, spoilers…), emoji, links and \
calls to action, words and phrases they remove or add, what they always fix. Keep what still holds, drop what \
the new edits contradict; a change seen once is a weak hint, say so. No facts about the news itself. Answer \
with the notes only: a short list in Russian, at most 1200 characters."""

_tasks: set[asyncio.Task] = set()


def key(project_id: int) -> str:
    # "<project>:0": made for each person once, the owner's notes became the project's
    return f"{project_id}:0"


def _edited(project_id: int):
    """The project's posts the AI wrote that someone edited: in the owner's chats, its staff's, «Публикации»."""
    # not the posts people wrote themselves: there is no text of the AI's to learn from
    return (ChatPost.project_id == project_id, ChatPost.edited_at.is_not(None), ChatPost.text != ChatPost.ai_text,
            ChatPost.written_by.is_(None))


async def prompt_part(project_id: int) -> str:
    """What the chat's AI is told about the project's way with posts: the notes and the latest edits (empty when
    there are none yet)."""
    async with SessionLocal() as s:
        style = await s.get(PostStyle, key(project_id))
        edits = (await s.execute(select(ChatPost).where(*_edited(project_id))
                                 .order_by(ChatPost.edited_at.desc()).limit(EXAMPLES))).scalars().all()
    if not edits and not (style and style.notes):
        return ""
    out = ["How this project wants its posts written — learned from how its people edited the posts you wrote; "
           "follow it whenever you write a post for its channel:"]
    if style and style.notes:
        out.append(style.notes)
    for n, e in enumerate(edits, 1):
        out.append(f"Example {n}. You wrote:\n{e.ai_text[:EXAMPLE_TEXT]}\nThey made it:\n{e.text[:EXAMPLE_TEXT]}")
    return "\n\n".join(out)


async def learn(project_id: int, member_id: int | None = None) -> bool:
    """The notes made anew, if there are enough new edits for it; True when they were. `member_id`: whose edit
    asked for it (their spend)."""
    async with SessionLocal() as s:
        style = await s.get(PostStyle, key(project_id))
        edits = (await s.execute(select(ChatPost).where(*_edited(project_id))
                                 .order_by(ChatPost.edited_at.desc()).limit(LEARN_FROM))).scalars().all()
        project = await s.get(Project, project_id)
    since = style.learned_until if style else None
    new = [e for e in edits if since is None or e.edited_at > since]
    if not new or (style and style.notes and len(new) < LEARN_EVERY):
        return False
    payload = {"notes": style.notes if style else "",
               "edits": [{"ai": e.ai_text, "final": e.text} for e in reversed(edits)]}
    try:
        data = await ai.complete(user_id=project.owner_id, operation="chat", project_id=project_id, member_id=member_id,
                                 messages=[{"role": "system", "content": LEARN_PROMPT},
                                           {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    except ai.AiError as exc:
        log.info("post style of project %s not learned now: %s", project_id, exc)
        return False
    notes = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()[:NOTES_LIMIT]
    if not notes:
        return False
    async with SessionLocal() as s:
        style = await s.get(PostStyle, key(project_id)) or PostStyle(person=key(project_id))
        style.notes, style.learned_until, style.updated_at = notes, max(e.edited_at for e in edits), utcnow()
        s.add(style)
        await s.commit()
    return True


def learn_later(project_id: int, member_id: int | None) -> None:
    """After an edit: the notes are made anew in the background (nobody waits for the model)."""

    async def go() -> None:
        try:
            await learn(project_id, member_id)
        except Exception:  # noqa: BLE001
            log.exception("post style of project %s failed", project_id)

    task = asyncio.create_task(go())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
