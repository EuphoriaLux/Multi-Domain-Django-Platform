from functools import wraps

from django.conf import settings
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


def crush_empire_enabled(function):
    """
    Gate a view behind the Crush Empire launch flag.

    When CRUSH_EMPIRE_ENABLED is False, redirects to the teaser. Staff bypass
    the flag so internal review works before public launch. Mirrors
    crush_lu.decorators.crush_connect_enabled.
    """
    @wraps(function)
    def wrapper(request, *args, **kwargs):
        if not settings.CRUSH_EMPIRE_ENABLED and not (
            request.user.is_authenticated and request.user.is_staff
        ):
            return redirect('crush_empire:teaser')
        return function(request, *args, **kwargs)
    return wrapper
