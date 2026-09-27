"""
Locale-aware price formatting (finding 4-17).

One filter, one place: en renders "€25" (prefix, no cents on whole euros),
de/fr render "25 €" (suffix, comma decimal separator) — matching how French
and German readers actually write prices, instead of the hardcoded
"€{{ amount }}" that shipped a "€25,00" reading as English punctuation to a
DE/FR reader.

Usage:
    {% load crush_price %}
    {% if event.registration_fee > 0 %}{{ event.registration_fee|price }}{% else %}{% trans "Free" %}{% endif %}
"""

from decimal import Decimal, InvalidOperation

from django import template
from django.utils.translation import get_language

register = template.Library()

# Languages that write the currency symbol after the number with a comma
# decimal separator. Everything else (English) keeps the "€" prefix and a
# dot. Matches the site's three supported languages (en/de/fr).
_SUFFIX_LANGUAGES = {"de", "fr"}


@register.filter
def price(amount):
    """Format ``amount`` (a Decimal/float/str of EUR) for the active language."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        return amount

    lang = (get_language() or "en").split("-")[0]
    is_whole = value == value.to_integral_value()
    if is_whole:
        number = str(int(value))
    elif lang in _SUFFIX_LANGUAGES:
        number = f"{value:.2f}".replace(".", ",")
    else:
        number = f"{value:.2f}"

    if lang in _SUFFIX_LANGUAGES:
        return f"{number} €"
    return f"€{number}"
