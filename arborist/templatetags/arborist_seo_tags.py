"""
Custom template tags for SEO-related functionality for arborist.lu.

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

register = template.Library()

# Language prefix pattern (matches /en/, /de/, /fr/, etc.)
LANGUAGE_PREFIX_PATTERN = re.compile(r'^/([a-z]{2})/')

# Domain for arborist.lu
DOMAIN = 'https://arborist.lu'


def strip_language_prefix(path):
    """
    Remove language prefix from URL path if present.

    Examples:
        /en/about/ -> /about/
        /de/contact/ -> /contact/
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
    plus x-default pointing to the German version (primary market).

    Usage:
        {% load seo_tags %}
        {% hreflang_tags %}

    Output:
        <link rel="alternate" hreflang="en" href="https://arborist.lu/en/about/">
        <link rel="alternate" hreflang="de" href="https://arborist.lu/de/about/">
        <link rel="alternate" hreflang="fr" href="https://arborist.lu/fr/about/">
        <link rel="alternate" hreflang="x-default" href="https://arborist.lu/de/about/">
    """
    request = context.get('request')
    if not request:
        return ''

    # Get path without language prefix
    base_path = get_path_without_language(request)

    # Build hreflang tags for each language
    tags = []

    for lang_code, lang_name in settings.LANGUAGES:
        url = f"{DOMAIN}/{lang_code}{base_path}"
        tags.append(f'<link rel="alternate" hreflang="{lang_code}" href="{url}">')

    # x-default points to German version (Luxembourg primary language)
    default_url = f"{DOMAIN}/de{base_path}"
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
        https://arborist.lu/en/about/

    Output for /de/contact/:
        https://arborist.lu/de/contact/
    """
    request = context.get('request')
    if not request:
        return f'{DOMAIN}/'

    # Self-referencing: canonical points to current URL with language prefix
    return f"{DOMAIN}{request.path}"


@register.simple_tag(takes_context=True)
def localized_url(context, lang_code):
    """
    Generate URL for current page in a specific language.

    Useful for language switcher or navigation.

    Usage:
        {% load seo_tags %}
        <a href="{% localized_url 'de' %}">Deutsch</a>
        <a href="{% localized_url 'fr' %}">Français</a>
    """
    request = context.get('request')
    if not request:
        return f'/{lang_code}/'

    # Get path without current language prefix
    base_path = get_path_without_language(request)

    return f"/{lang_code}{base_path}"
