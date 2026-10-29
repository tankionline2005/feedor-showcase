"""Proxy pool: parsing pasted lists, health checks and picking a proxy for an account.

Policy:
- accounts connect only through pool proxies, never directly from the server's IP
  (an empty pool means nothing connects);
- accounts get a proxy automatically and keep it while it works (IP changes look suspicious);
- a proxy that fails DEAD_AFTER_FAILS checks in a row is dead, its accounts move to others;
- at most `accounts_per_proxy` live accounts share one proxy; with no free working proxy
  an account waits.
"""

import asyncio
import base64
import binascii
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qs, unquote, urlsplit

from python_socks import ProxyType
from python_socks.async_.asyncio import Proxy as SocksProxy
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Account, Proxy, utcnow

# Telegram production DC used to test that a proxy actually reaches Telegram
TELEGRAM_DC = ("149.154.167.50", 443)
CHECK_TIMEOUT = 10
CHECK_INTERVAL = timedelta(minutes=5)
RECHECK_AFTER_FAIL = timedelta(seconds=30)
DEAD_AFTER_FAILS = 2


@dataclass(frozen=True)
class ProxyConfig:
    kind: str  # socks5 | mtproto
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    secret: str | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}://{self.username or ''}@{self.host.lower()}:{self.port}/{self.secret or ''}"

    @property
    def label(self) -> str:
        return f"{self.host}:{self.port}"

    @classmethod
    def of(cls, p: Proxy) -> "ProxyConfig":
        return cls(p.kind, p.host, p.port, p.username, p.password, p.secret)


# --- parsing ------------------------------------------------------------------

_HOST = re.compile(r"^[A-Za-z0-9.-]+$|^\[[0-9a-fA-F:]+\]$")


def _port(raw: str) -> int:
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise ValueError(f"неверный порт: {raw}")
    return int(raw)


def _host(raw: str) -> str:
    raw = raw.strip()
    if not raw or not _HOST.match(raw):
        raise ValueError(f"неверный адрес: {raw}")
    return raw


def _mtproto_secret(secret: str) -> str:
    """Validates a secret; Telethon supports plain and dd-secrets but not fake-TLS (ee)."""
    s = secret.strip()
    if re.fullmatch(r"[0-9a-fA-F]+", s):
        raw = bytes.fromhex(s) if len(s) % 2 == 0 else b""
    else:
        try:
            raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        except (binascii.Error, ValueError):
            raw = b""
    if raw[:1] == b"\xee":
        raise ValueError("MTProto с fake-TLS (секрет начинается с ee) не поддерживается — нужен обычный или dd-секрет")
    body = raw[1:] if raw[:1] == b"\xdd" and len(raw) == 17 else raw
    if len(body) != 16:
        raise ValueError("неверный секрет MTProto")
    return s.lower() if re.fullmatch(r"[0-9a-fA-F]+", s) else s


def parse_proxy_line(line: str) -> ProxyConfig:
    """Accepts socks5://user:pass@host:port, user:pass@host:port, host:port:user:pass, host:port,
    tg://proxy?server=…&port=…&secret=… and https://t.me/proxy?…"""
    s = line.strip()
    if not s:
        raise ValueError("пустая строка")

    if s.startswith(("tg://proxy", "https://t.me/proxy", "http://t.me/proxy", "t.me/proxy")):
        q = parse_qs(urlsplit(s if "://" in s else f"https://{s}").query)
        try:
            server, port, secret = q["server"][0], q["port"][0], q["secret"][0]
        except KeyError as exc:
            raise ValueError("в ссылке MTProto нет server, port или secret") from exc
        return ProxyConfig("mtproto", _host(server), _port(port), secret=_mtproto_secret(secret))

    if "://" in s:
        parts = urlsplit(s)
        if parts.scheme not in ("socks5", "socks5h"):
            raise ValueError(f"тип {parts.scheme} не поддерживается — только SOCKS5 и MTProto")
        if not parts.hostname or parts.port is None:
            raise ValueError("нужен адрес и порт")
        return ProxyConfig(
            "socks5",
            _host(parts.hostname),
            parts.port,
            unquote(parts.username) if parts.username else None,
            unquote(parts.password) if parts.password else None,
        )

    if "@" in s:  # user:pass@host:port
        creds, _, addr = s.rpartition("@")
        user, _, password = creds.partition(":")
        host, _, port = addr.rpartition(":")
        return ProxyConfig("socks5", _host(host), _port(port), user or None, password or None)

    parts = s.split(":")
    if len(parts) == 2:  # host:port
        return ProxyConfig("socks5", _host(parts[0]), _port(parts[1]))
    if len(parts) == 4:  # host:port:user:pass
        return ProxyConfig("socks5", _host(parts[0]), _port(parts[1]), parts[2] or None, parts[3] or None)
    raise ValueError(f"не удалось разобрать: {s}")


def split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


# --- health checks ------------------------------------------------------------


async def check(
    cfg: ProxyConfig, *, full: bool, api_id: int | None = None, api_hash: str | None = None
) -> tuple[bool, int | None, str | None]:
    """(works, latency ms, error).

    SOCKS5: handshake and a TCP connection through the proxy to a Telegram DC.
    MTProto: `full` performs a real MTProto connection (validates the secret, needs api
    credentials); otherwise only checks that the proxy port answers.
    """
    started = time.monotonic()
    try:
        if cfg.kind == "socks5":
            proxy = SocksProxy(
                proxy_type=ProxyType.SOCKS5,
                host=cfg.host,
                port=cfg.port,
                username=cfg.username,
                password=cfg.password,
                rdns=True,
            )
            sock = await proxy.connect(*TELEGRAM_DC, timeout=CHECK_TIMEOUT)
            sock.close()
        elif full and api_id and api_hash:
            from .tg.client import make_client  # local import: telethon is heavy for the checker path

            client = make_client("", api_id, api_hash, cfg, receive_updates=False)
            try:
                await asyncio.wait_for(client.connect(), CHECK_TIMEOUT + 5)
            finally:
                await client.disconnect()
        else:
            _, writer = await asyncio.wait_for(asyncio.open_connection(cfg.host, cfg.port), CHECK_TIMEOUT)
            writer.close()
    except Exception as exc:  # noqa: BLE001 — any failure means "does not work"
        message = str(exc) or exc.__class__.__name__
        return False, None, message[:300]
    return True, int((time.monotonic() - started) * 1000), None


def apply_check(p: Proxy, ok: bool, latency_ms: int | None, error: str | None) -> None:
    p.checked_at = utcnow()
    if ok:
        p.status, p.fails, p.latency_ms, p.last_error = "ok", 0, latency_ms, None
        return
    p.fails += 1
    p.last_error = error
    # a new proxy is dead right away; a working one gets a quick recheck first
    if p.status == "unknown" or p.fails >= DEAD_AFTER_FAILS:
        p.status = "dead"


def check_due(p: Proxy) -> bool:
    if p.checked_at is None:
        return True
    wait = RECHECK_AFTER_FAIL if p.fails == 1 and p.status == "ok" else CHECK_INTERVAL
    return utcnow() - p.checked_at >= wait


# --- assignment ---------------------------------------------------------------


async def account_loads(session: AsyncSession) -> dict[int, int]:
    """Live accounts per proxy (disabled and dead accounts do not hold a slot)."""
    rows = await session.execute(
        select(Account.proxy_id, func.count())
        .where(Account.proxy_id.is_not(None), Account.enabled, Account.status != "dead")
        .group_by(Account.proxy_id)
    )
    return dict(rows.all())


def pick(proxies: list[Proxy], loads: dict[int, int], limit: int, exclude: set[int] | None = None) -> Proxy | None:
    """Least loaded working proxy with a free slot, the fastest among equals."""
    exclude = exclude or set()
    candidates = [
        p for p in proxies
        if p.enabled and p.status == "ok" and p.id not in exclude and loads.get(p.id, 0) < limit
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda p: (loads.get(p.id, 0), p.latency_ms or 10**6, p.id))


async def choose_for_new_account(session: AsyncSession, cfg: dict) -> Proxy | None:
    """The least loaded free working proxy for a new account; None when there is none."""
    proxies = list((await session.execute(select(Proxy))).scalars())
    return pick(proxies, await account_loads(session), int(cfg["accounts_per_proxy"]))
