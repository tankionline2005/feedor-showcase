import time

from app.models import Account, Channel
from app.worker.pool import AccountPool, AccountWorker
from app.worker.reposter import Reposter


def worker(id):
    return AccountWorker(Account(id=id, name=f"w{id}", session=""), client=None)


def make():
    pool = AccountPool()

    class Collector:
        ignored_tg_ids: set = set()

    return Reposter(pool, Collector())


def test_public_channel_goes_to_the_least_busy_account():
    r = make()
    w1, w2, w3 = worker(1), worker(2), worker(3)
    ch = Channel(id=10, username="public", account_id=1)
    now = time.monotonic()
    r._sent[1].extend([now] * 5)
    r._sent[2].extend([now] * 1)
    assert r.choose(ch, [w1, w2, w3], limit=30).id == 3


def test_prefers_an_account_that_knows_the_channel_when_load_is_close():
    r = make()
    w1, w2, w3 = worker(1), worker(2), worker(3)
    ch = Channel(id=10, username="public", account_id=1)
    now = time.monotonic()
    r._sent[1].extend([now] * 2)  # owner: 2 reposts, others 0 — close enough
    assert r.choose(ch, [w1, w2, w3], limit=30).id == 1
    r._sent[1].extend([now] * 3)  # owner far busier now
    assert r.choose(ch, [w1, w2, w3], limit=30).id == 2
    r._knows[10].add(3)
    assert r.choose(ch, [w1, w2, w3], limit=30).id == 3


def test_private_and_member_only_channels_use_the_member():
    r = make()
    w1, w2 = worker(1), worker(2)
    private = Channel(id=11, username=None, account_id=2)
    assert r.choose(private, [w1, w2], limit=30).id == 2
    public = Channel(id=12, username="pub", account_id=2)
    r._member_only.add(12)
    assert r.choose(public, [w1, w2], limit=30).id == 2


def test_hourly_limit():
    r = make()
    w1, w2 = worker(1), worker(2)
    now = time.monotonic()
    r._sent[1].extend([now] * 3)
    r._sent[2].extend([now] * 3)
    assert r.choose(Channel(id=1, username="a", account_id=1), [w1, w2], limit=3) is None
    r._sent[1].appendleft(now - 3700)  # older than an hour: drops out of the count
    r._sent[1].popleft()
    r._sent[2][0] = now - 3700
    assert r.choose(Channel(id=1, username="a", account_id=1), [w1, w2], limit=3).id == 2
