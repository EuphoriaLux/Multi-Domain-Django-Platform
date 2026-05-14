"""
Crush.lu brand-color template tags.

Single source of truth for the brand colors used in inline-styled HTML — primarily
email templates and the allauth confirmation page — where neither @theme tokens
nor utility classes are available (email clients strip <link> tags and most do
not support CSS custom properties).

The hex values mirror the canonical @theme block in
``tailwind-src/crush_lu/tailwind-input.css``. When you change a brand color,
update both this file and that one.

Usage:
    {% load crush_brand %}
    {% brand_colors as brand %}
    <a style="background:{{ brand.purple }};color:{{ brand.on_brand }};">Confirm</a>
"""
from django import template

register = template.Library()


BRAND = {
    # Primary palette — matches @theme --color-crush-* in tailwind-input.css
    "purple": "#9b59b6",
    "purple_dark": "#8e44ad",
    "purple_light": "#af7ac5",
    "pink": "#ff6b9d",
    "pink_dark": "#d94d7b",
    "pink_light": "#ff8fb3",
    # Gradient (matches --gradient-crush-primary)
    "gradient_start": "#9b59b6",
    "gradient_end": "#ff6b9d",
    "gradient": "linear-gradient(135deg, #9b59b6, #ff6b9d)",
    # Surfaces & text (Tailwind gray scale used across email clients)
    "on_brand": "#ffffff",
    "text": "#111827",
    "muted": "#6b7280",
    "subtle": "#9ca3af",
    "bg": "#f3f4f6",
    "card": "#ffffff",
    "divider": "#f3f4f6",
    # Tinted purple background for icon chips
    "purple_tint": "#ede9fe",
}


@register.simple_tag
def brand_colors():
    """Return the Crush.lu brand color dict for use in inline styles."""
    return BRAND
