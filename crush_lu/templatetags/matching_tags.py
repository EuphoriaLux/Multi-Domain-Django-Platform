"""Template tags for the matching system (zodiac display, score labels)."""

from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from crush_lu.matching import (
    get_western_zodiac,
    get_chinese_zodiac,
    get_score_display,
    ZODIAC_SIGN_EMOJIS,
    CHINESE_ANIMAL_EMOJIS,
)

register = template.Library()


@register.simple_tag
def western_zodiac(profile):
    """Return western zodiac sign with emoji for a profile.

    Usage: {% western_zodiac profile %}
    """
    if not profile or not profile.date_of_birth:
        return ""
    sign = get_western_zodiac(profile.date_of_birth)
    if not sign:
        return ""
    emoji = ZODIAC_SIGN_EMOJIS.get(sign, "")
    return f"{emoji} {sign.capitalize()}"


@register.simple_tag
def chinese_zodiac(profile):
    """Return Chinese zodiac animal with emoji for a profile.

    Usage: {% chinese_zodiac profile %}
    """
    if not profile or not profile.date_of_birth:
        return ""
    animal = get_chinese_zodiac(profile.date_of_birth)
    if not animal:
        return ""
    emoji = CHINESE_ANIMAL_EMOJIS.get(animal, "")
    return f"{emoji} {animal.capitalize()}"


@register.simple_tag
def match_label(score):
    """Return a colored HTML label for a match score.

    Usage: {% match_label score %}
    """
    display = get_score_display(score)
    if not display:
        return ""
    return mark_safe(
        f'<span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs '
        f'font-semibold {display["color"]} {display["bg_color"]}">'
        f'{_(display["label"])}</span>'
    )
