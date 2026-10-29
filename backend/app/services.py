"""DB operations shared by the web app and the worker."""

import logging
from collections import defaultdict
from functools import lru_cache
from collections.abc import Iterable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import Channel, ChannelReserve

log = logging.getLogger(__name__)


async def release_channels(session: AsyncSession, account_id: int) -> None:
    """Hand an account's channels back to the queue so another account rejoins them."""
    await session.execute(
        update(Channel)
        .where(Channel.account_id == account_id, Channel.status.in_(("active", "pending")))
        .values(account_id=None, status="pending", status_detail=None)
    )
    await session.execute(
        update(Channel).where(Channel.account_id == account_id).values(account_id=None)
    )


async def account_loads(session: AsyncSession) -> dict[int, int]:
    """Channels per account: the ones it reads plus the private ones it backs up."""
    loads: dict[int, int] = defaultdict(int)
    for account_id, n in (await session.execute(
        select(Channel.account_id, func.count()).where(Channel.account_id.is_not(None)).group_by(Channel.account_id)
    )).all():
        loads[account_id] += n
    for account_id, n in (await session.execute(
        select(ChannelReserve.account_id, func.count()).group_by(ChannelReserve.account_id)
    )).all():
        loads[account_id] += n
    return dict(loads)


def preview_path(post_id: int, slot: int = 0):
    """The picture of an album's `slot`-th item with one; the first keeps the old name."""
    return settings.media_dir / (f"{post_id}.jpg" if slot == 0 else f"{post_id}_{slot}.jpg")


def media_slots(post) -> list[tuple[int, str]]:
    """(slot, kind) of every saved picture of the post: photo | video | other."""
    if post.previews is not None:
        return [(i, p["k"]) for i, p in enumerate(post.previews) if p]
    if post.has_preview:  # saved before per-item pictures: one, of the first item
        kind = {"photo": "photo", "webpage": "photo", "video": "video", "gif": "video"}.get(post.media_type or "", "other")
        return [(0, kind)]
    return []


SAME_PICTURE = 6  # bits of difference between two pictures' hashes: the same picture


@lru_cache(maxsize=20_000)
def picture_hash(post_id: int, slot: int, size: int) -> int | None:
    """A 64-bit difference hash of a preview: the same picture reposted by several channels has
    nearly the same one. `size` is in the key so that a re-saved preview is hashed again."""
    from PIL import Image

    try:
        with Image.open(preview_path(post_id, slot)) as img:
            small = img.convert("L").resize((9, 8))
            px = small.tobytes()
    except OSError:
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def same_picture(a: int, b: int) -> bool:
    return bin(a ^ b).count("1") <= SAME_PICTURE


def media_position(post, slot: int) -> int | None:
    """The place in its album of the post's `slot`-th picture (0 for a single post)."""
    if post.previews is not None:
        return post.previews[slot]["p"] if 0 <= slot < len(post.previews) and post.previews[slot] else None
    return 0 if slot == 0 and post.has_preview else None


def video_url(post, slot: int, kind: str) -> str | None:
    """Where the page asks for a video to play (posts.video): for the video items of a post."""
    return f"/api/media/{post.id}/video?i={slot}" if kind == "video" else None


def delete_previews(post_ids: Iterable[int]) -> None:
    for post_id in post_ids:
        try:
            preview_path(post_id).unlink(missing_ok=True)
            for extra in settings.media_dir.glob(f"{post_id}_*.jpg"):
                extra.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("cannot delete preview %s: %s", post_id, exc)
