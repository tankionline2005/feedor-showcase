"""AI → «Публикации» → «Обложки» (covers.py): the project's cover style, the owner's — saved as it changes, its logo,
a preview of how a cover looks."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from .. import chat_posts, covers
from ..models import Post, Project, utcnow
from ..scope import tracked_channel_ids
from ..services import preview_path
from .deps import DB, Principal, Viewer

router = APIRouter(prefix="/api/covers", tags=["covers"])

LOGO_MOST = 2 * 1024 * 1024
SAMPLE = "Здесь будет заголовок поста — его первая строка или ваш текст"


def _owner_only(viewer: Principal) -> None:
    if not viewer.is_owner:
        raise HTTPException(status_code=403, detail="Обложки настраивает владелец проекта")


def _out(s) -> dict:
    return {"mode": s.mode, "template": s.template, "size": s.size, "accent": s.accent, "font": s.font,
            "position": s.position, "show_channel": s.show_channel, "has_logo": s.has_logo,
            "version": int(s.updated_at.timestamp()),
            "templates": [{"key": k, "name": v} for k, v in covers.TEMPLATES.items()],
            "fonts": [{"key": k, "name": v} for k, v in covers.FONTS.items()],
            "sizes": list(covers.SIZES), "accents": covers.ACCENTS, "modes": list(covers.MODES)}


@router.get("")
async def get_covers(viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    s = await covers.settings_of(db, viewer.project_id)
    await db.commit()
    return _out(s)


class CoversIn(BaseModel):
    mode: Literal["off", "always", "no_photo"] | None = None
    template: str | None = None
    size: str | None = None
    accent: str | None = None
    font: str | None = None
    position: Literal["top", "center", "bottom"] | None = None
    show_channel: bool | None = None


@router.put("")
async def save_covers(body: CoversIn, viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    s = await covers.settings_of(db, viewer.project_id)
    if body.template is not None:
        if body.template not in covers.TEMPLATES:
            raise HTTPException(status_code=422, detail="Такого шаблона нет")
        s.template = body.template
    if body.size is not None:
        if body.size not in covers.SIZES:
            raise HTTPException(status_code=422, detail="Такого формата нет")
        s.size = body.size
    if body.font is not None:
        if body.font not in covers.FONTS:
            raise HTTPException(status_code=422, detail="Такого шрифта нет")
        s.font = body.font
    if body.accent is not None:
        a = body.accent.strip().lower()
        if len(a) != 7 or a[0] != "#" or any(ch not in "0123456789abcdef" for ch in a[1:]):
            raise HTTPException(status_code=422, detail="Цвет — в виде #rrggbb")
        s.accent = a
    for field in ("mode", "position", "show_channel"):
        value = getattr(body, field)
        if value is not None:
            setattr(s, field, value)
    s.updated_at = utcnow()
    await db.commit()
    return _out(s)


@router.post("/logo")
async def upload_logo(file: UploadFile, viewer: Viewer, db: DB) -> dict:
    """The project's logo, in the cover's corner: PNG or JPEG, up to 2 MB (kept as PNG)."""
    _owner_only(viewer)
    data = await file.read(LOGO_MOST + 1)
    if len(data) > LOGO_MOST:
        raise HTTPException(status_code=422, detail="Логотип — не больше 2 МБ")
    import io

    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except OSError as exc:
        raise HTTPException(status_code=422, detail="Это не картинка — загрузите PNG или JPEG") from exc
    img.thumbnail((600, 600))
    path = covers.logo_path(viewer.project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGBA").save(path, "PNG")
    s = await covers.settings_of(db, viewer.project_id)
    s.has_logo, s.updated_at = True, utcnow()
    await db.commit()
    return _out(s)


@router.delete("/logo")
async def delete_logo(viewer: Viewer, db: DB) -> dict:
    _owner_only(viewer)
    covers.logo_path(viewer.project_id).unlink(missing_ok=True)
    s = await covers.settings_of(db, viewer.project_id)
    s.has_logo, s.updated_at = False, utcnow()
    await db.commit()
    return _out(s)


async def _sample_photo(db, project_id: int) -> bytes | None:
    """A recent photo of the project's channels, for the preview of the layouts with a photo."""
    ids = (await db.execute(select(Post.id).where(Post.channel_id.in_(tracked_channel_ids(project_id)),
                                                  Post.has_preview, Post.media_type == "photo")
                            .order_by(Post.id.desc()).limit(20))).scalars()
    for pid in ids:
        path = preview_path(pid, 0)
        if path.is_file():
            return path.read_bytes()
    return None


@router.get("/preview.jpg")
async def preview(viewer: Viewer, db: DB, template: str | None = None, size: str | None = None,
                  accent: str | None = None, font: str | None = None, position: str | None = None,
                  show_channel: bool | None = None, title: str | None = Query(default=None, max_length=200),
                  width: int | None = Query(default=None, ge=64, le=1280)) -> Response:
    """How a cover looks: the project's style, or the one asked about (the settings' gallery), on a sample title;
    `width`: smaller, for the gallery's tiles."""
    _owner_only(viewer)
    s = await covers.settings_of(db, viewer.project_id)
    style = covers.style_of(s)
    for key, value in (("template", template), ("size", size), ("accent", accent), ("font", font),
                       ("position", position), ("show_channel", show_channel)):
        if value is not None:
            style[key] = value
    project = await db.get(Project, viewer.project_id)
    logo = covers.logo_path(viewer.project_id)
    data = covers.render(style, title or SAMPLE, chat_posts.channel_title(project) if project else None,
                         await _sample_photo(db, viewer.project_id),
                         logo.read_bytes() if s.has_logo and logo.is_file() else None)
    if width:
        data = covers.shrink(data, width)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})
