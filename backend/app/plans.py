"""Plans: what a project may do. A project buys one from the owner's balance (billing.py), or gets it from the
admin or a promo code; the first project of a new account gets «Free Trial». It lasts until `plan_expires_at`,
or forever when that is empty."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import Project, utcnow

NO_PLAN = ""
TRIAL = "trial"
DEV = "dev"
PERIOD = timedelta(days=30)  # a plan is bought for this long; the AI included in it is for this long too
MICRO = 1_000_000  # money is kept in millionths of a dollar


@dataclass(frozen=True)
class Plan:
    id: str
    title: str
    price: float | None  # USD for PERIOD; None: not sold
    channels: int | None  # how many channels the project may track; None = no limit
    api: bool  # the API and the MCP connector
    rank: int  # a better plan has a higher rank (promo codes never downgrade)
    # staff accounts of a project ("Совместный мониторинг"); None = no limit
    members: int | None = None
    # «Триггер-слова» of a project, the owner's and the staff's together; None = no limit
    triggers: int | None = None
    groups: int | None = None  # «Мои группы» of a project; None = no limit
    ai: float | None = None  # USD of AI a PERIOD, at the service's prices (the trial's: once); None: own OpenRouter
    public: bool = True  # on the plans page and in «Тариф»: dev:service:plan only to those who have it
    by_promo: bool = True  # may a promo code give it
    forever: bool = False  # given only without an end
    badge: str | None = None

    @property
    def buyable(self) -> bool:
        return self.price is not None

    @property
    def own_ai(self) -> bool:
        """The AI goes with the owner's own OpenRouter, as it always did: not the service's, not paid here."""
        return self.ai is None


PLANS = {p.id: p for p in (
    Plan(TRIAL, "Free Trial", None, 50, False, 0, members=0, triggers=3, groups=1, ai=1, by_promo=False),
    Plan("starter", "Starter", 9, 100, True, 1, members=1, triggers=10, groups=2, ai=5),
    Plan("pro", "Pro", 29, 350, True, 2, members=5, triggers=50, groups=5, ai=15, badge="Popular"),
    Plan("studio", "Studio", 69, 600, True, 3, members=10, triggers=150, groups=10, ai=25),
    Plan("agency", "Agency", 79, 1000, True, 4, members=20, triggers=None, groups=20, ai=40, badge="Best value"),
    Plan(DEV, "dev:service:plan", None, None, True, 9, public=False, by_promo=False, forever=True),
)}


def plan_of(plan_id: str | None) -> Plan | None:
    return PLANS.get(plan_id or "")


def micro(usd: float) -> int:
    return round(usd * MICRO)


def usd(amount: int | None) -> float | None:
    return None if amount is None else round(amount / MICRO, 6)


def active_plan(plan_id: str | None, expires_at: datetime | None) -> str:
    """The plan in force: an expired one counts as none."""
    if plan_of(plan_id) is None or (expires_at is not None and expires_at <= utcnow()):
        return NO_PLAN
    return plan_id


def project_plan(project: Project) -> str:
    return active_plan(project.plan, project.plan_expires_at)


def plan_json(plan_id: str | None, expires_at: datetime | None = None) -> dict | None:
    plan = plan_of(plan_id)
    if plan is None:
        return None
    return {
        "id": plan.id,
        "title": plan.title,
        "price": plan.price,
        "channels": plan.channels,
        "api": plan.api,
        "members": plan.members,
        "triggers": plan.triggers,
        "groups": plan.groups,
        "ai": plan.ai,  # None: the owner's own OpenRouter
        "badge": plan.badge,
        "buyable": plan.buyable,
        # None: no end date
        "expires_at": expires_at.isoformat(timespec="seconds") + "Z" if expires_at else None,
    }


def plans_json(also: str | None = None) -> list[dict]:
    """The plans everyone may see, and `also` (the project's own, dev:service:plan included)."""
    return [plan_json(p.id) for p in sorted(PLANS.values(), key=lambda p: p.rank) if p.public or p.id == also]
