"""
Template tags for the Crush Connect feature flag.

Lets templates conditionally render Crush Connect UI without each template
having to import settings or reach into the request.

Usage:
    {% load crush_connect_tags %}

    {% if user|crush_connect_visible %}
        <a href="{% url 'crush_lu:crush_connect_home' %}">Crush Connect</a>
    {% endif %}
"""

from django import template
from django.conf import settings

register = template.Library()


@register.filter
def crush_connect_visible(user):
    """
    True if the Crush Connect entry point (teaser or app) should be visible.

    Visible when the global launch flag is on, OR the user is staff (so
    internal review and beta walkthroughs work before public launch).
    Eligibility (approved profile + attended event) is enforced at the
    view layer; this filter only controls nav/CTA visibility.
    """
    if getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return True
    return bool(user and user.is_authenticated and user.is_staff)


@register.filter
def crush_connect_nav_visible(user):
    """
    True if the **Today's Drop** nav tab should be visible to this user.

    Stricter than ``crush_connect_visible``: we only show the tab when the
    user can actually reach the Drop page without bouncing through the teaser.
    That means a Connect-onboarded membership is required. Staff bypass keeps
    internal review working before any user has onboarded.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    if not getattr(settings, "CRUSH_CONNECT_LAUNCHED", False):
        return False
    membership = getattr(user, "crush_connect_membership", None)
    return bool(membership and membership.is_onboarded)


@register.simple_tag
def crush_connect_launched():
    """Raw flag accessor for templates that need it directly."""
    return getattr(settings, "CRUSH_CONNECT_LAUNCHED", False)


# Per-option decoration for the shared _radio_tiles.html partial, keyed by the
# field's html_name so the partial needs nothing beyond the bound field.
# "prefer_not_say" deliberately shares one emoji across all fields so it reads
# as a single consistent gesture everywhere (including the height opt-out pill).
CHOICE_EMOJI = {
    "work_field": {
        "finance": "🏦",
        "eu_public": "🏛️",
        "it": "💻",
        "health": "🩺",
        "education": "📚",
        "legal": "⚖️",
        "construction": "🏗️",
        "hospitality": "🍽️",
        "logistics": "🚚",
        "creative": "🎨",
        "entrepreneur": "🚀",
        "student": "🎓",
        "other": "💼",
        "prefer_not_say": "🤫",
    },
    "education_level": {
        "high_school": "🏫",
        "vocational": "🛠️",
        "bachelor": "🎓",
        "master": "📜",
        "doctorate": "🔬",
        "prefer_not_say": "🤫",
    },
    "smoking": {
        "no": "🚭",
        "occasionally": "💨",
        "yes": "🚬",
        "prefer_not_say": "🤫",
    },
    "drinking": {
        "no": "🚱",
        "socially": "🥂",
        "regularly": "🍷",
        "prefer_not_say": "🤫",
    },
}

# Optional second line under a tile's label (lazily translated strings).
# Empty for the life section; future adopters (e.g. lifestyle/ideal-match
# steps) add entries here instead of passing dicts through the view.
CHOICE_DESC = {}


@register.filter
def choice_emoji(field_name, value):
    """Emoji for a choice tile: ``field.html_name|choice_emoji:value``."""
    return CHOICE_EMOJI.get(field_name, {}).get(value, "")


@register.filter
def choice_desc(field_name, value):
    """Description line for a choice tile: ``field.html_name|choice_desc:value``."""
    return str(CHOICE_DESC.get(field_name, {}).get(value, ""))
