from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING

from telethon import TelegramClient
from telethon.network import connection
from telethon.sessions import StringSession

from ..config import settings
from . import fastctr

# MTProto proxies encrypt every byte once more: through OpenSSL, not Telethon's pure-Python AES
fastctr.install()

if TYPE_CHECKING:
    from ..proxies import ProxyConfig


def api_credentials(api_id: int | None, api_hash: str | None) -> tuple[int, str]:
    api_id = api_id or settings.tg_api_id
    api_hash = api_hash or settings.tg_api_hash
    if not api_id or not api_hash:
        raise ValueError("не заданы api_id / api_hash (TG_API_ID и TG_API_HASH в .env или у аккаунта)")
    return api_id, api_hash


def _is_dd_secret(secret: str) -> bool:
    """dd-secrets require the randomized (padded) MTProxy protocol."""
    if all(c in "0123456789abcdefABCDEF" for c in secret):
        return secret[:2].lower() == "dd"
    try:
        return base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))[:1] == b"\xdd"
    except (binascii.Error, ValueError):
        return False


def telethon_secret(secret: str) -> str:
    """The secret in the form Telethon reads unambiguously.

    Telethon drops a leading "ee"/"dd" as if it were a hex prefix even when the secret is base64
    (so "eeNEgYdJ…" loses two real characters), and decodes only standard base64, while links
    often carry base64url. Hence: decode it ourselves; give a dd-secret as "dd" + hex (the prefix
    is dropped on purpose) and a plain one as UPPER-case hex, which is never taken for a prefix.
    """
    s = secret.strip()
    if all(c in "0123456789abcdefABCDEF" for c in s) and len(s) % 2 == 0:
        raw = bytes.fromhex(s)
    else:
        try:
            raw = base64.urlsafe_b64decode(s.replace("+", "-").replace("/", "_") + "=" * (-len(s) % 4))
        except (binascii.Error, ValueError):
            return s  # not ours to fix; Telethon reports it
    if raw[:1] == b"\xdd" and len(raw) == 17:
        return "dd" + raw[1:].hex()
    return raw[:16].hex().upper()


def _proxy_kwargs(proxy: ProxyConfig | None) -> dict:
    if proxy is None:
        return {}
    if proxy.kind == "mtproto":
        secret = proxy.secret or ""
        return {
            "connection": connection.ConnectionTcpMTProxyRandomizedIntermediate
            if _is_dd_secret(secret)
            else connection.ConnectionTcpMTProxyIntermediate,
            "proxy": (proxy.host, proxy.port, telethon_secret(secret)),
        }
    # Telethon hands this dict to python-socks
    return {
        "proxy": {
            "proxy_type": "socks5",
            "addr": proxy.host,
            "port": proxy.port,
            "username": proxy.username,
            "password": proxy.password,
            "rdns": True,
        }
    }


DEFAULT_DEVICE = {
    "device_model": "Desktop",
    "system_version": "Windows 10",
    "app_version": "5.6.3",
    "lang_code": "ru",
    "system_lang_code": "ru-RU",
}
DEVICE_FIELDS = tuple(DEFAULT_DEVICE)


def account_device(acc) -> dict:
    """Device parameters stored on an account (those it was created with), if any."""
    return {k: getattr(acc, k) for k in DEVICE_FIELDS if getattr(acc, k, None)}


def make_client(
    session: str,
    api_id: int | None,
    api_hash: str | None,
    proxy: ProxyConfig | None,
    *,
    device: dict | None = None,
    receive_updates: bool = True,
) -> TelegramClient:
    api_id, api_hash = api_credentials(api_id, api_hash)
    return TelegramClient(
        StringSession(session),
        api_id,
        api_hash,
        # short flood waits are slept through automatically, long ones raise FloodWaitError
        flood_sleep_threshold=30,
        receive_updates=receive_updates,
        **{**DEFAULT_DEVICE, **(device or {})},
        **_proxy_kwargs(proxy),
    )
