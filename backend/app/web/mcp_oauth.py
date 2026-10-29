"""The MCP connector's sign-in: OAuth 2.1 as AI apps (Claude, ChatGPT…) do it for a connector — the person pastes
the connector's address, the app registers itself (RFC 7591), sends the person here to sign in and allow it (the
page #/connect/<request>), and gets tokens that act as that person: the project's owner, or a staff member with the
rights the owner gave them (deps.key_principal). Programs that take a header use the API key instead.

Nothing is kept but the connections themselves: an app's client_id, a pending request and an authorization code are
signed, not stored; a code is good once (McpConnection.code_id).
"""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from ..config import settings, site_name
from ..db import SessionLocal
from ..models import McpConnection, Member, Project, utcnow
from ..plans import plan_of
from .deps import DB, MCP_TOKEN_PREFIX, Principal, Viewer, token_hash

router = APIRouter(tags=["mcp"])

REQUEST_TTL = 15 * 60  # seconds the person has to allow the app
CODE_TTL = 5 * 60
ACCESS_TTL = timedelta(days=7)  # then the app refreshes it (the refresh token lasts while the connection does)

_signer = URLSafeTimedSerializer(settings.secret_key, salt="mcp-oauth")


def origin(request: Request) -> str:
    """The site's address as the apps reach it."""
    return f"https://{settings.domain}" if settings.domain else str(request.base_url).rstrip("/")


@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
async def protected_resource(request: Request) -> dict:
    o = origin(request)
    return {"resource": f"{o}/mcp", "authorization_servers": [o], "bearer_methods_supported": ["header"],
            "resource_name": site_name()}


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
@router.get("/.well-known/oauth-authorization-server/mcp", include_in_schema=False)
@router.get("/.well-known/openid-configuration", include_in_schema=False)
async def authorization_server(request: Request) -> dict:
    o = origin(request)
    return {
        "issuer": o,
        "authorization_endpoint": f"{o}/oauth/authorize",
        "token_endpoint": f"{o}/oauth/token",
        "registration_endpoint": f"{o}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp"],
    }


def _redirect_ok(uri: str) -> bool:
    parts = urlsplit(uri)
    if parts.fragment or not parts.netloc and parts.scheme in ("http", "https"):
        return False
    if parts.scheme == "https":
        return True
    # a desktop app listens on the computer itself; other apps come back to a scheme of their own (cursor://…)
    if parts.scheme == "http":
        return parts.hostname in ("localhost", "127.0.0.1", "::1")
    return bool(parts.scheme) and parts.scheme not in ("javascript", "data", "file")


class RegisterIn(BaseModel):
    redirect_uris: list[str] = Field(min_length=1, max_length=10)
    client_name: str | None = Field(default=None, max_length=200)


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status,
                        headers={"Cache-Control": "no-store"})


@router.post("/oauth/register", include_in_schema=False)
async def register(body: RegisterIn) -> JSONResponse:
    """An app makes itself known: its name and where it waits for the code. The client_id carries both, signed."""
    if not all(_redirect_ok(u) for u in body.redirect_uris):
        return _oauth_error("invalid_redirect_uri", "Неподходящий адрес возврата")
    name = " ".join((body.client_name or "ИИ-приложение").split())[:120] or "ИИ-приложение"
    client_id = _signer.dumps({"n": name, "r": body.redirect_uris}, salt="client")
    return JSONResponse({
        "client_id": client_id, "client_name": name, "redirect_uris": body.redirect_uris,
        "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }, status_code=201)


def _client(client_id: str) -> dict | None:
    try:
        return _signer.loads(client_id, salt="client")
    except BadSignature:
        return None


def _page(text: str, status: int = 400) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                        f"<body style='font:17px -apple-system,sans-serif;padding:32px;max-width:520px;margin:auto'>"
                        f"<h3>Не получилось подключить</h3><p>{text}</p>", status_code=status)


@router.get("/oauth/authorize", include_in_schema=False)
async def authorize(response_type: str = "", client_id: str = "", redirect_uri: str = "", code_challenge: str = "",
                    code_challenge_method: str = "", state: str = "") -> Response:
    """The app sends the person here: to the site's page that asks them to allow it (they sign in first if need be)."""
    client = _client(client_id)
    if client is None:
        return _page("Приложение не зарегистрировано. Добавьте коннектор заново.")
    if redirect_uri not in client["r"]:
        return _page("Адрес возврата не совпадает с тем, что приложение указало при регистрации.")
    if response_type != "code" or code_challenge_method != "S256" or not code_challenge:
        return _page("Приложение просит вход, который мы не поддерживаем (нужны code и PKCE S256).")
    request_token = _signer.dumps({"n": client["n"], "c": client_id, "r": redirect_uri, "ch": code_challenge,
                                   "s": state}, salt="request")
    return RedirectResponse(f"/#/connect/{request_token}", status_code=302)


def _request(token: str) -> dict:
    try:
        return _signer.loads(token, salt="request", max_age=REQUEST_TTL)
    except SignatureExpired as exc:
        raise HTTPException(status_code=410, detail="Запрос устарел — подключите коннектор в приложении заново") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=404, detail="Такого запроса нет") from exc


def _person_only(viewer: Principal) -> None:
    if viewer.is_admin or viewer.project_id is None:
        raise HTTPException(status_code=403, detail="Администратор подключает MCP по ключу API — в заголовке")


class RequestIn(BaseModel):
    request: str = Field(max_length=4000)


@router.post("/api/mcp/request")
async def connect_request(body: RequestIn, viewer: Viewer, db: DB) -> dict:
    """The page that asks to allow an app: which app, where it goes back to, and as whom it will act."""
    _person_only(viewer)
    req = _request(body.request)
    project = await db.get(Project, viewer.project_id)
    member = await db.get(Member, viewer.member_id) if viewer.member_id else None
    return {"client_name": req["n"], "redirect_host": urlsplit(req["r"]).netloc or req["r"],
            "project_title": project.channel_title or "Проект", "role": viewer.role, "login": member.login if member else None,
            "has_api": bool(viewer.plan and plan_of(viewer.plan).api)}


class ApproveIn(RequestIn):
    allow: bool


@router.post("/api/mcp/approve")
async def approve(body: ApproveIn, viewer: Viewer) -> dict:
    """«Разрешить» or «Отклонить»: back to the app, with a code that becomes its tokens, or with a refusal."""
    _person_only(viewer)
    req = _request(body.request)
    if not body.allow:
        return {"redirect": _back(req["r"], {"error": "access_denied", "state": req["s"]})}
    if not (viewer.plan and plan_of(viewer.plan).api):
        raise HTTPException(status_code=409, detail="Нужен действующий тариф с доступом к API")
    code = _signer.dumps({"req": req, "p": viewer.project_id, "m": viewer.member_id, "j": secrets.token_hex(16)},
                         salt="code")
    return {"redirect": _back(req["r"], {"code": code, "state": req["s"]})}


def _back(redirect_uri: str, params: dict) -> str:
    query = urlencode({k: v for k, v in params.items() if v})
    return f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}{query}"


def _new_tokens() -> tuple[str, str]:
    return MCP_TOKEN_PREFIX + secrets.token_urlsafe(32), MCP_TOKEN_PREFIX + "r_" + secrets.token_urlsafe(32)


def _tokens_json(access: str, refresh: str) -> JSONResponse:
    return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
                         "refresh_token": refresh, "scope": "mcp"}, headers={"Cache-Control": "no-store"})


@router.post("/oauth/token", include_in_schema=False)
async def token(grant_type: str = Form(""), code: str = Form(""), redirect_uri: str = Form(""),
                client_id: str = Form(""), code_verifier: str = Form(""), refresh_token: str = Form("")) -> JSONResponse:
    """A code for tokens (with the PKCE check), or a refresh token for new ones — each refresh replaces both."""
    if grant_type == "authorization_code":
        try:
            data = _signer.loads(code, salt="code", max_age=CODE_TTL)
        except BadSignature:
            return _oauth_error("invalid_grant", "Код неверный или устарел")
        req = data["req"]
        if req["r"] != redirect_uri or req["c"] != client_id:
            return _oauth_error("invalid_grant", "Код выдан другому приложению")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        if not code_verifier or challenge != req["ch"]:
            return _oauth_error("invalid_grant", "Проверка PKCE не прошла")
        access, refresh = _new_tokens()
        async with SessionLocal() as s:
            s.add(McpConnection(project_id=data["p"], member_id=data["m"], client_name=req["n"],
                                redirect_host=(urlsplit(req["r"]).netloc or req["r"])[:255],
                                access_hash=token_hash(access), refresh_hash=token_hash(refresh), code_id=data["j"],
                                access_expires_at=utcnow() + ACCESS_TTL))
            try:
                await s.commit()
            except IntegrityError:
                return _oauth_error("invalid_grant", "Код уже использован")
        return _tokens_json(access, refresh)
    if grant_type == "refresh_token":
        async with SessionLocal() as s:
            conn = await s.scalar(select(McpConnection).where(McpConnection.refresh_hash == token_hash(refresh_token)))
            if conn is None:
                return _oauth_error("invalid_grant", "Подключение отключено — подключите коннектор заново")
            access, refresh = _new_tokens()
            conn.access_hash, conn.refresh_hash = token_hash(access), token_hash(refresh)
            conn.access_expires_at = utcnow() + ACCESS_TTL
            await s.commit()
        return _tokens_json(access, refresh)
    return _oauth_error("unsupported_grant_type", "Поддерживаются authorization_code и refresh_token")


def _mine(viewer: Principal):
    return (McpConnection.member_id == viewer.member_id) if viewer.member_id else (
        (McpConnection.project_id == viewer.project_id) & McpConnection.member_id.is_(None))


@router.get("/api/mcp/connections")
async def connections(viewer: Viewer, db: DB) -> dict:
    """«Подключённые приложения»: the AI apps this person let in."""
    _person_only(viewer)
    rows = (await db.execute(select(McpConnection).where(_mine(viewer)).order_by(McpConnection.id.desc()))).scalars()
    return {"items": [{"id": c.id, "client_name": c.client_name, "redirect_host": c.redirect_host,
                       "created_at": c.created_at.isoformat() + "Z",
                       "last_used_at": c.last_used_at.isoformat() + "Z" if c.last_used_at else None} for c in rows]}


@router.delete("/api/mcp/connections/{connection_id}")
async def disconnect(connection_id: int, viewer: Viewer, db: DB) -> dict:
    """«Отключить»: the app's tokens stop working at once."""
    _person_only(viewer)
    gone = await db.execute(delete(McpConnection).where(McpConnection.id == connection_id, _mine(viewer)))
    if not gone.rowcount:
        raise HTTPException(status_code=404, detail="Такого подключения нет")
    await db.commit()
    return {"ok": True}
