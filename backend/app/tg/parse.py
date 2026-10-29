"""Turning Telethon messages into post rows."""

import re
from collections import Counter
from datetime import datetime

from telethon.extensions import html as tl_html
from telethon.tl import types


# what Telegram puts instead of the content when a channel is blocked in the account's country
_PLACEHOLDER = re.compile(r"^This (channel|message|group) (can.?t|couldn.?t|cannot) be displayed", re.IGNORECASE)


# restriction reasons that only the official apps act on (hidden unless the person turns the filter off)
CLIENT_FILTERS = {"sensitive"}


def restriction_text(obj) -> str | None:
    """Why Telegram hides a channel or message from this account (legal/geo block), else None.

    Only platform "all" counts: restrictions for a single platform (e.g. iOS only) are applied
    by those apps, and our client still receives the content. Nor does "sensitive" (18+, gambling
    and the like): that is the apps' content filter, the posts themselves come through.
    """
    for r in getattr(obj, "restriction_reason", None) or []:
        if getattr(r, "platform", None) == "all" and getattr(r, "reason", None) not in CLIENT_FILTERS:
            return r.text or r.reason or "ограничено Telegram"
    text = getattr(obj, "message", None)
    if isinstance(text, str) and _PLACEHOLDER.match(text.strip()):
        return text.strip()
    return None


def invite_chat(result):
    """The chat joined by messages.importChatInvite.

    Older layers answered with Updates; current ones wrap them in ChatInviteJoinResultOk, or
    return ChatInviteJoinResultWebView when the chat lets members in only after a bot check.
    """
    if isinstance(result, types.messages.ChatInviteJoinResultWebView):
        raise ValueError("Вступить можно только после проверки ботом — аккаунт не может пройти её сам")
    # (plain Updates also has an `updates` field — the list of updates — so check the type)
    updates = result.updates if isinstance(result, types.messages.ChatInviteJoinResultOk) else result
    chats = getattr(updates, "chats", None) or []
    if not chats:
        raise ValueError("Telegram не вернул чат после вступления по ссылке")
    return chats[0]


def public_usernames(chat) -> list[str]:
    """Every public @username of the chat, the main one first; none when it is private. Chats with
    several usernames (e.g. bought on Fragment) have `username=None` and list them in `usernames`."""
    names = [name] if (name := getattr(chat, "username", None)) else []
    for u in getattr(chat, "usernames", None) or []:
        if u.active and u.username not in names:
            names.append(u.username)
    return names


def public_username(chat) -> str | None:
    """The chat's main @username."""
    return next(iter(public_usernames(chat)), None)


def is_placeholder_text(text: str | None) -> bool:
    return bool(text) and bool(_PLACEHOLDER.match(text.strip()))


def compute_er(views: int, reactions: int, comments: int, forwards: int) -> float:
    """ER = (reactions + comments + forwards) / views, in percent."""
    if views <= 0:
        return 0.0
    return round((reactions + comments + forwards) / views * 100, 3)


def compute_fr(views: int, forwards: int) -> float:
    """FR = forwards / views, in percent: how many of those who saw the post shared it."""
    if views <= 0:
        return 0.0
    return round(forwards / views * 100, 3)


def group_messages(messages: list) -> list[list[types.Message]]:
    """Real messages only, albums merged into one group, oldest first."""
    singles: list[list[types.Message]] = []
    albums: dict[int, list[types.Message]] = {}
    for m in messages:
        if not isinstance(m, types.Message):
            continue
        if m.grouped_id:
            albums.setdefault(m.grouped_id, []).append(m)
        else:
            singles.append([m])
    groups = singles + [sorted(g, key=lambda x: x.id) for g in albums.values()]
    return sorted(groups, key=lambda g: g[0].id)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# a post Telegram sends us empty: one of its newer kinds (rich messages, with headings, tables and
# pictures in the text) that our protocol layer does not know yet; the site sends people to Telegram
UNSUPPORTED = "unsupported"


def media_type(m: types.Message) -> str | None:
    if isinstance(m.media, types.MessageMediaUnsupported) or (m.media is None and not m.message):
        return UNSUPPORTED
    if m.photo:
        return "photo"
    if m.gif:
        return "gif"
    if m.video or m.video_note:
        return "video"
    if m.voice:
        return "voice"
    if m.audio:
        return "audio"
    if m.sticker:
        return "sticker"
    if m.poll:
        return "poll"
    if m.document:
        return "document"
    if m.web_preview:
        return "webpage"
    return None


def post_fields(group: list[types.Message]) -> dict:
    first = group[0]
    captioned = next((m for m in group if m.message), first)
    return {
        "msg_id": first.id,
        "album_ids": [m.id for m in group] if len(group) > 1 else None,
        "posted_at": _naive(first.date),
        "text": captioned.message or "",
        "html": tl_html.unparse(captioned.message or "", captioned.entities or []),
        "media_type": media_type(first),
        "media_count": sum(1 for m in group if m.media and not isinstance(
            m.media, (types.MessageMediaWebPage, types.MessageMediaUnsupported))),
    }


def _reaction_key(reaction) -> str:
    if isinstance(reaction, types.ReactionEmoji):
        return reaction.emoticon
    if isinstance(reaction, types.ReactionCustomEmoji):
        return "custom"
    return "paid"


def metrics(group: list[types.Message]) -> dict:
    views = max((m.views or 0) for m in group)
    forwards = max((m.forwards or 0) for m in group)
    comments = max((m.replies.replies if m.replies and m.replies.comments else 0) for m in group)
    counter: Counter[str] = Counter()
    for m in group:
        if m.reactions:
            for rc in m.reactions.results:
                counter[_reaction_key(rc.reaction)] += rc.count
    reactions = sum(counter.values())
    return {
        "views": views,
        "forwards": forwards,
        "comments": comments,
        "reactions": reactions,
        "reactions_top": [{"e": e, "c": c} for e, c in counter.most_common(5)] or None,
        "er": compute_er(views, reactions, comments, forwards),
        "fr": compute_fr(views, forwards),
    }


def without_own_forwards(values: dict, own: int) -> dict:
    """Takes our own forwards (the repost to the aggregator group) off what Telegram counts."""
    if own:
        values["forwards"] = max(0, values["forwards"] - own)
        values["er"] = compute_er(values["views"], values["reactions"], values["comments"], values["forwards"])
        values["fr"] = compute_fr(values["views"], values["forwards"])
    values["forwards_own"] = own
    return values


def own_forwards(post) -> int:
    """1 when our account forwarded the post to the aggregator group (a text copy is not a forward)."""
    return 1 if post.repost_status == "done" and not post.repost_copy else 0


def pick_thumb(sizes) -> str | None:
    """Type letter of the smallest size whose long side is >= 480px, else of the largest.

    Telethon's download_media accepts the type letter for every size kind (it rejects
    PhotoSizeProgressive objects).
    """
    usable = [
        s for s in sizes or []
        if getattr(s, "w", None) and getattr(s, "h", None) and getattr(s, "type", None)
    ]
    if not usable:
        return None
    usable.sort(key=lambda s: max(s.w, s.h))
    for s in usable:
        if max(s.w, s.h) >= 480:
            return s.type
    return usable[-1].type


def preview_source(m: types.Message) -> tuple[object, str] | None:
    """(media object, thumb type) to download as the post preview."""
    candidates = []
    if isinstance(m.photo, types.Photo):
        candidates.append((m.photo, m.photo.sizes))
    elif isinstance(m.document, types.Document) and m.document.thumbs:
        candidates.append((m.document, m.document.thumbs))
    else:
        wp = m.web_preview
        if wp is not None and isinstance(getattr(wp, "photo", None), types.Photo):
            candidates.append((wp.photo, wp.photo.sizes))
    for obj, sizes in candidates:
        thumb = pick_thumb(sizes)
        if thumb:
            return obj, thumb
    return None


def media_kind(m: types.Message) -> str:
    """What opening a preview should do: a photo opens in full, a video points to Telegram."""
    if isinstance(m.photo, types.Photo):
        return "photo"
    if m.gif or m.video or m.video_note:
        return "video"
    if m.document is None and m.web_preview is not None:
        return "photo"  # a link preview's picture
    return "other"


def preview_slots(group: list[types.Message]) -> list[tuple[int, object, str, str]]:
    """Every item of a post that has a picture: (position in the album, media, thumb type, kind)."""
    slots = []
    for pos, m in enumerate(group):
        src = preview_source(m)
        if src:
            slots.append((pos, src[0], src[1], media_kind(m)))
    return slots
