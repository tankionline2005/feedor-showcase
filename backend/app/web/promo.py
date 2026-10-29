"""Promo codes: the admin generates one-time codes for a plan and a number of days (or forever),
a user enters one in the settings, for the project they work in.

A code never makes things worse: a worse plan than the project's is refused, the same plan adds its
days to what is left, a better one replaces the plan at once with the code's term. «Free Trial» and
dev:service:plan are never given by a code.
"""

import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .. import billing
from ..models import Project, PromoCode, User, utcnow
from ..plans import PLANS, plan_json, plan_of, project_plan
from .deps import DB, Admin, Viewer
from .posts import iso

router = APIRouter(prefix="/api/promo", tags=["promo"])

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: easy to type from a screenshot
INVALID = "Промокод недействителен"
TRIES_LIMIT, TRIES_WINDOW = 10, 600  # wrong codes per user, seconds

_tries: dict[int, deque[float]] = defaultdict(deque)


def new_code() -> str:
    part = lambda: "".join(secrets.choice(ALPHABET) for _ in range(4))  # noqa: E731
    return f"FEED-{part()}-{part()}"


def normalize(code: str) -> str:
    return code.strip().upper().replace(" ", "")


def code_json(p: PromoCode, users: dict[int, User] | None = None) -> dict:
    user = (users or {}).get(p.used_by) if p.used_by else None
    return {
        "code": p.code,
        "plan": plan_json(p.plan),
        "days": p.days,
        "created_at": iso(p.created_at),
        "used_at": iso(p.used_at),
        "used_by": (
            {"id": user.id, "name": " ".join(x for x in (user.first_name, user.last_name) if x) or None,
             "username": user.username}
            if user else None
        ),
    }


# --- the admin ------------------------------------------------------------------


@router.get("")
async def list_codes(_: Admin, db: DB) -> dict:
    codes = (await db.execute(select(PromoCode).order_by(PromoCode.created_at.desc(), PromoCode.code))).scalars().all()
    ids = {p.used_by for p in codes if p.used_by}
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}
    return {"items": [code_json(p, users) for p in codes],
            "plans": [plan_json(p.id) for p in sorted(PLANS.values(), key=lambda p: p.rank) if p.by_promo]}


class GenerateIn(BaseModel):
    plan: str
    days: int | None = Field(default=None, ge=1, le=3650)  # None: forever
    count: int = Field(default=1, ge=1, le=100)


@router.post("")
async def generate(body: GenerateIn, _: Admin, db: DB) -> dict:
    if plan_of(body.plan) is None or not plan_of(body.plan).by_promo:
        raise HTTPException(status_code=422, detail="Этот тариф нельзя выдать промокодом")
    taken = set((await db.execute(select(PromoCode.code))).scalars())
    created = []
    while len(created) < body.count:
        code = new_code()
        if code in taken:
            continue
        taken.add(code)
        created.append(PromoCode(code=code, plan=body.plan, days=body.days))
    db.add_all(created)
    await db.commit()
    return {"items": [code_json(p) for p in created]}


@router.delete("/{code}")
async def delete_code(code: str, _: Admin, db: DB) -> dict:
    result = await db.execute(delete(PromoCode).where(PromoCode.code == normalize(code), PromoCode.used_by.is_(None)))
    await db.commit()
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="Нет такого неиспользованного промокода")
    return {"ok": True}


# --- a user -----------------------------------------------------------------------


class RedeemIn(BaseModel):
    code: str = Field(max_length=64)


@router.post("/redeem")
async def redeem(body: RedeemIn, viewer: Viewer, db: DB) -> dict:
    if not viewer.is_owner:
        raise HTTPException(status_code=403, detail="Промокод вводит владелец проекта")
    now = time.monotonic()
    tries = _tries[viewer.user_id]
    while tries and now - tries[0] > TRIES_WINDOW:
        tries.popleft()
    if len(tries) >= TRIES_LIMIT:
        raise HTTPException(status_code=429, detail="Слишком много попыток — попробуйте через 10 минут")

    promo = await db.get(PromoCode, normalize(body.code), with_for_update=True)
    project = await db.get(Project, viewer.project_id)
    current, offered = plan_of(project_plan(project)), plan_of(promo.plan) if promo else None
    if promo is None or promo.used_by is not None or offered is None or not offered.by_promo \
            or (current and current.rank > offered.rank):
        tries.append(now)
        raise HTTPException(status_code=409, detail=INVALID)
    term = timedelta(days=promo.days) if promo.days else None
    if current and current.id == offered.id:
        if project.plan_expires_at is None:
            raise HTTPException(status_code=409, detail=f"У проекта уже бессрочный тариф {offered.title}")
        # the same plan: the code's days are added to what is left (forever stays forever)
        project.plan_expires_at = None if term is None else project.plan_expires_at + term
    else:
        # no plan, or a better one: it starts now, with the code's term
        await billing.give(db, project, offered.id, None if term is None else utcnow() + term)
    promo.used_by, promo.project_id, promo.used_at = viewer.user_id, project.id, utcnow()
    await db.commit()
    return {"plan": plan_json(project.plan, project.plan_expires_at)}
