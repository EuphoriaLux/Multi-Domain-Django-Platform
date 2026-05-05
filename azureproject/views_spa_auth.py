"""
Session→JWT exchange for the hub.crush.lu SPA.

Two-step authorization-code flow:

1. The SPA (cross-origin, no session cookie reachable from api.crush.lu) sends
   the browser to GET /api/auth/spa-callback/?return=<allowed-callback-url> on
   crush.lu. If the user has a valid Django session AND is staff, this view
   mints a single-use code, stores (code → user_id) in the cache for 60s, and
   302s back to the return URL with ?code=ABC.

2. The SPA reads the code from the URL, scrubs it, and POSTs it to
   /api/token/exchange-code/ on api.crush.lu. That view pops the code (single
   use), re-checks staff, and returns a SimpleJWT access/refresh pair as JSON.

The split keeps the responsibilities narrow: crush.lu authorizes (it's where
allauth + sessions live), api.crush.lu issues tokens (it's where the SPA
already talks). The fragment-based "implicit" variant was rejected because
refresh tokens don't belong in URL history.
"""

import secrets
from urllib.parse import quote, urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import (
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseRedirect,
)
from django.urls import reverse
from django.views.decorators.http import require_GET
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

CODE_CACHE_PREFIX = "spa_auth_code:"
CODE_TTL_SECONDS = 60


def _is_allowed_return_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme,
        parsed.netloc,
        parsed.path,
    ) in settings.SPA_CALLBACK_ALLOWED_RETURN_URLS


@require_GET
def spa_session_callback(request):
    """Step 1: trade an authenticated staff session for a single-use code."""
    return_url = request.GET.get("return", "")
    if not _is_allowed_return_url(return_url):
        return HttpResponseBadRequest("Invalid return URL.")

    if not request.user.is_authenticated:
        login_url = reverse("account_login")
        # next= must be URL-encoded — its value contains its own ?return=...
        # that would otherwise be parsed as a sibling query string by allauth.
        next_target = f"{request.path}?return={quote(return_url, safe='')}"
        return HttpResponseRedirect(f"{login_url}?next={quote(next_target, safe='')}")

    if not request.user.is_staff:
        return HttpResponseForbidden("Hub access requires staff.")

    code = secrets.token_urlsafe(32)
    cache.set(f"{CODE_CACHE_PREFIX}{code}", request.user.pk, timeout=CODE_TTL_SECONDS)
    return HttpResponseRedirect(f"{return_url}?code={code}")


class ExchangeCodeView(APIView):
    """Step 2: pop the code (single-use) and return a JWT pair."""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        code = (request.data or {}).get("code")
        if not isinstance(code, str) or not code:
            return Response({"detail": "Missing code."}, status=400)

        cache_key = f"{CODE_CACHE_PREFIX}{code}"
        user_id = cache.get(cache_key)
        if user_id is None:
            return Response({"detail": "Invalid or expired code."}, status=401)

        # Atomic single-use claim: cache.add only succeeds when the key
        # didn't already exist, so concurrent requests with the same code
        # race here and only the first wins. Without this, two simultaneous
        # exchanges could both pass the get() above and mint two JWT pairs
        # from one code (django-redis-backed; LocMemCache also serializes
        # add() per-process, and production.py forces Redis anyway).
        consumed_key = f"{cache_key}:consumed"
        if not cache.add(consumed_key, True, timeout=CODE_TTL_SECONDS):
            return Response({"detail": "Invalid or expired code."}, status=401)
        cache.delete(cache_key)

        User = get_user_model()
        try:
            user = User.objects.get(pk=user_id, is_active=True)
        except User.DoesNotExist:
            return Response({"detail": "Invalid or expired code."}, status=401)

        if not user.is_staff:
            return Response({"detail": "Hub access requires staff."}, status=403)

        refresh = RefreshToken.for_user(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh)})
