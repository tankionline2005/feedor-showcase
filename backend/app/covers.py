"""Covers of posts: a picture with the post's title, made by the server in the project's style («Обложки»).

The project has one cover style (CoverSettings): a layout from TEMPLATES, a size (1:1, 4:5, 16:9), an accent
colour, a font from FONTS, where the title goes, the channel's name, a logo. A post with a cover gets it as its first
picture when it goes out (chat_posts._prepare): made then, from its title — its first line unless a person wrote
another — and the first photo of its sources for the layouts with a photo (without one the accent's tones take its
place).
Autoposts and rubrics' issues take a cover by the project's `mode`: never, always, or only without a photo.
"""

import io
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .config import settings as app_settings

log = logging.getLogger(__name__)

TEMPLATES = {
    "photo": "Фото и заголовок",  # the photo full, darkened on the title's side, the title on it
    "plate": "Фото и плашка",  # the photo, a plate of the accent colour with the title
    "card": "Карточка",  # the photo under a white card with the title
    "news": "Новость",  # the photo, a dark band with the title, the channel's name on an accent tag
    "split": "Фото сбоку",  # the photo on one side, a dark panel with the title on the other
    "blur": "Размытое фото",  # the photo blurred behind, the photo itself in a frame, the title
    "tint": "Тонированное фото",  # the photo in the accent colour's tones, the title on it
    "magazine": "Журнал",  # the photo full, the channel's name big on top as a masthead, the title
    "solid": "Цветной фон",  # the accent colour, a big title
    "gradient": "Градиент",  # from the accent colour to dark, the title
    "geometry": "Геометрия",  # the accent colour with big circles of its tones
    "glow": "Свечение",  # dark, soft lights of the accent colour, the title
    "poster": "Постер",  # dark, the title in capitals filling the picture
    "marker": "Маркер",  # light, the title's lines highlighted with the accent colour
    "minimal": "Минимализм",  # white, a thin accent line, the title
    "quote": "Цитата",  # light paper, a big quote mark, the title
}
PHOTO_TEMPLATES = {"photo", "plate", "card", "news", "split", "blur", "tint", "magazine"}
SIZES = {"1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1280, 720)}
POSITIONS = ("top", "center", "bottom")
MODES = ("off", "always", "no_photo")
ACCENTS = ["#0a84ff", "#ff3b30", "#ff9500", "#34c759", "#5e5ce6", "#ff2d55", "#1c1c1e", "#00a3a3"]
TITLE_LIMIT = 140

# a font: its name in «Обложки», its bold files and its regular ones tried in order — Debian's packages in the
# server's image (Dockerfile), then a Windows machine's (development) — its line height, and how much bigger a title
# in it starts (a narrow font fits more in a line)
_FONTS = {
    "sans": ("Строгий", ["liberation2/LiberationSans-Bold.ttf", "liberation/LiberationSans-Bold.ttf",
                         "dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf"],
             ["liberation2/LiberationSans-Regular.ttf", "dejavu/DejaVuSans.ttf", "arial.ttf"], 1.14),
    "serif": ("С засечками", ["paratype/PTF75F.ttf", "liberation2/LiberationSerif-Bold.ttf",
                              "dejavu/DejaVuSerif-Bold.ttf", "georgiab.ttf", "timesbd.ttf"],
              ["paratype/PTF55F.ttf", "liberation2/LiberationSerif-Regular.ttf", "georgia.ttf"], 1.14),
    "modern": ("Современный", ["roboto/unhinted/RobotoTTF/Roboto-Bold.ttf", "roboto/unhinted/Roboto-Bold.ttf",
                               "roboto/Roboto-Bold.ttf", "segoeuib.ttf"],
               ["roboto/unhinted/RobotoTTF/Roboto-Regular.ttf", "roboto/unhinted/Roboto-Regular.ttf",
                "roboto/Roboto-Regular.ttf", "segoeui.ttf"], 1.14),
    "geometric": ("Геометрический", ["montserrat/Montserrat-ExtraBold.ttf", "Montserrat-ExtraBold.ttf"],
                  ["montserrat/Montserrat-Medium.ttf", "Montserrat-Medium.ttf"], 1.16),
    "neat": ("Аккуратный", ["manrope/Manrope-ExtraBold.ttf", "Manrope-ExtraBold.ttf"],
             ["manrope/Manrope-Medium.ttf", "Manrope-Medium.ttf"], 1.14),
    "condensed": ("Узкий", ["paratype/PTN77F.ttf", "dejavu/DejaVuSansCondensed-Bold.ttf",
                            "liberation2/LiberationSansNarrow-Bold.ttf", "arialnb.ttf"],
                  ["paratype/PTN57F.ttf", "dejavu/DejaVuSansCondensed.ttf", "arialn.ttf"], 1.1, 1.15),
    "display": ("Плакатный", ["bebas-neue/BebasNeue-Bold.otf", "BebasNeue-Bold.otf", "BebasNeue-Regular.ttf"],
                ["bebas-neue/BebasNeue-Book.otf", "BebasNeue-Book.otf"], 1.0, 1.45),
    "slab": ("Брусковый", ["roboto/slab/RobotoSlab-Bold.otf", "RobotoSlab-Bold.ttf", "rockb.ttf"],
             ["roboto/slab/RobotoSlab-Regular.otf", "RobotoSlab-Regular.ttf"], 1.16),
    "rounded": ("Мягкий", ["comfortaa/Comfortaa-Bold.ttf", "Comfortaa-Bold.ttf"],
                ["comfortaa/Comfortaa-Regular.ttf", "Comfortaa-Regular.ttf"], 1.2),
    "tech": ("Технологичный", ["play/Play-Bold.ttf", "Play-Bold.ttf"], ["play/Play-Regular.ttf", "Play-Regular.ttf"],
             1.14),
}
FONTS = {key: spec[0] for key, spec in _FONTS.items()}
_FONT_DIRS = [Path("/usr/share/fonts/truetype"), Path("/usr/share/fonts/opentype"), Path("/usr/share/fonts"),
              Path("C:/Windows/Fonts")]

INK = (24, 24, 28)  # the dark text
WHITE = (255, 255, 255)


@lru_cache(maxsize=64)
def _font_file(kind: str, weight: str = "bold") -> str | None:
    """The font's file here; None when none of them is installed."""
    spec = _FONTS.get(kind)
    if spec is None:
        return None
    for name in spec[1] if weight == "bold" else spec[2]:
        for base in _FONT_DIRS:
            for path in (base / name, base / Path(name).name):
                if path.is_file():
                    return str(path)
    return None


@lru_cache(maxsize=256)
def _font(kind: str, size: int, weight: str = "bold") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_file(kind, weight) or _font_file("sans", weight) or _font_file(kind) or _font_file("sans")
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return 10, 132, 255


def _mix(a: tuple, b: tuple, t: float) -> tuple[int, int, int]:
    """From `a` (t = 0) to `b` (t = 1)."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _light(c: tuple) -> bool:
    """Dark text reads better on it than white."""
    r, g, b = (x / 255 for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.62


def _ink(bg: tuple) -> tuple[int, int, int]:
    return INK if _light(bg) else WHITE


@dataclass
class _Text:
    """A title laid out: its lines in a font, from one baseline to the next `step`; `height` from the capitals' top
    of the first line to the last baseline."""

    font: ImageFont.FreeTypeFont
    lines: list[str]
    step: int
    cap: int

    @property
    def height(self) -> int:
        return self.cap + (len(self.lines) - 1) * self.step


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if draw.textlength(test, font=font) <= width or not line:
            line = test
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit(draw, text: str, kind: str, width: int, height: int, start: int, least: int, max_lines: int = 6,
         weight: str = "bold") -> _Text:
    """The biggest font of `kind` the title fits `width` × `height` with; still too long at the least size: its
    last line ends with «…»."""
    spec = _FONTS.get(kind, _FONTS["sans"])
    leading = spec[3]
    size = round(start * (spec[4] if len(spec) > 4 else 1))
    while True:
        font = _font(kind, size, weight)
        lines = _wrap(draw, text, font, width)
        step = round(size * leading)
        cap = -font.getbbox("НH", anchor="ls")[1]
        fits = len(lines) <= max_lines and cap + (len(lines) - 1) * step <= height
        if fits or size <= least:
            break
        size = max(least, int(size * 0.94))
    rows = max(1, min(max_lines, (height - cap) // step + 1))
    if len(lines) > rows:
        lines = lines[:rows]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > width:
            last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
        lines[-1] = last.rstrip(",;:—-– ") + "…"
    return _Text(font, lines, step, cap)


def _draw(draw, t: _Text, x: int, top: int, fill, align: str = "left", width: int = 0) -> None:
    """The title from its first capitals' top at `top`."""
    for i, line in enumerate(t.lines):
        dx = x + (width - draw.textlength(line, font=t.font)) / 2 if align == "center" else x
        draw.text((dx, top + t.cap + i * t.step), line, font=t.font, fill=fill, anchor="ls")


def _shadowed(img: Image.Image, t: _Text, x: int, top: int, fill=WHITE, align: str = "left",
              width: int = 0) -> Image.Image:
    """The title over a photo: a soft dark shadow under it keeps it readable on a busy picture."""
    mask = Image.new("L", img.size, 0)
    _draw(ImageDraw.Draw(mask), t, x, top, 255, align, width)
    blur = max(2, t.font.size // 9)
    mask = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * 0.6))
    out = img.copy()
    out.paste(Image.new("RGB", img.size), (0, 0), mask)
    _draw(ImageDraw.Draw(out), t, x, top, fill, align, width)
    return out


def _at(position: str, top: int, bottom: int, height: int) -> int:
    """Where a block of `height` starts between `top` and `bottom` for the position."""
    if position == "top":
        return top
    if position == "center":
        return top + (bottom - top - height) // 2
    return bottom - height


def _gradient(w: int, h: int, corners: tuple) -> Image.Image:
    """Smooth colours from four corners: top left, top right, bottom left, bottom right."""
    grad = Image.new("RGB", (2, 2))
    grad.putdata(list(corners))
    return grad.resize((w, h), Image.BILINEAR)


def _glows(img: Image.Image, spots: list[tuple[float, float, float, tuple]], blur: float) -> Image.Image:
    """Soft round lights: (x, y, radius) as parts of the width, and a colour."""
    w, h = img.size
    layer = img.copy()
    d = ImageDraw.Draw(layer)
    for x, y, r, color in spots:
        d.ellipse([w * (x - r), h * y - w * r, w * (x + r), h * y + w * r], fill=color)
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _stand_in(w: int, h: int, accent: tuple) -> Image.Image:
    """No photo for a layout with one: the accent's tones with soft lights take its place."""
    img = _gradient(w, h, (_mix(accent, WHITE, 0.15), accent, _darker(accent, 0.55), _darker(accent, 0.3)))
    return _glows(img, [(0.8, 0.2, 0.28, _mix(accent, WHITE, 0.35)), (0.15, 0.85, 0.3, _darker(accent, 0.25))],
                  w * 0.08)


def _darker(c: tuple, k: float) -> tuple[int, int, int]:
    return tuple(max(0, int(x * k)) for x in c)


def _veil(img: Image.Image, position: str, start: int, end: int, strength: int = 225) -> Image.Image:
    """The photo darkened under a title from `start` to `end` (y): dark behind it and on to the picture's edge,
    fading out toward the rest; for the middle, around it."""
    w, h = img.size
    fade = int(h * 0.28)
    if position == "center":
        lo, hi = max(0, start - fade), min(h, end + fade)
        ramp = [0 if y < lo or y > hi else int(strength * 0.8 * min(1, (y - lo) / max(1, start - lo),
                                                                      (hi - y) / max(1, hi - end))) for y in range(h)]
        base = Image.blend(img, Image.new("RGB", (w, h)), 0.25)
    elif position == "top":
        ramp = [int(strength * min(1, max(0, (end + fade - y) / fade))) for y in range(h)]
        base = img
    else:
        ramp = [int(strength * min(1, max(0, (y - (start - fade)) / fade))) for y in range(h)]
        base = img
    col = Image.new("L", (1, h))
    col.putdata(ramp)
    out = base.copy()
    out.paste(Image.new("RGB", (w, h)), (0, 0), col.resize((w, h)))
    return out


def _rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.width - 1, img.height - 1], radius=radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def render(style: dict, title: str, channel: str | None, photo: bytes | None = None,
           logo: bytes | None = None) -> bytes:
    """The cover as a JPEG. `style`: template, size, accent, font, position, show_channel."""
    template = style.get("template") if style.get("template") in TEMPLATES else "gradient"
    w, h = SIZES.get(style.get("size", "1:1"), SIZES["1:1"])
    accent = _rgb(style.get("accent") or ACCENTS[0])
    kind = style.get("font") if style.get("font") in FONTS else "sans"
    position = style.get("position") if style.get("position") in POSITIONS else "bottom"
    title = " ".join((title or "").split())[:TITLE_LIMIT] or "Заголовок поста"
    if kind == "display":
        title = title.upper()
    channel = (channel or "").strip() or None if style.get("show_channel", True) else None
    pic = None
    if photo and template in PHOTO_TEMPLATES:
        try:
            pic = ImageOps.exif_transpose(Image.open(io.BytesIO(photo))).convert("RGB")
        except OSError:
            pic = None

    u = (w * h) ** 0.5 / 1080  # sizes are of a 1080 × 1080 picture
    pad = round(72 * u)
    small = _font(kind, round(34 * u), "regular")
    label = _font(kind, round(32 * u))
    big = round(w * (0.085 if w <= h else 0.07))
    least = round(36 * u)

    mark = None  # the logo: in the top right corner
    if logo:
        try:
            mark = Image.open(io.BytesIO(logo)).convert("RGBA")
            side = round(118 * u)
            mark.thumbnail((side, side))
        except OSError:
            log.info("cover: the logo is no picture")
            mark = None
    # what sits on top: the channel's name on the left, the logo on the right; a title on top starts below it
    head = round(34 * u * 0.75) if channel else 0
    head = max(head, mark.height if mark else 0)
    below_head = pad + (head + round(40 * u) if head else 0)

    def photo_fit(size: tuple[int, int]) -> Image.Image:
        return ImageOps.fit(pic, size, Image.LANCZOS) if pic is not None else _stand_in(*size, accent)

    def channel_text(draw, x, y, fill, font=small) -> None:
        if channel:
            draw.text((x, y), channel, font=font, fill=fill, anchor="lt")

    img = Image.new("RGB", (w, h), accent)
    draw = ImageDraw.Draw(img)
    text_w = w - 2 * pad

    if template == "photo":
        t = _fit(draw, title, kind, text_w, int(h * 0.48), big, least)
        bar = round(10 * u)
        y = _at(position, below_head + bar + round(28 * u), h - pad, t.height)
        img = _veil(photo_fit((w, h)), position, y - bar - round(28 * u), y + t.height)
        if position != "top" and (channel or mark):
            img = _veil(img, "top", 0, pad + head, 130)  # the name and the logo readable
        draw = ImageDraw.Draw(img)
        draw.rectangle([pad, y - bar - round(28 * u), pad + round(90 * u), y - round(28 * u)], fill=accent)
        channel_text(draw, pad, pad, WHITE)
        img = _shadowed(img, t, pad, y)

    elif template == "plate":
        room = mark.width + pad // 2 if mark is not None and position == "top" else 0
        t = _fit(draw, title, kind, text_w - room, int(h * 0.36), big, least)
        name = round(30 * u) + round(26 * u) if channel else 0
        plate = max(int(h * 0.34), t.height + name + 2 * pad + round(8 * u))
        on_top = position == "top"
        plate_y = 0 if on_top else h - plate
        img.paste(photo_fit((w, h - plate)), (0, plate if on_top else 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, plate_y, w, plate_y + plate], fill=accent)
        ink = _ink(accent)
        y = plate_y + (plate - t.height - name) // 2
        channel_text(draw, pad, y, _mix(ink, accent, 0.3), label)
        _draw(draw, t, pad, y + name, ink)

    elif template == "card":
        img = photo_fit((w, h))
        draw = ImageDraw.Draw(img)
        inner = pad
        t = _fit(draw, title, kind, text_w - 2 * inner - round(12 * u), int(h * 0.4), round(big * 0.82), least)
        name = round(30 * u) + round(22 * u) if channel else 0
        card_h = t.height + name + 2 * inner
        cy = _at(position, below_head, h - pad, card_h)
        draw.rounded_rectangle([pad, cy, w - pad, cy + card_h], radius=round(28 * u), fill=WHITE)
        draw.rounded_rectangle([pad + inner, cy + inner, pad + inner + round(8 * u), cy + card_h - inner],
                               radius=round(4 * u), fill=accent)
        x = pad + inner + round(32 * u)
        channel_text(draw, x, cy + inner, _darker(accent, 0.85) if _light(accent) else accent, label)
        _draw(draw, t, x, cy + inner + name, INK)

    elif template == "news":
        t = _fit(draw, title, kind, text_w, int(h * 0.34), round(big * 0.9), least)
        tag_h = round(54 * u)
        band = max(int(h * 0.36), t.height + 2 * pad + (tag_h // 2 if channel else 0))
        on_top = position == "top"
        band_y = 0 if on_top else h - band
        img.paste(photo_fit((w, h - band)), (0, band if on_top else 0))
        draw = ImageDraw.Draw(img)
        dark = (18, 18, 22)
        draw.rectangle([0, band_y, w, band_y + band], fill=dark)
        edge = band_y + band if on_top else band_y
        if channel:
            tw = draw.textlength(channel, font=label)
            ty = edge - tag_h // 2
            draw.rounded_rectangle([pad, ty, pad + tw + round(44 * u), ty + tag_h], radius=tag_h // 2, fill=accent)
            draw.text((pad + round(22 * u), ty + tag_h // 2), channel, font=label, fill=_ink(accent), anchor="lm")
            channel = None
        else:
            draw.rectangle([0, edge - round(5 * u), w, edge + round(5 * u)], fill=accent)
        space_top = band_y + (tag_h // 2 if not on_top else 0)
        space_bottom = band_y + band - (tag_h // 2 if on_top else 0)
        _draw(draw, t, pad, space_top + (space_bottom - space_top - t.height) // 2, WHITE)

    elif template == "split":
        dark = (18, 18, 22)
        landscape = w > h
        side = int(w * (0.46 if landscape else 0.42))
        panel_w = w - side
        img.paste(photo_fit((side, h)), (panel_w, 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, panel_w, h], fill=dark)
        draw.rectangle([panel_w - round(8 * u), 0, panel_w, h], fill=accent)
        inner_w = panel_w - 2 * pad
        t = _fit(draw, title, kind, inner_w, h - below_head - pad - round(40 * u), round(big * 0.82), least, 8)
        bar = round(8 * u)
        y = _at(position, below_head + bar + round(26 * u), h - pad, t.height)
        draw.rectangle([pad, y - bar - round(26 * u), pad + round(70 * u), y - round(26 * u)], fill=accent)
        _draw(draw, t, pad, y, WHITE)
        channel_text(draw, pad, pad, (200, 200, 205))

    elif template == "blur":
        bg = photo_fit((w, h)).filter(ImageFilter.GaussianBlur(40 * u))
        img = Image.blend(bg, Image.new("RGB", (w, h)), 0.45)
        draw = ImageDraw.Draw(img)
        radius = round(26 * u)
        if w > h:  # the photo on the left, the title on the right
            frame = (int(w * 0.44), h - 2 * pad)
            img.paste(_rounded(photo_fit(frame), radius), (pad, pad), _rounded(photo_fit(frame), radius))
            x = pad + frame[0] + pad
            t = _fit(draw, title, kind, w - x - pad, h - below_head - pad, round(big * 0.8), least, 7)
            _draw(draw, t, x, _at(position, below_head, h - pad, t.height), WHITE)
            channel_text(draw, x, pad, (225, 225, 230))
        else:
            t = _fit(draw, title, kind, text_w, int(h * 0.3), round(big * 0.85), least, 4)
            frame_h = h - below_head - pad - t.height - round(56 * u)
            frame = photo_fit((text_w, frame_h))
            title_first = position == "top"
            fy = below_head + (t.height + round(56 * u) if title_first else 0)
            img.paste(_rounded(frame, radius), (pad, fy), _rounded(frame, radius))
            ty = below_head if title_first else fy + frame_h + round(56 * u)
            _draw(draw, t, pad, ty, WHITE)
            channel_text(draw, pad, pad, (225, 225, 230))

    elif template == "tint":
        gray = ImageOps.autocontrast(ImageOps.grayscale(photo_fit((w, h))), cutoff=1)
        img = ImageOps.colorize(gray, black=_darker(accent, 0.18), white=_mix(accent, WHITE, 0.55),
                                mid=_darker(accent, 0.75))
        t = _fit(draw, title, kind, text_w, int(h * 0.5), big, least)
        y = _at(position, below_head, h - pad, t.height)
        img = _veil(img, position, y, y + t.height, 190)
        draw = ImageDraw.Draw(img)
        channel_text(draw, pad, pad, WHITE)
        img = _shadowed(img, t, pad, y)

    elif template == "magazine":
        masthead = None
        if channel:
            masthead = _fit(draw, channel.upper(), kind, text_w - (mark.width + pad if mark else 0),
                            round(100 * u), round(96 * u), round(40 * u), 1)
        top = pad + (masthead.height + round(70 * u) if masthead else max(head, 0) + round(40 * u))
        t = _fit(draw, title, kind, text_w, int(h * 0.42), big, least)
        y = _at(position, top, h - pad, t.height)
        img = _veil(_veil(photo_fit((w, h)), "top", 0, top - round(30 * u), 170), position, y, y + t.height)
        draw = ImageDraw.Draw(img)
        if masthead:
            _draw(draw, masthead, pad, pad, WHITE)
            draw.rectangle([pad, pad + masthead.height + round(22 * u), w - pad,
                            pad + masthead.height + round(27 * u)], fill=accent)
            channel = None
        img = _shadowed(img, t, pad, y)

    elif template == "solid":
        ink = _ink(accent)
        draw.ellipse([w * 0.62, -h * 0.18, w * 1.25, h * 0.45], fill=_mix(accent, ink, 0.07))
        t = _fit(draw, title, kind, text_w, int(h * 0.6), round(big * 1.08), least)
        bottom = h - pad - (round(64 * u) if channel else 0)
        center = position == "center"
        _draw(draw, t, pad, _at(position, below_head, bottom, t.height), ink, "center" if center else "left",
              text_w)
        if channel:
            draw.text((w // 2 if center else pad, h - pad), channel, font=small, fill=_mix(ink, accent, 0.25),
                      anchor="ms" if center else "ls")

    elif template == "gradient":
        img = _gradient(w, h, (_mix(accent, WHITE, 0.08), _darker(accent, 0.7), _darker(accent, 0.62),
                               _darker(accent, 0.28)))
        draw = ImageDraw.Draw(img)
        t = _fit(draw, title, kind, text_w, int(h * 0.58), big, least)
        bar = round(10 * u)
        y = _at(position, below_head + bar + round(30 * u), h - pad, t.height)
        draw.rectangle([pad, y - bar - round(30 * u), pad + round(100 * u), y - round(30 * u)], fill=WHITE)
        _draw(draw, t, pad, y, WHITE)
        channel_text(draw, pad, pad, WHITE)

    elif template == "geometry":
        ink = _ink(accent)
        lighter, darker = _mix(accent, WHITE, 0.18), _darker(accent, 0.82)
        draw.ellipse([w * 0.55, -w * 0.25, w * 1.3, w * 0.5], fill=lighter)
        draw.ellipse([-w * 0.3, h - w * 0.35, w * 0.35, h + w * 0.3], fill=darker)
        draw.ellipse([w * 0.72, h * 0.62, w * 0.86, h * 0.62 + w * 0.14], outline=ink, width=round(6 * u))
        t = _fit(draw, title, kind, int(text_w * 0.9), int(h * 0.55), big, least)
        _draw(draw, t, pad, _at(position, below_head, h - pad - round(20 * u), t.height), ink)
        channel_text(draw, pad, pad, ink)

    elif template == "glow":
        base = (12, 12, 16)
        img = _glows(Image.new("RGB", (w, h), base),
                     [(0.85, 0.12, 0.32, accent), (0.1, 0.95, 0.26, _darker(accent, 0.7)),
                      (0.55, 0.55, 0.14, _mix(accent, base, 0.55))], w * 0.12)
        draw = ImageDraw.Draw(img)
        t = _fit(draw, title, kind, text_w, int(h * 0.58), big, least)
        _draw(draw, t, pad, _at(position, below_head, h - pad, t.height), WHITE)
        channel_text(draw, pad, pad, _mix(accent, WHITE, 0.45), label)

    elif template == "poster":
        bg = _darker(accent, 0.16)
        img = Image.new("RGB", (w, h), bg)
        draw = ImageDraw.Draw(img)
        bar = round(16 * u)
        inner_x = pad + bar + round(36 * u)
        bottom = h - pad - (round(64 * u) if channel else 0)
        t = _fit(draw, title.upper(), kind, w - inner_x - pad, bottom - below_head, round(big * 1.5), least, 7)
        y = _at(position, below_head, bottom, t.height)
        draw.rectangle([pad, y, pad + bar, y + t.height], fill=accent)
        _draw(draw, t, inner_x, y, WHITE)
        if channel:
            draw.text((pad, h - pad), channel.upper(), font=label, fill=accent if not _light(bg) else INK,
                      anchor="ls")
            channel = None

    elif template == "marker":
        paper = (250, 249, 246)
        img = Image.new("RGB", (w, h), paper)
        draw = ImageDraw.Draw(img)
        hi = _mix(accent, paper, 0.45)
        t = _fit(draw, title, kind, text_w - round(24 * u), int(h * 0.6), big, least)
        bottom = h - pad - (round(64 * u) if channel else 0)
        y = _at(position, below_head, bottom, t.height)
        x = pad + round(12 * u)
        size = t.font.size
        for i, line in enumerate(t.lines):
            base = y + t.cap + i * t.step
            draw.rectangle([x - round(12 * u), base - round(t.cap * 0.62), x + draw.textlength(line, font=t.font)
                            + round(12 * u), base + round(size * 0.14)], fill=hi)
        _draw(draw, t, x, y, INK)
        if channel:
            r = round(9 * u)
            draw.ellipse([pad, h - pad - 2 * r - round(4 * u), pad + 2 * r, h - pad - round(4 * u)], fill=accent)
            draw.text((pad + 2 * r + round(16 * u), h - pad), channel, font=small, fill=(90, 90, 96), anchor="ls")

    elif template == "minimal":
        img = Image.new("RGB", (w, h), WHITE)
        draw = ImageDraw.Draw(img)
        bottom = h - pad - (round(80 * u) if channel else 0)
        t = _fit(draw, title, kind, text_w, int(h * 0.58), round(big * 0.9), least)
        y = _at(position, below_head + round(40 * u), bottom, t.height)
        draw.rectangle([pad, y - round(44 * u), pad + round(56 * u), y - round(38 * u)], fill=accent)
        _draw(draw, t, pad, y, INK)
        if channel:
            draw.line([pad, h - pad - round(56 * u), w - pad, h - pad - round(56 * u)], fill=(229, 229, 234),
                      width=max(1, round(2 * u)))
            draw.text((pad, h - pad), channel, font=small, fill=(110, 110, 116), anchor="ls")

    else:  # quote
        paper = (246, 244, 240)
        img = Image.new("RGB", (w, h), paper)
        draw = ImageDraw.Draw(img)
        mark_font = _font("serif", round(300 * u))
        draw.text((pad - round(10 * u), below_head - round(10 * u)), "“", font=mark_font, fill=accent, anchor="lt")
        quote_kind = "serif" if kind == "sans" else kind
        top = below_head + round(190 * u)
        bottom = h - pad - (round(110 * u) if channel else round(50 * u))
        t = _fit(draw, title, quote_kind, text_w, bottom - top, round(big * 0.9), least)
        _draw(draw, t, pad, _at(position, top, bottom, t.height), INK)
        draw.rectangle([pad, h - pad - round(80 * u), pad + round(80 * u), h - pad - round(72 * u)], fill=accent)
        if channel:
            draw.text((pad, h - pad), channel, font=small, fill=(90, 90, 96), anchor="ls")
            channel = None

    if mark is not None:  # the logo on top of all
        img.paste(mark, (w - pad - mark.width, pad), mark)
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


def shrink(jpeg: bytes, width: int) -> bytes:
    """A cover made smaller: a tile of the gallery needs no full picture."""
    img = Image.open(io.BytesIO(jpeg))
    img.thumbnail((width, width * 2), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


# --- the project's style, and a post's cover ---------------------------------------------------------------------

def logo_path(project_id: int) -> Path:
    return app_settings.media_dir / "covers" / f"logo_{project_id}.png"


def style_of(s) -> dict:
    """A CoverSettings row as render() takes it."""
    return {"template": s.template, "size": s.size, "accent": s.accent, "font": s.font, "position": s.position,
            "show_channel": s.show_channel}


async def settings_of(session, project_id: int):
    from .models import CoverSettings

    found = await session.get(CoverSettings, project_id)
    if found is None:
        found = CoverSettings(project_id=project_id)
        session.add(found)
        await session.flush()
    return found


def first_line(html_text: str) -> str:
    """The post's title for its cover: its first line — its first sentence, when the line is a whole paragraph."""
    import re

    from . import tg_html

    line = next((x.strip() for x in tg_html.plain(html_text).splitlines() if x.strip()), "")
    if len(line) > 100:
        end = re.search(r"[.!?…](?=\s)", line[:TITLE_LIMIT])
        if end:
            line = line[:end.start() + 1]
    return line[:TITLE_LIMIT]


async def for_post(session, post) -> bytes:
    """The post's cover now: its title (a person's, or its first line), the project's style, the first photo of its
    sources, the project's logo."""
    from . import chat_posts
    from .models import Project
    from .services import preview_path

    style = await settings_of(session, post.project_id)
    project = await session.get(Project, post.project_id)
    photo = None
    for c in chat_posts.candidates(await chat_posts.sources_of(session, post.id)):
        path = preview_path(c["post_id"], c["slot"])
        if c["kind"] == "photo" and not c.get("why") and path.is_file():
            photo = path.read_bytes()
            break
    logo = logo_path(post.project_id)
    return render(style_of(style), post.cover_title or first_line(post.text),
                  chat_posts.channel_title(project) if project else None, photo,
                  logo.read_bytes() if style.has_logo and logo.is_file() else None)


async def apply_mode(session, post) -> None:
    """An autopost or a rubric's issue takes a cover by the project's mode: always, or only without a photo."""
    style = await settings_of(session, post.project_id)
    if style.mode == "always" or (style.mode == "no_photo" and not (post.media or [])):
        post.cover = True
