import base64
import io
import json
import os
import sqlite3
import struct
import zipfile

import pytest
from fastapi.testclient import TestClient
from telethon.crypto import AuthKey
from telethon.sessions import SQLiteSession, StringSession

from app.tg.session_files import parse_string_session, parse_uploads

KEY_A = bytes(range(256))
KEY_B = bytes(reversed(range(256)))


def telethon_file(tmp_path, name: str, key: bytes) -> bytes:
    path = tmp_path / name
    s = SQLiteSession(str(path))
    s.set_dc(4, "149.154.167.91", 443)
    s.auth_key = AuthKey(key)
    s.save()
    s.close()
    data = (tmp_path / f"{name}.session").read_bytes()
    os.remove(tmp_path / f"{name}.session")
    return data


def pyrogram_file(tmp_path, key: bytes, *, test_mode=0) -> bytes:
    path = tmp_path / "pyro.session"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sessions (dc_id INTEGER PRIMARY KEY, api_id INTEGER, test_mode INTEGER, "
        "auth_key BLOB, date INTEGER NOT NULL, user_id INTEGER, is_bot INTEGER)"
    )
    conn.execute("INSERT INTO sessions VALUES (2, 12345, ?, ?, 0, 777000, 0)", (test_mode, key))
    conn.commit()
    conn.close()
    data = path.read_bytes()
    os.remove(path)
    return data


def decoded(session: str) -> tuple[int, str, int, bytes]:
    s = StringSession(session)
    return s.dc_id, s.server_address, s.port, s.auth_key.key


def test_telethon_file_with_seller_json(tmp_path):
    meta = {"phone": "79991234567", "app_id": 2040, "app_hash": "b18441a1ff607e10a989891a5462e627",
            "device": "iPhone 15", "sdk": "iOS 17.2", "app_version": "10.5", "system_lang_pack": "en-US"}
    sessions, errors = parse_uploads([
        ("79991234567.session", telethon_file(tmp_path, "a", KEY_A)),
        ("79991234567.json", json.dumps(meta).encode()),
    ])
    assert errors == []
    [s] = sessions
    assert decoded(s.session) == (4, "149.154.167.91", 443, KEY_A)
    assert (s.phone, s.api_id, s.api_hash) == ("+79991234567", 2040, meta["app_hash"])
    assert s.device == {"device_model": "iPhone 15", "system_version": "iOS 17.2", "app_version": "10.5",
                        "lang_code": "en", "system_lang_code": "en-US"}


def test_pyrogram_file_in_zip_folder(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pack/acc1.session", pyrogram_file(tmp_path, KEY_B))
        z.writestr("__MACOSX/pack/._acc1.session", b"junk")
        z.writestr("pack/readme.md", b"hi")
    sessions, errors = parse_uploads([("pack.zip", buf.getvalue())])
    [s] = sessions
    assert decoded(s.session) == (2, "149.154.167.51", 443, KEY_B)
    assert s.user_id == 777000
    assert s.api_id is None  # Pyrogram stores api_id but not the hash
    assert errors == [("pack/readme.md", "неподдерживаемый файл")]


def test_bad_files_are_reported(tmp_path):
    sessions, errors = parse_uploads([
        ("test.session", pyrogram_file(tmp_path, KEY_A, test_mode=1)),
        ("broken.session", b"not sqlite at all, just text" * 10),
        ("lonely.json", b'{"phone": "1"}'),
        ("tdata/key_datas", b"x"),
        ("tdata/D877F783D5D3EF8C/maps", b"x"),
    ])
    assert sessions == []
    messages = dict(errors)
    assert "тестового" in messages["test.session"]
    assert "не SQLite" in messages["broken.session"] or "не является" in messages["broken.session"]
    assert "нет файла .session" in messages["lonely.json"]
    assert sum("tdata" in e for _, e in errors) == 1


def test_string_sessions_in_txt():
    telethon = StringSession()
    telethon.set_dc(2, "149.154.167.51", 443)
    telethon.auth_key = AuthKey(KEY_A)
    pyro_v2 = base64.urlsafe_b64encode(struct.pack(">BI?256sQ?", 5, 999, False, KEY_B, 42, False)).decode().rstrip("=")
    text = f"# my sessions\n{telethon.save()}\n\n{pyro_v2}\ngarbage\n"
    sessions, errors = parse_uploads([("list.txt", text.encode())])
    assert [decoded(s.session)[0] for s in sessions] == [2, 5]
    assert sessions[1].user_id == 42
    assert errors == [("list.txt:5", "не похоже на строку сессии")]


def test_parse_string_session_rejects_test_dc():
    raw = struct.pack(">BI?256sQ?", 2, 1, True, KEY_A, 1, False)
    with pytest.raises(ValueError, match="тестового"):
        parse_string_session(base64.urlsafe_b64encode(raw).decode())


def test_import_endpoint_adds_and_dedupes(tmp_path):
    from app.web.main import app

    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "admin", "password": "secret"}).status_code == 200
        files = [
            ("files", ("u1.session", telethon_file(tmp_path, "u1", KEY_A), "application/octet-stream")),
            ("files", ("u1.json", json.dumps({"phone": "+70000000001", "device": "Pixel 8"}).encode(), "application/json")),
            ("files", ("u2.session", pyrogram_file(tmp_path, KEY_B), "application/octet-stream")),
        ]
        body = client.post("/api/accounts/import-files", files=files).json()
        assert body["added"] == 2 and body["errors"] == []
        again = client.post("/api/accounts/import-files", files=files).json()
        assert again["added"] == 0 and sorted(again["duplicates"]) == ["u1.session", "u2.session"]
        accounts = {a["phone"]: a for a in client.get("/api/accounts").json()["items"]}
        assert accounts["+70000000001"]["status"] == "new"
