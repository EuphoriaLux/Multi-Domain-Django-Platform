"""Template filters for showing personal data without fully exposing it."""

from urllib.parse import urlsplit

from django import template
from django.urls import resolve
from django.utils import translation

register = template.Library()


@register.filter
def mask_email(value):
    """Partially hide an email address for display: ``t***@gmail.com``.

    Keeps the first character of the local part (so a user recognises their
    own address) and the whole domain (so they know which inbox to check),
    masking everything else. Falls back to the original value for anything
    that isn't a plain ``local@domain`` string (empty, no ``@``, etc.) so a
    caller can safely ``{% if email %}`` around it either way.
    """
    if not value or "@" not in value:
        return value
    local, _, domain = value.partition("@")
    if not local or not domain:
        return value
    if len(local) <= 1:
        masked_local = local + "*"
    else:
        masked_local = local[0] + "*" * len(local[1:])
    return f"{masked_local}@{domain}"


# Anonymous pages whose App Insights page views are safe to record (#1085).
# The loader's automatic page-view tracking sends the full URL, so an
# anonymous page is allowed only when its URL carries no credential: token
# pages such as /book/<booking_token>/ and /invite/<code>/ must stay off.
ANONYMOUS_TELEMETRY_VIEWS = frozenset({"crush_lu:login", "crush_lu:signup"})


@register.filter
def anonymous_telemetry_allowed(request):
    """True when an anonymous visitor may get the App Insights loader here.

    Only a bare allowlisted URL qualifies: a query string can carry a
    credential too (login redirects add ``?next=/advent/qr/<token>/``).
    """
    resolver_match = getattr(request, "resolver_match", None)
    if getattr(resolver_match, "view_name", None) not in ANONYMOUS_TELEMETRY_VIEWS:
        return False
    if request.META.get("QUERY_STRING"):
        return False
    return _referrer_is_safe(request)


def _referrer_is_safe(request):
    """True when ``document.referrer`` on this page carries no credential.

    The SDK records the referrer, and under ``strict-origin-when-cross-origin``
    a same-origin click sends the full referring URL: "Login / Join" on
    /book/<booking_token>/ would put the token into telemetry. A cross-origin
    referrer is only an origin, so it is safe. A same-origin one must be a
    bare URL that resolves to a route without path parameters (every
    token-bearing route captures one). Anything unparseable fails closed.
    """
    referer = request.META.get("HTTP_REFERER")
    if not referer:
        return True
    try:
        parts = urlsplit(referer)
        if parts.netloc.lower() != request.get_host().lower():
            return True
        if parts.query or parts.fragment or "?" in referer or "#" in referer:
            return False
        path = parts.path or "/"
        # i18n_patterns only match the active language's prefix: resolve the
        # referrer under its own language (/fr/... from an /en/ page).
        language = translation.get_language_from_path(path)
        with translation.override(language or translation.get_language()):
            match = resolve(path, urlconf=getattr(request, "urlconf", None))
        return not match.args and not match.kwargs
    except Exception:
        return False
