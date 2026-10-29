"""«MCP-коннектор»: the site for AI apps (Claude, ChatGPT, Cursor…) — an MCP server at /mcp, Streamable HTTP, without
sessions (every message stands alone, the answer is JSON).

Its tools are the site's own methods a key may call (api_routes.BY_KEY) and the read API (api_routes.DATA), only
those the caller's rights allow; a tool is called as the method itself, with the caller's key or connector token
(mcp_oauth) — the same checks as on the site and in the API, nothing to fall out of step.
"""

import base64
import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..config import site_name
from . import api_routes
from .deps import KeyRefused, Principal, Viewer, given_key, key_principal
from .mcp_oauth import origin

router = APIRouter(tags=["mcp"])

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")  # the newest first: offered when the app's is unknown
TEXT_MOST = 60_000  # characters of a tool's answer: more would crowd the AI's context
IMAGE_MOST = 3 * 1024 * 1024

# the methods an AI has no use for as tools, and why
NOT_TOOLS = {
    "GET /api/live": "бесконечный поток событий для страниц сайта",
    "GET /api/badges": "счётчики для значков на сайте",
    "POST /api/badges/seen": "счётчики для значков на сайте",
    "POST /api/triggers/{trigger_id}/seen": "счётчики для значков на сайте",
    "GET /api/chats/status": "ответ ИИ инструмент и так ждёт до конца",
    "GET /api/api-key": "ключ не нужен ИИ, у него своё подключение",
    "POST /api/api-key/reset": "сбросить ключ может только человек",
    "GET /api/api-key/reference": "справка для людей; у ИИ — описания инструментов",
    "GET /api/mcp/tools": "у ИИ и так есть список инструментов",
    "DELETE /api/mcp/connections/{connection_id}": "отключить приложение может только человек",
    "GET /api/channels/{channel_id}/avatar": "аватары — для страниц сайта",
    "GET /api/projects/{project_id}/avatar": "аватары — для страниц сайта",
}

VERBS = {"GET": "get", "POST": "post", "PUT": "put", "PATCH": "patch", "DELETE": "delete"}

INSTRUCTIONS = (
    "Сервис мониторинга Telegram-каналов. Подключение принадлежит проекту, у которого есть свой канал: весь "
    "мониторинг — для развития этого канала, остальные каналы — источники и конкуренты. Начните с get_auth_me (кто "
    "вы и что вам можно) и get_data_project (канал проекта и его средние цифры). Темы — новости, которые ИИ сервиса "
    "собрал из постов отслеживаемых каналов: get_data_topics и get_data_topics_topic_id. Посты — get_data_posts "
    "(period, sort=fr_plus — что зашло лучше обычного). Публикации канала — get_publications. Пост пишет ИИ сервиса "
    "в чате: post_chats, затем post_chats_thread_id_messages с просьбой; готовый пост — в ответе, его можно поправить "
    "(patch_chat_posts_post_id), опубликовать (post_chat_posts_post_id_publish) или запланировать "
    "(put_chat_posts_post_id_schedule). Свой готовый текст — post_chat_posts (source_post_ids — посты, из которых "
    "взять фото), дальше так же. Пишите голосом канала проекта. Всё выполняется от имени человека, который "
    "подключил вас, и с его правами: прежде чем публиковать, удалять или менять настройки, спросите его."
)


@dataclass
class Tool:
    name: str
    method: str
    path: str
    who: str
    group: str
    description: str
    schema: dict
    path_params: list[str] = field(default_factory=list)
    query: list[str] = field(default_factory=list)
    body: list[str] | None = None


def tool_name(method: str, path: str) -> str:
    """GET /api/topics/{topic_id} → get_topics_topic_id; the read API's: GET /api/v1/topics → get_data_topics."""
    parts = [p.strip("{}") for p in path.split("/")[2:] if p]
    if parts[:1] == ["v1"]:
        parts = ["data", *parts[1:]]
    return re.sub(r"[^a-zA-Z0-9_]", "_", "_".join([VERBS[method], *parts]))


def _plain(schema, components: dict, depth: int = 0):
    """A schema as an app reads it: references put in place, pydantic's titles left out."""
    if isinstance(schema, list):
        return [_plain(x, components, depth) for x in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema and depth < 6:
        return _plain(components.get(schema["$ref"].rsplit("/", 1)[-1], {}), components, depth + 1)
    return {k: _plain(v, components, depth) for k, v in schema.items() if k != "title"}


_tools: list[Tool] | None = None


def all_tools(app) -> list[Tool]:
    """Every tool there is, built once from the methods themselves (their parameters and bodies)."""
    global _tools
    if _tools is not None:
        return _tools
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    order = {name: i for i, name in enumerate([*api_routes.DATA, *api_routes.BY_KEY])}
    found = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            key = f"{method.upper()} {path}"
            entry = api_routes.DATA.get(key) or api_routes.BY_KEY.get(key)
            if entry is None or key in NOT_TOOLS:
                continue
            content = op.get("requestBody", {}).get("content", {})
            if any("form-data" in ct for ct in content):
                continue  # files: an AI app has none to send
            props: dict = {}
            required: list[str] = []
            tool = Tool(tool_name(method.upper(), path), method.upper(), path, entry.who, entry.group,
                        f"{entry.text} [{entry.group}; {key}]", {})
            for p in op.get("parameters", []):
                props[p["name"]] = _plain(p.get("schema", {}), components)
                (tool.path_params if p["in"] == "path" else tool.query).append(p["name"])
                if p.get("required"):
                    required.append(p["name"])
            if content:
                body = _plain(next(iter(content.values())).get("schema", {}), components)
                if "anyOf" in body:  # an optional body
                    body = next((x for x in body["anyOf"] if x.get("type") != "null"), {})
                tool.body = list(body.get("properties", {}))
                for name, s in body.get("properties", {}).items():
                    props.setdefault(name, s)
                required += [r for r in body.get("required", []) if r not in required]
                if not tool.body:  # a body of any keys (the admin's settings)
                    props["values"] = {"type": "object", "description": "ключи и значения"}
                    tool.body = ["values"]
            tool.schema = {"type": "object", "properties": props, **({"required": required} if required else {})}
            found.append((order[key], tool))
    _tools = [t for _, t in sorted(found, key=lambda x: x[0])]
    return _tools


def tools_for(app, viewer: Principal) -> list[Tool]:
    return [t for t in all_tools(app) if api_routes.may(t.who, viewer)]


def _tool_json(t: Tool) -> dict:
    return {"name": t.name, "description": t.description, "inputSchema": t.schema,
            "annotations": {"readOnlyHint": t.method == "GET", "destructiveHint": t.method == "DELETE",
                            "openWorldHint": False}}


async def _call(app, token: str, tool: Tool, args: dict) -> dict:
    """The tool done as its method: a request to the site itself, with the caller's token."""
    url = tool.path
    for name in tool.path_params:
        if name not in args:
            return _text(f"Не хватает параметра {name}", error=True)
        url = url.replace(f"{{{name}}}", quote(str(args[name]), safe=""))
    query = {q: args[q] for q in tool.query if args.get(q) is not None}
    body = None
    if tool.body is not None:
        body = args.get("values", {}) if tool.body == ["values"] else {b: args[b] for b in tool.body if b in args}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mcp.local",
                                 headers={"Authorization": f"Bearer {token}"}, timeout=600) as client:
        r = await client.request(tool.method, url, params=query, json=body)
        kind = r.headers.get("content-type", "")
        if r.status_code >= 400:
            try:
                data = r.json()
                detail = data.get("detail") or data.get("error", {}).get("message") or data
            except ValueError:
                detail = r.text
            return _text(f"Ошибка {r.status_code}: {detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)}",
                         error=True)
        if kind.startswith("image/"):
            if len(r.content) > IMAGE_MOST:
                return _text("Картинка слишком большая")
            return {"content": [{"type": "image", "data": base64.b64encode(r.content).decode(),
                                 "mimeType": kind.split(";")[0]}], "isError": False}
        if kind.startswith("application/x-ndjson"):
            # the AI's answer in a chat: the stream has ended — the chat's latest messages are the answer
            thread = args.get("thread_id")
            chat = (await client.get(f"/api/chats/{quote(str(thread), safe='')}")).json() if thread else {}
            return _text(json.dumps({"id": chat.get("id"), "messages": chat.get("messages", [])[-2:]},
                                    ensure_ascii=False))
        try:
            return _text(json.dumps(r.json(), ensure_ascii=False))
        except ValueError:
            return _text(r.text)


def _text(text: str, error: bool = False) -> dict:
    if len(text) > TEXT_MOST:
        text = text[:TEXT_MOST] + "\n…ответ обрезан: запросите меньше (limit, offset или фильтры)"
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


async def _handle(request: Request, viewer: Principal, token: str, msg) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    if "id" not in msg:
        return None  # a notification (initialized, cancelled…): nothing to answer
    id_, method, params = msg["id"], msg["method"], msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": site_name(), "version": "1.0"},
            "instructions": INSTRUCTIONS,
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": [_tool_json(t) for t in tools_for(request.app, viewer)]}}
    if method == "tools/call":
        tool = next((t for t in tools_for(request.app, viewer) if t.name == params.get("name")), None)
        if tool is None:
            return _error(id_, -32602, f"Нет такого инструмента: {params.get('name')}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(id_, -32602, "arguments — объект")
        return {"jsonrpc": "2.0", "id": id_, "result": await _call(request.app, token, tool, args)}
    return _error(id_, -32601, f"Метод не поддерживается: {method}")


def _unauthorized(request: Request, message: str) -> JSONResponse:
    # where the app learns how to sign in (mcp_oauth)
    meta = f"{origin(request)}/.well-known/oauth-protected-resource"
    return JSONResponse(_error(None, -32001, message), status_code=401,
                        headers={"WWW-Authenticate": f'Bearer resource_metadata="{meta}"'})


@router.post("/mcp", include_in_schema=False)
async def mcp(request: Request) -> Response:
    token = given_key(request)
    if not token:
        return _unauthorized(request, "Нужно подключение: войдите через приложение или передайте ключ API")
    try:
        viewer = await key_principal(token, count=False)  # each tool's own request is counted
    except KeyRefused as exc:
        if exc.status == 401:
            return _unauthorized(request, exc.message)
        return JSONResponse(_error(None, -32001, exc.message), status_code=exc.status, headers=exc.headers)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    messages = body if isinstance(body, list) else [body]
    answers = [a for m in messages if (a := await _handle(request, viewer, token, m)) is not None]
    if not answers:
        return Response(status_code=202)
    return JSONResponse(answers if isinstance(body, list) else answers[0])


@router.get("/mcp", include_in_schema=False)
@router.delete("/mcp", include_in_schema=False)
async def mcp_other() -> Response:
    """No stream from the server and no sessions to end: messages go by POST."""
    return Response(status_code=405, headers={"Allow": "POST"})


@router.get("/api/mcp/tools")
async def tools(request: Request, viewer: Viewer) -> dict:
    """«Инструменты» on the MCP connector's page: those the person's AI app would get."""
    return {"items": [{"name": t.name, "group": t.group, "description": t.description.rsplit(" [", 1)[0],
                       "read_only": t.method == "GET"} for t in tools_for(request.app, viewer)]}
