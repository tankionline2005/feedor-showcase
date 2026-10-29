import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from ..config import settings
from ..models import Account, Proxy
from ..proxies import ProxyConfig, account_loads, apply_check, check, parse_proxy_line, split_lines
from .deps import DB, Admin
from .posts import iso

router = APIRouter(prefix="/api/proxies", tags=["proxies"])

PARALLEL_CHECKS = 20


def proxy_out(p: Proxy, accounts: int = 0) -> dict:
    return {
        "id": p.id,
        "kind": p.kind,
        "host": p.host,
        "port": p.port,
        "label": f"{p.host}:{p.port}",
        "username": p.username,
        "enabled": p.enabled,
        "status": p.status,
        "latency_ms": p.latency_ms,
        "checked_at": iso(p.checked_at),
        "last_error": p.last_error,
        "accounts": accounts,
        "source": p.source,
    }


async def _full_check(proxies: list[Proxy]) -> None:
    sem = asyncio.Semaphore(PARALLEL_CHECKS)

    async def run(p: Proxy):
        async with sem:
            return await check(
                ProxyConfig.of(p), full=True, api_id=settings.tg_api_id, api_hash=settings.tg_api_hash
            )

    results = await asyncio.gather(*(run(p) for p in proxies))
    for p, (ok, latency, error) in zip(proxies, results, strict=True):
        apply_check(p, ok, latency, error)


@router.get("")
async def list_proxies(_: Admin, db: DB) -> dict:
    proxies = (await db.execute(select(Proxy).order_by(Proxy.created_at))).scalars().all()
    loads = await account_loads(db)
    return {"items": [proxy_out(p, loads.get(p.id, 0)) for p in proxies]}


class AddIn(BaseModel):
    text: str = Field(max_length=200_000)


@router.post("")
async def add_proxies(body: AddIn, _: Admin, db: DB) -> dict:
    known = set((await db.execute(select(Proxy.key))).scalars())
    added: list[Proxy] = []
    duplicates: list[str] = []
    invalid: list[dict] = []
    for line in split_lines(body.text):
        try:
            cfg = parse_proxy_line(line)
        except ValueError as exc:
            invalid.append({"line": line, "error": str(exc)})
            continue
        if cfg.key in known:
            duplicates.append(line)
            continue
        known.add(cfg.key)
        p = Proxy(
            kind=cfg.kind, host=cfg.host, port=cfg.port, username=cfg.username,
            password=cfg.password, secret=cfg.secret, key=cfg.key, status="unknown", fails=0,
        )
        db.add(p)
        added.append(p)
    # checked right away so the list shows which ones work and accounts can use them
    await _full_check(added)
    await db.commit()
    return {
        "added": [proxy_out(p) for p in added],
        "working": sum(p.status == "ok" for p in added),
        "duplicates": duplicates,
        "invalid": invalid,
    }


@router.post("/{proxy_id}/check")
async def check_now(proxy_id: int, _: Admin, db: DB) -> dict:
    p = await db.get(Proxy, proxy_id)
    if p is None:
        raise HTTPException(status_code=404)
    await _full_check([p])
    await db.commit()
    return proxy_out(p, (await account_loads(db)).get(p.id, 0))


class PatchIn(BaseModel):
    enabled: bool


@router.patch("/{proxy_id}")
async def patch_proxy(proxy_id: int, body: PatchIn, _: Admin, db: DB) -> dict:
    p = await db.get(Proxy, proxy_id)
    if p is None:
        raise HTTPException(status_code=404)
    p.enabled = body.enabled
    await db.commit()
    # a disabled proxy's accounts are moved to other proxies by the worker
    return proxy_out(p, (await account_loads(db)).get(p.id, 0))


@router.delete("/{proxy_id}")
async def delete_proxy(proxy_id: int, _: Admin, db: DB) -> dict:
    p = await db.get(Proxy, proxy_id)
    if p is None:
        raise HTTPException(status_code=404)
    # explicit: databases upgraded in place have no foreign key on accounts.proxy_id
    await db.execute(update(Account).where(Account.proxy_id == proxy_id).values(proxy_id=None))
    await db.delete(p)
    await db.commit()
    return {"ok": True}
