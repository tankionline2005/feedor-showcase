"""Disk usage on the server: shown in Settings → Storage and watched by the worker."""

import os
import shutil
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from .config import settings

LOW_FREE = 0.15  # warn below 15% free
CRITICAL_FREE = 0.05  # and again below 5%

_media_cache: tuple[float, int] = (0.0, 0)


def disk() -> tuple[int, int, int]:
    """(total, used, free) bytes of the disk holding our data (the host disk under Docker)."""
    path = settings.media_dir if settings.media_dir.exists() else Path(".")
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


def media_bytes(max_age: float = 300) -> int:
    """Size of stored post previews; cached because there can be tens of thousands of files."""
    global _media_cache
    at, size = _media_cache
    if time.monotonic() - at < max_age and at:
        return size
    size = 0
    if settings.media_dir.exists():
        with os.scandir(settings.media_dir) as entries:
            for entry in entries:
                try:
                    if entry.is_file():
                        size += entry.stat().st_size
                except OSError:
                    pass
    _media_cache = (time.monotonic(), size)
    return size


async def database_bytes(session) -> int:
    url = make_url(settings.database_url)
    if url.get_backend_name() == "postgresql":
        return int(await session.scalar(text("SELECT pg_database_size(current_database())")) or 0)
    if url.database:
        return sum(
            os.path.getsize(p) for p in (url.database, f"{url.database}-wal", f"{url.database}-shm") if os.path.exists(p)
        )
    return 0


def level(free: int, total: int) -> str:
    """ok | low | critical by the share of free space."""
    share = free / total if total else 1
    if share < CRITICAL_FREE:
        return "critical"
    if share < LOW_FREE:
        return "low"
    return "ok"


def human(size: int) -> str:
    for unit, scale in (("ТБ", 1024**4), ("ГБ", 1024**3), ("МБ", 1024**2)):
        if size >= scale:
            return f"{size / scale:.1f}".replace(".", ",") + f" {unit}"
    return f"{max(1, size // 1024)} КБ"
