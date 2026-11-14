"""Authentication schemes for Hub API endpoints."""

from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

logger = logging.getLogger(__name__)


class AdminApiKeyAuthentication(BaseAuthentication):
    """Authenticate machine-to-machine requests bearing settings.ADMIN_API_KEY.

    Compares the 'Authorization: Bearer <key>' header against settings.ADMIN_API_KEY.
    If valid, associates the request with the primary active staff user (e.g. tom@crush.lu).
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header.replace("Bearer ", "", 1).strip()
        expected = getattr(settings, "ADMIN_API_KEY", None)
        if not expected or not secrets.compare_digest(
            token.encode("utf-8"), expected.encode("utf-8")
        ):
            return None

        User = get_user_model()
        user = (
            User.objects.filter(is_superuser=True, is_active=True).first()
            or User.objects.filter(is_staff=True, is_active=True).first()
        )
        if not user:
            logger.error("AdminApiKeyAuthentication: No active staff user found.")
            raise exceptions.AuthenticationFailed(
                "No active staff user configured for API key access."
            )

        return (user, None)

    def authenticate_header(self, request):
        return 'Bearer realm="api"'


try:
    from rest_framework_simplejwt.authentication import JWTAuthentication
except ImportError:  # pragma: no cover - simplejwt is a hard dependency
    JWTAuthentication = None


class PlaygroundFreshJWTAuthentication(JWTAuthentication):
    """SimpleJWT authentication that also enforces the coach playground's
    7-day crush.lu recheck on test.crush.lu.

    For a staging coach account linked to crush.lu (and only while the
    staging side of that sign-in is configured), a token must carry a crush.lu
    sign-in time (``pg_sso_at``) from the last 7 days. Tokens from any other
    path, or older than that, are refused, so a coach removed on crush.lu
    cannot keep using the API with tokens obtained earlier. Everyone else,
    and production, behaves exactly like ``JWTAuthentication``.
    Spec: ai-memory-hub/specs/2026-11-13-crush-coach-playground.md
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        from crush_lu.services import playground_sso

        if playground_sso.freshness_applies(user) and not (
            playground_sso.timestamp_fresh(token.get(playground_sso.JWT_CLAIM))
        ):
            raise exceptions.AuthenticationFailed("Sign in with crush.lu again.")
        return user, token
