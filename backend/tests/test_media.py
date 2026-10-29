import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from telethon.tl import types

from app.config import settings
from app.db import SessionLocal, engine, init_db
from app.models import Account, Channel, Post, utcnow
from app.services import delete_previews, preview_path
from app.tg.parse import preview_slots
from app.web import posts as posts_module
from app.worker import internal as internal_module
from app.worker import pool as pool_module
from app.worker.collector import Collector
from app.worker.internal import Internal
from app.worker.pool import AccountPool, AccountWorker

DATE = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
SIZES = [types.PhotoSize(type="x", w=800, h=600, size=1), types.PhotoSize(type="y", w=1280, h=960, size=2)]


def photo(pid: int) -> types.Photo:
    return types.Photo(id=pid, access_hash=1, file_reference=b"", date=DATE, sizes=SIZES, dc_id=2)


def video(did: int) -> types.Document:
    return types.Document(id=did, access_hash=1, file_reference=b"", date=DATE, mime_type="video/mp4", size=10,
                          dc_id=2, attributes=[types.DocumentAttributeVideo(duration=5, w=640, h=360)],
                          thumbs=SIZES)


def msg(mid: int, media=None, text="") -> types.Message:
    return types.Message(id=mid, peer_id=types.PeerChannel(1), date=DATE, message=text, grouped_id=7, media=media)


def album():
    return [
        msg(10, types.MessageMediaPhoto(photo=photo(1)), "album"),
        msg(11, types.MessageMediaDocument(document=video(2))),
        msg(12, types.MessageMediaPhoto(photo=photo(3))),
    ]


def test_every_album_item_gets_a_picture():
    slots = preview_slots(album())
    assert [(pos, kind, thumb) for pos, _media, thumb, kind in slots] == [(0, "photo", "x"), (1, "video", "x"), (2, "photo", "x")]
    assert preview_slots([msg(1, None, "text only")]) == []


async def _channel_and_post(**post) -> tuple[int, int]:
    await init_db()
    async with SessionLocal() as s:
        await s.execute(delete(Channel).where(Channel.link == "@media_chan"))
        ch = Channel(link="@media_chan", username="media_chan", tg_id=97001, title="Media", status="active")
        s.add(ch)
        await s.flush()
        p = Post(channel_id=ch.id, msg_id=10, posted_at=utcnow(), text="album", **post)
        s.add(p)
        await s.commit()
        return ch.id, p.id


async def test_collector_saves_and_records_every_picture(monkeypatch):
    await engine.dispose()
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    _, post_id = await _channel_and_post(album_ids=[10, 11, 12], media_type="photo")

    class Client:
        async def download_media(self, media, file, thumb):
            with open(file, "wb") as f:
                f.write(b"jpg")
            return file

    settings.media_dir.mkdir(parents=True, exist_ok=True)
    pool = AccountPool()
    w = AccountWorker(await _account(), Client())
    pool.workers[w.id] = w
    await Collector(pool)._download_previews(w, post_id, preview_slots(album()))
    async with SessionLocal() as s:
        post = await s.get(Post, post_id)
    assert post.has_preview and post.previews == [{"k": "photo", "p": 0}, {"k": "video", "p": 1}, {"k": "photo", "p": 2}]
    assert all(preview_path(post_id, i).is_file() for i in range(3))
    assert preview_path(post_id, 2).name == f"{post_id}_2.jpg"

    delete_previews([post_id])
    assert not any(preview_path(post_id, i).exists() for i in range(3))


async def _account() -> Account:
    async with SessionLocal() as s:
        acc = Account(name="Media", session="", status="active", enabled=False)
        s.add(acc)
        await s.commit()
        return acc


class GroupClient:
    """An account in the aggregator group and the channel: the album copy is messages 70, 71, 72."""

    def __init__(self, in_group=True):
        self.in_group = in_group
        self.read: list[str] = []

    async def get_input_entity(self, peer):
        return types.InputPeerChannel(97001, 1)

    async def get_messages(self, peer, ids):
        where = "group" if getattr(peer, "channel_id", None) == 555 else "channel"
        self.read.append(f"{where}:{ids}")
        if where == "group" and not self.in_group:
            raise ValueError("not in the group")
        pid = {70: 1, 72: 3, 10: 1, 12: 3}.get(ids)
        return msg(ids, types.MessageMediaPhoto(photo=photo(pid))) if pid else msg(ids, types.MessageMediaDocument(document=video(2)))

    async def download_media(self, media, file):
        assert file is bytes
        return f"full-{media.id}".encode()


class Reposter:
    async def _target(self, w, ref):
        return types.InputPeerChannel(555, 1)


@pytest.fixture
def web(monkeypatch):
    asyncio.run(engine.dispose())
    from app.web.main import app

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
        yield client


def connect_web_to(monkeypatch, internal: Internal):
    """The web app reaches the worker's service over HTTP; here, straight through ASGI."""
    real = httpx.AsyncClient
    monkeypatch.setattr(posts_module.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.ASGITransport(app=internal.app()), base_url="http://worker"))
    monkeypatch.setattr(settings, "worker_url", "http://worker")


def test_full_photo_comes_from_telegram_and_is_not_stored(web, monkeypatch):
    monkeypatch.setattr(pool_module, "MIN_INTERVAL", 0)
    ch_id, post_id = web.portal.call(lambda: _channel_and_post(
        album_ids=[10, 11, 12], media_type="photo", repost_status="done", repost_copy=False,
        repost_url="https://t.me/c/3000000001/70", has_preview=True,
        previews=[{"k": "photo", "p": 0}, {"k": "video", "p": 1}, {"k": "photo", "p": 2}],
    ))

    async def settings_and_account():
        from app import settings_store
        async with SessionLocal() as s:
            await settings_store.update(s, {"aggregator_channel": "https://t.me/+group"})
            ch = await s.get(Channel, ch_id)
            ch.account_id = None
            await s.commit()

    web.portal.call(settings_and_account)
    pool = AccountPool()
    client = GroupClient()
    w = AccountWorker(web.portal.call(_account), client)
    pool.workers[w.id] = w
    connect_web_to(monkeypatch, Internal(pool, Reposter()))
    files_before = set(settings.media_dir.iterdir())

    # the third album item is the copy's third message in the group
    r = web.get(f"/api/media/{post_id}/full?i=2")
    assert r.status_code == 200 and r.content == b"full-3" and client.read == ["group:72"]
    assert set(settings.media_dir.iterdir()) == files_before  # nothing written

    # a video has no full photo; the list of pictures says which is which
    assert web.get(f"/api/media/{post_id}/full?i=1").status_code == 404
    media = web.get(f"/api/posts?channel_id={ch_id}").json()["items"][0]["media"]
    assert [(m["kind"], m["full_url"] is not None) for m in media] == [("photo", True), ("video", False), ("photo", True)]
    assert media[2]["url"] == f"/api/media/{post_id}?i=2"

    # the group does not answer: the channel itself, through its own account
    async def give_account():
        async with SessionLocal() as s:
            (await s.get(Channel, ch_id)).account_id = w.id
            await s.commit()

    web.portal.call(give_account)
    client.in_group, client.read = False, []
    r = web.get(f"/api/media/{post_id}/full")
    assert r.content == b"full-1" and client.read == ["group:70", "channel:10"]

    # without a key the worker's service refuses
    raw = TestClient(Internal(pool, Reposter()).app())
    assert raw.get(f"/photo/{post_id}").status_code == 403
