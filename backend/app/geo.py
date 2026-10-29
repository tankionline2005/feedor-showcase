"""Where an IP address is: «Belgrade, Serbia» under a device in Settings → «Устройства».

From DB-IP's free city database (IP to City Lite, CC BY 4.0: the page names DB-IP), which the worker
downloads once a month into the shared media volume; the web reads it from there. No database yet, or an
address it does not know: no place, the page shows the IP alone.
"""

import asyncio
import gzip
import logging
import os
import re
import shutil
from datetime import date, timedelta
from pathlib import Path

import httpx

from .config import settings

log = logging.getLogger(__name__)

URL = "https://download.db-ip.com/free/dbip-city-lite-{month}.mmdb.gz"
MAX_AGE = timedelta(days=35)  # a new month's database comes out on its first days


def path() -> Path:
    return settings.media_dir / "_geo" / "dbip-city-lite.mmdb"


_reader = None
_reader_mtime = 0.0


def _open():
    """The database, opened again when the worker put a new one in its place."""
    global _reader, _reader_mtime
    try:
        mtime = path().stat().st_mtime
    except OSError:
        return None
    if _reader is None or mtime != _reader_mtime:
        import maxminddb

        try:
            _reader, _reader_mtime = maxminddb.open_database(str(path())), mtime
        except Exception as exc:  # noqa: BLE001  a broken file: no places until the next download
            log.warning("geo database unreadable: %s", exc)
            _reader = None
            return None
    return _reader


def place(ip: str | None) -> str | None:
    """«Belgrade, Serbia» (or «Москва, Россия»), the country alone, or None."""
    reader = _open() if ip else None
    if reader is None:
        return None
    try:
        found = reader.get(ip)
    except ValueError:  # not an IP address
        return None
    if not found:
        return None
    city = (found.get("city") or {}).get("names") or {}
    country = (found.get("country") or {}).get("names") or {}
    # one language for both: Russian when the city has a Russian name too (most have only English)
    lang = "ru" if "ru" in city or (not city and "ru" in country) else "en"
    # «Moscow (Tsentralnyy administrativnyy okrug)»: the city alone
    town = re.sub(r"\s*\(.*\)$", "", city.get(lang) or "")
    return ", ".join(x for x in (town, country.get(lang)) if x) or None


def _stale() -> bool:
    try:
        age = date.today() - date.fromtimestamp(path().stat().st_mtime)
    except OSError:
        return True
    return age > MAX_AGE


async def update() -> None:
    """The month's database, if the one here is older than MAX_AGE (or there is none): this month's, or the
    last month's while this one is not out yet."""
    if not _stale():
        return
    first = date.today().replace(day=1)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    packed = target.with_suffix(".mmdb.gz.part")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=120), follow_redirects=True) as client:
        for month in (first, (first - timedelta(days=1)).replace(day=1)):
            url = URL.format(month=month.strftime("%Y-%m"))
            async with client.stream("GET", url) as r:
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                with packed.open("wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        f.write(chunk)
            break
        else:
            log.warning("no geo database to download")
            return
    await asyncio.to_thread(_unpack, packed, target)
    log.info("geo database updated from %s", url)


def _unpack(packed: Path, target: Path) -> None:
    unpacked = target.with_suffix(".mmdb.part")
    try:
        with gzip.open(packed, "rb") as src, unpacked.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        os.replace(unpacked, target)
    finally:
        packed.unlink(missing_ok=True)
        unpacked.unlink(missing_ok=True)
