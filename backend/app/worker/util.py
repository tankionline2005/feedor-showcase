import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


async def forever(name: str, step: Callable[[], Awaitable[float | None]], interval: float) -> None:
    """Run `step` in a loop; it may return its own delay before the next run."""
    while True:
        try:
            delay = await step()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s failed", name)
            delay = None
        await asyncio.sleep(interval if delay is None else delay)
