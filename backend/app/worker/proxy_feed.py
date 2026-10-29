"""Proxies picked up automatically from public channels that post them.

No Telegram account is involved: the worker reads each channel's public web page (t.me/s/…), the
same one a browser shows, takes the proxy links (t.me/proxy and tg://proxy, in buttons or text)
from new posts and adds the ones Telethon can use (fake-TLS "ee" secrets cannot be). They go
through the usual health checks; the ones that turn out dead and carry no accounts are removed
after a day, so the list does not grow by dozens of proxies a day. The channels are not
monitored in any other way.
"""

import html
import json
import logging
import re
from datetime import timedelta

import httpx
from sqlalchemy import delete, select

from .. import settings_store
from ..db import SessionLocal
from ..models import Account, Proxy, utcnow
from ..proxies import parse_proxy_line
from .util import forever

log = logging.getLogger(__name__)

# Channels whose public web page shows their posts. (@iMTProto, for one, has it closed.)
FEED_CHANNELS = ("ProxyMTProto", "TProxyRU", "ProxyFree_Ru")
FEED_EVERY = 300  # seconds; the channels post every half an hour or so
FIRST_RUN_POSTS = 4  # a channel's very first read takes its latest few posts
DEAD_AUTO_REMOVE_AFTER = timedelta(hours=24)

_LINK = re.compile(r'href="((?:https://t\.me|tg:/)/proxy\?[^"]+)"')


def feed_url(channel: str) -> str:
    return f"https://t.me/s/{channel}"


def parse_feed(page: str, channel: str) -> list[tuple[int, list[str]]]:
    """[(post id, proxy links)] in page order (oldest first)."""
    parts = re.split(rf'data-post="{re.escape(channel)}/(\d+)"', page)  # prefix, id, html, id, html, …
    posts = []
    for i in range(1, len(parts), 2):
        links = list(dict.fromkeys(html.unescape(link) for link in _LINK.findall(parts[i + 1])))
        posts.append((int(parts[i]), links))
    return posts


def _last_posts(value: str) -> dict[str, int]:
    """The stored {channel: last post id}. A bare number is from when @ProxyMTProto was the only one."""
    if not value:
        return {}
    data = json.loads(value)
    return {"ProxyMTProto": int(data)} if isinstance(data, int) else {k: int(v) for k, v in data.items()}


async def add_from_links(links: list[str], source: str) -> tuple[int, int]:
    """(added, unsupported or broken); known proxies are skipped silently."""
    added = skipped = 0
    async with SessionLocal() as s:
        known = set((await s.execute(select(Proxy.key))).scalars())
        for link in links:
            try:
                cfg = parse_proxy_line(link)
            except ValueError:
                skipped += 1
                continue
            if cfg.key in known:
                continue
            known.add(cfg.key)
            s.add(Proxy(kind=cfg.kind, host=cfg.host, port=cfg.port, secret=cfg.secret, key=cfg.key,
                        status="unknown", fails=0, source=source))
            added += 1
        await s.commit()
    return added, skipped


async def remove_dead_auto() -> int:
    """Auto-added proxies that are dead, a day old and carry no accounts."""
    async with SessionLocal() as s:
        used = select(Account.proxy_id).where(Account.proxy_id.is_not(None))
        result = await s.execute(
            delete(Proxy).where(
                Proxy.source.is_not(None),
                Proxy.status == "dead",
                Proxy.created_at < utcnow() - DEAD_AUTO_REMOVE_AFTER,
                Proxy.id.not_in(used),
            )
        )
        await s.commit()
        return result.rowcount or 0


async def _read_channel(client: httpx.AsyncClient, channel: str, last: int | None) -> int | None:
    """Adds the proxies of the channel's new posts; returns its newest post id (None on failure)."""
    try:
        response = await client.get(feed_url(channel))
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("proxy feed @%s: cannot load the page: %s", channel, exc)
        return None
    posts = parse_feed(response.text, channel)
    if not posts:
        log.warning("proxy feed @%s: no posts on the public page (closed, or the layout changed)", channel)
        return None
    new = [p for p in posts if p[0] > last] if last else posts[-FIRST_RUN_POSTS:]
    if new:
        added, skipped = await add_from_links([link for _, links in new for link in links], f"@{channel}")
        log.info("proxy feed @%s: %d new posts, +%d proxies, %d unsupported", channel, len(new), added, skipped)
    return max(p[0] for p in posts)


async def feed_step() -> None:
    async with SessionLocal() as s:
        cfg = await settings_store.get_all(s)
    if not cfg["proxy_feed_enabled"]:
        return
    last_posts = _last_posts(str(cfg["proxy_feed_last_post"]))
    changed = False
    # t.me redirects to the channel card when the public page is closed: not following it is the signal
    async with httpx.AsyncClient(timeout=20) as client:
        for channel in FEED_CHANNELS:
            newest = await _read_channel(client, channel, last_posts.get(channel))
            if newest is not None and newest != last_posts.get(channel):
                last_posts[channel] = newest
                changed = True
    if changed:
        async with SessionLocal() as s:
            await settings_store.update(s, {"proxy_feed_last_post": json.dumps(last_posts)})

    if removed := await remove_dead_auto():
        log.info("proxy feed: removed %d dead auto-added proxies", removed)


async def run() -> None:
    await forever("proxy feed", feed_step, FEED_EVERY)
