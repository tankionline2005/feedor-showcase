from datetime import timedelta

from sqlalchemy import delete, select

from app import ocr
from app.config import settings
from app.db import SessionLocal, engine, init_db
from app.models import Channel, Post, utcnow
from app.services import preview_path

TSV = "\t".join(["level", "page_num", "block_num", "par_num", "line_num", "word_num",
                 "left", "top", "width", "height", "conf", "text"]) + "\n" + "\n".join(
    "\t".join(map(str, row)) for row in [
        (5, 1, 1, 1, 1, 1, 0, 0, 10, 10, 91.5, "ЯБЛОКИ,"),
        (5, 1, 1, 1, 1, 2, 0, 0, 10, 10, 88.0, "ВАРЕНЬЕ"),
        (5, 1, 1, 1, 1, 3, 0, 0, 10, 10, 30.0, "шум"),     # not sure enough
        (5, 1, 1, 1, 2, 1, 0, 0, 10, 10, 77.0, "сливы"),
        (5, 1, 1, 1, 2, 2, 0, 0, 10, 10, 95.0, "|"),       # no letter: junk
        (4, 1, 1, 1, 3, 0, 0, 0, 10, 10, -1, ""),          # a line row, not a word
    ]
)


def test_tesseract_output_becomes_lines_of_confident_words():
    text, conf = ocr._parse_tsv(TSV)
    assert text == "ЯБЛОКИ, ВАРЕНЬЕ\nсливы"
    assert round(conf, 1) == round((91.5 + 88 + 77) / 3, 1)
    assert ocr._parse_tsv(TSV.splitlines()[0]) == ("", 0.0)


async def test_new_posts_get_the_text_of_all_their_pictures(monkeypatch):
    await engine.dispose()
    await init_db()
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    async with SessionLocal() as s:
        await s.execute(delete(Channel).where(Channel.link == "@ocr_chan"))
        ch = Channel(link="@ocr_chan", username="ocr_chan", status="active")
        s.add(ch)
        await s.flush()
        album = Post(channel_id=ch.id, msg_id=1, posted_at=utcnow(), has_preview=True, ocr_status="pending",
                     previews=[{"k": "photo", "p": 0}, {"k": "photo", "p": 1}, {"k": "video", "p": 2}])
        old = Post(channel_id=ch.id, msg_id=2, posted_at=utcnow(), has_preview=True, ocr_status=None,
                   previews=[{"k": "photo", "p": 0}])
        lost = Post(channel_id=ch.id, msg_id=3, posted_at=utcnow(), has_preview=False, ocr_status="pending",
                    created_at=utcnow() - timedelta(hours=1))
        s.add_all([album, old, lost])
        await s.commit()
    pictures = {0: b"first", 1: b"second", 2: b"third"}
    for slot, data in pictures.items():
        preview_path(album.id, slot).write_bytes(data)
    preview_path(old.id).write_bytes(b"old")

    read = {b"first": "ЯБЛОКИ, ВАРЕНЬЕ\nсливы", b"second": "Омайгад апельсины", b"third": "а\nб\nВ\nГГГГ", b"old": "x"}
    seen: list[bytes] = []

    async def fake_read(data: bytes) -> str:
        seen.append(data)
        return read[data]

    monkeypatch.setattr(ocr, "read_picture", fake_read)
    await ocr.ocr_step()

    async with SessionLocal() as s:
        rows = {p.msg_id: (p.ocr_status, p.extracted_text_from_media) for p in (await s.execute(
            select(Post).where(Post.channel_id == album.channel_id)
        )).scalars()}
    assert rows[1] == ("done", "ЯБЛОКИ, ВАРЕНЬЕ\nсливы\nОмайгад апельсины\nа\nб\nВ\nГГГГ")
    assert rows[2] == (None, None)  # an old post is not read
    assert rows[3] == (None, None)  # its pictures never came: not waited for any more
    assert seen == [b"first", b"second", b"third"]


def test_scraps_of_photos_without_text_are_dropped():
    # an ordinary photo: Tesseract "sees" letters in its patterns
    assert ocr.drop_noise("i\n4\n13\ni=\nit\nи.\n7%.\nBy\nos\nAy") == ""
    assert ocr.drop_noise("КОМ") == ""  # a word, but too little to be the picture's text
    # real text keeps its lines; a scrap line among them goes
    assert ocr.drop_noise('а\n"Почта России" получит\nдопсредства за счет\nа') == '"Почта России" получит\nдопсредства за счет'
    assert ocr.drop_noise("Альфа Банк\nКассы само-\nобслуживания\n6") == "Альфа Банк\nКассы само-\nобслуживания"
    # sums keep their lines; short numbers of photo noise do not
    assert ocr.drop_noise("Total Value\n$711,404.57\n27%,\nSOL Balance") == "Total Value\n$711,404.57\nSOL Balance"
    assert ocr.drop_noise("%16777216\n13") == ""  # digits alone are not a picture's text
