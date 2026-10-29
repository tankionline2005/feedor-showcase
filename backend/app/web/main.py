import logging
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .. import settings_store
from ..config import SITE_NAME_MARK, settings, site_name
from ..db import SessionLocal, init_db
from . import (
    accounts, ai_api, covers_api, publications_api, auth, badges_api, channels, chat_api, client_log, comments_api, devices, groups, live, members,
    notify_api, posts,
    projects, promo,
    proxies, public_api, rubrics_api, settings_api, smart_api, topics_api, triggers_api, users,
)
from . import billing_api, chat_posts_api, mcp, mcp_oauth

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs contain the bot token
    if settings.admin_password == "admin" or settings.secret_key.startswith("dev-"):
        log.warning("ADMIN_PASSWORD / SECRET_KEY are defaults — set them in .env before going public")
    await init_db()
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    if settings.notify_bot_token:
        # sets up a fresh server only: afterwards the admin changes the bot in the web UI
        async with SessionLocal() as s:
            if not (await settings_store.get_all(s))["notify_bot_token"]:
                await notify_api.connect_bot(s, settings.notify_bot_token.strip())
    yield


app = FastAPI(title="Kindergarten Monitoring", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="km_session",
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=settings.cookie_secure,
)

for module in (auth, posts, live, channels, accounts, proxies, notify_api, settings_api, public_api, users, promo, groups,
               projects, members, ai_api, smart_api, topics_api, publications_api, chat_api, chat_posts_api, badges_api,
               triggers_api, devices, client_log, comments_api, rubrics_api, covers_api, mcp, mcp_oauth, billing_api):
    app.include_router(module.router)
app.include_router(billing_api.public)
app.include_router(public_api.manage)
public_api.install(app)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


# The built frontend; routing is hash-based, so serving index.html at "/" is enough.
if settings.static_dir and settings.static_dir.is_dir():
    @lru_cache(maxsize=4)
    def _named(name: str) -> str:
        """A page that names the site: the name is the address it is served at (DOMAIN), see site_name()."""
        return (settings.static_dir / name).read_text(encoding="utf-8").replace(SITE_NAME_MARK, site_name())

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    async def index() -> HTMLResponse:
        # no-cache: a new build (new script names) is picked up at once
        return HTMLResponse(_named("index.html"), headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.json", include_in_schema=False)
    async def manifest() -> Response:
        return Response(_named("manifest.json"), media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")
