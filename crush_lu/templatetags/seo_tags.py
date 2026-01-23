"""
Custom template tags for SEO-related functionality.

Provides robust hreflang and canonical URL generation for multi-language support.

Usage in templates:
    {% load seo_tags %}

    In <head>:
        {% hreflang_tags %}
        <link rel="canonical" href="{% canonical_url %}">
"""

import re
from django import template
from django.conf import settings
from django.utils.safestring import mark_safe
from django.utils.translation import get_language

register = template.Library()

# Language prefix pattern (matches /en/, /de/, /fr/, etc.)
LANGUAGE_PREFIX_PATTERN = re.compile(r'^/([a-z]{2})/')


def strip_language_prefix(path):
    """
    Remove language prefix from URL path if present.

    Examples:
        /en/about/ -> /about/
        /de/events/123/ -> /events/123/
        /about/ -> /about/
    """
    match = LANGUAGE_PREFIX_PATTERN.match(path)
    if match:
        lang_code = match.group(1)
        # Only strip if it's a valid language code
        valid_langs = [code for code, name in settings.LANGUAGES]
        if lang_code in valid_langs:
            return path[3:]  # Remove /xx/
    return path


def get_path_without_language(request):
    """
    Get the current path without language prefix.

    Returns the path that can be prefixed with any language code.
    """
    if not request:
        return '/'
    return strip_language_prefix(request.path)


@register.simple_tag(takes_context=True)
def hreflang_tags(context):
    """
    Generate proper hreflang tags for all supported languages.

    Creates self-referencing hreflang tags for each language variant,
    plus x-default pointing to the English version.

    Usage:
        {% load seo_tags %}
        {% hreflang_tags %}

    Output:
        <link rel="alternate" hreflang="en" href="https://crush.lu/en/about/">
        <link rel="alternate" hreflang="de" href="https://crush.lu/de/about/">
        <link rel="alternate" hreflang="fr" href="https://crush.lu/fr/about/">
        <link rel="alternate" hreflang="x-default" href="https://crush.lu/en/about/">
    """
    request = context.get('request')
    if not request:
        return ''

    # Get path without language prefix
    base_path = get_path_without_language(request)

    # Build hreflang tags for each language
    tags = []
    domain = 'https://crush.lu'

    for lang_code, lang_name in settings.LANGUAGES:
        url = f"{domain}/{lang_code}{base_path}"
        tags.append(f'<link rel="alternate" hreflang="{lang_code}" href="{url}">')

    # x-default points to English version (default language)
    default_url = f"{domain}/en{base_path}"
    tags.append(f'<link rel="alternate" hreflang="x-default" href="{default_url}">')

    return mark_safe('\n    '.join(tags))


@register.simple_tag(takes_context=True)
def canonical_url(context):
    """
    Generate canonical URL for the current page.

    Uses self-referencing canonicals - each language version
    points to itself as canonical.

    Usage:
        {% load seo_tags %}
        <link rel="canonical" href="{% canonical_url %}">

    Output for /en/about/:
        https://crush.lu/en/about/

    Output for /de/events/:
        https://crush.lu/de/events/
    """
    request = context.get('request')
    if not request:
        return 'https://crush.lu/'

    domain = 'https://crush.lu'

    # Self-referencing: canonical points to current URL with language prefix
    # request.path already includes language prefix after i18n_patterns
    return f"{domain}{request.path}"


@register.simple_tag(takes_context=True)
def localized_url(context, lang_code):
    """
    Generate URL for current page in a specific language.

    Useful for language switcher or navigation.

    Usage:
        {% load seo_tags %}
        <a href="{% localized_url 'de' %}">Deutsch</a>
        <a href="{% localized_url 'fr' %}">Francais</a>
    """
    request = context.get('request')
    if not request:
        return f'/{lang_code}/'

    # Get path without current language prefix
    base_path = get_path_without_language(request)

    return f"/{lang_code}{base_path}"


@register.simple_tag
def og_locale():
    """
    Return valid og:locale for the current language.

    Maps language codes to proper Open Graph locale format (language_TERRITORY).

    Usage:
        {% load seo_tags %}
        <meta property="og:locale" content="{% og_locale %}">

    Output for English: en_US
    Output for German: de_DE
    Output for French: fr_FR
    """
    lang = get_language() or 'en'
    locale_map = {
        'en': 'en_US',
        'de': 'de_DE',
        'fr': 'fr_FR',
    }
    return locale_map.get(lang, 'en_US')
