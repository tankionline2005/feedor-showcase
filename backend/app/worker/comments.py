"""Comments of a post, read from Telegram when someone opens them — never stored.

A channel post's comments are a thread in the channel's discussion group. The worker finds the thread through
the account that watches the channel (or any account, for a public channel), reads it page by page from the
oldest comment, and keeps what it read in memory for a few minutes, so that several people opening the same
post cost Telegram one request. Pictures of the comments come the same way when the page shows them: a photo's
or a video's preview, a photo in full, a sticker, a voice message. Videos and GIFs never go through our
accounts: they play from Telegram's public page of the comment (tg_web.py), as posts' videos do.
"""

import asyncio
import base64
import gzip
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

from telethon import errors, utils
from telethon.extensions import html as tl_html
from telethon.tl import functions, types

from ..models import Channel, Post
from ..tg.parse import pick_thumb, public_username
from .pool import AccountPool, AccountUnavailable, AccountWorker

log = logging.getLogger(__name__)

PAGE = 50
THREAD_KEEP = 60 * 60  # the thread's group and top message
PAGE_KEEP = 5 * 60  # a page of comments
MESSAGE_KEEP = 60 * 60  # comments read, for their pictures, stickers and voice messages
THUMB_KEEP = 60 * 60
MAX_STICKER = 1024 * 1024
MAX_VOICE = 20 * 1024 * 1024
_reads = asyncio.Semaphore(3)  # Telegram requests for comments, a few at a time: the accounts watch channels first


class CommentsUnavailable(Exception):
    """Why the comments cannot be read, for people (`reason`: none | private | gone | busy)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Thread:
    account_id: int
    group: types.InputPeerChannel
    top_id: int
    username: str | None
    expires: float


class _Lru(OrderedDict):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def get_fresh(self, key):
        item = self.get(key)
        if item is None or item[0] < time.monotonic():
            return None
        self.move_to_end(key)
        return item[1]

    def put(self, key, value, keep: float) -> None:
        self[key] = (time.monotonic() + keep, value)
        self.move_to_end(key)
        while len(self) > self.limit:
            self.popitem(last=False)


def waveform(data: bytes | None) -> list[int]:
    """A voice message's waveform: 5-bit values packed one after another."""
    if not data:
        return []
    out = []
    for i in range(len(data) * 8 // 5):
        byte, bit = divmod(i * 5, 8)
        out.append((int.from_bytes(data[byte:byte + 2].ljust(2, b"\0"), "little") >> bit) & 31)
    return out


def _blur(sizes) -> str | None:
    """The tiny blurred preview Telegram sends with the message itself, as a data URI."""
    for s in sizes or []:
        if isinstance(s, types.PhotoStrippedSize):
            return "data:image/jpeg;base64," + base64.b64encode(utils.stripped_photo_to_jpg(s.bytes)).decode()
    return None


def _size(sizes) -> tuple[int | None, int | None]:
    best = max((s for s in sizes or [] if getattr(s, "w", None)), key=lambda s: s.w * s.h, default=None)
    return (best.w, best.h) if best else (None, None)


def _attr(doc, kind):
    return next((a for a in doc.attributes if isinstance(a, kind)), None)


def sticker_format(doc) -> str:
    if doc.mime_type == "application/x-tgsticker":
        return "tgs"
    if doc.mime_type == "video/webm":
        return "webm"
    return "webp"


def media_out(m: types.Message) -> dict | None:
    """What a comment has besides its text, as the page shows it."""
    if isinstance(m.media, types.MessageMediaUnsupported):
        return {"kind": "other", "label": "Сообщение нового формата — видно только в Telegram"}
    if isinstance(m.photo, types.Photo):
        w, h = _size(m.photo.sizes)
        return {"kind": "photo", "w": w, "h": h, "blur": _blur(m.photo.sizes)}
    doc = m.document
    if isinstance(doc, types.Document):
        if m.sticker:
            alt = _attr(doc, types.DocumentAttributeSticker)
            return {"kind": "sticker", "format": sticker_format(doc), "emoji": alt.alt if alt else None}
        if m.gif or m.video or m.video_note:
            video = _attr(doc, types.DocumentAttributeVideo)
            return {
                "kind": "gif" if m.gif else "round" if m.video_note else "video",
                "w": video.w if video else None,
                "h": video.h if video else None,
                "duration": int(video.duration) if video and not m.gif else None,
                "blur": _blur(doc.thumbs),
                "thumb": bool(pick_thumb(doc.thumbs)),
            }
        if m.voice:
            audio = _attr(doc, types.DocumentAttributeAudio)
            return {"kind": "voice", "duration": audio.duration if audio else 0,
                    "waveform": waveform(audio.waveform if audio else None)}
        if m.audio:
            audio = _attr(doc, types.DocumentAttributeAudio)
            name = " — ".join(x for x in (audio and audio.performer, audio and audio.title) if x)
            return {"kind": "other", "label": f"Аудио{': ' + name if name else ''}"}
        file = _attr(doc, types.DocumentAttributeFilename)
        return {"kind": "other", "label": f"Файл{' «' + file.file_name + '»' if file else ''}"}
    if m.poll:
        return {"kind": "other", "label": f"Опрос: {m.poll.poll.question.text}"}
    if isinstance(m.media, (types.MessageMediaGeo, types.MessageMediaGeoLive, types.MessageMediaVenue)):
        return {"kind": "other", "label": "Геопозиция"}
    if isinstance(m.media, types.MessageMediaContact):
        return {"kind": "other", "label": "Контакт"}
    if isinstance(m.media, types.MessageMediaDice):
        return {"kind": "other", "label": m.media.emoticon}
    return None  # none, or a link's preview: the link is in the text


def _reaction(r) -> str:
    if isinstance(r, types.ReactionEmoji):
        return r.emoticon
    if isinstance(r, types.ReactionCustomEmoji):
        return "custom"
    return "paid"


def _author(peer, users: dict, chats: dict, group_id: int) -> dict:
    if isinstance(peer, types.PeerUser) and (u := users.get(peer.user_id)) is not None:
        name = " ".join(x for x in (u.first_name, u.last_name) if x) or ("Удалённый аккаунт" if u.deleted else "?")
        color = u.color.color if u.color and u.color.color is not None else u.id
        return {"name": name, "username": public_username(u), "seed": color % 7}
    chat_id = peer.channel_id if isinstance(peer, types.PeerChannel) else group_id
    c = chats.get(chat_id)
    if c is None:
        return {"name": "?", "username": None, "seed": chat_id % 7}
    color = c.color.color if getattr(c, "color", None) and c.color.color is not None else c.id
    return {"name": c.title, "username": public_username(c), "seed": color % 7}


def _snippet(m: types.Message) -> str:
    text = " ".join((m.message or "").split())
    if text:
        return text[:120]
    media = media_out(m)
    return {"photo": "Фото", "video": "Видео", "round": "Видеосообщение", "gif": "GIF", "voice": "Голосовое сообщение",
            "sticker": "Стикер"}.get(media["kind"], media.get("label", "")) if media else ""


class Comments:
    def __init__(self, pool: AccountPool) -> None:
        self.pool = pool
        self.threads: dict[int, Thread] = {}
        self.pages = _Lru(300)
        self.messages = _Lru(5000)
        self.thumbs = _Lru(400)

    # --- the thread -----------------------------------------------------------

    def _accounts(self, ch: Channel) -> list[AccountWorker]:
        own = self.pool.get(ch.account_id)
        others = [w for w in self.pool.available() if w is not own] if ch.username else []
        return ([own] if own else []) + others

    async def thread(self, post: Post, ch: Channel) -> tuple[AccountWorker, Thread]:
        """The account to read with and the post's thread in the discussion group."""
        known = self.threads.get(post.id)
        if known and known.expires > time.monotonic() and (w := self.pool.get(known.account_id)):
            return w, known
        if not ch.tg_id:
            raise CommentsUnavailable("gone")
        accounts = self._accounts(ch)
        if not accounts:
            raise CommentsUnavailable("busy")
        for w in accounts:
            try:
                peer = await self.pool.input_peer(w, ch)
                # in an album the thread hangs on one of its messages
                for msg_id in post.album_ids or [post.msg_id]:
                    try:
                        r = await self.pool.call(w, w.client, functions.messages.GetDiscussionMessageRequest(
                            peer=peer, msg_id=msg_id))
                        break
                    except errors.MsgIdInvalidError:
                        continue
                else:
                    raise CommentsUnavailable("none")
                top = r.messages[0]
                group = next(c for c in r.chats if c.id == top.peer_id.channel_id)
                found = Thread(w.id, types.InputPeerChannel(group.id, group.access_hash), top.id,
                               public_username(group), time.monotonic() + THREAD_KEEP)
                self.threads[post.id] = found
                return w, found
            except CommentsUnavailable:
                raise
            except AccountUnavailable:
                continue
            except (errors.ChannelPrivateError, errors.ChatForbiddenError, errors.ChannelInvalidError) as exc:
                log.info("comments of post %s via account %s: %r", post.id, w.id, exc)
                if w is accounts[-1]:
                    raise CommentsUnavailable("private") from exc
            except Exception as exc:  # noqa: BLE001 — the next account
                log.info("comments of post %s via account %s: %r", post.id, w.id, exc)
        raise CommentsUnavailable("busy")

    # --- a page ---------------------------------------------------------------

    async def page(self, post: Post, ch: Channel, after: int) -> dict:
        """Comments after `after` (0: from the first), oldest first."""
        cached = self.pages.get_fresh((post.id, after))
        if cached is not None:
            return cached
        async with _reads:
            w, th = await self.thread(post, ch)
            try:
                # Telegram gives the newest first: from `after` on, PAGE of them, then the right way round
                r = await self.pool.call(w, w.client, functions.messages.GetRepliesRequest(
                    peer=th.group, msg_id=th.top_id, offset_id=after + 1, offset_date=None, add_offset=-PAGE,
                    limit=PAGE, max_id=0, min_id=after, hash=0))
            except AccountUnavailable as exc:
                raise CommentsUnavailable("busy") from exc
            except (errors.ChannelPrivateError, errors.ChatForbiddenError) as exc:
                raise CommentsUnavailable("private") from exc
            users = {u.id: u for u in r.users}
            chats = {c.id: c for c in r.chats}
            msgs = sorted((m for m in r.messages if isinstance(m, types.Message)), key=lambda m: m.id)
            for m in msgs:
                self.messages.put((post.id, m.id), m, MESSAGE_KEEP)
            replied = await self._replied(w, th, post.id, msgs, users, chats)
        out = {
            "total": getattr(r, "count", len(msgs)),
            "items": [self._comment(m, th, users, chats, replied) for m in msgs],
            "has_more": len(msgs) == PAGE,
            "group": th.username,
        }
        self.pages.put((post.id, after), out, PAGE_KEEP)
        return out

    async def _replied(self, w, th: Thread, post_id: int, msgs, users, chats) -> dict[int, types.Message]:
        """The comments these answer, as the page quotes them; those not on it, one request for all."""
        known = {m.id: m for m in msgs}
        wanted = {m.reply_to.reply_to_msg_id for m in msgs if self._answers(m, th)} - set(known)
        if wanted:
            try:
                r = await self.pool.call(w, w.client, functions.channels.GetMessagesRequest(
                    channel=th.group, id=[types.InputMessageID(i) for i in sorted(wanted)]))
                users.update({u.id: u for u in r.users})
                chats.update({c.id: c for c in r.chats})
                for m in r.messages:
                    if isinstance(m, types.Message):
                        known[m.id] = m
                        self.messages.put((post_id, m.id), m, MESSAGE_KEEP)
            except Exception as exc:  # noqa: BLE001 — the quotes stay without their text
                log.info("comments answered, post %s: %r", post_id, exc)
        return known

    @staticmethod
    def _answers(m: types.Message, th: Thread) -> bool:
        r = m.reply_to
        return isinstance(r, types.MessageReplyHeader) and bool(r.reply_to_msg_id) and r.reply_to_msg_id != th.top_id

    def _comment(self, m: types.Message, th: Thread, users, chats, replied) -> dict:
        author = _author(m.from_id or m.peer_id, users, chats, th.group.channel_id)
        reply = None
        if self._answers(m, th):
            target = replied.get(m.reply_to.reply_to_msg_id)
            reply = {
                "id": m.reply_to.reply_to_msg_id,
                "name": _author(target.from_id or target.peer_id, users, chats, th.group.channel_id)["name"]
                if target else None,
                "text": " ".join(m.reply_to.quote_text.split())[:120] if m.reply_to.quote_text
                else (_snippet(target) if target else "Сообщение удалено"),
            }
        reactions = []
        if m.reactions:
            reactions = [{"e": _reaction(rc.reaction), "c": rc.count} for rc in m.reactions.results]
        return {
            "id": m.id,
            "date": m.date.replace(tzinfo=None).isoformat(timespec="seconds") + "Z",
            "edited": bool(m.edit_date) and not m.edit_hide,
            "html": tl_html.unparse(m.message or "", m.entities or []),
            "author": author,
            "reply": reply,
            "media": media_out(m),
            "reactions": reactions,
        }

    # --- what a comment has ---------------------------------------------------

    async def _message(self, post: Post, ch: Channel, comment_id: int) -> tuple[AccountWorker, types.Message]:
        w, th = await self.thread(post, ch)
        m = self.messages.get_fresh((post.id, comment_id))
        if m is None:
            got = await self.pool.call(w, w.client, functions.channels.GetMessagesRequest(
                channel=th.group, id=[types.InputMessageID(comment_id)]))
            m = next((x for x in got.messages if isinstance(x, types.Message)), None)
            if m is None or not m.reply_to or th.top_id not in (m.reply_to.reply_to_msg_id, m.reply_to.reply_to_top_id):
                raise CommentsUnavailable("gone")
            self.messages.put((post.id, comment_id), m, MESSAGE_KEEP)
        return w, m

    async def media(self, post: Post, ch: Channel, comment_id: int, what: str) -> tuple[bytes, str] | None:
        """(bytes, content type) of a comment's picture: `thumb` (a photo's or a video's preview), `full` (a photo),
        `file` (a sticker, a voice message); None when the comment has no such thing."""
        key = (post.id, comment_id, what)
        if what == "thumb" and (cached := self.thumbs.get_fresh(key)) is not None:
            return cached
        async with _reads:
            w, m = await self._message(post, ch, comment_id)
            doc = m.document if isinstance(m.document, types.Document) else None
            try:
                if what == "thumb":
                    if isinstance(m.photo, types.Photo) and (t := pick_thumb(m.photo.sizes)):
                        data = await self.pool.call(w, w.client.download_media, m.photo, file=bytes, thumb=t)
                    elif doc and (m.gif or m.video or m.video_note) and (t := pick_thumb(doc.thumbs)):
                        data = await self.pool.call(w, w.client.download_media, doc, file=bytes, thumb=t)
                    else:
                        return None
                    out = (data, "image/jpeg")
                    self.thumbs.put(key, out, THUMB_KEEP)
                    return out
                if what == "full" and isinstance(m.photo, types.Photo):
                    return await self.pool.call(w, w.client.download_media, m.photo, file=bytes), "image/jpeg"
                if what == "file" and doc and m.sticker and doc.size <= MAX_STICKER:
                    data = await self.pool.call(w, w.client.download_media, doc, file=bytes)
                    fmt = sticker_format(doc)
                    if fmt == "tgs":  # an animated sticker is gzipped Lottie JSON: the page plays the JSON
                        return gzip.decompress(data), "application/json"
                    return data, "video/webm" if fmt == "webm" else "image/webp"
                if what == "file" and doc and m.voice and doc.size <= MAX_VOICE:
                    return await self.pool.call(w, w.client.download_media, doc, file=bytes), "audio/ogg"
                return None
            except AccountUnavailable as exc:
                raise CommentsUnavailable("busy") from exc

    async def group_username(self, post: Post, ch: Channel) -> str | None:
        return (await self.thread(post, ch))[1].username
