"""The server's load for Settings → System Performance: CPU, RAM, disk and network, every 30 seconds.

Read from the host's /proc. Inside Docker /proc/stat, /proc/meminfo and /proc/diskstats already are
the host's; network counters are per container, so the worker gets the host's /proc mounted at
/host/proc (docker-compose.yml) and reads the network of the host's PID 1 there. Without /proc
(development on Windows, macOS) nothing is recorded.
"""

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from .db import SessionLocal
from .models import PerfSample, utcnow

log = logging.getLogger(__name__)

PROC = Path("/proc")
HOST_PROC = Path(os.environ.get("HOST_PROC", "/host/proc"))
SYS_BLOCK = Path("/sys/block")
INTERVAL = 30  # seconds between samples
KEEP = timedelta(days=14)
SECTOR = 512  # /proc/diskstats counts in 512-byte sectors whatever the disk
# partitions and virtual disks would count the same bytes twice
NOT_DISKS = ("loop", "ram", "zram", "dm-", "md", "sr", "fd")
DISK_NAME = re.compile(r"(sd|vd|xvd|hd)[a-z]+|nvme\d+n\d+|mmcblk\d+")  # when /sys/block is not there
# the server's own traffic is on its real interface; Docker's bridges and pairs carry the same bytes again
NOT_NETWORK = ("lo", "docker", "br-", "veth", "virbr")

# chart periods: how far back, and the width of one point
PERIODS = {
    "1h": (timedelta(hours=1), INTERVAL),
    "24h": (timedelta(hours=24), 300),
    "7d": (timedelta(days=7), 1800),
}


@dataclass
class Counters:
    at: float  # monotonic seconds
    cpu_busy: int
    cpu_total: int
    mem_total: int
    mem_available: int
    disk_read: int  # bytes since boot
    disk_write: int
    net_in: int
    net_out: int


def _cpu(proc: Path) -> tuple[int, int]:
    """(busy, total) jiffies of all cores since boot."""
    fields = [int(v) for v in (proc / "stat").read_text().splitlines()[0].split()[1:9]]
    # user nice system idle iowait irq softirq steal (guest time is already inside user)
    idle = fields[3] + fields[4]
    return sum(fields) - idle, sum(fields)


def _memory(proc: Path) -> tuple[int, int]:
    """(total, available) bytes."""
    values = {}
    for line in (proc / "meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        values[key] = int(rest.split()[0]) * 1024
    return values["MemTotal"], values.get("MemAvailable", values.get("MemFree", 0))


def _is_disk(name: str, whole: set[str] | None) -> bool:
    if name.startswith(NOT_DISKS):
        return False
    return name in whole if whole is not None else bool(DISK_NAME.fullmatch(name))


def _disk(proc: Path) -> tuple[int, int]:
    """(read, written) bytes since boot over the whole disks."""
    whole = {p.name for p in SYS_BLOCK.iterdir()} if SYS_BLOCK.is_dir() else None
    read = written = 0
    for line in (proc / "diskstats").read_text().splitlines():
        cols = line.split()
        if len(cols) > 9 and _is_disk(cols[2], whole):
            read += int(cols[5]) * SECTOR
            written += int(cols[9]) * SECTOR
    return read, written


def _network(proc: Path, host: Path) -> tuple[int, int]:
    """(received, sent) bytes since boot over the real interfaces of the host."""
    path = host / "1" / "net" / "dev"
    if not path.is_file():
        path = proc / "net" / "dev"  # no host /proc: what this container sends and receives
    received = sent = 0
    for line in path.read_text().splitlines()[2:]:
        name, _, rest = line.partition(":")
        cols = rest.split()
        if len(cols) > 8 and not name.strip().startswith(NOT_NETWORK):
            received += int(cols[0])
            sent += int(cols[8])
    return received, sent


def read_counters(proc: Path = PROC, host: Path = HOST_PROC) -> Counters | None:
    if not (proc / "stat").is_file():
        return None
    busy, total = _cpu(proc)
    mem_total, mem_available = _memory(proc)
    disk_read, disk_write = _disk(proc)
    net_in, net_out = _network(proc, host)
    return Counters(time.monotonic(), busy, total, mem_total, mem_available, disk_read, disk_write, net_in, net_out)


def sample(before: Counters, now: Counters) -> PerfSample:
    """The load between two readings."""
    seconds = max(now.at - before.at, 1e-6)

    def rate(a: int, b: int) -> float:
        return max(0, b - a) / seconds  # counters start over after a reboot

    ticks = now.cpu_total - before.cpu_total
    cpu = (now.cpu_busy - before.cpu_busy) / ticks * 100 if ticks > 0 else 0.0
    return PerfSample(
        cpu=round(min(100.0, max(0.0, cpu)), 1),
        mem_used=now.mem_total - now.mem_available,
        mem_total=now.mem_total,
        disk_read=rate(before.disk_read, now.disk_read),
        disk_write=rate(before.disk_write, now.disk_write),
        net_in=rate(before.net_in, now.net_in),
        net_out=rate(before.net_out, now.net_out),
    )


_last: Counters | None = None
_pruned_at = 0.0


async def perf_step() -> None:
    """Records the load since the step before; drops samples older than two weeks once an hour."""
    global _last, _pruned_at
    now = read_counters()
    if now is None:
        return
    before, _last = _last, now
    if before is None:
        return
    async with SessionLocal() as s:
        s.add(sample(before, now))
        if time.monotonic() - _pruned_at > 3600:
            await s.execute(delete(PerfSample).where(PerfSample.at < utcnow() - KEEP))
            _pruned_at = time.monotonic()
        await s.commit()


EPOCH = datetime(1970, 1, 1)  # times are naive UTC (models.utcnow)
METRICS = ("cpu", "mem_used", "disk_read", "disk_write", "net_in", "net_out")


async def history(session, period: str) -> dict:
    """Points of the period (averaged over each point's width) and, per metric, now / average / peak."""
    span, step = PERIODS[period]
    since = utcnow() - span
    rows = (await session.execute(
        select(PerfSample).where(PerfSample.at >= since).order_by(PerfSample.at)
    )).scalars().all()

    buckets: dict[int, list[PerfSample]] = {}
    for row in rows:
        buckets.setdefault(int((row.at - EPOCH).total_seconds()) // step, []).append(row)
    points = []
    for key, group in buckets.items():
        point = {"t": (EPOCH + timedelta(seconds=key * step)).isoformat() + "Z"}
        for m in METRICS:
            point[m] = sum(getattr(r, m) for r in group) / len(group)
        points.append(point)

    summary = {}
    for m in METRICS:
        values = [getattr(r, m) for r in rows]
        summary[m] = {
            "now": values[-1] if values else None,
            "avg": sum(values) / len(values) if values else None,
            "max": max(values) if values else None,
        }
    return {
        "period": period,
        "step": step,
        "since": since.isoformat() + "Z",
        "until": utcnow().isoformat() + "Z",
        "mem_total": rows[-1].mem_total if rows else None,
        "points": points,
        "summary": summary,
    }
