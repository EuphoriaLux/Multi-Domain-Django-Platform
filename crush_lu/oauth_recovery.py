"""Single-use OAuth recovery bound to the browser that completed the callback.

OAuth state is public correlation data, never a login credential. Only a fresh
random secret issued AFTER successful provider authentication permits recovery
without a session cookie. This keeps the PWA/system-browser callback fallback,
without letting the browser that merely initiated a state redeem another user's
completed login. No IP/UA fingerprint is used as proof of possession.
"""

import hashlib
import json
import re
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model, login
from django.utils import timezone

BROWSER_COOKIE = "__Host-crush_oauth_browser"
BROWSER_SECONDS = 15 * 60
RECOVERY_COOKIE = "__Host-crush_oauth_recovery"
RECOVERY_SECONDS = 120


def _cookie_hash(request, cookie=RECOVERY_COOKIE):
    token = request.COOKIES.get(cookie, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def bind_browser_state(request):
    """Bind an outgoing state to a cookie independent of the Django session."""
    token = request.COOKIES.get(BROWSER_COOKIE, "")
    digest = _cookie_hash(request, BROWSER_COOKIE)
    if not digest:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    request._oauth_browser_cookie = token
    return digest


def callback_browser_hash(request):
    return _cookie_hash(request, BROWSER_COOKIE)


def store_callback_result(request, user):
    """Issue proof only for a state consumed by allauth on THIS callback."""
    from .models import OAuthState

    state_id = request.__dict__.pop("_oauth_callback_state_id", None)
    if not state_id or not user.is_active:
        return
    now = timezone.now()
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    updated = OAuthState.objects.filter(
        state_id=state_id,
        used=True,
        auth_completed=False,
        expires_at__gt=now,
    ).update(
        auth_completed=True,
        auth_user_id=user.pk,
        auth_recovery_hash=digest,
        last_callback_at=now,
    )
    if updated:
        request._oauth_recovery_cookie = token


def recover_callback_login(request, state_id):
    """Atomically retire recovery before login, returning authorised metadata.

    The conditional UPDATE is the single-use gate on PostgreSQL AND SQLite: two
    readers may find the row, but only one can claim it. Expiry is checked again at
    the update, and recovery never re-arms the user_logged_in callback signal.
    """
    from .models import OAuthState

    now = timezone.now()
    candidates = OAuthState.objects.filter(
        state_id=state_id,
        used=True,
        auth_completed=True,
        expires_at__gt=now,
        last_callback_at__gte=now - timedelta(seconds=RECOVERY_SECONDS),
    ).exclude(auth_recovery_hash="")
    if request.user.is_authenticated:
        candidates = candidates.filter(auth_user_id=request.user.pk)
    else:
        digest = _cookie_hash(request)
        if not digest:
            return None
        candidates = candidates.filter(auth_recovery_hash=digest)

    state = candidates.first()
    if state is None:
        return None
    user = (
        get_user_model().objects.filter(pk=state.auth_user_id, is_active=True).first()
    )
    if user is None:
        return None
    claim_time = timezone.now()
    claimed = candidates.filter(
        expires_at__gt=claim_time,
        last_callback_at__gte=claim_time - timedelta(seconds=RECOVERY_SECONDS),
    ).update(auth_completed=False, auth_user_id=None, auth_recovery_hash="")
    if not claimed:
        return None
    if _cookie_hash(request) == state.auth_recovery_hash:
        request._oauth_recovery_cookie_clear = True
    if not request.user.is_authenticated:
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    # Polling may be the first request that sees the callback cookie. Restore
    # the same authorised handoff marker used by the original callback path.
    from .mobile_auth import restore_mobile_handoff_from_state

    try:
        data = json.loads(state.state_data)
    except (ValueError, TypeError):
        data = None
    restore_mobile_handoff_from_state(request, data)
    return state


def revoke_callback_recovery(request):
    """Logout must also retire proof left by a direct native-app handoff."""
    from .models import OAuthState

    digest = _cookie_hash(request)
    if digest:
        OAuthState.objects.filter(auth_recovery_hash=digest).update(
            auth_completed=False, auth_user_id=None, auth_recovery_hash=""
        )
    request._oauth_recovery_cookie_clear = True


class OAuthRecoveryCookieMiddleware:
    """Deliver callback proof independently of the delayed session cookie."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        browser_token = getattr(request, "_oauth_browser_cookie", None)
        if browser_token and response.status_code < 400:
            response.set_cookie(
                BROWSER_COOKIE,
                browser_token,
                max_age=BROWSER_SECONDS,
                secure=True,
                httponly=True,
                # Apple uses a cross-site form_post callback. A Lax cookie
                # would be withheld there and break session-loss recovery.
                samesite="None",
                path="/",
            )
            response["Cache-Control"] = "no-store"
            response["Referrer-Policy"] = "no-referrer"
        token = getattr(request, "_oauth_recovery_cookie", None)
        if token and response.status_code < 400:
            response.set_cookie(
                RECOVERY_COOKIE,
                token,
                max_age=RECOVERY_SECONDS,
                secure=True,
                httponly=True,
                samesite="Lax",
                path="/",
            )
            response["Cache-Control"] = "no-store"
            response["Referrer-Policy"] = "no-referrer"
        if getattr(request, "_oauth_recovery_cookie_clear", False):
            response.delete_cookie(RECOVERY_COOKIE, path="/", samesite="Lax")
        return response
