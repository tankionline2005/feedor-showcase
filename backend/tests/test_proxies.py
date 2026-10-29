import asyncio
import struct
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import settings_store
from app.db import SessionLocal, engine, init_db
from app.models import Account, Proxy, utcnow
from app.proxies import ProxyConfig, apply_check, check, check_due, parse_proxy_line, pick
from app.worker.pool import WAIT, AccountPool

HEX = "0123456789abcdef0123456789abcdef"


# --- parsing --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("socks5://u:p%40ss@1.2.3.4:1080", ProxyConfig("socks5", "1.2.3.4", 1080, "u", "p@ss")),
        ("socks5://proxy.example.com:9050", ProxyConfig("socks5", "proxy.example.com", 9050)),
        ("user:pass@1.2.3.4:1080", ProxyConfig("socks5", "1.2.3.4", 1080, "user", "pass")),
        ("1.2.3.4:1080:user:pass", ProxyConfig("socks5", "1.2.3.4", 1080, "user", "pass")),
        ("1.2.3.4:1080", ProxyConfig("socks5", "1.2.3.4", 1080)),
        (f"tg://proxy?server=5.6.7.8&port=443&secret={HEX}", ProxyConfig("mtproto", "5.6.7.8", 443, secret=HEX)),
        (f"https://t.me/proxy?server=5.6.7.8&port=443&secret=dd{HEX}", ProxyConfig("mtproto", "5.6.7.8", 443, secret=f"dd{HEX}")),
    ],
)
def test_parse_proxy_line(line, expected):
    assert parse_proxy_line(line) == expected


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("http://1.2.3.4:8080", "только SOCKS5 и MTProto"),
        ("1.2.3.4:99999", "порт"),
        (f"tg://proxy?server=5.6.7.8&port=443&secret=ee{HEX}6578616d706c652e636f6d", "fake-TLS"),
        ("tg://proxy?server=5.6.7.8&port=443&secret=abc", "секрет"),
        ("tg://proxy?server=5.6.7.8&port=443", "secret"),
        ("just text", "разобрать"),
    ],
)
def test_parse_proxy_line_rejects(line, message):
    with pytest.raises(ValueError, match=message):
        parse_proxy_line(line)


def test_key_ignores_password_and_host_case():
    a = parse_proxy_line("u:one@Proxy.Example.com:1080")
    b = parse_proxy_line("socks5://u:two@proxy.example.com:1080")
    assert a.key == b.key


# --- pool policy ----------------------------------------------------------------


def make_proxy(id, status="ok", latency=100, enabled=True):
    return Proxy(id=id, kind="socks5", host=f"10.0.0.{id}", port=1080, key=str(id), status=status,
                 latency_ms=latency, enabled=enabled, fails=0)


def test_pick_least_loaded_then_fastest_within_limit():
    proxies = [make_proxy(1, latency=50), make_proxy(2, latency=300), make_proxy(3, status="dead"),
               make_proxy(4, enabled=False)]
    assert pick(proxies, {1: 2, 2: 1}, limit=5).id == 2
    assert pick(proxies, {1: 1, 2: 1}, limit=5).id == 1
    assert pick(proxies, {1: 5, 2: 5}, limit=5) is None
    assert pick(proxies, {}, limit=5, exclude={1}).id == 2


def test_apply_check_marks_dead_after_two_failures():
    p = make_proxy(1, status="unknown")
    apply_check(p, False, None, "refused")
    assert p.status == "dead"  # a new proxy that never worked

    p = make_proxy(2)
    apply_check(p, False, None, "timeout")
    assert (p.status, p.fails) == ("ok", 1)  # one hiccup is tolerated...
    assert not check_due(p)
    p.checked_at = utcnow() - timedelta(seconds=31)
    assert check_due(p)  # ...but rechecked soon
    apply_check(p, False, None, "timeout")
    assert p.status == "dead"
    apply_check(p, True, 80, None)
    assert (p.status, p.fails, p.latency_ms, p.last_error) == ("ok", 0, 80, None)


# --- real check against a fake SOCKS5 server --------------------------------------


async def _fake_socks5(reader, writer):
    greeting = await reader.readexactly(2)
    await reader.readexactly(greeting[1])  # methods
    writer.write(b"\x05\x02")  # username/password auth
    await writer.drain()
    ver_ulen = await reader.readexactly(2)
    user = await reader.readexactly(ver_ulen[1])
    plen = (await reader.readexactly(1))[0]
    password = await reader.readexactly(plen)
    ok = (user, password) == (b"u", b"p")
    writer.write(b"\x01\x00" if ok else b"\x01\x01")
    await writer.drain()
    if not ok:
        writer.close()
        return
    head = await reader.readexactly(4)
    if head[3] == 1:
        await reader.readexactly(4)
    elif head[3] == 3:
        await reader.readexactly((await reader.readexactly(1))[0])
    await reader.readexactly(2)
    writer.write(b"\x05\x00\x00\x01" + bytes(4) + struct.pack(">H", 0))
    await writer.drain()
    writer.close()


async def test_check_socks5_through_fake_server():
    server = await asyncio.start_server(_fake_socks5, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        ok, latency, error = await check(ProxyConfig("socks5", "127.0.0.1", port, "u", "p"), full=True)
        assert ok and error is None and latency is not None
        ok, _, error = await check(ProxyConfig("socks5", "127.0.0.1", port, "u", "wrong"), full=True)
        assert not ok and error
    ok, _, error = await check(ProxyConfig("socks5", "127.0.0.1", port, "u", "p"), full=True)
    assert not ok  # server is gone


# --- worker assignment ------------------------------------------------------------


async def _reset():
    await engine.dispose()
    await init_db()
    async with SessionLocal() as s:
        await s.execute(delete(Account))
        await s.execute(delete(Proxy))
        await settings_store.update(s, {"accounts_per_proxy": 2})


async def test_worker_assigns_moves_and_waits():
    await _reset()
    pool = AccountPool()
    async with SessionLocal() as s:
        accounts = [Account(name=f"a{i}", session="", status="new") for i in range(3)]
        s.add_all(accounts)
        await s.commit()

        # the list is empty: nobody connects, never directly from the server's IP
        plan = await pool._assign_proxies(s, accounts)
        assert set(plan.values()) == {WAIT}
        assert all(a.status == "waiting" and "Список прокси пуст" in a.status_detail for a in accounts)

        p1, p2 = make_proxy(1, latency=10), make_proxy(2, latency=20)
        p1.key, p2.key = "k1", "k2"
        s.add_all([p1, p2])
        await s.flush()
        plan = await pool._assign_proxies(s, accounts)
        await s.commit()
        assert [a.proxy_id for a in accounts] == [1, 2, 1]  # balanced, 2 per proxy at most

        # the proxy of two accounts dies: one moves, the other waits (limit 2)
        p1.status = "dead"
        await s.commit()
        plan = await pool._assign_proxies(s, accounts)
        await s.commit()
        assert accounts[1].proxy_id == 2
        moved = [a for a in (accounts[0], accounts[2]) if a.proxy_id == 2]
        waiting = [a for a in (accounts[0], accounts[2]) if plan[a.id] is WAIT]
        assert len(moved) == 1 and len(waiting) == 1
        assert waiting[0].status == "waiting"

        # the proxy comes back: the waiting account gets it, the moved one stays put
        p1.status = "ok"
        await s.commit()
        plan = await pool._assign_proxies(s, accounts)
        assert waiting[0].proxy_id == 1 and waiting[0].status == "new"
        assert moved[0].proxy_id == 2


async def test_no_situation_gives_a_direct_connection():
    await _reset()
    pool = AccountPool()
    async with SessionLocal() as s:
        accounts = [Account(name=f"d{i}", session="", status="new") for i in range(5)]
        s.add_all(accounts)
        await s.commit()

        def check(plan):
            assert None not in plan.values()  # None would mean the server's own IP
            assert all(v is WAIT or isinstance(v, ProxyConfig) for v in plan.values())

        check(await pool._assign_proxies(s, accounts))  # empty list

        p1, p2 = make_proxy(1, latency=10), make_proxy(2, latency=20)
        p1.key, p2.key = "d1", "d2"
        s.add_all([p1, p2])
        await s.flush()
        plan = await pool._assign_proxies(s, accounts)  # 2 proxies x 2 places for 5 accounts
        check(plan)
        assert sum(v is WAIT for v in plan.values()) == 1

        p1.status = p2.status = "dead"  # every proxy down
        await s.commit()
        plan = await pool._assign_proxies(s, accounts)
        check(plan)
        assert set(plan.values()) == {WAIT}

        p1.status, p2.status, p1.enabled, p2.enabled = "ok", "ok", False, False  # every proxy switched off
        await s.commit()
        plan = await pool._assign_proxies(s, accounts)
        check(plan)
        assert set(plan.values()) == {WAIT}


# --- API ------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    asyncio.run(_reset())

    async def fake_check(cfg, *, full, api_id=None, api_hash=None):
        return (cfg.port != 9999, 42 if cfg.port != 9999 else None, None if cfg.port != 9999 else "refused")

    monkeypatch.setattr("app.web.proxies.check", fake_check)
    from app.web.main import app

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
        yield client


def test_add_list_change_and_delete(api):
    body = api.post(
        "/api/proxies",
        json={"text": "1.1.1.1:1080:u:p\n# comment\nu:p@1.1.1.1:1080\n2.2.2.2:9999\nhttp://x:1\n"
                      f"tg://proxy?server=3.3.3.3&port=443&secret={HEX}"},
    ).json()
    assert [p["label"] for p in body["added"]] == ["1.1.1.1:1080", "2.2.2.2:9999", "3.3.3.3:443"]
    assert body["working"] == 2
    assert body["duplicates"] == ["u:p@1.1.1.1:1080"]
    assert body["invalid"][0]["line"] == "http://x:1"
    statuses = {p["label"]: p["status"] for p in api.get("/api/proxies").json()["items"]}
    assert statuses == {"1.1.1.1:1080": "ok", "2.2.2.2:9999": "dead", "3.3.3.3:443": "ok"}

    ids = {p["label"]: p["id"] for p in body["added"]}

    async def add_account():
        async with SessionLocal() as s:
            acc = Account(name="acc", session="", status="active", proxy_id=ids["1.1.1.1:1080"])
            s.add(acc)
            await s.commit()
            return acc.id

    account_id = api.portal.call(add_account)
    acc = next(a for a in api.get("/api/accounts").json()["items"] if a["id"] == account_id)
    assert acc["proxy"]["label"] == "1.1.1.1:1080"

    moved = api.post(f"/api/accounts/{account_id}/change-proxy").json()
    assert moved["proxy"]["label"] == "3.3.3.3:443"

    assert api.delete(f"/api/proxies/{ids['3.3.3.3:443']}").status_code == 200
    acc = next(a for a in api.get("/api/accounts").json()["items"] if a["id"] == account_id)
    assert acc["proxy"] is None  # the worker gives it another proxy

    assert api.get("/api/status").json()["proxies"] == {"ok": 1, "dead": 1, "disabled": 0}


def test_login_refused_when_no_free_proxy(api):
    api.post("/api/proxies", json={"text": "2.2.2.2:9999"})  # dead
    r = api.post("/api/accounts/login/start", json={"phone": "+70000000000"})
    assert r.status_code == 409  # refused before any connection to Telegram
    assert "прокси" in r.json()["detail"]
    # there is no switch to connect directly any more
    assert api.put("/api/settings", json={"proxy_enabled": False}).status_code == 422
    assert api.put("/api/settings", json={"proxy_hybrid": True}).status_code == 422
    assert "proxy_enabled" not in api.get("/api/settings").json()
    assert api.post("/api/accounts/1/change-proxy").status_code in (404, 409)


@pytest.mark.parametrize("secret", [
    "eeNEgYdJvXrFGRMCIMJdCQ",          # base64 that happens to start with "ee" (Telethon cut it)
    "EERighJJvXrFGRMCIMJdCQ",          # base64, upper case
    "3XnnAQIAAQAH8AMDhuJMOt0",         # base64 dd-secret
    "dd104462821249bd7ac519130220c25d09",  # hex dd-secret
    "ee4b5ba12e110657461267a3c1a1baf5",    # plain hex secret starting with ee
    "-_-_-_-_-_-_-_-_-_-_-w",          # base64url characters
])
def test_mtproto_secret_reaches_telethon_intact(secret):
    import base64

    from telethon.network.connection.tcpmtproxy import TcpMTProxy

    from app.tg.client import telethon_secret

    if all(c in "0123456789abcdefABCDEF" for c in secret):
        raw = bytes.fromhex(secret)
    else:
        raw = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    expected = raw[1:17] if raw[:1] == b"\xdd" and len(raw) == 17 else raw[:16]
    assert len(expected) == 16
    assert TcpMTProxy.normalize_secret(telethon_secret(secret)) == expected
