"""A post's video, played on the site straight from Telegram's own servers.

Telegram shows public channels' posts on the web (t.me/<channel>/<id>), and its page of a post has a direct
link to the post's video on its CDN (cdn*.telesco.pe): it plays in any browser, seeks (byte ranges), and works
from wherever it is opened, for about three hours. The site asks that page when someone opens a video, and
hands the browser the link: the video never goes through our server or our Telegram accounts.

What the page does not give, the person is told why, and offered to get the post in Telegram instead:
- a private channel (no @username): it has no public page;
- a big video: Telegram's page says «Media is too big»;
- a channel that protects its content, or a post Telegram keeps off the web: «Please open Telegram…»;
- a post deleted, or a channel gone;
- Telegram's page not answering.

This is Telegram's public page, not an API: if its markup changes, the parser below is what to look at.
"""

import asyncio
import html
import logging
import re
import time

import httpx

log = logging.getLogger(__name__)

PAGE = "https://t.me/{username}/{msg_id}?embed=1&mode=tme&single=1"  # single: one item of an album
TIMEOUT = httpx.Timeout(10, connect=5)
KEEP = 60 * 60  # a link lives about 3 hours (the CDN's Expires): one found is handed out for an hour
KEEP_MISSING = 5 * 60  # what the page did not give: asked again after a while (a post may still be processing)
CACHE_LIMIT = 3000
_pages = asyncio.Semaphore(4)  # Telegram's web pages, a few at a time
_cache: dict[tuple[str, int], tuple[float, dict]] = {}

# what Telegram's page says instead of a video → (reason, for people)
UNAVAILABLE = {
    "too_big": "Видео слишком большое, чтобы Telegram показал его вне приложения.",
    "protected": "Telegram показывает этот пост только в приложении — например, если канал запретил копировать "
                 "свои материалы.",
    "gone": "Пост удалён в канале или больше недоступен.",
    "private": "Это видео из закрытого канала: его можно посмотреть только в Telegram.",
    "private_group": "Это видео из закрытой группы обсуждения: его можно посмотреть только в Telegram.",
    "missing": "Telegram не отдал это видео для просмотра на сайте.",
    "telegram": "Telegram сейчас не отвечает. Попробуйте ещё раз чуть позже.",
}

_VIDEO = re.compile(r"<video\b([^>]*)>", re.I)
_ATTR = re.compile(r'([\w-]+)(?:\s*=\s*"([^"]*)")?')
_RATIO = re.compile(r'(?:video_wrap|roundvideo_wrap)"[^>]*padding-top:\s*([\d.]+)%', re.I)
_DURATION = re.compile(r'message_video_duration[^>]*>\s*([\d:]+)\s*<', re.I)


def unavailable(reason: str) -> dict:
    return {"error": reason, "message": UNAVAILABLE[reason]}


def parse(page: str) -> dict:
    """The video on Telegram's page of a post: {url, kind: video | gif | round, ratio, duration} — or why there
    is none: {error, message}."""
    for tag in _VIDEO.finditer(page):
        attrs = {k.lower(): html.unescape(v or "") for k, v in _ATTR.findall(tag.group(1))}
        url = attrs.get("src", "")
        if not url.startswith("https://"):
            continue
        classes = attrs.get("class", "")
        # Telegram's page plays a GIF muted, by itself, in a loop; a round video («кружок») has a class of its own
        kind = "round" if "roundvideo" in classes else "gif" if "loop" in attrs and "autoplay" in attrs else "video"
        ratio_match = _RATIO.search(page)
        ratio = 1.0 if kind == "round" else (100 / float(ratio_match.group(1)) if ratio_match and
                                              float(ratio_match.group(1)) > 0 else None)
        duration_match = _DURATION.search(page)
        return {"url": url, "kind": kind, "ratio": round(ratio, 4) if ratio else None,
                "duration": duration_match.group(1) if duration_match else None}
    if "Media is too big" in page:
        return unavailable("too_big")
    if "Please open Telegram to view this post" in page:
        return unavailable("protected")
    if "tgme_widget_message_error" in page:  # «Post not found», «Channel with username … not found»
        return unavailable("gone")
    return unavailable("missing")


async def video(username: str, msg_id: int, fresh: bool = False) -> dict:
    """The post's video as Telegram's page gives it (see parse). `fresh`: not the link handed out before (it
    stopped playing: expired)."""
    key = (username.lower(), msg_id)
    now = time.monotonic()
    if not fresh and (hit := _cache.get(key)) and hit[0] > now:
        return hit[1]
    try:
        async with _pages, httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, transport=_transport) as client:
            r = await client.get(PAGE.format(username=username, msg_id=msg_id),
                                 headers={"User-Agent": "Mozilla/5.0 (compatible; Feedor)"})
    except httpx.HTTPError as exc:
        log.info("telegram page of %s/%s: %r", username, msg_id, exc)
        return unavailable("telegram")  # not kept: the next try asks again
    if r.status_code != 200:
        log.info("telegram page of %s/%s: HTTP %s", username, msg_id, r.status_code)
        return unavailable("telegram")
    found = parse(r.text)
    if len(_cache) >= CACHE_LIMIT:
        for stale in sorted(_cache, key=lambda k: _cache[k][0])[:CACHE_LIMIT // 4]:
            del _cache[stale]
    _cache[key] = (now + (KEEP if "url" in found else KEEP_MISSING), found)
    return found


# tests put an httpx.MockTransport here
_transport: httpx.AsyncBaseTransport | None = None
