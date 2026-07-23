"""
Server-Side High-Quality Graphic Card Generator for hub.crush.lu.

Generates professional 1080x1080px PNG image cards for social media posts:
- Auto text wrapping & dynamic line height (no text clipping)
- Background image support with dark gradient overlay veil
- Glassmorphism translucent pill badges for metadata
- Vibrant red/purple gradient CTA buttons & clean typography
"""
import io
import os
import logging
import requests
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

# Brand Colors (Crush.lu Palette)
COLOR_BG_DARK = (15, 15, 26)         # #0f0f1a
COLOR_CARD_BG = (30, 30, 48, 220)    # Glassmorphism dark
COLOR_PRIMARY = (225, 29, 72)        # #e11d48 (Rose)
COLOR_SECONDARY = (147, 51, 234)    # #9333ea (Purple)
COLOR_TEXT_WHITE = (255, 255, 255)  # #ffffff
COLOR_TEXT_MUTED = (180, 180, 200)  # #b4b4c8
COLOR_ACCENT_BLUE = (56, 189, 248)  # #38bdf8


def _get_font(size=32, bold=False):
    """Load system font with requested point size."""
    font_paths = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Wrap text to fit within max_width in pixels."""
    words = text.split()
    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]

    if current_line:
        lines.append(" ".join(current_line))

    return lines or [text]


def _create_base_canvas(background_url: str = None) -> Image.Image:
    """Create a 1080x1080px base canvas with background image or dark luxury gradient."""
    canvas = Image.new("RGBA", (1080, 1080), COLOR_BG_DARK + (255,))

    bg_img = None
    if background_url:
        try:
            if background_url.startswith("http"):
                resp = requests.get(background_url, timeout=4)
                if resp.status_code == 200:
                    bg_img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
            elif os.path.exists(background_url):
                bg_img = Image.open(background_url).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load background image: %s", exc)

    if bg_img:
        # Aspect fill crop to 1080x1080
        img_w, img_h = bg_img.size
        ratio = max(1080 / img_w, 1080 / img_h)
        new_w, new_h = int(img_w * ratio), int(img_h * ratio)
        bg_img = bg_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        crop_x = (new_w - 1080) // 2
        crop_y = (new_h - 1080) // 2
        bg_img = bg_img.crop((crop_x, crop_y, crop_x + 1080, crop_y + 1080))
        # Blur slightly for depth
        bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=6))
        canvas.paste(bg_img, (0, 0))

    # Apply gradient overlay veil (Dark top-to-bottom vignette)
    overlay = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
    draw_ov = ImageDraw.Draw(overlay)
    for y in range(1080):
        alpha = int(120 + (y / 1080) * 115) # 120 -> 235 opacity
        draw_ov.line([(0, y), (1080, y)], fill=(15, 15, 26, alpha))

    canvas = Image.alpha_composite(canvas, overlay)
    return canvas


def generate_event_flyer(title="Speed Dating Oenologique Casino 2000", date_str="Jeudi 30 Juillet @ 19:30", location="Casino 2000, Mondorf", background_url: str = None) -> str:
    """Generate a high-conversion 1080x1080px event flyer."""
    canvas = _create_base_canvas(background_url)
    draw = ImageDraw.Draw(canvas)

    # Outer Glass Frame Card
    card_rect = [(50, 50), (1030, 1030)]
    draw.rounded_rectangle(card_rect, radius=30, fill=COLOR_CARD_BG, outline=COLOR_PRIMARY, width=3)

    # Top Brand Header Pill
    draw.rounded_rectangle([(90, 90), (450, 150)], radius=15, fill=(225, 29, 72, 200), outline=COLOR_PRIMARY, width=1)
    font_brand = _get_font(26, bold=True)
    draw.text((120, 107), "CRUSH.LU  EVENT", fill=COLOR_TEXT_WHITE, font=font_brand)

    # Wrapped Title (Dynamic sizing & multi-line)
    font_title = _get_font(48, bold=True)
    lines = _wrap_text(title, font_title, max_width=860, draw=draw)

    title_y = 220
    for line in lines[:3]:
        draw.text((90, title_y), line, fill=COLOR_TEXT_WHITE, font=font_title)
        title_y += 65

    # Date Glass Pill
    font_info = _get_font(34, bold=False)
    draw.rounded_rectangle([(90, title_y + 30), (990, title_y + 110)], radius=16, fill=(40, 40, 60, 200), outline=COLOR_SECONDARY, width=2)
    draw.text((120, title_y + 50), f"DATE : {date_str}", fill=COLOR_ACCENT_BLUE, font=font_info)

    # Location Glass Pill
    draw.rounded_rectangle([(90, title_y + 130), (990, title_y + 210)], radius=16, fill=(40, 40, 60, 200), outline=COLOR_SECONDARY, width=2)
    draw.text((120, title_y + 150), f"LIEU : {location}", fill=COLOR_TEXT_WHITE, font=font_info)

    # Red/Purple Gradient CTA Button
    btn_y = 860
    btn_rect = [(90, btn_y), (650, btn_y + 100)]
    
    # Draw horizontal gradient fill
    btn_layer = Image.new("RGBA", (1080, 1080), (0, 0, 0, 0))
    draw_btn = ImageDraw.Draw(btn_layer)
    for x in range(90, 650):
        ratio = (x - 90) / 560
        r = int(COLOR_PRIMARY[0] + ratio * (COLOR_SECONDARY[0] - COLOR_PRIMARY[0]))
        g = int(COLOR_PRIMARY[1] + ratio * (COLOR_SECONDARY[1] - COLOR_PRIMARY[1]))
        b = int(COLOR_PRIMARY[2] + ratio * (COLOR_SECONDARY[2] - COLOR_PRIMARY[2]))
        draw_btn.line([(x, btn_y), (x, btn_y + 100)], fill=(r, g, b, 255))

    # Mask to rounded rectangle
    mask = Image.new("L", (1080, 1080), 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.rounded_rectangle(btn_rect, radius=20, fill=255)
    
    canvas.paste(btn_layer, (0, 0), mask)
    
    # Re-obtain draw handle after paste
    draw = ImageDraw.Draw(canvas)
    font_btn = _get_font(36, bold=True)
    draw.text((140, btn_y + 28), "RESERVER MA PLACE  ->", fill=COLOR_TEXT_WHITE, font=font_btn)

    # Footer Website Tag
    font_foot = _get_font(26, bold=False)
    draw.text((700, btn_y + 35), "crush.lu", fill=COLOR_TEXT_MUTED, font=font_foot)

    return _save_image_to_storage(canvas.convert("RGB"), "event_flyer")


def generate_kpi_card(title="CHIFFRES DE LA SEMAINE", stats=None) -> str:
    """Generate a high-conversion 1080x1080px KPI statistic graphic card."""
    if not stats:
        stats = [
            {"value": "+140", "label": "Nouveaux Inscrits cette semaine"},
            {"value": "52", "label": "Matchs Confirmés à Luxembourg"},
            {"value": "50 / 50", "label": "Taux de Parité Hommes / Femmes"},
        ]

    canvas = _create_base_canvas()
    draw = ImageDraw.Draw(canvas)

    # Frame Box
    draw.rounded_rectangle([(50, 50), (1030, 1030)], radius=30, fill=COLOR_CARD_BG, outline=COLOR_SECONDARY, width=3)

    # Top Brand Header
    draw.rounded_rectangle([(90, 90), (450, 150)], radius=15, fill=(147, 51, 234, 200), outline=COLOR_SECONDARY, width=1)
    draw.text((120, 107), "CRUSH.LU  METRICS", fill=COLOR_TEXT_WHITE, font=_get_font(26, bold=True))

    draw.text((90, 180), title, fill=COLOR_TEXT_WHITE, font=_get_font(42, bold=True))

    # Render 3 Stat Cards
    card_y = 260
    card_height = 200
    card_gap = 25

    for stat in stats[:3]:
        draw.rounded_rectangle(
            [(90, card_y), (990, card_y + card_height)],
            radius=20,
            fill=(35, 35, 55, 230),
            outline=COLOR_PRIMARY,
            width=2,
        )

        val_str = str(stat.get("value", ""))
        lbl_str = str(stat.get("label", ""))

        draw.text((130, card_y + 35), val_str, fill=COLOR_PRIMARY, font=_get_font(60, bold=True))
        
        # Wrapped Label
        lbl_lines = _wrap_text(lbl_str, _get_font(30, bold=False), max_width=500, draw=draw)
        lbl_y = card_y + 45
        for line in lbl_lines[:2]:
            draw.text((450, lbl_y), line, fill=COLOR_TEXT_WHITE, font=_get_font(30, bold=False))
            lbl_y += 40

        card_y += card_height + card_gap

    # Footer Branding
    draw.text(
        (90, 970),
        "Rejoignez la communauté sur crush.lu  |  Rencontres & Événements",
        fill=COLOR_TEXT_MUTED,
        font=_get_font(26, bold=False),
    )

    return _save_image_to_storage(canvas.convert("RGB"), "kpi_card")


def generate_profile_card(first_name="Sophie", age=31, region="Luxembourg-Ville", passions=None, bio_quote=None) -> str:
    """Generate an anonymized member profile card (1080x1080px)."""
    if not passions:
        passions = ["Œnologie", "Randonnée", "Gastronomie"]
    if not bio_quote:
        bio_quote = "Recherche une belle histoire basée sur la complicité et le rire."

    canvas = _create_base_canvas()
    draw = ImageDraw.Draw(canvas)

    # Frame Box
    draw.rounded_rectangle([(50, 50), (1030, 1030)], radius=30, fill=COLOR_CARD_BG, outline=COLOR_PRIMARY, width=3)

    # Top Pill Header
    draw.rounded_rectangle([(90, 90), (520, 150)], radius=15, fill=(225, 29, 72, 200), outline=COLOR_PRIMARY, width=1)
    draw.text((120, 107), "CRUSH.LU  MEMBRE VÉRIFIÉ", fill=COLOR_TEXT_WHITE, font=_get_font(26, bold=True))

    # Avatar Circle Gradient
    avatar_center = (210, 290)
    draw.ellipse([(130, 210), (290, 370)], fill=COLOR_SECONDARY)
    draw.text((185, 240), first_name[0].upper(), fill=COLOR_TEXT_WHITE, font=_get_font(76, bold=True))

    # Name + Age + Location
    draw.text((330, 225), f"{first_name}, {age} ans", fill=COLOR_TEXT_WHITE, font=_get_font(52, bold=True))
    draw.text((330, 300), f"Région : {region}", fill=COLOR_ACCENT_BLUE, font=_get_font(32, bold=False))

    # Passions Section
    draw.text((90, 420), "CENTRES D'INTÉRÊT :", fill=COLOR_TEXT_MUTED, font=_get_font(26, bold=True))
    pill_x = 90
    pill_y = 470

    for passion in passions[:4]:
        font_p = _get_font(28, bold=False)
        bbox = draw.textbbox((0, 0), passion, font=font_p)
        text_w = bbox[2] - bbox[0] + 50
        draw.rounded_rectangle(
            [(pill_x, pill_y), (pill_x + text_w, pill_y + 55)],
            radius=15,
            fill=(45, 45, 70, 220),
            outline=COLOR_SECONDARY,
            width=1,
        )
        draw.text((pill_x + 25, pill_y + 12), passion, fill=COLOR_TEXT_WHITE, font=font_p)
        pill_x += text_w + 20

    # Bio Quote
    draw.text((90, 580), "CE QU'IL / ELLE RECHERCHE :", fill=COLOR_TEXT_MUTED, font=_get_font(26, bold=True))
    
    quote_lines = _wrap_text(f"« {bio_quote} »", _get_font(36, bold=False), max_width=860, draw=draw)
    qy = 630
    for qline in quote_lines[:3]:
        draw.text((90, qy), qline, fill=COLOR_TEXT_WHITE, font=_get_font(36, bold=False))
        qy += 50

    # Footer Privacy Note
    draw.text(
        (90, 960),
        "Profil certifié par Crush.lu  |  Confidentialité & vie privée garanties",
        fill=COLOR_TEXT_MUTED,
        font=_get_font(24, bold=False),
    )

    return _save_image_to_storage(canvas.convert("RGB"), "profile_card")


def _save_image_to_storage(img: Image.Image, prefix="graphic") -> str:
    """Save PIL image to media storage and return public absolute URL."""
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    file_name = f"social/{prefix}_{os.urandom(4).hex()}.png"
    saved_path = default_storage.save(file_name, ContentFile(buffer.getvalue()))
    media_url = default_storage.url(saved_path)

    if media_url.startswith("/"):
        backend_host = getattr(settings, "BACKEND_BASE_URL", "http://localhost:8000")
        media_url = f"{backend_host.rstrip('/')}{media_url}"

    return media_url
