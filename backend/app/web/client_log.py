"""What a browser tells about itself going wrong: the page that stopped scrolling (frontend lib/freezeGuard.ts) —
written to the web log with who it was, so a bug seen «now and then» can be found."""

import json
import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["client-log"])
log = logging.getLogger("client")

LIMIT = 20  # reports one IP may send in WINDOW
WINDOW = 3600

_sent: dict[str, deque[float]] = defaultdict(deque)


class ReportIn(BaseModel):
    kind: str = Field(max_length=40)
    detail: dict = Field(default_factory=dict)


@router.post("/client-log")
async def client_log(body: ReportIn, request: Request) -> dict:
    ip = request.client.host if request.client else "?"
    now = time.monotonic()
    sent = _sent[ip]
    while sent and now - sent[0] > WINDOW:
        sent.popleft()
    if len(sent) >= LIMIT:
        return {"ok": False}
    sent.append(now)
    who = request.session.get("uid") or (f"member {request.session['mid']}" if request.session.get("mid") else "-")
    detail = json.dumps(body.detail, ensure_ascii=False)[:3000]
    log.warning("%s (user %s, %s): %s", body.kind, who, request.headers.get("user-agent", "")[:160], detail)
    return {"ok": True}
