import re
from dataclasses import dataclass

_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_INVITE = re.compile(r"^(?:https?://)?(?:t|telegram)\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)/?$")
_PUBLIC = re.compile(r"^(?:https?://)?(?:t|telegram)\.me/(?:s/)?([A-Za-z0-9_]+)(?:/\d+)?/?(?:\?.*)?$")


@dataclass(frozen=True)
class ChannelLink:
    username: str | None = None
    invite_hash: str | None = None

    @property
    def key(self) -> str:
        return f"@{self.username.lower()}" if self.username else f"+{self.invite_hash}"


def parse_channel_link(raw: str) -> ChannelLink:
    """Accepts @name, name, t.me/name, t.me/name/123, t.me/s/name, t.me/+hash, t.me/joinchat/hash."""
    s = raw.strip()
    if not s:
        raise ValueError("пустая строка")
    if m := _INVITE.match(s):
        return ChannelLink(invite_hash=m.group(1))
    if m := _PUBLIC.match(s):
        s = m.group(1)
    s = s.removeprefix("@")
    if _USERNAME.match(s):
        return ChannelLink(username=s)
    raise ValueError(f"не похоже на ссылку на канал: {raw.strip()}")


def split_links(text: str) -> list[str]:
    return [part for part in re.split(r"[\s,;]+", text) if part]
