"""OpenRouter: the AI of a project (smart feed, topics, chat and posts).

Every plan but dev:service:plan uses the service's OpenRouter, linked by the admin, and pays for it at the
service's prices from the plan's AI and the owner's balance (billing.py). dev:service:plan uses the owner's own
OpenRouter, as everyone once did: they link it once, for all their projects, by OAuth with PKCE — we send them to
openrouter.ai/auth with a code challenge, they approve, OpenRouter sends them back with a code, and we trade the
code (with the verifier) for an API key of theirs; their staff use it too. The admin links the service's the same
way. `engine` tells which goes for a project, and whether it may go now.

Each request is written down in AiUsage with its exact cost (OpenRouter puts it in every response), and with the
service's: what the owner paid for it.
"""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select, update

from . import billing, settings_store
from .config import settings, site_url
from .db import SessionLocal
from .models import AiAccount, AiUsage, Project, utcnow
from .plans import plan_of, project_plan

log = logging.getLogger(__name__)

AUTH_URL = "https://openrouter.ai/auth"
API = "https://openrouter.ai/api/v1"
TITLE = "Feedor"  # how our requests are signed in the owner's OpenRouter activity
TIMEOUT = httpx.Timeout(120, connect=15)
# a request's whole time at most: OpenRouter keeps a slow model's connection alive with blank lines, so the
# read timeout alone never ends it (a free model was seen thinking 20 minutes)
REQUEST_LIMIT = 15 * 60
RATE_WAIT = 60  # seconds a model is let be after OpenRouter's "too many requests"
FREE_DAY_WAIT = 600  # and after "the free models' daily limit": it comes back at midnight, or once $10 are paid in

# What the AI is used for, the model each uses unless the owner picks another (`shared`: on the service's
# OpenRouter, unless the admin picks another), and what a model
# must be able to do for it: answer by a JSON schema, call our tools (the chat).
OPERATIONS: dict[str, dict] = {
    "filter": {"title": "Фильтрация в умной ленте", "default": "nvidia/nemotron-3.5-super-vl:free",
               "shared": "deepseek/deepseek-v4.1-flash", "needs": ["structured_outputs"]},
    "topics": {"title": "Генерация тем", "default": "nvidia/nemotron-3.5-super-vl:free",
               "shared": "deepseek/deepseek-v4.1-flash", "needs": ["structured_outputs"]},
    # the chat, and the posts the AI writes: in a chat and by topics («Публикации»)
    "chat": {"title": "Общение с ИИ и посты", "default": "nvidia/nemotron-3.5-super-vl:free",
             "shared": "deepseek/deepseek-v4.1-flash", "needs": ["tools"]},
}


# what was spent on before and is no longer done: its name in «Расход и остаток»
RETIRED_OPERATIONS = {"vision": "AI-распознавание фото", "advice": "Анализ и советы для канала"}


class AiError(Exception):
    """A request could not be made; the message is for people, `detail` is what OpenRouter said."""

    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message)
        self.detail = detail or message


class AiPaused(AiError):
    """The owner's AI stands still: no key, no credits, or our monthly cap reached."""


class AiRateLimited(AiError):
    """OpenRouter asks to wait (too many requests, or the free models' daily limit): try again later."""


# (owner, model) -> (until when, monotonic; the error): OpenRouter asked to wait, so the background work sends it
# nothing till then — the smart feed would ask every few seconds and the topics every minute, for nothing. The
# chat, asked by a person, still tries
_cooldown: dict[tuple[int, str], tuple[float, AiRateLimited]] = {}


def _wait_if_asked(user_id: int, model: str) -> None:
    until, error = _cooldown.get((user_id, model), (0.0, None))
    if error is not None and time.monotonic() < until:
        raise error
    _cooldown.pop((user_id, model), None)


def _asked_to_wait(user_id: int, model: str, error: AiError) -> AiError:
    if isinstance(error, AiRateLimited):
        wait = FREE_DAY_WAIT if "free-models-per-day" in (error.detail or "") else RATE_WAIT
        _cooldown[(user_id, model)] = (time.monotonic() + wait, error)
    return error


LOGGED_ANSWER = 3000  # characters of OpenRouter's answer to an error kept in the log


def _log_refusal(operation: str, user_id: int, model: str, status: int, answer: str) -> None:
    """OpenRouter's whole answer to an error: people are told it in short (openrouter_error), the log keeps what the
    model's provider said — its own code and words are in the answer's metadata."""
    log.warning("ai %s for user %s: %s answered HTTP %s: %s", operation, user_id, model, status,
                answer[:LOGGED_ANSWER])


def openrouter_error(status: int, message: str | None) -> AiError:
    """What OpenRouter answered, said for people; its own words stay in `detail`."""
    raw = f"OpenRouter: {message or f'HTTP {status}'}"
    text = (message or "").lower()
    if "free-models-per-day" in text:
        return AiRateLimited(
            "Закончился дневной лимит запросов к бесплатным моделям OpenRouter. Он обновится завтра. Чтобы "
            "получить 1000 бесплатных запросов в день, пополните баланс OpenRouter на $10 — или выберите "
            "платную модель в настройках OpenRouter.ai.", raw)
    if status == 429 or "rate limit" in text or "rate-limit" in text:
        return AiRateLimited(
            "OpenRouter временно ограничил запросы к этой модели — попробуйте через минуту или выберите "
            "другую модель в настройках OpenRouter.ai.", raw)
    if status in (502, 503, 504) or "overloaded" in text or "provider returned error" in text:
        return AiError("Модель сейчас не отвечает: её провайдер перегружен или недоступен. Попробуйте ещё раз "
                       "чуть позже или выберите другую модель.", raw)
    return AiError("OpenRouter вернул ошибку.", raw)


# --- the key, kept encrypted ------------------------------------------------------------


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"openrouter:{settings.secret_key}".encode()).digest()))


def encrypt_key(key: str) -> str:
    return _fernet().encrypt(key.encode()).decode()


def decrypt_key(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:  # SECRET_KEY changed: the key is lost, the owner links again
        return None


# --- OAuth with PKCE ----------------------------------------------------------------------


def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def auth_url(callback_url: str, challenge: str, state: str) -> str:
    return f"{AUTH_URL}?" + urlencode({
        "callback_url": callback_url, "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state, "key_label": TITLE,
    })


async def exchange_code(code: str, verifier: str) -> str:
    """The owner's API key for the code OpenRouter sent them back with."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{API}/auth/keys", json={
                "code": code, "code_verifier": verifier, "code_challenge_method": "S256",
            })
    except httpx.HTTPError as exc:
        raise AiError(f"Нет связи с OpenRouter: {exc}") from exc
    if r.status_code != 200 or not (key := (r.json() or {}).get("key")):
        log.warning("openrouter code exchange failed: %s %s", r.status_code, r.text[:300])
        raise AiError("OpenRouter не подтвердил подключение — попробуйте ещё раз")
    return key


# --- the owner's account -------------------------------------------------------------------


def model_for(account: AiAccount, operation: str) -> str:
    return (account.models or {}).get(operation) or OPERATIONS[operation]["default"]


async def account_of(session, user_id: int) -> AiAccount | None:
    return await session.get(AiAccount, user_id)


def is_linked(account: AiAccount | None) -> bool:
    return bool(account and account.key_enc)


def month_start(now: datetime | None = None) -> datetime:
    now = now or utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def spent_this_month(session, user_id: int) -> float:
    """What the owner's own OpenRouter paid this month (not the service's)."""
    return float(await session.scalar(
        select(func.coalesce(func.sum(AiUsage.cost), 0.0)).where(
            AiUsage.user_id == user_id, AiUsage.at >= month_start(), AiUsage.charged.is_(None))
    ) or 0.0)


# what the owner paid for a request, USD: the service's price on its OpenRouter, the cost on their own
PAID = func.coalesce(AiUsage.charged / 1000000.0, AiUsage.cost)

SHARED = 0  # the service's OpenRouter where an owner's id would be (the cooldown, the balance's cache)
SHARED_DOWN = "ИИ временно недоступен — попробуйте немного позже"


@dataclass
class Engine:
    """How a project's AI goes: with the owner's own OpenRouter (dev:service:plan) or the service's."""

    own: bool
    key: str | None
    models: dict
    stop: str | None  # why it cannot go now; None: it can
    owner_id: int
    pct: int = 100  # the service's markup, in percent

    @property
    def who(self) -> int:
        return self.owner_id if self.own else SHARED

    @property
    def linked(self) -> bool:
        return bool(self.key)

    def model(self, operation: str) -> str:
        info = OPERATIONS[operation]
        return self.models.get(operation) or (info["default"] if self.own else info["shared"])

    def paused_text(self, reason: str) -> str:
        return PAUSED_TEXT.get(reason, reason) if self.own else SHARED_DOWN


async def engine(session, project_id: int | None, owner_id: int | None = None) -> Engine:
    """The project's AI: whose OpenRouter, which models, and why it may not go now."""
    project = await session.get(Project, project_id) if project_id else None
    owner_id = project.owner_id if project else owner_id
    plan = plan_of(project_plan(project)) if project else None
    if project is None or (plan is not None and plan.own_ai):
        account = await account_of(session, owner_id)
        key = decrypt_key(account.key_enc) if account else None
        stop = None
        if not key:
            stop = PAUSED_TEXT["unlinked"]
        elif account.paused_reason:
            stop = PAUSED_TEXT.get(account.paused_reason, account.paused_reason)
        elif account.monthly_limit_usd is not None and \
                await spent_this_month(session, owner_id) >= account.monthly_limit_usd:
            stop = PAUSED_TEXT["limit"]
        return Engine(True, key, dict(account.models or {}) if account else {}, stop, owner_id)
    cfg = await settings_store.get_all(session)
    key = decrypt_key(str(cfg["ai_key"]))
    if plan is None:
        stop = billing.NO_PLAN_AI
    elif not key or cfg["ai_paused"]:
        stop = SHARED_DOWN
    else:
        stop = await billing.ai_stop(session, project)
    return Engine(False, key, dict(cfg["ai_models"] or {}), stop, owner_id, int(cfg["ai_markup_pct"]))


async def runs(session, project_id: int) -> bool:
    """May the project's AI work in the background now (the smart feed, topics): a plan, a key, money."""
    return (await engine(session, project_id)).stop is None


LOW_SHARED = 5.0  # USD left on the service's OpenRouter: the admin is told
_low_told = False


async def watch_shared() -> None:
    """The service's OpenRouter running low: the admin is told once, and again once it was topped up and ran low."""
    global _low_told
    async with SessionLocal() as s:
        key = decrypt_key(str((await settings_store.get_all(s))["ai_key"]))
    if not key:
        return
    left = (await balance(SHARED, key)).get("credits_left")
    if left is None:
        return
    if left >= LOW_SHARED:
        _low_told = False
    elif not _low_told:
        _low_told = True
        from .notify import admin_alert

        await admin_alert(f"На балансе OpenRouter сервиса осталось ${left:.2f} — пополните его, чтобы ИИ "
                          "пользователей не остановился.")


async def _pause(eng: Engine, reason: str) -> None:
    async with SessionLocal() as s:
        if eng.own:
            await s.execute(update(AiAccount).where(AiAccount.user_id == eng.owner_id,
                                                    AiAccount.paused_reason.is_(None))
                            .values(paused_reason=reason, paused_at=utcnow()))
            await s.commit()
            return
        if (await settings_store.get_all(s))["ai_paused"]:
            return
        await settings_store.update(s, {"ai_paused": reason})
    log.error("the service's OpenRouter stands still: %s", reason)
    from .notify import admin_alert

    await admin_alert("ИИ сервиса остановлен: " + (
        "на балансе OpenRouter закончились средства — пополните его, и ИИ продолжит работу сам"
        if reason == "no_credits" else "OpenRouter не принимает ключ — подключите его заново в настройках ИИ"))


PAUSED_TEXT = {
    "no_credits": "На балансе OpenRouter закончились средства — пополните его, и ИИ продолжит работу",
    "invalid_key": "OpenRouter не принимает ключ — подключите аккаунт заново в настройках",
    "limit": "Достигнут месячный лимит расходов на ИИ — его можно поднять в настройках OpenRouter.ai",
    "unlinked": "OpenRouter.ai не подключён",
}


async def complete(
    *,
    user_id: int,
    operation: str,
    messages: list[dict],
    project_id: int | None = None,
    member_id: int | None = None,
    response_format: dict | None = None,
    tools: list[dict] | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
) -> dict:
    """One chat completion with the project's OpenRouter, written down with its cost. The raw response."""
    async with SessionLocal() as s:
        eng = await engine(s, project_id, user_id)
    if eng.stop:
        raise AiPaused(eng.stop)
    key = eng.key
    model = model if model and eng.own else eng.model(operation)
    _wait_if_asked(eng.who, model)

    body: dict = {"model": model, "messages": messages}
    if response_format:
        body["response_format"] = response_format
    if tools:
        body["tools"] = tools
    if max_tokens:
        body["max_tokens"] = max_tokens
    started = time.monotonic()

    async def post() -> httpx.Response:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport) as client:
            return await client.post(f"{API}/chat/completions", json=body, headers=_headers(key))

    try:
        r = await asyncio.wait_for(post(), REQUEST_LIMIT)
    except httpx.HTTPError as exc:
        raise AiError(f"Нет связи с OpenRouter: {exc}") from exc
    except TimeoutError as exc:
        raise AiError(f"Модель не ответила за {REQUEST_LIMIT // 60} минут — попробуйте ещё раз или выберите "
                      "модель побыстрее.", f"no answer in {REQUEST_LIMIT} s") from exc
    if r.status_code == 402:
        await _pause(eng, "no_credits")
        raise AiPaused(eng.paused_text("no_credits"))
    if r.status_code == 401:
        await _pause(eng, "invalid_key")
        raise AiPaused(eng.paused_text("invalid_key"))
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200 or "error" in data:
        _log_refusal(operation, user_id, model, r.status_code, r.text)
        err = data.get("error") if isinstance(data.get("error"), dict) else {}
        # an error in an answer of HTTP 200 has its own code (429: the model's provider asks to wait)
        code = err.get("code") if r.status_code == 200 and isinstance(err.get("code"), int) else r.status_code
        raise _asked_to_wait(eng.who, model, openrouter_error(code, err.get("message")))

    usage = data.get("usage") or {}
    await _record(eng, project_id, member_id, operation, data.get("model") or model, usage, data.get("id"))
    log.info("ai %s for user %s: %s, %s tokens, $%.5f, %.1fs", operation, user_id, model,
             usage.get("total_tokens"), float(usage.get("cost") or 0), time.monotonic() - started)
    return data


async def stream(
    *,
    user_id: int,
    operation: str,
    messages: list[dict],
    model: str,
    project_id: int | None = None,
    member_id: int | None = None,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
):
    """A chat completion as it is written: yields {"content": piece} while the text comes, then
    {"message": {"content", "tool_calls"}, "usage": …} once it is done; written down with its cost. `model`: the
    thread's, on the owner's own OpenRouter; on the service's, the one the admin chose."""
    async with SessionLocal() as s:
        eng = await engine(s, project_id, user_id)
    if eng.stop:
        raise AiPaused(eng.stop)
    key = eng.key
    model = model if model and eng.own else eng.model(operation)
    body: dict = {"model": model, "messages": messages, "stream": True}
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
    content: list[str] = []
    calls: dict[int, dict] = {}
    usage: dict = {}
    generation = None
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport) as client:
            async with client.stream("POST", f"{API}/chat/completions", json=body, headers=_headers(key)) as r:
                if r.status_code != 200:
                    text = (await r.aread()).decode("utf-8", "replace")
                    if r.status_code == 402:
                        await _pause(eng, "no_credits")
                        raise AiPaused(eng.paused_text("no_credits"))
                    if r.status_code == 401:
                        await _pause(eng, "invalid_key")
                        raise AiPaused(eng.paused_text("invalid_key"))
                    _log_refusal(operation, user_id, model, r.status_code, text)
                    try:
                        message = json.loads(text).get("error", {}).get("message")
                    except (ValueError, AttributeError):
                        message = None
                    raise _asked_to_wait(eng.who, model, openrouter_error(r.status_code, message))
                async for line in r.aiter_lines():
                    if time.monotonic() - started > REQUEST_LIMIT:
                        # the keep-alives below keep a hung answer open forever, and the chat with it
                        raise AiError(f"Модель не ответила за {REQUEST_LIMIT // 60} минут — попробуйте ещё раз "
                                      "или выберите модель побыстрее.", f"no answer in {REQUEST_LIMIT} s")
                    if not line.startswith("data: "):
                        continue  # ": OPENROUTER PROCESSING" keep-alives
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if err := chunk.get("error"):
                        _log_refusal(operation, user_id, model, r.status_code, data)
                        code = err.get("code") if isinstance(err, dict) else None
                        raise _asked_to_wait(eng.who, model, openrouter_error(
                            code if isinstance(code, int) else 500,
                            err.get("message") if isinstance(err, dict) else str(err)))
                    generation = chunk.get("id") or generation
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if piece := delta.get("content"):
                            content.append(piece)
                            yield {"content": piece}
                        for tc in delta.get("tool_calls") or []:
                            call = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            call["id"] = tc.get("id") or call["id"]
                            fn = tc.get("function") or {}
                            call["name"] += fn.get("name") or ""
                            call["arguments"] += fn.get("arguments") or ""
    except httpx.HTTPError as exc:
        raise AiError(f"Нет связи с OpenRouter: {exc}") from exc
    finally:
        if usage:
            await _record(eng, project_id, member_id, operation, model, usage, generation)
            log.info("ai %s for user %s: %s, %s tokens, $%.5f, %.1fs (stream)", operation, user_id, model,
                     usage.get("total_tokens"), float(usage.get("cost") or 0), time.monotonic() - started)
    tool_calls = [
        {"id": c["id"] or f"call_{i}", "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
        for i, c in sorted(calls.items()) if c["name"]
    ]
    yield {"message": {"content": "".join(content), "tool_calls": tool_calls}, "usage": usage}


async def _record(eng: Engine, project_id, member_id, operation, model, usage: dict, generation: str | None) -> None:
    """The request written down; on the service's OpenRouter, paid for (billing.charge_ai)."""
    cost = float(usage.get("cost") or 0.0)
    async with SessionLocal() as s:
        charged = None if eng.own else (await billing.charge_ai(s, project_id, cost, eng.pct) if project_id else 0)
        s.add(AiUsage(
            user_id=eng.owner_id, project_id=project_id, member_id=member_id, operation=operation, model=model,
            charged=charged,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
            cost=float(usage.get("cost") or 0.0),
            generation_id=generation,
        ))
        await s.commit()


async def resume_paid() -> None:
    """«Закончились средства»: once the owner (or the admin, the service's) tops the OpenRouter balance up, the AI
    goes on by itself."""
    async with SessionLocal() as s:
        cfg = await settings_store.get_all(s)
        if cfg["ai_paused"] == "no_credits" and (shared_key := decrypt_key(str(cfg["ai_key"]))):
            _balance_cache.pop(SHARED, None)
            left = (await balance(SHARED, shared_key)).get("credits_left")
            if left is not None and left > 0:
                await settings_store.update(s, {"ai_paused": ""})
                log.info("the service's AI goes on: OpenRouter balance topped up ($%s)", left)
        accounts = (await s.execute(select(AiAccount).where(AiAccount.paused_reason == "no_credits",
                                                           AiAccount.key_enc.is_not(None)))).scalars().all()
    for account in accounts:
        key = decrypt_key(account.key_enc)
        if not key:
            continue
        _balance_cache.pop(account.user_id, None)
        money = await balance(account.user_id, key)
        left, key_left = money.get("credits_left"), money.get("key_limit_left")
        if left is None or left <= 0 or (key_left is not None and key_left <= 0):
            continue  # still nothing to pay with (or OpenRouter does not say): asked again later
        async with SessionLocal() as s:
            await s.execute(update(AiAccount).where(AiAccount.user_id == account.user_id,
                                                    AiAccount.paused_reason == "no_credits")
                            .values(paused_reason=None, paused_at=None))
            await s.commit()
        log.info("ai of user %s goes on: OpenRouter balance topped up ($%s)", account.user_id, left)


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "X-Title": TITLE, "HTTP-Referer": site_url()}


# tests put an httpx.MockTransport here
_transport: httpx.AsyncBaseTransport | None = None


async def _get(key: str, path: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=20, transport=_transport) as client:
            r = await client.get(f"{API}{path}", headers=_headers(key))
        return r.json().get("data") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


_balance_cache: dict[int, tuple[float, dict]] = {}


async def balance(user_id: int, key: str) -> dict:
    """What OpenRouter says about the account and the key: credits bought and used, the key's own
    limit and spending. Cached for a minute. Fields are None when OpenRouter does not tell."""
    at, cached = _balance_cache.get(user_id, (0.0, {}))
    if time.monotonic() - at < 60 and cached:
        return cached
    credits, key_info = await _get(key, "/credits"), await _get(key, "/key")
    total = credits.get("total_credits") if credits else None
    used = credits.get("total_usage") if credits else None
    out = {
        "credits_total": total,
        "credits_used": used,
        "credits_left": round(total - used, 6) if total is not None and used is not None else None,
        "key_limit": key_info.get("limit") if key_info else None,
        "key_limit_left": key_info.get("limit_remaining") if key_info else None,
        "key_usage": key_info.get("usage") if key_info else None,
        "key_usage_daily": key_info.get("usage_daily") if key_info else None,
        "key_usage_weekly": key_info.get("usage_weekly") if key_info else None,
        "key_usage_monthly": key_info.get("usage_monthly") if key_info else None,
        "label": key_info.get("label") if key_info else None,
    }
    _balance_cache[user_id] = (time.monotonic(), out)
    return out


# --- the models there are -------------------------------------------------------------------

_models_cache: tuple[float, list[dict]] = (0.0, [])


async def models() -> list[dict]:
    """OpenRouter's models (public list), an hour in the cache."""
    global _models_cache
    at, items = _models_cache
    if items and time.monotonic() - at < 3600:
        return items
    try:
        async with httpx.AsyncClient(timeout=30, transport=_transport) as client:
            r = await client.get(f"{API}/models")
        raw = r.json()["data"]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        if items:
            return items
        raise AiError(f"Не удалось загрузить список моделей OpenRouter: {exc}") from exc
    items = []
    for m in raw:
        pricing = m.get("pricing") or {}
        try:
            prompt, completion = float(pricing.get("prompt") or 0), float(pricing.get("completion") or 0)
        except ValueError:
            continue
        if prompt < 0 or completion < 0:  # "-1": priced by the router itself, not per token
            continue
        params = set(m.get("supported_parameters") or [])
        inputs = set((m.get("architecture") or {}).get("input_modalities") or [])
        outputs = set((m.get("architecture") or {}).get("output_modalities") or ["text"])
        if "text" not in outputs:
            continue
        caps = {c for c in ("structured_outputs", "tools") if c in params} | ({"image"} if "image" in inputs else set())
        items.append({
            "id": m["id"],
            "name": m.get("name") or m["id"],
            "context": m.get("context_length"),
            # USD per million tokens
            "prompt_price": round(prompt * 1e6, 4),
            "completion_price": round(completion * 1e6, 4),
            "caps": sorted(caps),
        })
    _models_cache = (time.monotonic(), items)
    return items


def fits(model: dict, operation: str) -> bool:
    return all(c in model["caps"] for c in OPERATIONS[operation]["needs"])
