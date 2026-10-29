"""«Баланс» and «Тариф» of a project owner, the plans everyone may see, and the admin's side of the money: the
service's OpenRouter, its models and markup, and opening the payments. See billing.py."""

import secrets
from urllib.parse import quote, urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import ai, billing, settings_store
from ..models import AiUsage, BalanceOp, Project, User
from ..plans import PLANS, micro, plan_json, plan_of, plans_json, project_plan, usd
from ..projects import project_title, user_projects
from .deps import DB, Admin, Principal, Viewer
from .posts import iso

router = APIRouter(prefix="/api/billing", tags=["billing"])
public = APIRouter(prefix="/api/plans", tags=["billing"])


def _owner_only(viewer: Principal) -> None:
    if not viewer.is_owner:
        raise HTTPException(status_code=403, detail="Баланс и тариф видит только владелец проекта")


@public.get("")
async def all_plans() -> dict:
    """The plans page, before signing in too: what each plan gives and costs."""
    return {"items": plans_json(), "trial_days": billing.PERIOD.days}


async def _state(db, viewer: Principal) -> dict:
    project = await db.get(Project, viewer.project_id)
    user = await db.get(User, viewer.user_id)
    active = project_plan(project)
    plan = plan_of(active)
    nxt = plan_of(project.next_plan) if project.next_plan else None
    paused = billing.Paused()
    if plan:
        paused = await billing.paused_counts(db, project)
    return {
        "balance": usd(user.balance),
        "topup": None,  # how to top up, once the payments are open; None: «Пополнение временно недоступно»
        "payments_live": await billing.payments_live(db),
        "plan": plan_json(active, project.plan_expires_at) if active else None,
        # a plan that ran out: the project is frozen till one is bought
        "expired_plan": plan_json(project.plan, project.plan_expires_at) if project.plan and not active else None,
        "delete_at": iso(billing.delete_at(project)),
        "started_at": iso(project.plan_started_at) if active else None,
        "auto_renew": project.auto_renew,
        "next_plan": plan_json(nxt.id) if nxt else None,
        "renew_price": (nxt or plan).price if (nxt or plan) and (nxt or plan).buyable else None,
        "ai": billing.ai_json(project),
        "paused": {"channels": paused.channels, "members": paused.members, "triggers": paused.triggers},
        "plans": plans_json(also=active or None),
        "project": project_title(project, [p.id for p in await user_projects(db, user.id)].index(project.id)),
    }


@router.get("")
async def get_billing(viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    out = await _state(db, viewer)
    await db.commit()  # a new AI month counted from zero
    return out


class BuyIn(BaseModel):
    plan: str = Field(max_length=32)


@router.post("/buy")
async def buy(body: BuyIn, viewer: Viewer, db: DB) -> dict:
    """The owner buys a plan for the current project from their balance."""
    _owner_only(viewer)
    project = await db.get(Project, viewer.project_id, with_for_update=True)
    try:
        done = await billing.buy(db, project, body.plan)
    except billing.BillingError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    await db.commit()
    return {"done": done, **await _state(db, viewer)}


class SettingsIn(BaseModel):
    auto_renew: bool | None = None
    ai_extra: bool | None = None
    ai_extra_cap: float | None = Field(default=None, ge=0, le=100_000)  # USD a month
    clear_ai_extra_cap: bool = False


@router.patch("")
async def patch_billing(body: SettingsIn, viewer: Viewer, db: DB) -> dict:
    """«Автопродление», «Докупать ИИ с баланса» and its monthly cap."""
    _owner_only(viewer)
    project = await db.get(Project, viewer.project_id)
    if body.auto_renew is not None:
        project.auto_renew = body.auto_renew
    if body.ai_extra is not None:
        project.ai_extra = body.ai_extra
    if body.clear_ai_extra_cap:
        project.ai_extra_cap = None
    elif body.ai_extra_cap is not None:
        project.ai_extra_cap = micro(body.ai_extra_cap)
    await db.commit()
    return await _state(db, viewer)


@router.post("/topup")
async def topup(viewer: Viewer) -> dict:
    _owner_only(viewer)
    raise HTTPException(status_code=503, detail=billing.TOPUP_CLOSED)


KINDS = {"topup": "Пополнение", "plan": "Оплата тарифа", "refund": "Возврат", "ai": "ИИ сверх тарифа",
         "admin": "Начисление"}


@router.get("/history")
async def history(viewer: Viewer, db: DB, before: int | None = None, limit: int = Query(50, ge=1, le=200)) -> dict:
    """«История операций», newest first; `before`: the id of the last one shown."""
    _owner_only(viewer)
    stmt = select(BalanceOp).where(BalanceOp.user_id == viewer.user_id)
    if before:
        stmt = stmt.where(BalanceOp.id < before)
    rows = (await db.execute(stmt.order_by(BalanceOp.id.desc()).limit(limit + 1))).scalars().all()
    projects = {p.id: project_title(p, i) for i, p in enumerate(await user_projects(db, viewer.user_id))}
    return {
        "items": [{
            "id": op.id, "kind": op.kind,
            "title": op.note if op.kind in ("plan", "refund") and op.note else
            ("Списание" if op.kind == "admin" and op.amount < 0 else KINDS.get(op.kind, op.kind)),
            "note": op.note if op.kind == "admin" else None,
            "amount": usd(op.amount), "project": projects.get(op.project_id) if op.project_id else None,
            # an AI row is a day's requests, added up as they go: its time is the latest of them
            "at": iso(op.at), "day": op.day,
        } for op in rows[:limit]],
        "more": len(rows) > limit,
    }


# --- the admin ------------------------------------------------------------------------------------------------

OAUTH = "ai_shared_oauth"


async def _admin_state(db) -> dict:
    cfg = await settings_store.get_all(db)
    key = ai.decrypt_key(str(cfg["ai_key"]))
    month = ai.month_start()
    cost, charged = (await db.execute(select(func.coalesce(func.sum(AiUsage.cost), 0.0),
                                             func.coalesce(func.sum(AiUsage.charged), 0))
                                      .where(AiUsage.charged.is_not(None), AiUsage.at >= month))).one()
    rows = (await db.execute(
        select(AiUsage.user_id, func.sum(AiUsage.cost), func.sum(AiUsage.charged), func.count())
        .where(AiUsage.charged.is_not(None), AiUsage.at >= month).group_by(AiUsage.user_id)
        .order_by(func.sum(AiUsage.charged).desc()).limit(50))).all()
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_([r[0] for r in rows])))).scalars()}
    plans_sold = (await db.execute(select(func.coalesce(func.sum(-BalanceOp.amount), 0)).where(
        BalanceOp.kind == "plan", BalanceOp.at >= month))).scalar()
    return {
        "linked": bool(key),
        "paused": ai.SHARED_DOWN if cfg["ai_paused"] else None,
        "paused_reason": cfg["ai_paused"] or None,
        "balance": await ai.balance(ai.SHARED, key) if key else None,
        "models": [{"operation": op, "title": info["title"], "default": info["shared"],
                    "model": (cfg["ai_models"] or {}).get(op) or info["shared"]} for op, info in ai.OPERATIONS.items()],
        "markup_pct": int(cfg["ai_markup_pct"]),
        "payments_live": bool(cfg["payments_live"]),
        "trials_waiting": await db.scalar(select(func.count()).where(Project.plan == "trial",
                                                                      Project.plan_expires_at.is_(None))),
        # this month: what the AI cost the service, what the owners paid for it, and the plans bought
        "month": {"cost": round(float(cost), 6), "charged": usd(int(charged)), "plans": usd(int(plans_sold))},
        "owners": [{
            "user_id": uid, "name": (" ".join(x for x in (users[uid].first_name, users[uid].last_name) if x)
                                     or users[uid].username) if uid in users else None,
            "username": users[uid].username if uid in users else None,
            "cost": round(float(c or 0), 6), "charged": usd(int(ch or 0)), "requests": n,
        } for uid, c, ch, n in rows],
    }


@router.get("/admin")
async def admin_state(_: Admin, db: DB) -> dict:
    return await _admin_state(db)


class AdminIn(BaseModel):
    markup_pct: int | None = Field(default=None, ge=100, le=1000)
    models: dict[str, str | None] | None = None  # None for an operation: back to its default


@router.patch("/admin")
async def admin_patch(body: AdminIn, _: Admin, db: DB) -> dict:
    values: dict = {}
    if body.markup_pct is not None:
        values["ai_markup_pct"] = body.markup_pct
    if body.models is not None:
        cfg = await settings_store.get_all(db)
        chosen = dict(cfg["ai_models"] or {})
        known = {m["id"]: m for m in await ai.models()} if any(body.models.values()) else {}
        for op, model in body.models.items():
            if op not in ai.OPERATIONS:
                raise HTTPException(status_code=422, detail=f"Неизвестная операция: {op}")
            if not model or model == ai.OPERATIONS[op]["shared"]:
                chosen.pop(op, None)
                continue
            if model not in known or not ai.fits(known[model], op):
                raise HTTPException(status_code=422, detail="Эта модель не подходит для этой операции")
            chosen[op] = model
        values["ai_models"] = chosen
    if values:
        await settings_store.update(db, values)
    return await _admin_state(db)


class ConnectIn(BaseModel):
    origin: str


@router.post("/admin/connect")
async def admin_connect(body: ConnectIn, request: Request, _: Admin) -> dict:
    """Where to send the admin to link the service's OpenRouter on openrouter.ai."""
    origin = urlparse(body.origin)
    if origin.scheme not in ("http", "https") or origin.netloc != request.headers.get("host"):
        raise HTTPException(status_code=422, detail="Неверный адрес сайта")
    verifier, challenge = ai.new_pkce()
    state = secrets.token_urlsafe(16)
    request.session[OAUTH] = {"v": verifier, "s": state}
    return {"url": ai.auth_url(f"{origin.scheme}://{origin.netloc}/api/billing/admin/callback", challenge, state)}


@router.get("/admin/callback")
async def admin_callback(request: Request, _: Admin, db: DB, code: str = "", state: str = "") -> RedirectResponse:
    saved = request.session.pop(OAUTH, None)
    back = "/#/service-ai"
    if not code:
        return RedirectResponse(back + "?error=" + quote("Подключение отменено"), status_code=303)
    if not saved or not secrets.compare_digest(saved.get("s", ""), state):
        return RedirectResponse(back + "?error=" + quote("Ссылка подключения устарела — попробуйте ещё раз"),
                                status_code=303)
    try:
        key = await ai.exchange_code(code, saved["v"])
    except ai.AiError as exc:
        return RedirectResponse(back + "?error=" + quote(str(exc)), status_code=303)
    await settings_store.update(db, {"ai_key": ai.encrypt_key(key), "ai_paused": ""})
    ai._balance_cache.pop(ai.SHARED, None)
    return RedirectResponse(back + "?connected=1", status_code=303)


@router.delete("/admin/key")
async def admin_unlink(_: Admin, db: DB) -> dict:
    """The service's OpenRouter unlinked: the AI of every plan but dev:service:plan stands till it is linked again."""
    await settings_store.update(db, {"ai_key": "", "ai_paused": ""})
    ai._balance_cache.pop(ai.SHARED, None)
    return await _admin_state(db)


@router.post("/admin/live")
async def admin_live(_: Admin, db: DB) -> dict:
    """«Оплата запущена»: every trial waiting for it gets its 30 days from now, and frozen projects get deleted
    after their days. Once: it is not undone."""
    if await billing.payments_live(db):
        raise HTTPException(status_code=409, detail="Оплата уже запущена")
    started = await billing.go_live(db)
    await db.commit()
    return {"trials_started": started, **await _admin_state(db)}


class MoneyIn(BaseModel):
    amount: float = Field(ge=-100_000, le=100_000)  # USD: above zero onto the balance, below off it
    note: str = Field(min_length=1, max_length=200)


@router.post("/admin/users/{user_id}/balance")
async def admin_money(user_id: int, body: MoneyIn, _: Admin, db: DB) -> dict:
    """The admin adds money to an owner's balance by hand (or takes it), with a note they see in their history."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if not body.amount:
        raise HTTPException(status_code=422, detail="Сумма — не ноль")
    await billing.move(db, user.id, micro(body.amount), "admin", note=body.note.strip())
    await db.commit()
    await db.refresh(user)
    return {"balance": usd(user.balance)}


@router.get("/admin/plans")
async def admin_plans(_: Admin) -> dict:
    """Every plan, dev:service:plan too: what the admin may give."""
    return {"items": [{**plan_json(p.id), "forever": p.forever, "by_promo": p.by_promo}
                      for p in sorted(PLANS.values(), key=lambda p: p.rank)]}
