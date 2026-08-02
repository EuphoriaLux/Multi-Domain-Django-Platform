"""
Centralized i18n utilities for Crush.lu

This module provides helper functions for internationalization that read from
Django settings rather than using hardcoded language lists.
"""

from django.conf import settings
from django.urls import reverse
from django.utils.translation import get_language, override


def get_supported_language_codes():
    """
    Get list of supported language codes from Django settings.

    Returns:
        list: Language codes (e.g., ['en', 'de', 'fr'])
    """
    return [code for code, name in settings.LANGUAGES]


def is_valid_language(lang_code):
    """
    Check if a language code is valid (supported by the application).

    Args:
        lang_code: Language code to validate (e.g., 'en', 'de', 'fr')

    Returns:
        bool: True if the language is supported, False otherwise
    """
    if not lang_code:
        return False
    return lang_code in get_supported_language_codes()


def validate_language(lang_code, default='en'):
    """
    Validate a language code and return it if valid, otherwise return default.

    Args:
        lang_code: Language code to validate
        default: Default language code to return if invalid

    Returns:
        str: Valid language code
    """
    if is_valid_language(lang_code):
        return lang_code
    return default


def get_user_preferred_language(user=None, request=None, default='en'):
    """
    Get the preferred language for a user, with fallback priority:
    1. User's CrushProfile.preferred_language
    2. Request's LANGUAGE_CODE
    3. Session language (via get_language())
    4. Default

    Args:
        user: Django User object (optional)
        request: HTTP request object (optional)
        default: Default language code

    Returns:
        str: Valid language code
    """
    # Priority 1: User's profile preferred language
    if user and hasattr(user, 'crushprofile') and user.crushprofile:
        profile_lang = getattr(user.crushprofile, 'preferred_language', None)
        if is_valid_language(profile_lang):
            return profile_lang

    # Priority 2: Request's LANGUAGE_CODE
    if request:
        request_lang = getattr(request, 'LANGUAGE_CODE', None)
        if is_valid_language(request_lang):
            return request_lang

    # Priority 3: Session/thread language
    session_lang = get_language()
    if is_valid_language(session_lang):
        return session_lang

    # Priority 4: Default
    return default


def get_onscreen_language(user=None, request=None, default='en'):
    """
    Get the language for text the user is reading RIGHT NOW, on a route that
    sits OUTSIDE ``i18n_patterns`` and so carries no ``/fr/`` prefix of its own.

    Deliberately the inverse of ``get_user_preferred_language``'s precedence.
    That helper exists for EMAIL, where no request is available and the stored
    profile value is the only signal there is, so it must win. On screen the
    opposite holds — and because ``CrushProfile.preferred_language`` is
    ``default="en"`` and non-blank, preferring it pins English on every member
    who never opened the setting, including one reading the site in French.
    Measured, not assumed: on the SumUp return page, preferring the profile
    turned a French confirmation (and a /fr/ redirect) into an English one.

    So the question is not "what is stored" but "did the member answer". A
    stored ``de`` or ``fr`` is unambiguous — nobody arrives at those by
    accident — and wins outright. A stored ``"en"`` wins only when
    ``language_explicitly_set`` says it was chosen; otherwise it is the
    untouched default and we defer to ``request.LANGUAGE_CODE``, i.e. whatever
    LocaleMiddleware resolved from the cookie or Accept-Language.

    That flag is the whole reason English is answerable at all. It is written
    by the language switcher and only there, so it means "the member picked
    this", not "something inferred it" — signup reads ``request.LANGUAGE_CODE``
    and LuxID reads a locale claim, and neither sets it. One wrinkle it has to
    carry: the switcher used to write only when the value CHANGED, which for an
    English member choosing English is no change at all, so the pick vanished.
    It now writes whenever either half is stale.

    Members whose profiles predate the flag read as not-explicit, since a
    stored ``"en"`` from before it existed genuinely cannot be disambiguated —
    they keep the previous behaviour (defer to the request) rather than being
    guessed either way.

    Args:
        user: Django User object (optional)
        request: HTTP request object (optional)
        default: Fallback language code

    Returns:
        str: Valid language code
    """
    profile = getattr(user, 'crushprofile', None) if user else None
    stored = getattr(profile, 'preferred_language', None)
    if is_valid_language(stored):
        # A non-default value is self-evidently a choice; "en" needs the flag
        # to say so, because it is also what an untouched profile holds.
        if stored != 'en' or getattr(profile, 'language_explicitly_set', False):
            return stored

    request_lang = getattr(request, 'LANGUAGE_CODE', None) if request else None
    if is_valid_language(request_lang):
        return request_lang

    return validate_language(stored, default)


def get_og_locale(lang_code=None):
    """
    Get the Open Graph locale for a language code.

    Maps language codes to proper og:locale format (language_TERRITORY).

    Args:
        lang_code: Language code (e.g., 'en', 'de', 'fr'). If None, uses current language.

    Returns:
        str: og:locale formatted string (e.g., 'en_US', 'de_DE', 'fr_FR')
    """
    if lang_code is None:
        lang_code = get_language() or 'en'

    locale_map = {
        'en': 'en_US',
        'de': 'de_DE',
        'fr': 'fr_FR',
    }
    return locale_map.get(lang_code, 'en_US')


def build_absolute_url(url_name, lang=None, domain='crush.lu', https=True, **kwargs):
    """
    Build an absolute URL with language prefix for use in emails (without request).

    This is useful for batch email sending where there's no request context.

    Args:
        url_name: URL name to reverse (e.g., 'crush_lu:dashboard')
        lang: Language code for the URL prefix. If None, uses 'en'.
        domain: Domain name (default: 'crush.lu')
        https: Use HTTPS protocol (default: True)
        **kwargs: Additional arguments to pass to reverse()

    Returns:
        str: Absolute URL with language prefix
    """
    if lang is None:
        lang = 'en'
    lang = validate_language(lang)

    # Use override() to ensure reverse() generates the correct language-prefixed URL
    # Must specify urlconf because this may run outside a request (e.g., newsletters)
    # where ROOT_URLCONF doesn't have the crush_lu namespace.
    with override(lang):
        path = reverse(url_name, urlconf='azureproject.urls_crush', **kwargs)

    protocol = 'https' if https else 'http'
    return f"{protocol}://{domain}{path}"
