"""
Server-Side Graphic Card Generator for hub.crush.lu.

Generates 1080x1080px PNG image cards for:
1. KPI / Statistics Cards (3 styled metrics)
2. Anonymized Profile Cards (Vector avatar + bio bullets)
3. Event Flyers (Banner photo + dark gradient + event metadata)

Returns relative media URL or uploads to Azure Blob Storage for Buffer dispatch.
"""
import io
import os
import logging
from PIL import Image, ImageDraw, ImageFont
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

# Brand Colors (Crush.lu Palette)
COLOR_BG_DARK = (15, 15, 26)       # #0f0f1a
COLOR_CARD_BG = (30, 30, 45)       # #1e1e2d
COLOR_PRIMARY = (225, 29, 72)       # #e11d48 (Rose)
COLOR_SECONDARY = (147, 51, 234)   # #9333ea (Purple)
COLOR_TEXT_WHITE = (255, 255, 255) # #ffffff
COLOR_TEXT_MUTED = (161, 161, 170) # #a1a1aa
COLOR_ACCENT_BLUE = (56, 189, 248) # #38bdf8


def _get_font(size=32):
    """Load default font with requested point size."""
    try:
        return ImageFont.truetype("arial.ttf", size)
    except IOError:
        return ImageFont.load_default()


def generate_kpi_card(title="COMMUNAUTÉ CRUSH.LU", stats=None) -> str:
    """Generate a 1080x1080px KPI statistic graphic card.
    
    stats: list of dicts [{'value': '+140', 'label': 'Nouveaux Célibataires'}]
    """
    if not stats:
        stats = [
            {"value": "+140", "label": "Nouveaux Inscrits (Semaine)"},
            {"value": "52", "label": "Matchs Confirmés"},
            {"value": "50/50", "label": "Taux de Parité Hommes/Femmes"},
        ]

    img = Image.new("RGB", (1080, 1080), COLOR_BG_DARK)
    draw = ImageDraw.Draw(img)

    # Top Brand Header
    font_header = _get_font(44)
    draw.text((60, 60), "◆ CRUSH.LU", fill=COLOR_PRIMARY, font=font_header)
    
    font_sub = _get_font(32)
    draw.text((60, 120), title, fill=COLOR_TEXT_MUTED, font=font_sub)

    # Render 3 Stat Cards
    card_y = 200
    card_height = 240
    card_gap = 30

    font_val = _get_font(64)
    font_lbl = _get_font(30)

    for stat in stats[:3]:
        # Draw card background rectangle
        draw.rounded_rectangle(
            [(60, card_y), (1020, card_y + card_height)],
            radius=16,
            fill=COLOR_CARD_BG,
            outline=COLOR_SECONDARY,
            width=2,
        )

        # Draw value and label
        val_str = str(stat.get("value", ""))
        lbl_str = str(stat.get("label", ""))

        draw.text((100, card_y + 50), val_str, fill=COLOR_PRIMARY, font=font_val)
        draw.text((100, card_y + 140), lbl_str, fill=COLOR_TEXT_WHITE, font=font_lbl)

        card_y += card_height + card_gap

    # Footer Branding
    font_foot = _get_font(26)
    draw.text(
        (60, 1010),
        "Rejoignez la communauté sur crush.lu • Rencontres & Événements à Luxembourg",
        fill=COLOR_TEXT_MUTED,
        font=font_foot,
    )

    return _save_image_to_storage(img, "kpi_card")


def generate_profile_card(first_name="Sophie", age=31, region="Luxembourg-Ville", passions=None, bio_quote=None) -> str:
    """Generate an anonymized member profile card (1080x1080px)."""
    if not passions:
        passions = ["Œnologie", "Randonnée", "Voyages"]
    if not bio_quote:
        bio_quote = "Recherche une relation authentique basée sur la complicité et l'humour."

    img = Image.new("RGB", (1080, 1080), COLOR_BG_DARK)
    draw = ImageDraw.Draw(img)

    # Header
    font_header = _get_font(40)
    draw.text((60, 60), "◆ MEMBRE DE LA SEMAINE", fill=COLOR_PRIMARY, font=font_header)

    # Main Card Box
    draw.rounded_rectangle(
        [(60, 140), (1020, 980)],
        radius=20,
        fill=COLOR_CARD_BG,
        outline=COLOR_PRIMARY,
        width=2,
    )

    # Avatar Circle Placeholder (Stylized Vector Circle)
    draw.ellipse([(120, 200), (280, 360)], fill=COLOR_SECONDARY)
    font_av = _get_font(72)
    draw.text((180, 240), first_name[0].upper(), fill=COLOR_TEXT_WHITE, font=font_av)

    # Name + Age + Location
    font_name = _get_font(52)
    draw.text((320, 220), f"{first_name}, {age} ans", fill=COLOR_TEXT_WHITE, font=font_name)

    font_loc = _get_font(32)
    draw.text((320, 290), f"📍 {region}", fill=COLOR_ACCENT_BLUE, font=font_loc)

    # Passions Pills
    draw.text((120, 420), "CENTRES D'INTÉRÊT :", fill=COLOR_TEXT_MUTED, font=_get_font(28))
    pill_x = 120
    pill_y = 470
    font_pill = _get_font(28)

    for passion in passions[:4]:
        text_w = len(passion) * 16 + 40
        draw.rounded_rectangle(
            [(pill_x, pill_y), (pill_x + text_w, pill_y + 50)],
            radius=12,
            fill=(45, 45, 65),
        )
        draw.text((pill_x + 20, pill_y + 10), passion, fill=COLOR_TEXT_WHITE, font=font_pill)
        pill_x += text_w + 20

    # Bio Quote
    draw.text((120, 580), "CE QU'IL / ELLE RECHERCHE :", fill=COLOR_TEXT_MUTED, font=_get_font(28))
    
    font_quote = _get_font(34)
    draw.text((120, 640), f"« {bio_quote} »", fill=COLOR_TEXT_WHITE, font=font_quote)

    # Footer Privacy Note
    draw.text(
        (120, 910),
        "🔒 Profil certifié Crush.lu • Identité & photos réelles protégées",
        fill=COLOR_TEXT_MUTED,
        font=_get_font(24),
    )

    return _save_image_to_storage(img, "profile_card")


def generate_event_flyer(title="Speed Dating Premium", date_str="Jeudi 30 Juillet @ 19:30", location="Casino 2000, Mondorf") -> str:
    """Generate an event flyer graphic card (1080x1080px)."""
    img = Image.new("RGB", (1080, 1080), COLOR_BG_DARK)
    draw = ImageDraw.Draw(img)

    # Dark Gradient Effect Box
    draw.rounded_rectangle([(40, 40), (1040, 1040)], radius=24, fill=COLOR_CARD_BG, outline=COLOR_PRIMARY, width=3)

    # Header
    font_brand = _get_font(44)
    draw.text((80, 80), "◆ CRUSH.LU ÉVÉNEMENT", fill=COLOR_PRIMARY, font=font_brand)

    # Event Title
    font_title = _get_font(56)
    draw.text((80, 240), title, fill=COLOR_TEXT_WHITE, font=font_title)

    # Date & Location Cards
    font_info = _get_font(38)
    draw.text((80, 420), f"📅 {date_str}", fill=COLOR_ACCENT_BLUE, font=font_info)
    draw.text((80, 500), f"📍 {location}", fill=COLOR_TEXT_WHITE, font=font_info)

    # CTA Button Box
    draw.rounded_rectangle([(80, 800), (600, 900)], radius=16, fill=COLOR_PRIMARY)
    draw.text((130, 830), "🎟️ Réserver ma place", fill=COLOR_TEXT_WHITE, font=_get_font(36))

    return _save_image_to_storage(img, "event_flyer")


def _save_image_to_storage(img: Image.Image, prefix="graphic") -> str:
    """Save PIL image to media storage and return public absolute URL."""
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    file_name = f"social/{prefix}_{os.urandom(4).hex()}.png"
    saved_path = default_storage.save(file_name, ContentFile(buffer.getvalue()))
    media_url = default_storage.url(saved_path)

    # If running locally and default_storage returns a relative path like /media/social/...
    if media_url.startswith("/"):
        backend_host = getattr(settings, "BACKEND_BASE_URL", "http://localhost:8000")
        media_url = f"{backend_host.rstrip('/')}{media_url}"

    return media_url
