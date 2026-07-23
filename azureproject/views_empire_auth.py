"""
Session handoff from crush.lu to game.crush.lu (Crush Empire).

game.crush.lu cannot read crush.lu's session cookie: SESSION_COOKIE_DOMAIN is a
*global* Django setting and this process also serves entreprinder.lu and
vinsdelux.com, so pinning it to '.crush.lu' would break their auth (see the
comment block in production.py). The game host therefore carries no login UI of
its own — which also means none of the five OAuth providers need a redirect URI
pointed at it.

Three-step authorization-code flow:

1. GET /auth/start/ on game.crush.lu stores a random `state` in the (anonymous)
   game-host session and bounces the browser to crush.lu.

2. GET /game/auth/handoff/?return=<allowed>&state=<state> on crush.lu. If the
   user has a valid Django session there, this mints a single-use code, caches
   (code → user_id, state) for 60s, and 302s back to the return URL with
   ?code=ABC&state=<state>. Unauthenticated users are sent through allauth
   first, so every existing provider keeps working, unchanged, on crush.lu.

   The path is deliberately NOT under /api/: CrushConsentMiddleware
   prefix-exempts "/api/", so minting a code there would bypass both the GDPR
   consent gate and the ban check, and a banned user could still obtain a game
   session. Under /game/ the middleware runs first.

3. GET /auth/callback/?code=&state= on game.crush.lu compares `state` against
   the value stashed in step 1, atomically claims the code, and opens a
   host-scoped session with django.contrib.auth.login().

The `state` round-trip is what stops login-CSRF: without it an attacker could
mint a code from their own crush.lu session and walk a victim's browser through
step 3, silently logging the victim into the attacker's game account. An
attacker cannot write to the victim's game.crush.lu session, so they cannot
produce a matching state.

This deliberately duplicates the mechanics of views_spa_auth.py rather than
reusing it: that flow is gated on is_staff in *both* steps because it issues
JWTs for the internal CRM. Loosening that gate to admit ordinary players would
hand a token-issuing path to every user. Separate views, separate cache prefix,
separate allowlist.
"""

import secrets
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.core.cache import cache
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

CODE_CACHE_PREFIX = "empire_auth_code:"
CODE_TTL_SECONDS = 60
STATE_SESSION_KEY = "empire_auth_state"

# login() must name a backend explicitly: AUTHENTICATION_BACKENDS has two
# entries, so Django cannot infer which one authenticated this user. The code
# was minted from an already-authenticated crush.lu session, so no backend
# actually ran here.
_LOGIN_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _is_allowed_return_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme,
        parsed.netloc,
        parsed.path,
    ) in settings.EMPIRE_CALLBACK_ALLOWED_RETURN_URLS


@require_GET
@never_cache
def empire_auth_start(request):
    """Step 1 (game.crush.lu): stash a state nonce, bounce to crush.lu."""
    state = secrets.token_urlsafe(32)
    request.session[STATE_SESSION_KEY] = state

    handoff = (
        f"{settings.EMPIRE_HANDOFF_URL}"
        f"?return={quote(settings.EMPIRE_RETURN_URL, safe='')}"
        f"&state={quote(state, safe='')}"
    )
    return HttpResponseRedirect(handoff)


@require_GET
@never_cache
def empire_auth_handoff(request):
    """Step 2 (crush.lu): trade an authenticated session for a single-use code."""
    return_url = request.GET.get("return", "")
    if not _is_allowed_return_url(return_url):
        return HttpResponseBadRequest("Invalid return URL.")

    state = request.GET.get("state", "")
    if not state:
        return HttpResponseBadRequest("Missing state.")

    if not request.user.is_authenticated:
        login_url = reverse("account_login")
        # next= must be URL-encoded — its value carries its own ?return=...&state=...
        # which allauth would otherwise parse as sibling query params.
        next_target = (
            f"{request.path}?return={quote(return_url, safe='')}"
            f"&state={quote(state, safe='')}"
        )
        login_redirect = f"{login_url}?next={quote(next_target, safe='')}"
        # login_redirect is always relative, so this passes trivially; it is a
        # CodeQL sanitiser marker on top of the strict whitelist already applied
        # to return_url above.
        if not url_has_allowed_host_and_scheme(
            login_redirect,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return HttpResponseBadRequest("Invalid return URL.")
        return HttpResponseRedirect(login_redirect)

    code = secrets.token_urlsafe(32)
    cache.set(
        f"{CODE_CACHE_PREFIX}{code}",
        {"user_id": request.user.pk, "state": state},
        timeout=CODE_TTL_SECONDS,
    )

    # Merge ?code= and ?state= into the return URL's existing query rather than
    # concatenating, and drop any attacker-supplied `code`/`state` already on it —
    # many parsers read the first value of a repeated key.
    parts = urlparse(return_url)
    preserved = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in ("code", "state")
    ]
    redirect_url = urlunparse(
        parts._replace(
            query=urlencode(preserved + [("code", code), ("state", state)])
        )
    )
    return HttpResponseRedirect(redirect_url)


@require_GET
@never_cache
def empire_auth_callback(request):
    """Step 3 (game.crush.lu): consume the code, open a host-scoped session."""
    code = request.GET.get("code", "")
    state = request.GET.get("state", "")
    expected_state = request.session.pop(STATE_SESSION_KEY, None)

    if not code or not state or not expected_state:
        return HttpResponseBadRequest("Invalid or expired code.")

    if not secrets.compare_digest(state, expected_state):
        return HttpResponseBadRequest("Invalid or expired code.")

    cache_key = f"{CODE_CACHE_PREFIX}{code}"
    payload = cache.get(cache_key)
    if not payload:
        return HttpResponseBadRequest("Invalid or expired code.")

    # Atomic single-use claim: cache.add only succeeds when the key did not
    # already exist, so two concurrent exchanges of one code race here and only
    # the first wins. Redis in production; LocMemCache serialises add() per
    # process, which is all the dev server needs.
    if not cache.add(f"{cache_key}:consumed", True, timeout=CODE_TTL_SECONDS):
        return HttpResponseBadRequest("Invalid or expired code.")
    cache.delete(cache_key)

    if not secrets.compare_digest(str(payload.get("state", "")), state):
        return HttpResponseBadRequest("Invalid or expired code.")

    User = get_user_model()
    try:
        user = User.objects.get(pk=payload["user_id"], is_active=True)
    except User.DoesNotExist:
        return HttpResponseBadRequest("Invalid or expired code.")

    login(request, user, backend=_LOGIN_BACKEND)
    return HttpResponseRedirect(reverse("crush_empire:play"))
