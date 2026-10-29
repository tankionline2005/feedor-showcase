"""Text on the pictures of new posts (OCR), kept in Post.extracted_text_from_media.

Tesseract with the light tessdata_fast models (rus+eng), the way ocr_fast.py does it: the picture
in greys, small ones enlarged twice, read as it is and once more as a "white text" mask (memes),
the reading with more confident letters wins; words below 55% confidence and letterless junk go; then lines without a word of three letters, and a
picture's text altogether if fewer than ten letters are left (what photos without text give).
Every saved preview of a post is read in order and the texts are joined line by line.

The server has one core: one picture at a time, one Tesseract thread, the lowest priority — the
collector never waits for OCR. Only new posts are read (ocr_status "pending" from ingest).

    python -m app.ocr --recent 30    # read the 30 latest posts with pictures and print the result
"""

import argparse
import asyncio
import io
import logging
import os
import time
from datetime import timedelta

from sqlalchemy import select, update

from .config import settings
from .db import SessionLocal
from .models import Channel, Post, utcnow
from .services import media_slots, preview_path

log = logging.getLogger(__name__)

TESSDATA = os.environ.get("TESSDATA_FAST", "/usr/share/tessdata_fast")
LANG = "rus+eng"
PSM = 11  # sparse text: photos and memes
MIN_CONF = 55
MAX_SIDE = 1600
ENLARGE_BELOW = 1200
WHITE = 200  # the white-text mask keeps what is lighter than this
# noise filter: Tesseract "sees" letters in the patterns of ordinary photos ("i / 4 / aN / ve");
# a line stays only with a word of MIN_WORD letters (or a number of MIN_NUMBER digits: prices, sums),
# a picture's text only with MIN_LETTERS letters in all
MIN_WORD = 3
MIN_NUMBER = 4
MIN_LETTERS = 10
TIMEOUT = 90  # seconds per Tesseract run
PREVIEW_WAIT = timedelta(minutes=30)  # a post whose pictures never came is not waited for longer


def _prepare(data: bytes) -> list[bytes]:
    """The picture as Tesseract reads it best: greys, not too big, small ones twice as large; and
    its white-text mask. PNG bytes of both."""
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("L")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    if max(img.size) < ENLARGE_BELOW:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    mask = img.point(lambda v: 0 if v > WHITE else 255)
    out = []
    for picture in (img, mask):
        buf = io.BytesIO()
        picture.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def _parse_tsv(tsv: str) -> tuple[str, float]:
    """(text, how sure) from Tesseract's TSV: confident words with a letter or digit, by line."""
    lines: dict[tuple[int, int, int], list[str]] = {}
    confs: list[float] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12:
            continue
        word = cols[11].strip()
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if not word or conf < MIN_CONF or not any(ch.isalnum() for ch in word):
            continue
        lines.setdefault((int(cols[2]), int(cols[3]), int(cols[4])), []).append(word)
        confs.append(conf)
    text = "\n".join(" ".join(words) for _, words in sorted(lines.items()))
    return text, (sum(confs) / len(confs) if confs else 0.0)


def _letters(word: str) -> int:
    return sum(ch.isalpha() for ch in word)


def _meaningful(word: str) -> bool:
    return _letters(word) >= MIN_WORD or sum(ch.isdigit() for ch in word) >= MIN_NUMBER


def drop_noise(text: str) -> str:
    """What is left of a picture's text without the scraps a photo with no text gives."""
    lines = [line for line in text.splitlines() if any(_meaningful(w) for w in line.split())]
    kept = "\n".join(lines)
    return kept if _letters(kept) >= MIN_LETTERS else ""


async def _tesseract(png: bytes) -> str:
    proc = await asyncio.create_subprocess_exec(
        "nice", "-n", "19", "tesseract", "stdin", "stdout", "--tessdata-dir", TESSDATA,
        # TSV (words with confidence) by the setting itself: the "tsv" config file is not in tessdata_fast
        "-l", LANG, "--oem", "1", "--psm", str(PSM), "-c", "tessedit_create_tsv=1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(png), TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    tsv = out.decode("utf-8", "replace")
    if not tsv.startswith("level\t"):
        # not the word table: a broken setup must not pass for "no text on the picture"
        raise RuntimeError(f"tesseract gave no TSV: {tsv[:200]!r}")
    return tsv


async def read_picture(data: bytes) -> str:
    """The text on one picture ("" when there is none)."""
    best, best_score = "", -1.0
    for png in await asyncio.to_thread(_prepare, data):
        text, conf = _parse_tsv(await _tesseract(png))
        score = conf * sum(ch.isalnum() for ch in text)
        if score > best_score:
            best, best_score = text, score
    return drop_noise(best)


async def read_post(post: Post) -> str:
    """All its pictures, in order, one text."""
    texts = []
    for slot, _kind in media_slots(post):
        path = preview_path(post.id, slot)
        if path.is_file():
            if text := await read_picture(path.read_bytes()):
                texts.append(text)
    return "\n".join(texts)


async def _save(post_id: int, text: str | None, status: str) -> None:
    async with SessionLocal() as s:
        await s.execute(update(Post).where(Post.id == post_id).values(extracted_text_from_media=text, ocr_status=status))
        await s.commit()


async def ocr_step() -> None:
    """Reads the pending posts, newest first, one by one; gives up on those whose pictures never came."""
    async with SessionLocal() as s:
        await s.execute(
            update(Post)
            .where(Post.ocr_status == "pending", Post.has_preview.is_(False), Post.created_at < utcnow() - PREVIEW_WAIT)
            .values(ocr_status=None)
        )
        await s.commit()
    while True:
        async with SessionLocal() as s:
            post = await s.scalar(
                select(Post).where(Post.ocr_status == "pending", Post.has_preview).order_by(Post.id.desc()).limit(1)
            )
        if post is None:
            return
        started = time.monotonic()
        try:
            text = await read_post(post)
        except Exception as exc:  # noqa: BLE001
            log.warning("ocr of post %s failed: %s", post.id, exc)
            await _save(post.id, None, "failed")
            continue
        await _save(post.id, text, "done")
        log.info("ocr of post %s: %s chars in %.1fs", post.id, len(text), time.monotonic() - started)


async def _recent(count: int) -> None:
    """Reads the latest posts with pictures right now and prints what came out (to see how it does)."""
    from .db import init_db

    await init_db()
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Post, Channel).join(Channel, Channel.id == Post.channel_id)
            .where(Post.has_preview, Post.deleted_at.is_(None)).order_by(Post.id.desc()).limit(count)
        )).all()
    total_started = time.monotonic()
    pictures = 0
    for post, ch in rows:
        started = time.monotonic()
        text = await read_post(post)
        pictures += len(media_slots(post))
        await _save(post.id, text, "done")
        url = f"https://t.me/{ch.username}/{post.msg_id}" if ch.username else ch.link
        print(f"=== post {post.id} | {ch.title or ch.link} | {url} | pictures: {len(media_slots(post))} | "
              f"{time.monotonic() - started:.1f}s")
        print(text or "(текст не найден)")
        print()
    elapsed = time.monotonic() - total_started
    print(f"--- {len(rows)} posts, {pictures} pictures, {elapsed:.0f}s ({elapsed / max(pictures, 1):.1f}s per picture)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--recent", type=int, default=30)
    logging.basicConfig(level=settings.log_level)
    asyncio.run(_recent(ap.parse_args().recent))
