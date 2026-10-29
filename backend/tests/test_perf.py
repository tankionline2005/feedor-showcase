from datetime import timedelta

from sqlalchemy import delete

from app import perf
from app.db import SessionLocal, engine, init_db
from app.models import PerfSample, utcnow

NET_HEAD = "Inter-|   Receive\n face |bytes    packets errs drop fifo frame compressed multicast|bytes\n"


def fake_proc(root, *, cpu, disk, net, host_net=None):
    """A /proc with the files perf reads: cpu = (busy, idle) jiffies, disk = (read, written) sectors of vda,
    net = (in, out) bytes of eth0 inside the container, host_net = the same of the host's ens3."""
    (root / "net").mkdir(parents=True, exist_ok=True)
    (root / "stat").write_text(f"cpu  {cpu[0]} 0 0 {cpu[1]} 0 0 0 0 0 0\ncpu0 1 2 3 4 5 6 7 8 0 0\n")
    (root / "meminfo").write_text("MemTotal:        2000000 kB\nMemFree:  100 kB\nMemAvailable:     500000 kB\n")
    (root / "diskstats").write_text(
        f" 253 0 vda 10 0 {disk[0]} 0 20 0 {disk[1]} 0 0 0 0\n"
        f" 253 1 vda1 10 0 {disk[0]} 0 20 0 {disk[1]} 0 0 0 0\n"  # a partition: the same bytes again
        " 7 0 loop0 5 0 999 0 0 0 999 0 0 0 0\n"
    )
    (root / "net" / "dev").write_text(
        NET_HEAD + f"    lo: 777 1 0 0 0 0 0 0 777 1 0 0 0 0 0 0\n  eth0: {net[0]} 1 0 0 0 0 0 0 {net[1]} 1 0 0 0 0 0 0\n"
    )
    if host_net:
        (root / "host" / "1" / "net").mkdir(parents=True, exist_ok=True)
        (root / "host" / "1" / "net" / "dev").write_text(
            NET_HEAD
            + f"  ens3: {host_net[0]} 1 0 0 0 0 0 0 {host_net[1]} 1 0 0 0 0 0 0\n"
            + "docker0: 5000 1 0 0 0 0 0 0 5000 1 0 0 0 0 0 0\n"
            + "veth12ab: 9000 1 0 0 0 0 0 0 9000 1 0 0 0 0 0 0\n"
        )


def test_load_between_two_readings(tmp_path, monkeypatch):
    monkeypatch.setattr(perf, "SYS_BLOCK", tmp_path / "no-sys")  # the disk names decide
    proc = tmp_path / "proc"
    fake_proc(proc, cpu=(100, 900), disk=(0, 0), net=(0, 0), host_net=(1000, 2000))
    before = perf.read_counters(proc, proc / "host")
    fake_proc(proc, cpu=(130, 970), disk=(20, 40), net=(5, 5), host_net=(31000, 3500))
    now = perf.read_counters(proc, proc / "host")
    now.at = before.at + 10

    s = perf.sample(before, now)
    assert s.cpu == 30.0  # 30 of 100 jiffies busy
    assert (s.mem_used, s.mem_total) == (1500000 * 1024, 2000000 * 1024)
    assert (s.disk_read, s.disk_write) == (20 * 512 / 10, 40 * 512 / 10)  # vda only, not its partition
    assert (s.net_in, s.net_out) == (3000.0, 150.0)  # the host's ens3, not Docker's interfaces

    # without the host's /proc: the container's own traffic
    assert perf.read_counters(proc, tmp_path / "nothing").net_in == 5
    # a reboot in between starts the counters over: no negative rates
    assert perf.sample(now, before).disk_read == 0
    assert perf.read_counters(tmp_path / "windows") is None


async def test_history_averages_points_and_sums_up():
    await engine.dispose()
    await init_db()
    now = utcnow()
    async with SessionLocal() as s:
        await s.execute(delete(PerfSample))
        for minutes, cpu in [(90, 99.0), (50, 10.0), (20, 20.0), (19, 60.0), (1, 30.0)]:
            s.add(PerfSample(at=now - timedelta(minutes=minutes), cpu=cpu, mem_used=100, mem_total=1000,
                             disk_read=0, disk_write=cpu, net_in=1, net_out=2))
        await s.commit()
        hour = await perf.history(s, "1h")
        day = await perf.history(s, "24h")

    assert [p["cpu"] for p in hour["points"]] == [10.0, 20.0, 60.0, 30.0]  # the 90-minute-old one is out
    assert hour["summary"]["cpu"] == {"now": 30.0, "avg": 30.0, "max": 60.0}
    assert hour["mem_total"] == 1000 and hour["step"] == 30
    assert all(p["t"].endswith("Z") for p in hour["points"])
    # five-minute points: the samples 19 and 20 minutes ago may fall into one
    assert len(day["points"]) in (4, 5) and day["points"][0]["cpu"] == 99.0
    assert day["summary"]["cpu"]["max"] == 99.0
