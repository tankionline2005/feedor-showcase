""""Посмотреть оригинал поста": the bot sends a user the post from our aggregator group.

Users never see the group itself. The bot (a member of the group) forwards the group's copy to
the user's private chat; a forward of a forward shows the original channel as its source, so
the user gets the post "from the channel". A copy that is our own text (the channel forbids
forwarding) is copied instead — a forward would show the name of the account that posted it.
"""

import logging
import re

import httpx

from sqlalchemy import select

from . import settings_store
from .config import settings
from .db import SessionLocal
from .models import Post
from .notify import BotError, bot_call, esc, notifier

log = logging.getLogger(__name__)

IN_CHAT = ("creator", "administrator", "member", "restricted")
_bot_in_group: bool | None = None  # last known: the alert goes out when it turns false

# https://t.me/c/<internal id>/<message> for a private group, https://t.me/<username>/<message> for a public one
_GROUP_LINK = re.compile(r"^https://t\.me/(?:c/(\d+)|([A-Za-z0-9_]{4,32}))/(\d+)$")


async def full_photo(post_id: int, slot: int) -> bytes | None:
    """The post's photo in full size, as the worker reads it from Telegram (worker/internal.py); None when it
    cannot now."""
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.get(f"{settings.worker_url}/photo/{post_id}", params={"i": slot},
                                 headers={"X-Internal-Key": settings.secret_key})
    except httpx.HTTPError as exc:
        log.warning("full photo %s/%s: worker unreachable: %s", post_id, slot, exc)
        return None
    return r.content if r.status_code == 200 and r.content else None


def group_message(repost_url: str | None) -> tuple[int | str, int] | None:
    """(Bot API chat id of the group, id of the copy) from a post's repost_url."""
    m = _GROUP_LINK.match(repost_url or "")
    if not m:
        return None
    internal, username, message_id = m.groups()
    return (int(f"-100{internal}") if internal else f"@{username}"), int(message_id)


async def send_original(token: str, chat_id: int, group: int | str, first_id: int, count: int,
                        copy: bool | None) -> None:
    """Delivers the group's copy of a post (`count` messages for an album) to `chat_id`.
    `copy`: True — our text copy, False — a forward, None — unknown (reposted before it was recorded)."""
    if copy:
        await bot_call(token, "copyMessage", chat_id=chat_id, from_chat_id=group, message_id=first_id)
        return
    ids = list(range(first_id, first_id + count))
    if copy is False:
        await bot_call(token, "forwardMessages", chat_id=chat_id, from_chat_id=group, message_ids=ids)
        return
    # unknown: forward the first message and look at where it says it comes from
    sent = await bot_call(token, "forwardMessage", chat_id=chat_id, from_chat_id=group, message_id=first_id)
    from_channel = (sent.get("forward_origin") or {}).get("type") == "channel"
    if from_channel and count == 1:
        return
    await bot_call(token, "deleteMessage", chat_id=chat_id, message_id=sent["message_id"])
    if from_channel:
        await bot_call(token, "forwardMessages", chat_id=chat_id, from_chat_id=group, message_ids=ids)
    else:
        await bot_call(token, "copyMessage", chat_id=chat_id, from_chat_id=group, message_id=first_id)


async def bot_in_group_step() -> None:
    """The bot must be in the aggregator group to send users their posts. A changed bot is not
    there until someone adds it, so the admin is told once instead of users seeing failures."""
    global _bot_in_group
    async with SessionLocal() as s:
        cfg = await settings_store.get_all(s)
        url = await s.scalar(
            select(Post.repost_url).where(Post.repost_url.is_not(None)).order_by(Post.reposted_at.desc().nulls_last()).limit(1)
        )
    token, target = str(cfg["notify_bot_token"]), group_message(url)
    if not token or target is None:
        return
    try:
        member = await bot_call(token, "getChatMember", chat_id=target[0], user_id=int(token.split(":")[0]))
        inside = member.get("status") in IN_CHAT
    except BotError as exc:
        if "chat not found" not in str(exc).lower() and "not a member" not in str(exc).lower():
            log.warning("bot membership check: %s", exc)
            return  # Telegram is unreachable or so: say nothing
        inside = False
    if not inside and _bot_in_group is not False:
        log.warning("the bot is not in the aggregator group")
        notifier.emit(
            "bot-not-in-group",
            f"<b>Бот не в группе-агрегаторе</b>\n@{esc(cfg['notify_bot_username'])} не состоит в группе, куда "
            "пересылаются посты, поэтому пользователи не могут получить оригинал поста. Добавьте бота в группу "
            "(можно администратором без прав).",
        )
    _bot_in_group = inside
