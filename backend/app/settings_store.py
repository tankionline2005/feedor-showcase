"""Runtime settings editable from the web UI (stored in the `settings` table)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting

DEFAULTS: dict[str, object] = {
    "aggregator_channel": "",
    "retention_days": 30,
    # when free disk space drops below this, the oldest posts go before their retention period
    "cleanup_free_mb": 5120,
    # when a channel is added, its posts of the last this many days are fetched (never reposted)
    "backfill_days": 7,
    # fallback polling for channels whose updates were not pushed
    "poll_interval_sec": 300,
    # pause between channel joins on one account
    "join_interval_sec": 90,
    # pause between reposts into the aggregator channel
    "repost_interval_sec": 3,
    # posts older than this are not reposted (e.g. after downtime)
    "repost_max_age_hours": 6,
    # pick up new proxies from the public channel @ProxyMTProto (see worker/proxy_feed.py)
    "proxy_feed_enabled": True,
    # live accounts sharing one proxy
    "accounts_per_proxy": 5,
    # repost errors up to this moment (ISO, UTC) are hidden from the settings screen
    "repost_error_dismissed_at": "",
    # notification bot
    "notify_bot_token": "",
    "notify_bot_username": "",
    "notify_link_code": "",
    # key of the external read-only API (/api/v1); generated on first use
    "api_key": "",
    # aggregator already reported as a basic group (no message links), so the bot says it once
    "aggregator_basic_warned": "",
    # last @ProxyMTProto post already taken proxies from
    "proxy_feed_last_post": "",
    # the service's OpenRouter (billing.py), linked by the admin: the AI of every plan but dev:service:plan.
    # The key encrypted (ai.encrypt_key); the models by operation ({} : ai.OPERATIONS' defaults); why it stands still
    "ai_key": "",
    "ai_models": {},
    "ai_paused": "",
    # what the owners pay for the AI: OpenRouter's cost times this, in percent (200: twice)
    "ai_markup_pct": 200,
    # the payments are open (billing.go_live): the trials count their days, frozen projects get deleted
    "payments_live": False,
}

# managed by dedicated endpoints / the worker, never shown or set through /api/settings
INTERNAL = {
    "repost_error_dismissed_at",
    "notify_bot_token",
    "notify_bot_username",
    "notify_link_code",
    "api_key",
    "aggregator_basic_warned",
    "proxy_feed_last_post",
    "ai_key",
    "ai_models",
    "ai_paused",
    "payments_live",
}

LIMITS: dict[str, tuple[int, int]] = {
    "retention_days": (1, 3650),
    "cleanup_free_mb": (256, 10240),
    "backfill_days": (0, 30),
    "poll_interval_sec": (30, 86400),
    "join_interval_sec": (10, 86400),
    "repost_interval_sec": (1, 600),
    "repost_max_age_hours": (1, 720),
    "accounts_per_proxy": (1, 15),
    "ai_markup_pct": (100, 1000),
}


async def get_all(session: AsyncSession) -> dict[str, object]:
    rows = (await session.execute(select(Setting))).scalars().all()
    values = dict(DEFAULTS)
    values.update({r.key: r.value for r in rows if r.key in DEFAULTS})
    return values


def validate(values: dict[str, object]) -> dict[str, object]:
    clean: dict[str, object] = {}
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ValueError(f"Неизвестная настройка: {key}")
        default = DEFAULTS[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f"{key}: ожидается true/false")
        elif isinstance(default, int):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key}: ожидается целое число")
            lo, hi = LIMITS[key]
            if not lo <= value <= hi:
                raise ValueError(f"{key}: допустимо от {lo} до {hi}")
        elif isinstance(default, dict):
            if not isinstance(value, dict):
                raise ValueError(f"{key}: ожидается объект")
        else:
            if not isinstance(value, str):
                raise ValueError(f"{key}: ожидается строка")
            value = value.strip()
        clean[key] = value
    return clean


async def update(session: AsyncSession, values: dict[str, object]) -> None:
    for key, value in validate(values).items():
        await session.merge(Setting(key=key, value=value))
    await session.commit()
