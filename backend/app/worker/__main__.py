import asyncio
import logging

from ..config import settings
from ..db import init_db
from ..notify import notifier
from . import maintenance, proxy_checker, proxy_feed
from .collector import Collector
from .internal import Internal
from .pool import AccountPool
from .reposter import Reposter
from .util import forever


async def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    # httpx logs every request URL at INFO, and Bot API URLs contain the bot token
    logging.getLogger("httpx").setLevel(logging.WARNING)
    await init_db()
    settings.media_dir.mkdir(parents=True, exist_ok=True)

    pool = AccountPool()
    collector = Collector(pool)
    reposter = Reposter(pool, collector)
    internal = Internal(pool, reposter)  # full-size photos and comments of posts for the web app
    try:
        await asyncio.gather(
            forever("accounts", pool.sync, 10),
            proxy_checker.run(),
            proxy_feed.run(),
            notifier.run(),
            collector.run(),
            reposter.run(),
            maintenance.run(),
            internal.serve(),
        )
    finally:
        await pool.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
