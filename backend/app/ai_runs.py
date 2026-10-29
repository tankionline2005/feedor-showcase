"""A topics run as the pages see it: going or not, how far, and how it ended.

A run may be the worker's (on the schedule) or the web's («Собрать сейчас»), so it is kept in the
database, in the state's `run`: both processes see it, and only one run of a project goes at a time.

The run beats while it goes. A run whose process died stops beating and soon stops counting as
going, so the buttons it locked never stay locked. A run that beats goes on, however long: a slow free model
takes up to a quarter of an hour a request (ai.REQUEST_LIMIT), and a second run beside it would read the same
posts again.

A run on the schedule that failed is not tried again at once: it rests a minute, then two, four… up to half
an hour while the failures go on (`resting`), or a model that keeps failing would be paid for every minute.

How far: the model answers in one go and tells nothing on the way, so a request's part of the
percent grows with the time it takes against how long such requests took before.
"""

import asyncio
import logging
import math
from datetime import datetime, timedelta

from .db import SessionLocal
from .models import utcnow

log = logging.getLogger(__name__)

BEAT = timedelta(seconds=15)
GONE = timedelta(seconds=60)  # no beat this long: the process is gone, the run with it
LONGEST = timedelta(hours=2)  # no run takes this long, its requests' time limits and all: it is taken for stuck
REST_MAX = 30  # minutes a failing schedule rests at most between its tries
PREPARE = 0.04  # the part of a run before its first request
EXPECT = {"topics": 60.0}  # seconds a request takes, until the runs tell better


def _at(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def going(run: dict | None, now: datetime | None = None) -> bool:
    if not run or run.get("finished"):
        return False
    now = now or utcnow()
    beat, started = _at(run.get("beat")), _at(run.get("started"))
    return bool(beat and started and now - beat < GONE and now - started < LONGEST)


def resting(run: dict | None, now: datetime | None = None) -> bool:
    """The last run failed, so the schedule waits: 1, 2, 4… minutes (REST_MAX at most) for failures in a row."""
    if not run or not run.get("finished") or not run.get("error"):
        return False
    fails = max(1, run.get("fails") or 1)
    rest = timedelta(minutes=min(REST_MAX, 2 ** (fails - 1)))
    return (now or utcnow()) - _at(run["finished"]) < rest


def percent(run: dict, now: datetime | None = None) -> int:
    """0…99 while the run goes: the requests answered, and the one being waited for by its time."""
    now = now or utcnow()
    part = 0.0
    if run.get("asked"):
        elapsed = (now - _at(run["asked"])).total_seconds()
        expect = max(run.get("expect") or 30.0, 5.0)
        # steady up to the usual time, then slower and slower: never full before the answer
        part = 0.85 * elapsed / expect if elapsed < expect else 0.85 + 0.15 * (1 - math.exp(-(elapsed - expect) / expect))
        part *= run.get("share") or 0
    return max(1, min(99, int(((run.get("done") or 0) + part) * 100)))


def shown(run: dict | None) -> dict | None:
    """What the page gets: a run going, or how the last one ended."""
    if not run:
        return None
    now = utcnow()
    if going(run, now):
        return {"going": True, "percent": percent(run, now), "started_at": run["started"] + "Z"}
    return {
        "going": False,
        "finished_at": (run.get("finished") or run.get("beat") or run["started"]) + "Z",
        "result": run.get("result"),
        # a run that stopped beating ended without a word: tell that too
        "error": run.get("error") or (None if run.get("finished") else {
            "text": "Запуск прервался — попробуйте ещё раз", "detail": None}),
    }


def _iso(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


class Run:
    """A run being made: claimed for the project, beating, and told how far it is."""

    def __init__(self, model, project_id: int, operation: str, data: dict):
        self.model, self.project_id, self.operation, self.data = model, project_id, operation, data
        self.error: dict | None = None
        self._lock = asyncio.Lock()
        self._beating = asyncio.create_task(self._beat())
        self._finished = False

    @classmethod
    async def claim(cls, model, project_id: int, operation: str) -> "Run | None":
        """The project's run, unless one goes already (then None)."""
        async with SessionLocal() as s:
            state = await s.get(model, project_id, with_for_update=True)
            if state is None or going(state.run):
                return None
            now = _iso(utcnow())
            before = state.run or {}
            expect = before.get("expect") or EXPECT[operation]
            # failures in a row so far: the schedule rests the longer the more of them (see resting)
            fails = (before.get("fails") or 1) if before.get("error") else 0
            data = {"started": now, "beat": now, "done": 0.0, "share": 0.0, "asked": None, "expect": expect,
                    "fails": fails}
            state.run = data
            await s.commit()
        return cls(model, project_id, operation, data)

    async def _save(self, **changes) -> None:
        async with self._lock:
            self.data = {**self.data, **changes, "beat": _iso(utcnow())}
            async with SessionLocal() as s:
                state = await s.get(self.model, self.project_id)
                if state is not None:
                    state.run = self.data
                    await s.commit()

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(BEAT.total_seconds())
            try:
                await self._save()
            except Exception:  # noqa: BLE001
                log.exception("%s run of project %s: no beat", self.operation, self.project_id)

    async def asking(self, share: float) -> None:
        """A request to the model goes; it is `share` of the whole run."""
        await self._save(asked=_iso(utcnow()), share=share, done=self.data["done"] or PREPARE)

    async def answered(self) -> None:
        asked = _at(self.data.get("asked"))
        took = (utcnow() - asked).total_seconds() if asked else None
        # how long requests take, for the next ones: the latest counts most
        expect = self.data["expect"] * 0.4 + took * 0.6 if took else self.data["expect"]
        await self._save(done=self.data["done"] + self.data["share"], share=0.0, asked=None, expect=expect)

    def failed(self, exc: Exception) -> None:
        self.error = {"text": str(exc), "detail": getattr(exc, "detail", None)}

    async def finish(self, result: dict | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        self._beating.cancel()
        fails = self.data.get("fails") or 0
        await self._save(finished=_iso(utcnow()), result=result, error=self.error, asked=None, share=0.0,
                         fails=fails + 1 if self.error else 0)
