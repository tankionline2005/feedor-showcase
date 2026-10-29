"""Money: an owner's balance, the plans bought from it, and the AI paid from them.

- One balance per owner (User.balance), in millionths of a dollar; every move is a BalanceOp («История операций»).
  Top-ups are not open yet: «Пополнить» says so; the admin may add or take money by hand.
- A plan is bought for 30 days (plans.PERIOD) from the balance. A better one bought before the end starts at once,
  and the unused part of what was paid for the old one comes back; a cheaper one comes at the end of the period
  (Project.next_plan). «Автопродление» buys it again at the end, if the balance has enough.
- The AI of every plan but dev:service:plan goes with the service's OpenRouter (the admin's), at the service's
  prices: what OpenRouter charged times the markup (settings: ai_markup_pct). Each plan includes some AI a month
  (the trial's: once); above it the AI is paid from the balance, if the owner lets it («Докупать ИИ с баланса»), up
  to their cap. When there is nothing to pay with, the project's AI stands still till there is.
- Over a plan's limits, the newest channels, staff and trigger words are paused, never deleted (apply_limits).
- «Free Trial»: once a Telegram account, to its first project. Till the payments go live (settings:
  payments_live) it has no end; going live gives every trial its 30 days from then.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update

from . import settings_store
from .models import BalanceOp, Channel, Member, Project, ProjectChannel, PromoCode, Trigger, User, utcnow
from .plans import DEV, MICRO, PERIOD, PLANS, TRIAL, micro, plan_of, project_plan, usd

log = logging.getLogger(__name__)


class BillingError(Exception):
    """Why it cannot be done, for people; `status` is the HTTP one."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


TOPUP_CLOSED = "Пополнение временно недоступно"
NO_PLAN_TEXT = "Нет действующего тарифа — выберите тариф в настройках"


def money(amount: int | None) -> str:
    """$1 234,50 — for people (the bot's words), as the site writes it (frontend lib/money.ts, cents)."""
    return "$" + f"{(amount or 0) / MICRO:,.2f}".replace(",", " ").replace(".", ",")


async def markup_pct(s) -> int:
    return int((await settings_store.get_all(s))["ai_markup_pct"])


async def payments_live(s) -> bool:
    return bool((await settings_store.get_all(s))["payments_live"])


# --- the balance ------------------------------------------------------------------------------------------------


async def move(s, user_id: int, amount: int, kind: str, *, project_id: int | None = None, plan: str | None = None,
               note: str | None = None) -> None:
    """Money onto (above zero) or off the balance, written down; the caller commits."""
    await s.execute(update(User).where(User.id == user_id).values(balance=User.balance + amount))
    s.add(BalanceOp(user_id=user_id, project_id=project_id, kind=kind, amount=amount, plan=plan, note=note))


async def balance_of(s, user_id: int) -> int:
    return int(await s.scalar(select(User.balance).where(User.id == user_id)) or 0)


# --- the AI's money ---------------------------------------------------------------------------------------------


def roll_ai_period(project: Project, now: datetime | None = None) -> None:
    """A new month of the plan's AI: what was spent starts again from zero (the trial's never does)."""
    now = now or utcnow()
    if project.ai_period_start is None:
        project.ai_period_start = project.plan_started_at or now
    if project.plan == TRIAL or now < project.ai_period_start + PERIOD:
        return
    project.ai_period_start += PERIOD * ((now - project.ai_period_start) // PERIOD)
    project.ai_used = project.ai_extra_used = 0


def ai_included(project: Project) -> int:
    plan = plan_of(project_plan(project))
    return micro(plan.ai) if plan and plan.ai is not None else 0


def ai_left(project: Project) -> int:
    """What is left of the plan's own AI this period."""
    return max(0, ai_included(project) - project.ai_used)


NO_PLAN_AI = "Нет действующего тарифа — выберите тариф, чтобы пользоваться ИИ"
AI_OUT_EXTRA_OFF = ("ИИ, включённый в тариф, закончился. Включите «Докупать ИИ с баланса» в «Тарифе» или дождитесь "
                    "нового месяца тарифа")
AI_OUT_NO_MONEY = "ИИ, включённый в тариф, закончился, а на балансе нет денег — пополните баланс"
AI_OUT_CAP = "Достигнут лимит докупки ИИ в этом месяце — его можно поднять в «Тарифе»"


async def ai_stop(s, project: Project, owner_balance: int | None = None) -> str | None:
    """Why the project's AI (on the service's OpenRouter) cannot go now; None: it can."""
    if not project_plan(project):
        return NO_PLAN_AI
    roll_ai_period(project)
    if ai_left(project) > 0:
        return None
    if not project.ai_extra:
        return AI_OUT_EXTRA_OFF
    if project.ai_extra_cap is not None and project.ai_extra_used >= project.ai_extra_cap:
        return AI_OUT_CAP
    if (owner_balance if owner_balance is not None else await balance_of(s, project.owner_id)) <= 0:
        return AI_OUT_NO_MONEY
    return None


async def charge_ai(s, project_id: int, cost_usd: float, pct: int | None = None) -> int:
    """A request made with the service's OpenRouter is paid: from the plan's AI first, then from the balance.
    What it cost the owner, the markup in; the caller commits (with the AiUsage row)."""
    pct = pct if pct is not None else await markup_pct(s)
    charged = round(cost_usd * MICRO * pct / 100)
    if charged <= 0:
        return 0
    project = await s.get(Project, project_id, with_for_update=True)
    if project is None:
        return charged
    roll_ai_period(project)
    included = min(ai_left(project), charged)
    rest = charged - included
    project.ai_used += included
    if rest and project.ai_extra:
        project.ai_extra_used += rest
        await s.execute(update(User).where(User.id == project.owner_id).values(balance=User.balance - rest))
        day = utcnow().strftime("%Y-%m-%d")
        op = await s.scalar(select(BalanceOp).where(BalanceOp.user_id == project.owner_id, BalanceOp.kind == "ai",
                                                    BalanceOp.project_id == project_id, BalanceOp.day == day))
        if op is None:
            s.add(BalanceOp(user_id=project.owner_id, project_id=project_id, kind="ai", amount=-rest, day=day))
        else:
            op.amount -= rest
    elif rest:
        project.ai_used += rest  # a request sent with the last of the plan's AI: the service takes the rest
    return charged


# --- buying a plan ----------------------------------------------------------------------------------------------


def _start(project: Project, plan_id: str, paid: int, now: datetime, expires: datetime | None) -> None:
    project.plan, project.plan_expires_at = plan_id, expires
    project.plan_started_at, project.plan_paid = now, paid
    project.next_plan = None
    project.ai_period_start, project.ai_used, project.ai_extra_used = now, 0, 0
    project.reminded_days = None


def unused_part(project: Project, now: datetime | None = None) -> int:
    """What comes back of the current period's payment when a better plan starts before its end."""
    now = now or utcnow()
    if not project.plan_paid or project.plan_expires_at is None or project.plan_started_at is None:
        return 0
    whole = (project.plan_expires_at - project.plan_started_at).total_seconds()
    left = (project.plan_expires_at - now).total_seconds()
    if whole <= 0 or left <= 0:
        return 0
    return int(project.plan_paid * min(1.0, left / whole))


async def buy(s, project: Project, plan_id: str) -> dict:
    """The owner buys a plan for the project from their balance. What happened: {"started"} or {"next"} (a cheaper
    one at the end of the period). BillingError: why not. The caller commits."""
    plan = plan_of(plan_id)
    if plan is None or not plan.public or not plan.buyable:
        raise BillingError("Этот тариф нельзя купить", 403)
    now = utcnow()
    current = plan_of(project_plan(project))
    if current and current.id == DEV:
        raise BillingError("У проекта dev:service:plan — покупать тариф не нужно")
    if current and current.id == plan.id:
        if project.next_plan:  # changed their mind about the cheaper one
            project.next_plan = None
            return {"kept": plan.id}
        raise BillingError("Этот тариф уже действует — он продлится сам с баланса, если включено автопродление")
    if current and current.buyable and plan.rank < current.rank and project.plan_expires_at:
        project.next_plan = plan.id
        return {"next": plan.id}
    price = micro(plan.price)
    back = unused_part(project, now) if current else 0
    have = await balance_of(s, project.owner_id)
    if have + back < price:
        raise BillingError(f"Не хватает {money(price - have - back)} на балансе", 402)
    if back:
        await move(s, project.owner_id, back, "refund", project_id=project.id, plan=project.plan,
                   note=f"Неиспользованные дни тарифа {current.title}")
    await move(s, project.owner_id, -price, "plan", project_id=project.id, plan=plan.id, note=f"Тариф {plan.title}")
    _start(project, plan.id, price, now, now + PERIOD)
    await apply_limits(s, project)
    return {"started": plan.id}


async def renew(s, project: Project) -> bool:
    """The period is over: «Автопродление» buys the plan (or the cheaper one chosen) again from the balance, from
    the moment the old period ended. False: not this time (off, not a plan to buy, too little money)."""
    plan = plan_of(project.next_plan or project.plan)
    if not project.auto_renew or plan is None or not plan.buyable or project.plan_expires_at is None:
        return False
    price = micro(plan.price)
    if await balance_of(s, project.owner_id) < price:
        return False
    await move(s, project.owner_id, -price, "plan", project_id=project.id, plan=plan.id,
               note=f"Продление тарифа {plan.title}")
    start = max(project.plan_expires_at, utcnow() - PERIOD)
    _start(project, plan.id, price, start, start + PERIOD)
    await apply_limits(s, project)
    return True


async def give(s, project: Project, plan_id: str, expires: datetime | None) -> None:
    """The admin or a promo code gives a plan: nothing paid, a period of its own."""
    _start(project, plan_id, 0, utcnow(), None if plan_of(plan_id).forever else expires)
    await apply_limits(s, project)


async def start_trial(s, user: User, project: Project) -> bool:
    """A new account's first project gets «Free Trial» — once a Telegram account."""
    if user.trial_used_at is not None or project.plan:
        return False
    now = utcnow()
    user.trial_used_at = now
    _start(project, TRIAL, 0, now, now + PERIOD if await payments_live(s) else None)
    return True


async def go_live(s) -> int:
    """The payments are open: every trial waiting for it gets its 30 days from now. How many."""
    now = utcnow()
    trials = (await s.execute(select(Project).where(Project.plan == TRIAL, Project.plan_expires_at.is_(None)))
              ).scalars().all()
    for project in trials:
        project.plan_expires_at = now + PERIOD
    await settings_store.update(s, {"payments_live": True})
    return len(trials)


# --- limits -----------------------------------------------------------------------------------------------------


@dataclass
class Paused:
    channels: int = 0
    members: int = 0
    triggers: int = 0


async def _fit(s, rows: list, limit: int | None) -> int:
    """Rows oldest first: the first `limit` of the working ones stay, the rest pause; with room, the paused ones
    come back, oldest first. How many are paused now."""
    now = utcnow()
    working = [r for r in rows if r.paused_at is None]
    paused = [r for r in rows if r.paused_at is not None]
    if limit is None:
        for r in paused:
            r.paused_at = None
        return 0
    for r in working[limit:]:
        r.paused_at = now
    for r in paused[:max(0, limit - len(working))]:
        r.paused_at = None
    return sum(1 for r in rows if r.paused_at is not None)


async def apply_limits(s, project: Project) -> Paused:
    """The project's channels, staff and trigger words to its plan's limits. A project without a plan keeps
    everything as it was: it is frozen as a whole (it reads nothing, its staff cannot sign in)."""
    plan = plan_of(project_plan(project))
    if plan is None:
        return Paused()
    channels = (await s.execute(select(ProjectChannel).where(
        ProjectChannel.project_id == project.id, ~ProjectChannel.own).order_by(ProjectChannel.created_at))).scalars().all()
    members = (await s.execute(select(Member).where(Member.project_id == project.id).order_by(Member.id))
               ).scalars().all()
    triggers = (await s.execute(select(Trigger).where(Trigger.project_id == project.id).order_by(Trigger.id))
                ).scalars().all()
    return Paused(await _fit(s, list(channels), plan.channels), await _fit(s, list(members), plan.members),
                  await _fit(s, list(triggers), plan.triggers))


async def paused_counts(s, project: Project) -> Paused:
    async def count(model) -> int:
        return await s.scalar(select(func.count()).where(model.project_id == project.id,
                                                         model.paused_at.is_not(None))) or 0

    return Paused(await count(ProjectChannel), await count(Member), await count(Trigger))


# --- the end of a plan ------------------------------------------------------------------------------------------

FREEZE = timedelta(days=7)  # a plan ran out: the project is frozen this long, then its data goes


def delete_at(project: Project) -> datetime | None:
    """When a frozen project's data goes (once the payments are live); None: it is not frozen."""
    if not project.plan or project.plan_expires_at is None or project.plan_expires_at > utcnow():
        return None
    return project.plan_expires_at + FREEZE


REMIND_DAYS = 5  # «осталось N дней»: from this many days before the end, once a day
FROZEN_MARK = 0  # reminded_days of a frozen project: told it ran out; -N: told N days before its data goes


def days_until(moment: datetime, now: datetime | None = None) -> int:
    """Whole days left, rounded up: 1 on the last day."""
    left = (moment - (now or utcnow())).total_seconds()
    return max(0, -int(-left // 86400))


def _name(project: Project, several: bool) -> str:
    return f" «{project.channel_title}»" if several and project.channel_title else ""


async def wipe(s, project: Project) -> Project:
    """A frozen project's days are over: its channels, staff, topics, chats, posts and settings go for good. What
    stays is the project with its own channel, without a plan — a new row, so nothing of the old one is left behind
    (everything of a project goes with its row). The new project."""
    from .orphans import mark_if_orphaned
    from .projects import new_project

    links = (await s.execute(select(ProjectChannel).where(ProjectChannel.project_id == project.id))).scalars().all()
    own = [link.channel_id for link in links if link.own]
    channel_ids = [link.channel_id for link in links]
    for link in links:
        await s.delete(link)
    await s.execute(update(PromoCode).where(PromoCode.project_id == project.id).values(project_id=None))
    await s.execute(delete(Member).where(Member.project_id == project.id))
    fresh = new_project(project.owner_id)
    fresh.channel_chat_id, fresh.channel_title = project.channel_chat_id, project.channel_title
    fresh.channel_username = project.channel_username
    fresh.channel_photo = await s.scalar(select(Project.channel_photo).where(Project.id == project.id))  # deferred
    fresh.created_at = project.created_at
    owner = await s.get(User, project.owner_id)
    await s.delete(project)
    await s.flush()
    s.add(fresh)
    await s.flush()
    for channel_id in own:
        s.add(ProjectChannel(project_id=fresh.id, channel_id=channel_id, own=True))
    if owner.project_id == project.id:
        owner.project_id = fresh.id
    await s.flush()
    for ch in (await s.execute(select(Channel).where(Channel.id.in_(channel_ids)))).scalars():
        await mark_if_orphaned(s, ch)
    log.warning("project %s of user %s deleted after its freeze; it is project %s now", project.id,
                project.owner_id, fresh.id)
    return fresh


async def _tell(user: User, text: str) -> None:
    from .notify import tell_user

    if not user.blocked:
        await tell_user(user.tg_user_id, text)


async def lifecycle_step() -> None:
    """Every hour: plans renewed at their end, owners told before it, frozen projects deleted after their days."""
    from .db import SessionLocal

    now = utcnow()
    async with SessionLocal() as s:
        live = await payments_live(s)
        ids = (await s.execute(select(Project.id).where(Project.plan != "", Project.plan_expires_at.is_not(None))
                               )).scalars().all()
    for project_id in ids:
        try:
            await _project_step(project_id, now, live)
        except Exception:  # noqa: BLE001 — one project's trouble must not stop the others
            log.exception("billing of project %s failed", project_id)


async def _project_step(project_id: int, now: datetime, live: bool) -> None:
    from .db import SessionLocal

    async with SessionLocal() as s:
        project = await s.get(Project, project_id, with_for_update=True)
        if project is None or not project.plan or project.plan_expires_at is None:
            return
        owner = await s.get(User, project.owner_id)
        several = (await s.scalar(select(func.count()).where(Project.owner_id == owner.id)) or 0) > 1
        name = _name(project, several)
        plan = plan_of(project.plan)
        title = plan.title if plan else project.plan
        message = None
        if project.plan_expires_at <= now:
            if await renew(s, project):
                message = (f"Тариф {plan_of(project.plan).title} проекта{name} продлён на 30 дней — с баланса списано "
                           f"{money(project.plan_paid)}.")
            else:
                gone = project.plan_expires_at + FREEZE
                left = days_until(gone, now)
                if live and gone <= now:
                    await wipe(s, project)
                    message = (f"Проект{name} удалён: тариф не продлили в течение {FREEZE.days} дней. Каналы, "
                               "сотрудники, темы и публикации удалены без возможности восстановления.")
                elif project.reminded_days is None or project.reminded_days > FROZEN_MARK:
                    project.reminded_days = FROZEN_MARK
                    message = (f"Тариф {title} проекта{name} закончился — проект заморожен: каналы не читаются, ИИ и "
                               f"триггеры стоят, сотрудники не могут войти. Продлите тариф в течение {FREEZE.days} "
                               "дней, иначе данные проекта будут удалены без возможности восстановления.")
                elif live and left <= 2 and project.reminded_days != -left:
                    project.reminded_days = -left
                    when = "завтра" if left <= 1 else f"через {left} дня"
                    message = (f"Проект{name} будет удалён {when} без возможности восстановления: каналы, "
                               "сотрудники, темы и публикации. Чтобы сохранить его, оплатите тариф.")
        else:
            left = days_until(project.plan_expires_at, now)
            if left <= REMIND_DAYS and project.reminded_days != left:
                project.reminded_days = left
                nxt = plan_of(project.next_plan or project.plan)
                price = micro(nxt.price) if nxt and nxt.buyable else None
                when = "завтра" if left <= 1 else f"через {left} {_days_word(left)}"
                if project.plan == TRIAL:
                    message = (f"Пробный период проекта{name} закончится {when}. Выберите тариф в настройках, "
                               "чтобы продолжить работу.")
                elif price is not None and project.auto_renew and await balance_of(s, owner.id) >= price:
                    message = (f"Тариф {nxt.title} проекта{name} продлится {when} — с баланса будет списано "
                               f"{money(price)}.") if left <= 1 else None
                elif price is not None and project.auto_renew:
                    short = price - await balance_of(s, owner.id)
                    message = (f"Тариф {title} проекта{name} закончится {when}. Для продления не хватает "
                               f"{money(short)} на балансе.")
                else:
                    message = f"Тариф {title} проекта{name} закончится {when}."
        await s.commit()
    if message:
        await _tell(owner, message)


def _days_word(n: int) -> str:
    return "день" if n % 10 == 1 and n % 100 != 11 else "дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 \
        else "дней"


async def limits_step() -> None:
    """Every few minutes: every project's channels, staff and trigger words to its plan (a deleted one frees room
    for a paused one; a plan given by the admin or a promo code brings its own limits)."""
    from .db import SessionLocal

    async with SessionLocal() as s:
        for project in (await s.execute(select(Project).where(Project.plan != ""))).scalars().all():
            await apply_limits(s, project)
        await s.commit()


# --- what the owner is shown ------------------------------------------------------------------------------------


async def notice(s, project: Project, ai_stop_text: str | None = None) -> dict | None:
    """The banner the owner sees on every screen: the plan ends soon, the project is frozen, the AI stands. None:
    nothing to say. {"tone": "warn" | "danger", "text", "action": "plan" | "balance"}."""
    now = utcnow()
    plan = plan_of(project.plan)
    if project.plan and project.plan_expires_at is not None and project.plan_expires_at <= now:
        gone = project.plan_expires_at + FREEZE
        if await payments_live(s):
            left = days_until(gone, now)
            when = "завтра" if left <= 1 else f"через {left} {_days_word(left)}"
            return {"tone": "danger", "action": "plan",
                    "text": f"Тариф закончился, проект заморожен. {when.capitalize()} его данные будут удалены без "
                            "возможности восстановления — продлите тариф"}
        return {"tone": "danger", "action": "plan",
                "text": "Тариф закончился, проект заморожен: каналы не читаются, ИИ и триггеры стоят — выберите тариф"}
    if plan and project.plan_expires_at is not None:
        left = days_until(project.plan_expires_at, now)
        if left <= REMIND_DAYS:
            when = "завтра" if left <= 1 else f"через {left} {_days_word(left)}"
            tone = "danger" if left <= 2 else "warn"
            if project.plan == TRIAL:
                return {"tone": tone, "action": "plan", "text": f"Пробный период закончится {when} — выберите тариф"}
            nxt = plan_of(project.next_plan or project.plan)
            if nxt and nxt.buyable and project.auto_renew:
                short = micro(nxt.price) - await balance_of(s, project.owner_id)
                if short <= 0:
                    return None  # it renews by itself
                return {"tone": tone, "action": "balance",
                        "text": f"Тариф {plan.title} закончится {when}: для продления не хватает {money(short)}"}
            return {"tone": tone, "action": "plan", "text": f"Тариф {plan.title} закончится {when}"}
    if ai_stop_text and ai_stop_text != NO_PLAN_AI:
        return {"tone": "warn", "action": "balance" if "баланс" in ai_stop_text else "plan", "text": ai_stop_text}
    return None



def ai_json(project: Project) -> dict | None:
    """The plan's AI this period, at the service's prices (USD); None: the owner's own OpenRouter."""
    plan = plan_of(project_plan(project))
    if plan is None or plan.own_ai:
        return None
    roll_ai_period(project)
    return {
        "included": plan.ai,
        "used": usd(min(project.ai_used, ai_included(project))),
        "extra_used": usd(project.ai_extra_used),
        "extra": project.ai_extra,
        "extra_cap": usd(project.ai_extra_cap),
        # the trial's AI is once; a plan's comes again a month after its period began
        "renews_at": None if project.plan == TRIAL or project.ai_period_start is None
        else (project.ai_period_start + PERIOD).isoformat(timespec="seconds") + "Z",
    }


async def spent_by_kind(s, user_id: int) -> dict:
    rows = (await s.execute(select(BalanceOp.kind, func.sum(BalanceOp.amount)).where(BalanceOp.user_id == user_id)
                            .group_by(BalanceOp.kind))).all()
    return {kind: usd(total) for kind, total in rows}


def public_plans() -> list:
    return [p for p in sorted(PLANS.values(), key=lambda p: p.rank) if p.public]
