"""Periodic proxy health checks; the account pool moves accounts off dead proxies."""

import asyncio
import logging

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import Proxy
from ..notify import esc, notifier
from ..proxies import ProxyConfig, apply_check, check, check_due
from .util import forever

log = logging.getLogger(__name__)

PARALLEL_CHECKS = 10
_all_dead = False  # every enabled proxy is down (alerted once per occurrence)


async def check_step() -> None:
    global _all_dead
    async with SessionLocal() as s:
        due = [p for p in (await s.execute(select(Proxy).where(Proxy.enabled))).scalars() if check_due(p)]
        if not due:
            return
        sem = asyncio.Semaphore(PARALLEL_CHECKS)

        async def run(p: Proxy):
            async with sem:
                # a full MTProto handshake only when the proxy is new or an account reported it
                return await check(
                    ProxyConfig.of(p),
                    full=p.checked_at is None,
                    api_id=settings.tg_api_id,
                    api_hash=settings.tg_api_hash,
                )

        results = await asyncio.gather(*(run(p) for p in due))
        for p, (ok, latency, error) in zip(due, results, strict=True):
            was = p.status
            apply_check(p, ok, latency, error)
            if p.status != was:
                log.warning("proxy %s (%s:%s): %s -> %s %s", p.id, p.host, p.port, was, p.status, error or "")
            if was == "ok" and p.status == "dead":
                notifier.emit(
                    f"proxy-dead-{p.id}",
                    f"<b>Прокси не работает</b>\n{esc(p.host)}:{p.port} — {esc(error or 'не отвечает')}. "
                    "Его аккаунты переезжают на другие прокси.",
                )
            elif p.status == "ok":
                notifier.clear(f"proxy-dead-{p.id}")
        await s.commit()

        enabled = list((await s.execute(select(Proxy).where(Proxy.enabled))).scalars())
    all_dead = bool(enabled and not any(p.status == "ok" for p in enabled))
    if all_dead and not _all_dead:
        # once when it happens; again only after at least one proxy came back
        notifier.emit("proxies-all-dead", "<b>Все прокси не работают</b>\nАккаунты ждут, сбор постов остановлен.")
    _all_dead = all_dead


async def run() -> None:
    await forever("proxy check", check_step, 15)
