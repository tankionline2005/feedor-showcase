"""Projects side by side: a worker step starts each project's AI work as a task of its own, so a slow one (a free
model may think a quarter of an hour) does not hold the other projects. A project whose task still goes is
left alone by the step until it ends.

The worker's loop does not wait for the tasks (`wait=False`): the next step comes on time for the rest. A
direct call (tests, scripts) waits for them.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


class Lanes:
    def __init__(self, name: str):
        self.name = name
        self._tasks: dict[int, asyncio.Task] = {}

    def busy(self, key: int) -> bool:
        task = self._tasks.get(key)
        return task is not None and not task.done()

    async def run(self, work: dict[int, Callable[[], Awaitable[object]]], wait: bool = True) -> None:
        """Starts `work[key]()` for every key whose task is not going already."""
        started = []
        for key, job in work.items():
            if self.busy(key):
                continue
            task = asyncio.create_task(self._guarded(key, job))
            self._tasks[key] = task
            started.append(task)
        if wait and started:
            await asyncio.gather(*started)

    async def _guarded(self, key: int, job: Callable[[], Awaitable[object]]) -> None:
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("%s of project %s failed", self.name, key)
        finally:
            if self._tasks.get(key) is asyncio.current_task():
                del self._tasks[key]
