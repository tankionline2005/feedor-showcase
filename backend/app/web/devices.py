"""Settings → «Устройства»: the browsers a person is signed in on, signing any of them out, and the phone's side of
the login page's QR code.

Every sign-in (the bot, a staff member's password, a QR code) makes a Device; the session cookie carries its id.
A device signed out from another is marked so, and its cookie is refused from then on (deps.py). Signed-out
devices and devices unseen for longer than a cookie lives are forgotten by the worker.
"""

import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, select, update

from .. import geo
from ..logins import COOKIE_LIFE
from ..models import Device, LoginRequest, Member, User, utcnow
from .deps import DB, Principal, Viewer
from .posts import iso

router = APIRouter(prefix="/api/devices", tags=["devices"])

ONLINE = timedelta(minutes=3)  # seen this recently: «в сети»
QR_TTL = timedelta(minutes=3)  # a QR code can be confirmed this long after it was shown
QR_GONE = "QR-код устарел. Обновите его на компьютере и отсканируйте снова"


def ip_of(request: Request) -> str:
    return (request.client.host if request.client else "?")[:64]


async def sign_in(request: Request, db, *, user_id: int | None = None, member: Member | None = None) -> None:
    """A new session for the browser of `request`, as a user or (member) a staff member: a device of theirs."""
    request.session.clear()
    device = Device(
        id=secrets.token_urlsafe(24),
        user_id=None if member else user_id,
        member_id=member.id if member else None,
        user_agent=request.headers.get("user-agent", "")[:512],
        ip=ip_of(request),
    )
    db.add(device)
    await db.commit()
    if member:
        request.session["mid"], request.session["mv"] = member.id, member.session_version
    else:
        request.session["uid"] = user_id
    request.session["did"] = device.id


async def sign_out(db, device_id: str | None) -> None:
    if device_id:
        await db.execute(update(Device).where(Device.id == device_id).values(revoked=True))
        await db.commit()


# what the browser says about itself → what a person recognises: «iPhone», «Safari 18.1»
_BROWSERS = (
    (r"YaBrowser/([\d.]+)", "Яндекс Браузер"),
    (r"SamsungBrowser/([\d.]+)", "Samsung Internet"),
    (r"EdgA?/([\d.]+)", "Edge"),
    (r"EdgiOS/([\d.]+)", "Edge"),
    (r"OPR/([\d.]+)", "Opera"),
    (r"FxiOS/([\d.]+)", "Firefox"),
    (r"Firefox/([\d.]+)", "Firefox"),
    (r"CriOS/([\d.]+)", "Chrome"),
    (r"Chrome/([\d.]+)", "Chrome"),
    (r"Version/([\d.]+).*Safari/", "Safari"),
    (r"Safari/", "Safari"),
)


def _version(found: str, browser: str) -> str:
    parts = found.split(".")
    # Safari and Яндекс are known by «18.1»; the rest by the major version alone
    return ".".join(parts[:2]) if browser in ("Safari", "Яндекс Браузер") and len(parts) > 1 and parts[1] != "0" \
        else parts[0]


def describe(user_agent: str | None, model: str | None = None, os_version: str | None = None,
             standalone: bool = False) -> dict:
    """{"name": the device, "app": the browser, "platform": ios | android | windows | mac | linux | other,
    "form": phone | tablet | computer}."""
    ua = user_agent or ""
    browser = "Браузер"
    for pattern, name in _BROWSERS:
        if m := re.search(pattern, ua):
            browser = f"{name} {_version(m.group(1), name)}" if m.groups() else name
            break
    if model == "iPad" or "iPad" in ua:
        platform, form, name = "ios", "tablet", "iPad"
    elif "iPhone" in ua or "iPod" in ua:
        platform, form, name = "ios", "phone", "iPhone"
    elif "Android" in ua:
        platform, form = "android", ("phone" if "Mobile" in ua else "tablet")
        if not model and (m := re.search(r"Android [\d.]+; ([^;)]+?)(?: Build/[^;)]*)?\)", ua)):
            model = None if m.group(1).strip() in ("K", "wv") else m.group(1).strip()
        name = model or ("Android-смартфон" if form == "phone" else "Android-планшет")
    elif "Windows" in ua:
        platform, form = "windows", "computer"
        major = int(os_version.split(".")[0]) if os_version and os_version.split(".")[0].isdigit() else 0
        name = "ПК с Windows 11" if major >= 13 else "ПК с Windows 10" if major > 0 else "ПК с Windows"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        platform, form, name = "mac", "computer", "Mac"
    elif "CrOS" in ua:
        platform, form, name = "linux", "computer", "Chromebook"
    elif "Linux" in ua:
        platform, form, name = "linux", "computer", "ПК с Linux"
    else:
        platform, form, name = "other", "computer", "Устройство"
    return {"name": name, "app": f"{browser} · на экране «Домой»" if standalone else browser,
            "platform": platform, "form": form}


def device_json(d: Device, current: bool = False) -> dict:
    return {
        "id": d.id,
        **describe(d.user_agent, d.model, d.os_version, d.standalone),
        "place": geo.place(d.ip),
        "ip": d.ip,
        "created_at": iso(d.created_at),
        "last_seen_at": iso(d.last_seen_at),
        "online": current or utcnow() - d.last_seen_at < ONLINE,
    }


def _person(viewer: Principal):
    """The viewer's own devices: a user's, or a staff member's."""
    if viewer.is_admin:
        raise HTTPException(status_code=400, detail="У администратора нет списка устройств")
    if viewer.member_id:
        return Device.member_id == viewer.member_id
    return and_(Device.user_id == viewer.user_id, Device.member_id.is_(None))


def _alive():
    return and_(Device.revoked.is_(False), Device.last_seen_at > utcnow() - COOKIE_LIFE)


@router.get("")
async def devices(viewer: Viewer, request: Request, db: DB) -> dict:
    """This device, and the person's other devices, the latest used first."""
    mine = _person(viewer)
    current_id = request.session.get("did")
    rows = (await db.execute(select(Device).where(mine, _alive()).order_by(Device.last_seen_at.desc()))).scalars()
    current, others = None, []
    for d in rows:
        if d.id == current_id:
            current = device_json(d, current=True)
        else:
            others.append(device_json(d))
    return {"current": current, "others": others}


class HintsIn(BaseModel):
    model: str | None = Field(default=None, max_length=64)
    os_version: str | None = Field(default=None, max_length=32)
    standalone: bool = False


@router.put("/current")
async def hints(body: HintsIn, viewer: Viewer, request: Request, db: DB) -> dict:
    """What the browser tells about itself beyond its user agent (an Android phone's model, the Windows version, an
    app on the home screen): the list names the device better."""
    _person(viewer)
    device = await db.get(Device, request.session.get("did") or "")
    if device is not None:
        device.model = (body.model or "").strip() or None
        device.os_version = (body.os_version or "").strip() or None
        device.standalone = body.standalone
        await db.commit()
    return {"ok": True}


@router.delete("/{device_id}")
async def terminate(device_id: str, viewer: Viewer, request: Request, db: DB) -> dict:
    """Signs one of the person's other devices out."""
    if device_id == request.session.get("did"):
        raise HTTPException(status_code=400, detail="Чтобы выйти на этом устройстве, нажмите «Выйти» в настройках")
    done = await db.execute(update(Device).where(Device.id == device_id, _person(viewer)).values(revoked=True))
    await db.commit()
    if not done.rowcount:
        raise HTTPException(status_code=404, detail="Этот сеанс уже завершён")
    return {"ok": True}


@router.post("/terminate-others")
async def terminate_others(viewer: Viewer, request: Request, db: DB) -> dict:
    """Signs out every device of the person but this one."""
    done = await db.execute(
        update(Device)
        .where(_person(viewer), Device.revoked.is_(False), Device.id != (request.session.get("did") or ""))
        .values(revoked=True)
    )
    await db.commit()
    return {"ok": True, "count": done.rowcount}


async def _qr(db, token: str) -> LoginRequest:
    req = await db.get(LoginRequest, token)
    if req is None or req.kind != "qr" or req.status not in ("pending", "scanned") \
            or utcnow() - req.created_at > QR_TTL:
        raise HTTPException(status_code=404, detail=QR_GONE)
    return req


@router.get("/qr/{token}")
async def qr_scanned(token: str, viewer: Viewer, db: DB) -> dict:
    """A phone scanned a login page's QR code: which browser asks to sign in (the page then says to confirm it on
    the phone)."""
    _person(viewer)
    req = await _qr(db, token)
    req.status = "scanned"
    await db.commit()
    return {**describe(req.user_agent), "place": geo.place(req.ip), "ip": req.ip}


@router.post("/qr/{token}")
async def qr_confirm(token: str, viewer: Viewer, db: DB) -> dict:
    """The phone confirmed it: the browser of the QR code signs in as the person (on its next check)."""
    _person(viewer)
    req = await _qr(db, token)
    if viewer.member_id:
        req.member_id = viewer.member_id
    else:
        if (user := await db.get(User, viewer.user_id)) is None or user.blocked:
            raise HTTPException(status_code=403, detail="Доступ к аккаунту закрыт администратором")
        req.user_id = viewer.user_id
    req.status = "confirmed"
    await db.commit()
    return {"ok": True}


@router.post("/qr/{token}/decline")
async def qr_decline(token: str, viewer: Viewer, db: DB) -> dict:
    _person(viewer)
    req = await db.get(LoginRequest, token)
    if req is not None and req.kind == "qr" and req.status in ("pending", "scanned"):
        req.status = "declined"
        await db.commit()
    return {"ok": True}
