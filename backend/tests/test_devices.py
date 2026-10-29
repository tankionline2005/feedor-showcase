import json
from base64 import b64encode
from datetime import timedelta
from functools import partial

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import select, update

from app.config import settings
from app.db import SessionLocal
from app.logins import confirm_login, forget_old
from app.models import Device, LoginRequest, User, utcnow
from app.web import members as members_api
from app.web.devices import describe

from test_user_channels import ctx, give_plan, run  # noqa: F401  (ctx is a fixture)

IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/18.1 Mobile/15E148 Safari/604.1")
WINDOWS = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 "
           "Safari/537.36")
ANDROID = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile "
           "Safari/537.36")


def browser(app, ua: str) -> TestClient:
    return TestClient(app, headers={"user-agent": ua})


def bot_sign_in(app, client, tg_id: int, ua: str) -> TestClient:
    b = browser(app, ua)
    code = b.post("/api/auth/tg/start").json()["link"].rsplit("login_", 1)[1]
    client.portal.call(confirm_login, code, {"id": tg_id, "first_name": f"u{tg_id}"})
    assert b.get("/api/auth/tg/status").json()["status"] == "ok"
    return b


def test_the_user_sees_their_devices_and_signs_one_out(ctx):
    app, client = ctx
    phone = bot_sign_in(app, client, 9801, IPHONE)
    laptop = bot_sign_in(app, client, 9801, WINDOWS)
    stranger = bot_sign_in(app, client, 9802, ANDROID)

    seen = phone.get("/api/devices").json()
    assert seen["current"]["name"] == "iPhone" and seen["current"]["app"] == "Safari 18.1"
    assert seen["current"]["online"] is True and seen["current"]["platform"] == "ios"
    assert [(d["name"], d["app"]) for d in seen["others"]] == [("ПК с Windows", "Chrome 141")]  # not the stranger's

    # what the browser tells besides: the Windows version, an app on the home screen
    laptop.put("/api/devices/current", json={"os_version": "15.0.0"})
    phone.put("/api/devices/current", json={"standalone": True})
    seen = phone.get("/api/devices").json()
    assert seen["current"]["app"] == "Safari 18.1 · на экране «Домой»"
    assert seen["others"][0]["name"] == "ПК с Windows 11"

    # one of their own only, never this one
    assert phone.delete(f"/api/devices/{stranger.get('/api/devices').json()['current']['id']}").status_code == 404
    assert phone.delete(f"/api/devices/{seen['current']['id']}").status_code == 400
    assert phone.delete(f"/api/devices/{seen['others'][0]['id']}").json() == {"ok": True}
    r = laptop.get("/api/auth/me")
    assert r.status_code == 401 and "другого устройства" in r.json()["detail"]
    assert laptop.get("/api/auth/me").status_code == 401  # the cookie is gone too
    assert phone.get("/api/devices").json()["others"] == []
    assert stranger.get("/api/auth/me").status_code == 200

    # «Завершить другие сеансы»
    second, third = bot_sign_in(app, client, 9801, ANDROID), bot_sign_in(app, client, 9801, WINDOWS)
    assert len(phone.get("/api/devices").json()["others"]) == 2
    assert phone.post("/api/devices/terminate-others").json() == {"ok": True, "count": 2}
    assert second.get("/api/auth/me").status_code == 401 and third.get("/api/auth/me").status_code == 401
    assert phone.get("/api/auth/me").status_code == 200

    # «Выйти»: this device is gone from the list
    phone.post("/api/auth/logout")
    other = bot_sign_in(app, client, 9801, WINDOWS)
    assert other.get("/api/devices").json()["others"] == []


def test_a_phone_signs_a_computer_in_by_its_qr_code(ctx):
    app, client = ctx
    phone = bot_sign_in(app, client, 9811, IPHONE)
    laptop = browser(app, WINDOWS)

    token = laptop.post("/api/auth/qr/start").json()["token"]
    assert laptop.get("/api/auth/qr/status").json() == {"status": "pending"}
    assert laptop.get("/api/auth/me").status_code == 401
    # the bot takes no QR code for a login of its own
    assert client.portal.call(confirm_login, token, {"id": 9812})[0] == "expired"

    asked = phone.get(f"/api/devices/qr/{token}").json()
    assert asked["name"] == "ПК с Windows" and asked["app"] == "Chrome 141" and asked["ip"]
    assert laptop.get("/api/auth/qr/status").json() == {"status": "scanned"}  # «confirm it on the phone»
    assert phone.post(f"/api/devices/qr/{token}").json() == {"ok": True}
    assert laptop.get("/api/auth/qr/status").json() == {"status": "ok"}
    me = laptop.get("/api/auth/me").json()
    assert me["role"] == "user" and me["user"] == "u9811"
    assert [d["name"] for d in phone.get("/api/devices").json()["others"]] == ["ПК с Windows"]
    # used once
    assert phone.post(f"/api/devices/qr/{token}").status_code == 404

    # a code the page showed before still signs in (the phone may have scanned it just before a new one came)
    other = browser(app, WINDOWS)
    first = other.post("/api/auth/qr/start").json()["token"]
    other.post("/api/auth/qr/start")
    phone.get(f"/api/devices/qr/{first}")
    phone.post(f"/api/devices/qr/{first}")
    assert other.get("/api/auth/qr/status").json() == {"status": "ok"}

    # «Отмена» on the phone: the page says so, and a new code waits again
    third = browser(app, WINDOWS)
    token = third.post("/api/auth/qr/start").json()["token"]
    phone.get(f"/api/devices/qr/{token}")
    phone.post(f"/api/devices/qr/{token}/decline")
    assert third.get("/api/auth/qr/status").json() == {"status": "declined"}
    assert phone.post(f"/api/devices/qr/{token}").status_code == 404
    third.post("/api/auth/qr/start")
    assert third.get("/api/auth/qr/status").json() == {"status": "pending"}

    # an old code: gone
    token = third.post("/api/auth/qr/start").json()["token"]
    run(client, partial(_age, token, timedelta(minutes=4)))
    assert phone.get(f"/api/devices/qr/{token}").status_code == 404
    assert phone.post(f"/api/devices/qr/{token}").status_code == 404


async def _age(token: str, by: timedelta) -> None:
    async with SessionLocal() as s:
        await s.execute(update(LoginRequest).where(LoginRequest.token == token).values(created_at=utcnow() - by))
        await s.commit()


def test_a_staff_member_s_phone_signs_a_computer_in_as_them(ctx):
    app, client = ctx
    members_api._failed.clear()
    owner = bot_sign_in(app, client, 9821, WINDOWS)
    client.portal.call(give_plan, 9821)
    password = owner.post("/api/members", json={"login": "dev.staff"}).json()["password"]
    member_id = owner.get("/api/members").json()["items"][0]["id"]
    phone = browser(app, ANDROID)
    assert phone.post("/api/members/login", json={"login": "dev.staff", "password": password}).status_code == 200
    phone.put("/api/devices/current", json={"model": "Pixel 9"})

    laptop = browser(app, WINDOWS)
    token = laptop.post("/api/auth/qr/start").json()["token"]
    phone.get(f"/api/devices/qr/{token}")
    phone.post(f"/api/devices/qr/{token}")
    assert laptop.get("/api/auth/qr/status").json() == {"status": "ok"}
    assert laptop.get("/api/auth/me").json()["role"] == "member"

    # each person their own devices
    seen = laptop.get("/api/devices").json()
    assert seen["current"]["name"] == "ПК с Windows"
    assert [d["name"] for d in seen["others"]] == ["Pixel 9"]
    assert owner.get("/api/devices").json()["others"] == []

    # a new password signs the staff member out everywhere: their devices are gone
    owner.post(f"/api/members/{member_id}/password")
    assert laptop.get("/api/auth/me").status_code == 401
    phone2 = browser(app, IPHONE)
    new = owner.post(f"/api/members/{member_id}/password").json()["password"]
    phone2.post("/api/members/login", json={"login": "dev.staff", "password": new})
    assert phone2.get("/api/devices").json()["others"] == []


def _cookie(session: dict) -> str:
    data = b64encode(json.dumps(session).encode("utf-8"))
    return TimestampSigner(str(settings.secret_key)).sign(data).decode("utf-8")


async def _user_id(tg_id: int) -> int:
    async with SessionLocal() as s:
        return await s.scalar(select(User.id).where(User.tg_user_id == tg_id))


def test_a_session_from_before_devices_becomes_one(ctx):
    app, client = ctx
    phone = bot_sign_in(app, client, 9831, IPHONE)
    user_id = run(client, partial(_user_id, 9831))
    old = browser(app, WINDOWS)
    old.cookies.set("km_session", _cookie({"uid": user_id}))
    assert old.get("/api/auth/me").status_code == 200
    assert [d["name"] for d in phone.get("/api/devices").json()["others"]] == ["ПК с Windows"]
    assert old.get("/api/devices").json()["current"]["name"] == "ПК с Windows"

    # signed out, the old cookie no longer works, even a copy of it from before
    phone.post("/api/devices/terminate-others")
    copy = browser(app, WINDOWS)
    copy.cookies.set("km_session", _cookie({"uid": user_id}))
    assert copy.get("/api/auth/me").status_code == 401


async def _old_devices(tg_id: int) -> int:
    async with SessionLocal() as s:
        user_id = await s.scalar(select(User.id).where(User.tg_user_id == tg_id))
        await s.execute(update(Device).where(Device.user_id == user_id)
                        .values(last_seen_at=utcnow() - timedelta(days=32)))
        await s.commit()
    await forget_old()
    async with SessionLocal() as s:
        return len((await s.execute(select(Device).where(Device.user_id == user_id))).scalars().all())


def test_devices_unused_for_longer_than_a_cookie_lives_are_forgotten(ctx):
    app, client = ctx
    bot_sign_in(app, client, 9841, IPHONE)
    assert run(client, partial(_old_devices, 9841)) == 0


def test_devices_are_named_as_people_know_them():
    assert describe(IPHONE)["form"] == "phone"
    assert describe(ANDROID) == {"name": "Android-смартфон", "app": "Chrome 141", "platform": "android",
                                 "form": "phone"}
    samsung = ("Mozilla/5.0 (Linux; Android 13; SM-S918B Build/TP1A) AppleWebKit/537.36 (KHTML, like Gecko) "
               "SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36")
    assert describe(samsung)["name"] == "SM-S918B" and describe(samsung)["app"] == "Samsung Internet 23"
    mac = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 "
           "Safari/605.1.15")
    assert describe(mac)["name"] == "Mac" and describe(mac)["app"] == "Safari 18"
    assert describe(mac, model="iPad")["name"] == "iPad"  # an iPad's Safari says it is a Mac
    yandex = WINDOWS.replace("Safari/537.36", "YaBrowser/25.8.1.800 Safari/537.36")
    assert describe(yandex, os_version="10.0.0")["name"] == "ПК с Windows 10"
    assert describe(yandex)["app"] == "Яндекс Браузер 25.8"


def test_a_browser_tells_the_server_its_page_froze(ctx, caplog):
    app, client = ctx
    phone = bot_sign_in(app, client, 9851, IPHONE)
    with caplog.at_level("WARNING", logger="client"):
        r = phone.post("/api/client-log", json={"kind": "invisible-layer", "detail": {"layer": "div.lightbox"}})
    assert r.json() == {"ok": True}
    assert "invisible-layer" in caplog.text and "div.lightbox" in caplog.text
