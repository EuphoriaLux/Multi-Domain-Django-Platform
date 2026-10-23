"""Custom template tags for Delegations.lu app (OAuth and multilingual SEO)."""

import re

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()

LANGUAGE_PREFIX_PATTERN = re.compile(r"^/([a-z]{2})/")
DOMAIN = "https://delegations.lu"


def strip_language_prefix(path: str) -> str:
    """
    Remove language prefix from URL path if present.

    Examples:
        /fr/ -> /
        /de/ -> /
        / -> /
    """
    if not path:
        return "/"
    match = LANGUAGE_PREFIX_PATTERN.match(path)
    if match:
        lang_code = match.group(1)
        valid_langs = [code for code, _name in settings.LANGUAGES]
        if lang_code in valid_langs:
            stripped = path[3:]
            return stripped if stripped.startswith("/") else f"/{stripped}"
    return path


def get_path_without_language(request) -> str:
    """Return the current request path without its language prefix."""
    if not request:
        return "/"
    return strip_language_prefix(request.path)


@register.simple_tag(takes_context=True)
def hreflang_tags(context):
    """
    Generate hreflang alternate link tags for all supported languages (en, de, fr)
    plus x-default pointing to French (primary employment law language in Luxembourg).
    """
    request = context.get("request")
    base_path = get_path_without_language(request)

    tags = []
    for lang_code, _lang_name in settings.LANGUAGES:
        url = f"{DOMAIN}/{lang_code}{base_path}"
        tags.append(f'<link rel="alternate" hreflang="{lang_code}" href="{url}">')

    default_url = f"{DOMAIN}/fr{base_path}"
    tags.append(f'<link rel="alternate" hreflang="x-default" href="{default_url}">')
    return mark_safe("\n    ".join(tags))


@register.simple_tag(takes_context=True)
def canonical_url(context):
    """
    Generate self-referencing canonical URL for the current language page.
    If accessed at unprefixed root '/', canonicalizes to '/<active_lang>/'.
    """
    request = context.get("request")
    if not request:
        return f"{DOMAIN}/fr/"

    path = request.path or "/"
    if LANGUAGE_PREFIX_PATTERN.match(path):
        return f"{DOMAIN}{path}"

    lang_code = getattr(request, "LANGUAGE_CODE", None) or "en"
    valid_langs = {code for code, _name in settings.LANGUAGES}
    if lang_code not in valid_langs:
        lang_code = "en"
    base_path = strip_language_prefix(path)
    return f"{DOMAIN}/{lang_code}{base_path}"


@register.simple_tag(takes_context=True)
def localized_url(context, lang_code):
    """
    Generate URL for the current page in a specific language (en, de, fr).
    """
    request = context.get("request")
    base_path = get_path_without_language(request)
    return f"/{lang_code}{base_path}"


@register.simple_tag(takes_context=True)
def get_microsoft_login_url(context):
    """
    Safely get the Microsoft OAuth login URL.

    Returns the login URL if Microsoft provider is configured, otherwise None.
    This prevents SocialApp.DoesNotExist errors from crashing the template.
    """
    from allauth.socialaccount.models import SocialApp

    request = context.get("request")
    if not request:
        return None

    try:
        # Check if Microsoft SocialApp exists for any site
        if not SocialApp.objects.filter(provider="microsoft").exists():
            return None

        # Get the URL using allauth's helper
        from allauth.socialaccount.templatetags.socialaccount import provider_login_url

        return provider_login_url(context, "microsoft")
    except SocialApp.DoesNotExist:
        return None
    except Exception:
        return None
