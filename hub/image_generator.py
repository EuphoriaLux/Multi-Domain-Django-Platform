"""Self-contained 1080px social-card renderer for the Crush Hub planner."""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

SIZE = 1080
ROSE = (225, 29, 72)
PURPLE = (147, 51, 234)
WHITE = (255, 255, 255)
MUTED = (188, 190, 207)
CARD = (31, 29, 49, 225)
CARD_COPY = {
    "fr": {
        "tagline": "RENCONTRES AUTHENTIQUES",
        "community_badge": "COMMUNAUTÉ CRUSH.LU",
        "kpi_title": "CHIFFRES DE LA SEMAINE",
        "kpi_subtitle": "Les données réelles de notre communauté",
        "profile_badge": "CONSENTANT & VÉRIFIÉ",
        "interests": "CENTRES D'INTÉRÊT",
        "event_vibe": "AMBIANCE EN ÉVÉNEMENT",
    },
    "en": {
        "tagline": "AUTHENTIC CONNECTIONS",
        "community_badge": "CRUSH.LU COMMUNITY",
        "kpi_title": "THIS WEEK'S NUMBERS",
        "kpi_subtitle": "Real data from our community",
        "profile_badge": "CONSENTING & VERIFIED",
        "interests": "INTERESTS",
        "event_vibe": "EVENT VIBE",
    },
    "de": {
        "tagline": "ECHTE BEGEGNUNGEN",
        "community_badge": "CRUSH.LU COMMUNITY",
        "kpi_title": "ZAHLEN DER WOCHE",
        "kpi_subtitle": "Echte Daten aus unserer Community",
        "profile_badge": "EINVERSTANDEN & VERIFIZIERT",
        "interests": "INTERESSEN",
        "event_vibe": "EVENT-STIMMUNG",
    },
}


def _card_copy(language: str) -> dict[str, str]:
    return CARD_COPY.get(language, CARD_COPY["fr"])


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    filenames = (
        ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"]
    )
    roots = [
        Path("C:/Windows/Fonts"),
        Path("/usr/share/fonts/truetype/dejavu"),
        Path("/usr/share/fonts/truetype/liberation2"),
    ]
    for filename in filenames:
        for root in roots:
            path = root / filename
            if path.exists():
                return ImageFont.truetype(str(path), size)
        try:
            return ImageFont.truetype(filename, size)
        except OSError:
            continue
    logger.warning("No scalable system font found; using Pillow's fallback font")
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = str(text).split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines or [""]


def _gradient() -> Image.Image:
    image = Image.new("RGB", (SIZE, SIZE))
    pixels = image.load()
    for y in range(SIZE):
        ratio = y / (SIZE - 1)
        start = (13, 12, 27)
        end = (32, 15, 47)
        color = tuple(round(start[i] * (1 - ratio) + end[i] * ratio) for i in range(3))
        for x in range(SIZE):
            pixels[x, y] = color
    return image.convert("RGBA")


def _canvas() -> Image.Image:
    background = _gradient()
    draw = ImageDraw.Draw(background, "RGBA")
    draw.ellipse((-180, -220, 520, 480), fill=(*PURPLE, 52))
    draw.ellipse((690, 700, 1290, 1300), fill=(*ROSE, 55))
    return background


def _pill(draw, xy, text: str, *, fill=(*PURPLE, 72), font_size=27):
    font = _font(font_size, bold=True)
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0] + 42
    height = bbox[3] - bbox[1] + 28
    x, y = xy
    draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=fill)
    draw.text((x + 21, y + 10), text, fill=WHITE, font=font)
    return width, height


def _brand_footer(draw, *, language: str):
    copy = _card_copy(language)
    draw.line((70, 990, 1010, 990), fill=(255, 255, 255, 38), width=2)
    draw.text((70, 1017), "CRUSH.LU", font=_font(27, bold=True), fill=WHITE)
    draw.text(
        (1010, 1017),
        copy["tagline"],
        font=_font(19, bold=True),
        fill=MUTED,
        anchor="ra",
    )


def _save(image: Image.Image, prefix: str) -> str:
    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG", optimize=True)
    storage = storages["crush_media"]
    path = storage.save(
        f"social/{prefix}_{os.urandom(4).hex()}.png",
        ContentFile(output.getvalue()),
    )
    url = storage.url(path)
    if url.startswith("/"):
        return f"{settings.BACKEND_BASE_URL.rstrip('/')}{url}"
    return url


def generate_kpi_card(title=None, stats=None, *, language: str = "fr") -> str:
    copy = _card_copy(language)
    title = title or copy["kpi_title"]
    stats = stats or []
    image = _canvas()
    draw = ImageDraw.Draw(image, "RGBA")
    _pill(draw, (70, 68), copy["community_badge"], font_size=23)
    draw.text((70, 166), title, font=_font(58, bold=True), fill=WHITE)
    draw.text(
        (70, 238),
        copy["kpi_subtitle"],
        font=_font(28),
        fill=MUTED,
    )

    y = 330
    for item in stats[:3]:
        draw.rounded_rectangle((70, y, 1010, y + 180), radius=34, fill=CARD)
        value = str(item.get("value", ""))
        label = str(item.get("label", ""))
        draw.text(
            (115, y + 82), value, font=_font(55, bold=True), fill=WHITE, anchor="lm"
        )
        draw.text((430, y + 82), label, font=_font(31), fill=MUTED, anchor="lm")
        y += 205
    _brand_footer(draw, language=language)
    return _save(image, "kpi_card")


def generate_profile_card(
    first_name="Membre",
    age="30-34",
    region="Luxembourg",
    passions=None,
    bio_quote="",
    language: str = "fr",
) -> str:
    copy = _card_copy(language)
    passions = passions or []
    image = _canvas()
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle((55, 55, 1025, 970), radius=44, fill=CARD)
    _pill(draw, (90, 88), copy["profile_badge"], fill=(*ROSE, 185), font_size=22)

    initial = str(first_name)[:1].upper() or "C"
    draw.ellipse((90, 205, 290, 405), fill=(*PURPLE, 210))
    draw.text((190, 305), initial, font=_font(88, bold=True), fill=WHITE, anchor="mm")
    draw.text((335, 250), f"{first_name}, {age}", font=_font(55, bold=True), fill=WHITE)
    draw.text((335, 330), region, font=_font(31), fill=MUTED)

    draw.text((90, 475), copy["interests"], font=_font(24, bold=True), fill=MUTED)
    x, y = 90, 525
    for passion in passions[:4]:
        width, height = _pill(draw, (x, y), str(passion), font_size=23)
        x += width + 16
        if x > 900:
            x = 90
            y += height + 16

    draw.text((90, 690), copy["event_vibe"], font=_font(24, bold=True), fill=MUTED)
    draw.rounded_rectangle((90, 735, 990, 900), radius=28, fill=(255, 255, 255, 20))
    quote_font = _font(34)
    quote_lines = _wrap(draw, str(bio_quote), quote_font, 800)[:3]
    quote_y = 770
    for line in quote_lines:
        draw.text((135, quote_y), line, font=quote_font, fill=WHITE)
        quote_y += 43
    _brand_footer(draw, language=language)
    return _save(image, "profile_card")
