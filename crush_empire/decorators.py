from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render


def _is_banned(user):
    """
    Mirror CrushConsentMiddleware.is_banned.

    That middleware is scoped to the crush.lu urlconf (`is_on_crush_domain`
    compares `request.urlconf` to azureproject.urls_crush), so it never runs on
    game.crush.lu. Sessions here live for SESSION_COOKIE_AGE (14 days), which
    would let someone banned *after* signing in keep playing for a fortnight.
    Re-check on every gated game view.
    """
    data_consent = getattr(user, "data_consent", None)
    if data_consent is None:
        return False
    return data_consent.crushlu_banned


def empire_login_required(function):
    """
    Require a non-banned session on game.crush.lu.

    There is no login form on this host — allauth lives on crush.lu, and so do
    every OAuth provider's redirect URIs. Anonymous users are sent through the
    cross-host handoff (azureproject.views_empire_auth), which bounces them to
    crush.lu's login if they aren't signed in there either. Consent itself is
    enforced on the crush.lu leg of that handoff, which is why it is mounted
    outside the middleware's exempt "/api/" prefix.
    """
    @wraps(function)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            # Language-neutral: the return leg from crush.lu carries no /en/ prefix.
            return redirect('empire_auth_start')
        if _is_banned(request.user):
            return render(request, "crush_empire/banned.html", status=403)
        return function(request, *args, **kwargs)
    return wrapper


def _flag_open(user):
    """The launch flag, with the staff bypass so internal review works pre-launch."""
    return settings.CRUSH_EMPIRE_ENABLED or (user.is_authenticated and user.is_staff)


def crush_empire_enabled(function):
    """
    Gate a page behind the Crush Empire launch flag.

    When CRUSH_EMPIRE_ENABLED is False, redirects to the teaser. Mirrors the
    staff-bypass idiom from crush_lu.decorators.
    """
    @wraps(function)
    def wrapper(request, *args, **kwargs):
        if not _flag_open(request.user):
            return redirect('crush_empire:teaser')
        return function(request, *args, **kwargs)
    return wrapper


def empire_api_required(function):
    """
    Gate a JSON endpoint: signed in, not banned, flag open.

    Deliberately not empire_login_required + crush_empire_enabled: those redirect,
    and fetch() follows redirects. A 302 to crush.lu would surface to the caller
    as an opaque cross-origin failure rather than "you are signed out". Answer in
    JSON so the client can react.
    """
    @wraps(function)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse(
                {"success": False, "error": "Not signed in", "reauth": True}, status=403
            )
        if _is_banned(request.user):
            return JsonResponse(
                {"success": False, "error": "Account suspended"}, status=403
            )
        if not _flag_open(request.user):
            return JsonResponse(
                {"success": False, "error": "Crush Empire is not open yet"}, status=403
            )
        return function(request, *args, **kwargs)
    return wrapper
