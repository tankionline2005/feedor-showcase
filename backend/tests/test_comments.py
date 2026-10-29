from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from telethon import errors
from telethon.tl import functions, types

from app.db import SessionLocal
from app.models import Channel, Post, utcnow
from app.web import comments_api
from app.worker import comments as comments_module
from app.worker import pool as pool_module
from app.worker.comments import waveform
from app.worker.internal import Internal
from app.worker.pool import AccountPool, AccountWorker

from test_media import Reposter, _account, _channel_and_post, connect_web_to, photo, video, web  # noqa: F401

DATE = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)
GROUP = 777
TOP = 500  # the post's copy in the discussion group: the comments answer it


def comment(mid: int, user: int, text: str = "", media=None, answers: int | None = None) -> types.Message:
    reply = types.MessageReplyHeader(reply_to_msg_id=answers or TOP, reply_to_top_id=TOP if answers else None)
    return types.Message(id=mid, peer_id=types.PeerChannel(GROUP), date=DATE, message=text,
                         from_id=types.PeerUser(user), reply_to=reply, media=media)


def voice() -> types.Document:
    return types.Document(id=9, access_hash=1, file_reference=b"", date=DATE, mime_type="audio/ogg", size=4000, dc_id=2,
                          attributes=[types.DocumentAttributeAudio(duration=7, voice=True, waveform=b"\xff\x03")])


def sticker() -> types.Document:
    return types.Document(id=8, access_hash=1, file_reference=b"", date=DATE, mime_type="image/webp", size=3000, dc_id=2,
                          attributes=[types.DocumentAttributeSticker(alt="😀", stickerset=types.InputStickerSetEmpty())])


USERS = [
    SimpleNamespace(id=1, first_name="Аня", last_name=None, deleted=False, color=None, username="anya", usernames=None),
    SimpleNamespace(id=2, first_name="Боб", last_name="Б.", deleted=False, color=None, username=None, usernames=None),
]
COMMENTS = [
    comment(501, 1, "первый"),
    comment(503, 2, "ответ", answers=501),
    comment(504, 1, media=types.MessageMediaDocument(document=voice())),
    comment(505, 2, media=types.MessageMediaDocument(document=sticker())),
    comment(506, 1, media=types.MessageMediaPhoto(photo=photo(4))),
    comment(507, 2, media=types.MessageMediaDocument(document=video(5))),
]


class Client:
    """An account that sees the channel and its discussion group; `requests` — what it asked Telegram."""

    def __init__(self, discussion=True, group_username="media_chat"):
        self.discussion = discussion
        self.group_username = group_username
        self.requests: list[str] = []

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(97001, 1)

    async def __call__(self, request):
        self.requests.append(type(request).__name__)
        group = SimpleNamespace(id=GROUP, access_hash=5, title="Обсуждение", color=None, username=self.group_username,
                                usernames=None)
        if isinstance(request, functions.messages.GetDiscussionMessageRequest):
            if not self.discussion:
                raise errors.MsgIdInvalidError(request)
            return SimpleNamespace(messages=[types.Message(id=TOP, peer_id=types.PeerChannel(GROUP), date=DATE,
                                                           message="")], chats=[group])
        if isinstance(request, functions.messages.GetRepliesRequest):
            assert request.msg_id == TOP and request.peer.channel_id == GROUP
            newer = [m for m in COMMENTS if m.id > request.min_id][: request.limit]
            return SimpleNamespace(messages=newer[::-1], users=USERS, chats=[group], count=len(COMMENTS))
        if isinstance(request, functions.channels.GetMessagesRequest):
            wanted = {i.id for i in request.id}
            return SimpleNamespace(messages=[m for m in COMMENTS if m.id in wanted], users=USERS, chats=[group])
        raise AssertionError(request)

    async def download_media(self, media, file, thumb=None):
        assert file is bytes
        return f"{type(media).__name__}-{media.id}{'-thumb' if thumb else ''}".encode()


@pytest.fixture
def comments(web, monkeypatch):  # noqa: F811
    """A post of a channel with comments, read through its own account; the web app reaches that worker."""
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    monkeypatch.setattr(comments_module, "PAGE", 2)
    ch_id, post_id = web.portal.call(lambda: _channel_and_post(media_type="photo", comments=6))
    pool = AccountPool()
    client = Client()
    w = AccountWorker(web.portal.call(_account), client)
    pool.workers[w.id] = w

    async def give_account():
        async with SessionLocal() as s:
            (await s.get(Channel, ch_id)).account_id = w.id
            await s.commit()

    web.portal.call(give_account)
    internal = Internal(pool, Reposter())
    connect_web_to(monkeypatch, internal)
    monkeypatch.setattr(comments_api.httpx, "AsyncClient", comments_module_client(internal))
    return SimpleNamespace(web=web, post_id=post_id, client=client, internal=internal)


def comments_module_client(internal):
    import httpx
    real = httpx.AsyncClient
    return lambda **kw: real(transport=httpx.ASGITransport(app=internal.app()), base_url="http://worker")


def test_comments_are_read_page_by_page_from_the_first(comments):
    web, post_id = comments.web, comments.post_id
    first = web.get(f"/api/posts/{post_id}/comments").json()
    assert first["total"] == 6 and first["has_more"] and first["group"] == "media_chat"
    assert [c["id"] for c in first["items"]] == [501, 503]
    ann, answer = first["items"]
    assert ann["author"] == {"name": "Аня", "username": "anya", "seed": 1} and ann["html"] == "первый"
    assert ann["reply"] is None
    # an answer quotes what it answers, with its author
    assert answer["author"]["name"] == "Боб Б." and answer["author"]["username"] is None
    assert answer["reply"] == {"id": 501, "name": "Аня", "text": "первый"}

    second = web.get(f"/api/posts/{post_id}/comments?after=503").json()
    assert [c["id"] for c in second["items"]] == [504, 505]
    voice_c, sticker_c = second["items"]
    assert voice_c["media"]["kind"] == "voice" and voice_c["media"]["duration"] == 7
    assert sticker_c["media"] == {"kind": "sticker", "format": "webp", "emoji": "😀"}
    last = web.get(f"/api/posts/{post_id}/comments?after=505").json()
    assert [c["media"]["kind"] for c in last["items"]] == ["photo", "video"]
    assert last["items"][1]["media"]["duration"] == 5

    # a page read a moment ago is not asked of Telegram again
    asked = len(comments.client.requests)
    assert web.get(f"/api/posts/{post_id}/comments").json() == first
    assert len(comments.client.requests) == asked


def test_pictures_stickers_and_voice_come_when_shown_never_videos(comments):
    web, post_id = comments.web, comments.post_id
    web.get(f"/api/posts/{post_id}/comments?after=503")
    web.get(f"/api/posts/{post_id}/comments?after=505")
    base = f"/api/posts/{post_id}/comments"
    r = web.get(f"{base}/506/media?what=thumb")
    assert r.status_code == 200 and r.content == b"Photo-4-thumb" and r.headers["content-type"] == "image/jpeg"
    assert web.get(f"{base}/506/media?what=full").content == b"Photo-4"
    assert web.get(f"{base}/507/media?what=thumb").content == b"Document-5-thumb"  # a video's preview
    v = web.get(f"{base}/504/media?what=file")
    assert v.content == b"Document-9" and v.headers["content-type"] == "audio/ogg"
    s = web.get(f"{base}/505/media?what=file")
    assert s.content == b"Document-8" and s.headers["content-type"] == "image/webp"
    # a video itself never goes through our accounts; a photo is no file
    assert web.get(f"{base}/507/media?what=file").status_code == 404
    assert web.get(f"{base}/506/media?what=file").status_code == 404


def test_a_comment_s_video_plays_from_the_group_s_public_page(comments, monkeypatch):
    asked = []

    async def video_page(username, msg_id, fresh=False):
        asked.append((username, msg_id, fresh))
        return {"url": "https://cdn/v.mp4", "kind": "video", "ratio": None, "duration": "0:05"}

    monkeypatch.setattr(comments_api.tg_web, "video", video_page)
    r = comments.web.get(f"/api/posts/{comments.post_id}/comments/507/video?fresh=true").json()
    assert r["url"] == "https://cdn/v.mp4" and asked == [("media_chat", 507, True)]

    # a closed discussion group has no public page
    comments.client.group_username = None
    comments.internal.comments.threads.clear()
    r = comments.web.get(f"/api/posts/{comments.post_id}/comments/507/video").json()
    assert r["error"] == "private_group"


def test_a_post_without_comments_says_so(comments):
    comments.client.discussion = False
    r = comments.web.get(f"/api/posts/{comments.post_id}/comments").json()
    assert r == {"unavailable": "none", "message": "У этого поста нет комментариев."}

    # one deleted in its channel: said so, Telegram not asked
    async def deleted():
        async with SessionLocal() as s:
            (await s.get(Post, comments.post_id)).deleted_at = utcnow()
            await s.commit()

    comments.web.portal.call(deleted)
    asked = len(comments.client.requests)
    r = comments.web.get(f"/api/posts/{comments.post_id}/comments").json()
    assert r["unavailable"] == "gone" and len(comments.client.requests) == asked


def test_the_post_itself_for_its_comments_page(comments):
    post = comments.web.get(f"/api/posts/{comments.post_id}").json()
    assert post["id"] == comments.post_id and post["comments"] == 6
    assert comments.web.get("/api/posts/999999").status_code == 404


def test_worker_refuses_without_the_key(comments):
    raw = TestClient(comments.internal.app())
    assert raw.get(f"/comments/{comments.post_id}").status_code == 403


def test_voice_waveform_is_unpacked():
    # 5-bit values one after another, low bits first: 0xff 0x03 → 31, 31 and the zeros left
    assert waveform(b"\xff\x03") == [31, 31, 0]
    assert waveform(None) == []
