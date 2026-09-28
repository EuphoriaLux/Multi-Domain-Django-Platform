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
        if not expected or not secrets.compare_digest(token, expected):
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
