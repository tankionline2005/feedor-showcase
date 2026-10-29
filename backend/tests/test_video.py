"""A post's video plays on the site straight from Telegram's servers: the link comes from Telegram's public page of
the post (tg_web.py); what the page does not give is told, for people."""

import httpx
import pytest
from sqlalchemy import select

from app import tg_web
from app.db import SessionLocal
from app.models import Channel, Post

from test_user_channels import _activate, ctx, run, sign_in  # noqa: F401  (ctx is a fixture)

CDN = "https://cdn4.telesco.pe/file/ab12.mp4?token=x&amp;y=1"

VIDEO = f"""<a class="tgme_widget_message_video_player js-message_video_player" href="https://t.me/c/1?single">
<i class="tgme_widget_message_video_thumb" style="background-image:url('https://cdn4.telesco.pe/file/t.jpg')"></i>
<div class="tgme_widget_message_video_wrap" style="width:848px;padding-top:56.25%">
<video src="{CDN}" class="tgme_widget_message_video js-message_video" width="100%" height="100%"></video></div>
<time class="message_video_duration js-message_video_duration">3:32</time>
<div class="message_media_not_supported_wrap"><div class="message_media_not_supported">
<div class="message_media_not_supported_label">This media is not supported in your browser</div></div></div></a>"""
GIF = f"""<div class="tgme_widget_message_video_wrap" style="width:640px;padding-top:50%">
<video src="{CDN}" class="tgme_widget_message_video js-message_video" width="100%" height="100%" preload muted
autoplay loop playsinline></video></div>"""
ROUND = f"""<div class="tgme_widget_message_roundvideo_wrap"><i class="tgme_widget_message_roundvideo_thumb"></i>
<video class="tgme_widget_message_roundvideo js-message_roundvideo" src="{CDN}" width="100%" height="100%"></video>"""
TOO_BIG = """<a class="tgme_widget_message_video_player not_supported js-message_video_player" href="x">
<div class="tgme_widget_message_video_wrap" style="width:848px;padding-top:54.7%"></div>
<div class="message_media_not_supported_label">Media is too big</div></a>"""
PROTECTED = '<div class="message_media_not_supported_label">Please open Telegram to view this post</div>'
GONE = '<div class="tgme_widget_message_error" dir="auto">Post not found</div>'


def test_the_page_s_video_is_found_and_told_what_it_is():
    url = "https://cdn4.telesco.pe/file/ab12.mp4?token=x&y=1"  # as the browser needs it: entities undone
    assert tg_web.parse(VIDEO) == {"url": url, "kind": "video", "ratio": round(100 / 56.25, 4), "duration": "3:32"}
    assert tg_web.parse(GIF) == {"url": url, "kind": "gif", "ratio": 2.0, "duration": None}
    assert tg_web.parse(ROUND)["kind"] == "round" and tg_web.parse(ROUND)["ratio"] == 1.0
    assert tg_web.parse(TOO_BIG)["error"] == "too_big"
    assert tg_web.parse(PROTECTED)["error"] == "protected"
    assert tg_web.parse(GONE)["error"] == "gone"
    assert tg_web.parse("<html>something else</html>")["error"] == "missing"
    assert all(tg_web.parse(p)["message"] for p in (TOO_BIG, PROTECTED, GONE, ""))


@pytest.fixture
def telegram(monkeypatch):
    """Telegram's public pages of posts: path → page; asked paths are kept."""
    pages: dict[str, str] = {}
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.path}?{request.url.query.decode()}")
        if pages.get("down"):
            raise httpx.ConnectError("no route")
        return httpx.Response(200, text=pages.get(request.url.path, GONE))

    monkeypatch.setattr(tg_web, "_transport", httpx.MockTransport(handler))
    tg_web._cache.clear()
    yield pages, asked
    tg_web._cache.clear()


async def _video_post(link: str, msg_id: int, previews: list | None, album: list[int] | None = None) -> int:
    async with SessionLocal() as s:
        ch = await s.scalar(select(Channel).where(Channel.link == link))
        p = Post(channel_id=ch.id, msg_id=msg_id, posted_at=ch.created_at, text="", media_type="video",
                 has_preview=True, previews=previews, album_ids=album)
        s.add(p)
        await s.commit()
        return p.id


async def _private(link: str) -> None:
    async with SessionLocal() as s:
        (await s.scalar(select(Channel).where(Channel.link == link))).username = None
        await s.commit()


def test_a_video_is_played_from_telegram_s_servers(ctx, telegram):
    pages, asked = telegram
    app, client = ctx
    owner = sign_in(app, client, 9871)
    assert owner.post("/api/channels", json={"links": "@uc_video"}).json()["added"]
    run(client, _activate, "@uc_video", 98711, 0)
    single = run(client, _video_post, "@uc_video", 10, [{"k": "video", "p": 0}])
    # an album: a photo, then a video (its message is the album's second)
    album = run(client, _video_post, "@uc_video", 20, [{"k": "photo", "p": 0}, {"k": "video", "p": 1}], [20, 21])
    pages["/uc_video/10"] = VIDEO
    pages["/uc_video/21"] = GIF

    [media] = [p for p in owner.get("/api/posts").json()["items"] if p["id"] == single][0]["media"]
    assert media["video_url"] == f"/api/media/{single}/video?i=0" and media["full_url"] is None
    got = owner.get(media["video_url"]).json()
    assert got["kind"] == "video" and got["url"].startswith("https://cdn4.telesco.pe/") and got["duration"] == "3:32"
    assert asked == ["/uc_video/10?embed=1&mode=tme&single=1"]
    owner.get(media["video_url"])  # handed out again from memory: Telegram is not asked every time
    assert len(asked) == 1
    owner.get(media["video_url"], params={"fresh": True})  # the link stopped playing: a new one
    assert len(asked) == 2

    photo, gif = [p for p in owner.get("/api/posts").json()["items"] if p["id"] == album][0]["media"]
    assert photo["video_url"] is None and gif["video_url"] == f"/api/media/{album}/video?i=1"
    assert owner.get(gif["video_url"]).json()["kind"] == "gif"
    assert asked[-1].startswith("/uc_video/21?")
    assert owner.get(f"/api/media/{album}/video", params={"i": 0}).status_code == 404  # a photo is no video

    # someone who does not watch the channel gets nothing
    stranger = sign_in(app, client, 9872)
    assert stranger.get(media["video_url"]).status_code == 404


def test_what_telegram_does_not_give_is_told(ctx, telegram):
    pages, asked = telegram
    app, client = ctx
    owner = sign_in(app, client, 9873)
    assert owner.post("/api/channels", json={"links": "@uc_video_no"}).json()["added"]
    run(client, _activate, "@uc_video_no", 98731, 0)
    big = run(client, _video_post, "@uc_video_no", 1, [{"k": "video", "p": 0}])
    gone = run(client, _video_post, "@uc_video_no", 2, [{"k": "video", "p": 0}])
    pages["/uc_video_no/1"] = TOO_BIG

    assert owner.get(f"/api/media/{big}/video").json()["error"] == "too_big"
    assert owner.get(f"/api/media/{gone}/video").json()["error"] == "gone"

    # Telegram not answering: told so, and asked again next time
    pages["down"] = "yes"
    tg_web._cache.clear()
    down = owner.get(f"/api/media/{big}/video").json()
    assert down["error"] == "telegram" and "не отвечает" in down["message"]
    del pages["down"]
    assert owner.get(f"/api/media/{big}/video").json()["error"] == "too_big"

    # a private channel has no public page: not asked at all
    run(client, _private, "@uc_video_no")
    before = len(asked)
    private = owner.get(f"/api/media/{big}/video").json()
    assert private["error"] == "private" and "закрытого канала" in private["message"] and len(asked) == before
