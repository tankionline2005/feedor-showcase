from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# local development data lives in backend/data regardless of the working directory
_DEV_DATA = Path(__file__).resolve().parents[1] / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = f"sqlite+aiosqlite:///{(_DEV_DATA / 'dev.db').as_posix()}"

    # Single web user
    admin_username: str = "admin"
    admin_password: str = "admin"
    secret_key: str = "dev-secret-change-me"
    cookie_secure: bool = False

    # Default Telegram app credentials (my.telegram.org); an account may override them
    tg_api_id: int | None = None
    tg_api_hash: str | None = None

    # The alert bot (from @BotFather) for a fresh server; afterwards the admin changes it in the web UI.
    notify_bot_token: str | None = None

    # the worker's internal service (full-size photos, see worker/internal.py)
    worker_url: str = "http://127.0.0.1:8001"

    # the site's domain (also Caddy's): links in the bot's messages lead there
    domain: str = ""

    media_dir: Path = _DEV_DATA / "media"
    static_dir: Path | None = None

    log_level: str = "INFO"


settings = Settings()


def site_url() -> str:
    return f"https://{settings.domain}" if settings.domain else "http://localhost:5173"


# in the built index.html and manifest.json: the site's name goes there when they are served
SITE_NAME_MARK = "__SITE_NAME__"


def site_name() -> str:
    """The site is called by its address, the first letter a capital: feedor.xyz → Feedor.xyz."""
    name = settings.domain or "localhost"
    return name[:1].upper() + name[1:]
