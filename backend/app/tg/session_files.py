"""Bulk account import: session files as sellers ship them, converted to Telethon string sessions.

Supported:
- Telethon `.session` (SQLite with server_address/port);
- Pyrogram `.session` (SQLite with test_mode, v1 and v2);
- a `.json` next to a session with the same name (app_id/app_hash, device, phone…);
- `.zip` archives with any of the above, folders included;
- `.txt` with one string session per line (Telethon or Pyrogram format).
"""

import base64
import binascii
import io
import json
import os
import sqlite3
import struct
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from telethon.crypto import AuthKey
from telethon.sessions import StringSession

# production data centers (Pyrogram sessions store only the DC number)
PROD_DCS = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}
MAX_FILES = 5000


@dataclass
class ImportedSession:
    name: str
    session: str  # Telethon StringSession
    phone: str | None = None
    user_id: int | None = None
    api_id: int | None = None
    api_hash: str | None = None
    device: dict = field(default_factory=dict)


def telethon_string(dc_id: int, auth_key: bytes, address: str | None = None, port: int = 443) -> str:
    if len(auth_key) != 256:
        raise ValueError("повреждённый ключ авторизации")
    address = address or PROD_DCS.get(dc_id)
    if not address:
        raise ValueError(f"неизвестный дата-центр {dc_id}")
    s = StringSession()
    s.set_dc(dc_id, address, port)
    s.auth_key = AuthKey(auth_key)
    return s.save()


def _read_sqlite(data: bytes) -> dict:
    """Session fields from a Telethon or Pyrogram SQLite session file."""
    fd, path = tempfile.mkstemp(suffix=".session")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        conn = sqlite3.connect(path)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]
            if not cols:
                raise ValueError("в файле нет таблицы sessions — это не файл сессии")
            rows = [dict(zip(cols, r, strict=True)) for r in conn.execute("SELECT * FROM sessions")]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError("файл не является сессией (не SQLite)") from exc
    finally:
        os.remove(path)
    row = next((r for r in rows if r.get("auth_key")), None)
    if row is None:
        raise ValueError("в сессии нет ключа авторизации — аккаунт не залогинен")
    if "server_address" in cols:  # Telethon
        return {"session": telethon_string(row["dc_id"], row["auth_key"], row["server_address"], row["port"] or 443)}
    if row.get("test_mode"):
        raise ValueError("сессия тестового сервера Telegram")
    return {  # Pyrogram
        "session": telethon_string(row["dc_id"], row["auth_key"]),
        "user_id": row.get("user_id"),
        "api_id": row.get("api_id"),
    }


def parse_string_session(line: str) -> dict:
    """A Telethon string session, or a Pyrogram one (converted)."""
    s = line.strip()
    if s.startswith("1"):
        try:
            StringSession(s)
            return {"session": s}
        except (ValueError, binascii.Error, struct.error):
            pass
    try:
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("не похоже на строку сессии") from exc
    # Pyrogram formats: v2 (with api_id), and two older layouts
    layouts = {271: ">BI?256sQ?", 267: ">B?256sQ?", 263: ">B?256sI?"}
    fmt = layouts.get(len(raw))
    if fmt is None:
        raise ValueError("не похоже на строку сессии")
    if len(raw) == 271:
        dc_id, api_id, test_mode, auth_key, user_id, _bot = struct.unpack(fmt, raw)
    else:
        dc_id, test_mode, auth_key, user_id, _bot = struct.unpack(fmt, raw)
        api_id = None
    if test_mode:
        raise ValueError("сессия тестового сервера Telegram")
    return {"session": telethon_string(dc_id, auth_key), "user_id": user_id, "api_id": api_id}


def _from_json(meta: dict) -> dict:
    """Account details from a seller's .json (field names vary between sellers)."""
    def first(*keys):
        for k in keys:
            v = meta.get(k)
            if v not in (None, ""):
                return v
        return None

    system_lang = first("system_lang_code", "system_lang_pack")
    device = {
        "device_model": first("device_model", "device"),
        "system_version": first("system_version", "sdk"),
        "app_version": first("app_version"),
        "lang_code": first("lang_code") or (system_lang[:2] if isinstance(system_lang, str) else None),
        "system_lang_code": system_lang,
    }
    phone = first("phone", "phone_number")
    api_id = first("app_id", "api_id")
    return {
        "phone": f"+{str(phone).lstrip('+')}" if phone else None,
        "user_id": first("user_id", "id"),
        "api_id": int(api_id) if api_id else None,
        "api_hash": first("app_hash", "api_hash"),
        "device": {k: str(v)[:128] for k, v in device.items() if v},
        "string": first("string_session", "session_string"),
    }


def _expand(files: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for name, data in files:
        if name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for info in z.infolist():
                        if info.is_dir() or "__MACOSX" in info.filename:
                            continue
                        out.append((info.filename, z.read(info)))
            except zipfile.BadZipFile:
                out.append((name, b"\0bad zip"))
        else:
            out.append((name, data))
        if len(out) > MAX_FILES:
            raise ValueError(f"слишком много файлов (больше {MAX_FILES})")
    return out


def parse_uploads(files: list[tuple[str, bytes]]) -> tuple[list[ImportedSession], list[tuple[str, str]]]:
    """(sessions, [(file, error)]) from uploaded files and archives."""
    errors: list[tuple[str, str]] = []
    groups: dict[str, dict] = {}
    sessions: list[ImportedSession] = []
    tdata_reported = False

    for name, data in _expand(files):
        path = PurePosixPath(name.replace("\\", "/"))
        suffix = path.suffix.lower()
        if "tdata" in path.parts or path.name.startswith("key_data"):
            if not tdata_reported:
                errors.append((name, "формат tdata пока не поддерживается — нужны .session или строки сессий"))
                tdata_reported = True
            continue
        if data == b"\0bad zip":
            errors.append((name, "архив повреждён"))
            continue
        key = str(path.with_suffix("")).lower()
        if suffix == ".session":
            groups.setdefault(key, {"name": path.name})["session"] = data
        elif suffix == ".json":
            try:
                groups.setdefault(key, {"name": path.name})["json"] = json.loads(data.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                errors.append((name, "не удалось прочитать JSON"))
        elif suffix == ".txt":
            for i, line in enumerate(data.decode("utf-8-sig", "replace").splitlines(), 1):
                if not line.strip() or line.strip().startswith("#"):
                    continue
                try:
                    sessions.append(ImportedSession(name=f"{path.name}:{i}", **parse_string_session(line)))
                except ValueError as exc:
                    errors.append((f"{path.name}:{i}", str(exc)))
        elif path.name.startswith("."):
            continue
        else:
            errors.append((name, "неподдерживаемый файл"))

    for group in groups.values():
        meta = _from_json(group["json"]) if isinstance(group.get("json"), dict) else None
        try:
            if "session" in group:
                found = _read_sqlite(group["session"])
            elif meta and meta["string"]:
                found = parse_string_session(meta["string"])
            else:
                errors.append((group["name"], "нет файла .session для этого .json"))
                continue
        except ValueError as exc:
            errors.append((group["name"], str(exc)))
            continue
        item = ImportedSession(name=group["name"], session=found["session"],
                               user_id=found.get("user_id"), api_id=found.get("api_id"))
        if meta:
            item.phone = meta["phone"]
            item.user_id = item.user_id or (int(meta["user_id"]) if str(meta["user_id"] or "").isdigit() else None)
            if meta["api_id"] and meta["api_hash"]:
                item.api_id, item.api_hash = meta["api_id"], str(meta["api_hash"])
            item.device = meta["device"]
        if item.api_id and not item.api_hash:
            item.api_id = None  # an api_id without its hash is useless
        sessions.append(item)
    return sessions, errors
