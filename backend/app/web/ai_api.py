"""Settings → «Настройки OpenRouter.ai» of an owner with dev:service:plan (their own OpenRouter): linking the
account, the models, the monthly cap; and «Расход» of every owner — on the service's OpenRouter, at its prices,
without the models and tokens. See ai.py."""

import secrets
from datetime import datetime, timedelta
from urllib.parse import quote, urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from .. import ai
from ..models import AiAccount, AiUsage, Member, Project, utcnow
from ..plans import plan_of, project_plan
from .deps import DB, Principal, Viewer
from .posts import iso

router = APIRouter(prefix="/api/ai", tags=["ai"])

OAUTH = "ai_oauth"  # the PKCE verifier and state, in the session between leaving and coming back


def _owner_only(viewer: Principal) -> None:
    if not viewer.is_owner:
        raise HTTPException(status_code=403, detail="ИИ подключает и настраивает владелец проекта")


async def has_own_ai(db, user_id: int) -> bool:
    """Has the owner a project whose AI is their own OpenRouter (dev:service:plan)."""
    projects = (await db.execute(select(Project).where(Project.owner_id == user_id))).scalars()
    return any((plan := plan_of(project_plan(p))) and plan.own_ai for p in projects)


async def _own_only(db, viewer: Principal) -> None:
    _owner_only(viewer)
    if not await has_own_ai(db, viewer.user_id):
        raise HTTPException(status_code=403, detail="ИИ входит в тариф — подключать OpenRouter не нужно")


async def _account(db, user_id: int) -> AiAccount:
    account = await ai.account_of(db, user_id)
    if account is None:
        account = AiAccount(user_id=user_id)
        db.add(account)
        await db.flush()
    return account


async def _state(db, account: AiAccount) -> dict:
    return {
        "linked": ai.is_linked(account),
        "connected_at": iso(account.connected_at),
        # the page warns until accepted: the owner pays for the tokens and picks the models
        "warning_accepted": account.warning_accepted_at is not None,
        "paused": ai.PAUSED_TEXT.get(account.paused_reason) if account.paused_reason else None,
        "models": [
            {"operation": op, "title": info["title"], "model": ai.model_for(account, op), "default": info["default"]}
            for op, info in ai.OPERATIONS.items()
        ],
        "monthly_limit_usd": account.monthly_limit_usd,
        "spent_month": round(await ai.spent_this_month(db, account.user_id), 6),
    }


@router.get("")
async def get_ai(viewer: Viewer, db: DB) -> dict:
    await _own_only(db, viewer)
    account = await _account(db, viewer.user_id)
    await db.commit()
    return await _state(db, account)


class ConnectIn(BaseModel):
    origin: str  # the site's address as the browser sees it: OpenRouter sends the owner back there


@router.post("/connect")
async def connect(body: ConnectIn, request: Request, viewer: Viewer, db: DB) -> dict:
    """Where to send the owner to approve the link on openrouter.ai."""
    await _own_only(db, viewer)
    origin = urlparse(body.origin)
    # only back to this very site, not to an address someone slipped in
    if origin.scheme not in ("http", "https") or origin.netloc != request.headers.get("host"):
        raise HTTPException(status_code=422, detail="Неверный адрес сайта")
    verifier, challenge = ai.new_pkce()
    state = secrets.token_urlsafe(16)
    callback = f"{origin.scheme}://{origin.netloc}/api/ai/callback"
    request.session[OAUTH] = {"v": verifier, "s": state}
    return {"url": ai.auth_url(callback, challenge, state)}


@router.get("/callback")
async def callback(request: Request, viewer: Viewer, db: DB, code: str = "", state: str = "") -> RedirectResponse:
    """OpenRouter sends the owner back here with a code: it becomes their key."""
    await _own_only(db, viewer)
    saved = request.session.pop(OAUTH, None)
    if not code:
        return RedirectResponse("/#/openrouter?error=" + quote("Подключение отменено"), status_code=303)
    if not saved or not secrets.compare_digest(saved.get("s", ""), state):
        return RedirectResponse("/#/openrouter?error=" + quote("Ссылка подключения устарела — попробуйте ещё раз"), status_code=303)
    try:
        key = await ai.exchange_code(code, saved["v"])
    except ai.AiError as exc:
        return RedirectResponse("/#/openrouter?error=" + quote(str(exc)), status_code=303)
    account = await _account(db, viewer.user_id)
    account.key_enc = ai.encrypt_key(key)
    account.connected_at = utcnow()
    account.warning_accepted_at = None  # a new link: the warning once more
    account.paused_reason = account.paused_at = None
    ai._balance_cache.pop(viewer.user_id, None)
    await db.commit()
    return RedirectResponse("/#/openrouter?connected=1", status_code=303)


@router.delete("")
async def unlink(viewer: Viewer, db: DB) -> dict:
    """Only the key goes: models, prompts, feeds and topics stay for the next link."""
    await _own_only(db, viewer)
    account = await _account(db, viewer.user_id)
    account.key_enc = None
    account.connected_at = None
    account.paused_reason = account.paused_at = None
    ai._balance_cache.pop(viewer.user_id, None)
    await db.commit()
    return await _state(db, account)


@router.post("/accept-warning")
async def accept_warning(viewer: Viewer, db: DB) -> dict:
    """«Понятно, принимаю»: the owner read that tokens are paid by them and models are their choice."""
    await _own_only(db, viewer)
    account = await _account(db, viewer.user_id)
    account.warning_accepted_at = utcnow()
    await db.commit()
    return await _state(db, account)


class SettingsIn(BaseModel):
    models: dict[str, str | None] | None = None  # None for an operation: back to its default
    monthly_limit_usd: float | None = None
    clear_monthly_limit: bool = False


@router.put("")
async def put_ai(body: SettingsIn, viewer: Viewer, db: DB) -> dict:
    await _own_only(db, viewer)
    account = await _account(db, viewer.user_id)
    if body.models is not None:
        chosen = dict(account.models or {})
        known = {m["id"]: m for m in await ai.models()} if any(body.models.values()) else {}
        for op, model in body.models.items():
            if op not in ai.OPERATIONS:
                raise HTTPException(status_code=422, detail=f"Неизвестная операция: {op}")
            if not model or model == ai.OPERATIONS[op]["default"]:
                chosen.pop(op, None)
                continue
            if model not in known or not ai.fits(known[model], op):
                raise HTTPException(status_code=422, detail="Эта модель не подходит для этой операции")
            chosen[op] = model
        account.models = chosen or None
    if body.clear_monthly_limit:
        account.monthly_limit_usd = None
    elif body.monthly_limit_usd is not None:
        if not 0 < body.monthly_limit_usd <= 100_000:
            raise HTTPException(status_code=422, detail="Лимит — сумма в долларах больше нуля")
        account.monthly_limit_usd = round(body.monthly_limit_usd, 2)
    if account.paused_reason == "limit":
        account.paused_reason = None
    # another model may be a free one, or the balance was topped up: it tries, and pauses again if it must
    if body.models is not None and account.paused_reason == "no_credits":
        account.paused_reason = account.paused_at = None
    await db.commit()
    return await _state(db, account)


@router.get("/models")
async def list_models(viewer: Viewer, operation: str = Query(...)) -> dict:
    if not viewer.is_admin:  # the admin picks the service's models
        _owner_only(viewer)
    if operation not in ai.OPERATIONS:
        raise HTTPException(status_code=422, detail="Неизвестная операция")
    try:
        items = [m for m in await ai.models() if ai.fits(m, operation)]
    except ai.AiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    items.sort(key=lambda m: (m["prompt_price"] + m["completion_price"], m["id"]))
    return {"default": ai.OPERATIONS[operation]["default"], "items": items}


# --- «Расход и остаток» ------------------------------------------------------------------------

EPOCH = datetime(1970, 1, 1)  # times are naive UTC (models.utcnow)
PERIODS = {"24h": (timedelta(hours=24), 3600), "7d": (timedelta(days=7), 86400), "30d": (timedelta(days=30), 86400)}


@router.get("/usage")
async def usage(
    viewer: Viewer, db: DB, period: str = Query("7d", pattern="^(24h|7d|30d)$"), tz: int = Query(0, ge=-900, le=900)
) -> dict:
    """Spending over the period: bars (per hour or day of the owner's time zone, `tz` minutes east
    of UTC) split by operation, and totals by operation, project, person and model; and what
    OpenRouter says of the balance. On the service's OpenRouter: what the owner paid, at its prices — the models,
    the tokens and OpenRouter's balance are their own OpenRouter's only (dev:service:plan)."""
    _owner_only(viewer)
    own = await has_own_ai(db, viewer.user_id)
    span, step = PERIODS[period]
    now = utcnow()
    shift = timedelta(minutes=tz)
    # bars start on a whole hour or day of the owner's clock; the last one holds the present moment
    first = (int((now + shift - span - EPOCH).total_seconds()) // step + 1) * step
    since = EPOCH + timedelta(seconds=first) - shift
    rows = (await db.execute(select(AiUsage).where(AiUsage.user_id == viewer.user_id, AiUsage.at >= since))).scalars().all()

    count = int(span.total_seconds() // step)
    bars = [{"t": iso(since + timedelta(seconds=i * step)), "cost": 0.0, "by": {}} for i in range(count)]
    totals: dict[str, dict[str, dict]] = {"operation": {}, "project": {}, "person": {}, "model": {}}
    cost = prompt = completion = cached = 0
    for r in rows:
        paid = r.cost if r.charged is None else r.charged / 1_000_000
        i = min(count - 1, int((r.at - since).total_seconds() // step))
        bars[i]["cost"] += paid
        bars[i]["by"][r.operation] = bars[i]["by"].get(r.operation, 0.0) + paid
        for kind, key in (("operation", r.operation), ("project", r.project_id), ("person", r.member_id),
                          ("model", r.model if r.charged is None else None)):
            item = totals[kind].setdefault(str(key), {"key": key, "cost": 0.0, "requests": 0})
            item["cost"] += paid
            item["requests"] += 1
        cost += paid
        prompt += r.prompt_tokens
        completion += r.completion_tokens
        cached += r.cached_tokens

    projects = {p.id: p for p in (await db.execute(select(Project).where(Project.owner_id == viewer.user_id))).scalars()}
    member_ids = [int(k) for k in totals["person"] if k != "None"]
    members = {m.id: m.login for m in (await db.execute(select(Member).where(Member.id.in_(member_ids)))).scalars()}
    titles = {
        "operation": lambda k: ai.OPERATIONS.get(k, {}).get("title") or ai.RETIRED_OPERATIONS.get(k, k),
        "project": lambda k: (projects[k].channel_title or f"Проект {k}") if k in projects else "Удалённый проект",
        "person": lambda k: "Вы" if k is None else members.get(k, "Удалённый сотрудник"),
        "model": lambda k: k or "ИИ сервиса",
    }
    out_totals = {
        kind: sorted(
            ({**item, "title": titles[kind](item["key"])} for item in items.values()), key=lambda x: -x["cost"]
        )
        for kind, items in totals.items() if kind != "model" or own
    }

    account = await _account(db, viewer.user_id)
    key = ai.decrypt_key(account.key_enc) if own else None
    return {
        "period": period,
        "step": step,
        "own": own,  # their own OpenRouter: the models, tokens, its balance and the monthly cap are shown
        "bars": bars,
        "cost": round(cost, 6),
        "requests": len(rows),
        "tokens": {"prompt": prompt, "completion": completion, "cached": cached} if own else None,
        "totals": out_totals,
        "spent_month": round(await ai.spent_this_month(db, viewer.user_id), 6) if own else None,
        "monthly_limit_usd": account.monthly_limit_usd if own else None,
        "balance": await ai.balance(viewer.user_id, key) if key else None,
        "first_at": iso(await db.scalar(select(func.min(AiUsage.at)).where(AiUsage.user_id == viewer.user_id))),
    }
