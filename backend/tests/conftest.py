import os
import tempfile
from pathlib import Path

import pytest

# must happen before the app modules read settings
_tmp = Path(tempfile.mkdtemp(prefix="km-test-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_tmp / 'test.db').as_posix()}"
os.environ["MEDIA_DIR"] = str(_tmp / "media")
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "secret"
os.environ["SECRET_KEY"] = "test-secret"
os.environ.pop("STATIC_DIR", None)
# Tests must never reach the real Telegram. Environment variables win over the developer's .env,
# and 0 / empty make api_credentials() refuse to build a client.
os.environ["TG_API_ID"] = "0"
os.environ["TG_API_HASH"] = ""


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    """Second line of defence: any attempt to open a real Telegram connection fails the test."""
    from telethon import TelegramClient

    async def forbidden(self, *args, **kwargs):
        raise AssertionError("a test tried to connect to the real Telegram")

    monkeypatch.setattr(TelegramClient, "connect", forbidden)


@pytest.fixture(autouse=True)
def no_rate_limit_left_over():
    """OpenRouter's "wait" is remembered by the process (ai._cooldown): one test's must not reach the next."""
    from app import ai

    ai._cooldown.clear()
    yield
    ai._cooldown.clear()


@pytest.fixture(autouse=True)
def smart_feed_without_collecting(monkeypatch):
    """The smart feed lets a few posts wait up to 5 minutes for more (smart_feed.COLLECT); tests judge at once,
    except the one that tests the waiting."""
    from datetime import timedelta

    from app import smart_feed

    monkeypatch.setattr(smart_feed, "COLLECT", timedelta(0))
