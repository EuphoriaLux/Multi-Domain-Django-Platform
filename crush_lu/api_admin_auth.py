"""
Shared Bearer-token authentication for the ``/api/admin/...`` endpoints.

These endpoints are called by Azure Function timer triggers and Claude Code
routines, not by browsers, so they authenticate with a Bearer token matching
``settings.ADMIN_API_KEY`` rather than a session/CSRF flow.

This helper was previously duplicated verbatim in ``api_admin_sync`` and
``api_admin_hybrid`` (the latter literally commented "cloned from
api_admin_sync"). It now lives here so every admin endpoint shares one
implementation.
"""
from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def authenticate_admin_request(request) -> bool:
    """Return True iff the request carries a valid admin Bearer token.

    Compares the ``Authorization: Bearer <token>`` header against
    ``settings.ADMIN_API_KEY`` in constant time. Returns False (never raises)
    when the header is missing/malformed or when ``ADMIN_API_KEY`` is unset.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    token = auth_header.replace("Bearer ", "", 1)
    expected = getattr(settings, "ADMIN_API_KEY", None)
    if not expected:
        logger.error("ADMIN_API_KEY not configured; rejecting admin request")
        return False
    return secrets.compare_digest(token, expected)


def unauthorized(request) -> JsonResponse:
    """Standard 401 response for admin endpoints, with a logged warning."""
    logger.warning(
        "Unauthorized admin endpoint call from %s",
        request.META.get("REMOTE_ADDR"),
    )
    return JsonResponse({"error": "Unauthorized"}, status=401)
