"""Staff accounts of a project ("Совместный мониторинг"): logins, passwords, linking their Telegram. A project
whose plan ran out keeps its staff while it is frozen; they cannot sign in (billing.py)."""

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta

from sqlalchemy import select

from .db import SessionLocal
from .models import Member, utcnow

log = logging.getLogger(__name__)

LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
LOGIN_RULES = "Логин: от 3 до 32 латинских букв, цифр, точек, дефисов или подчёркиваний"
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/l/I
PASSWORD_LENGTH = 14
LINK_PREFIX = "staff_"  # /start staff_<token>: links the Telegram of whoever opens it
LINK_TTL = timedelta(minutes=10)

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def normalize_login(login: str) -> str:
    return login.strip().lower()


def new_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


async def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = await asyncio.to_thread(_scrypt, password, salt)
    return f"scrypt${salt.hex()}${digest.hex()}"


async def check_password(password: str, stored: str) -> bool:
    try:
        kind, salt, digest = stored.split("$")
    except ValueError:
        return False
    if kind != "scrypt":
        return False
    given = await asyncio.to_thread(_scrypt, password, bytes.fromhex(salt))
    return hmac.compare_digest(given, bytes.fromhex(digest))


async def link_from_bot(token: str, tg_user: dict) -> Member | None:
    """/start staff_<token> in the bot: links the sender's Telegram to the staff account, if the link is fresh."""
    async with SessionLocal() as s:
        member = await s.scalar(select(Member).where(Member.link_token == token))
        if member is None or member.link_token_at is None or utcnow() - member.link_token_at > LINK_TTL:
            return None
        member.tg_user_id = tg_user["id"]
        member.tg_username = tg_user.get("username")
        member.tg_name = " ".join(x for x in (tg_user.get("first_name"), tg_user.get("last_name")) if x) or None
        member.link_token = None
        await s.commit()
        return member
