"""
Admin API endpoints for the Hybrid Coach Review System + pre-screening sweeps.

Invoked by Azure Function timer triggers in `azure-functions/hybrid-maintenance/`
on the schedule documented in the Phase-D plan. Each endpoint:

- Requires a Bearer token matching ``settings.ADMIN_API_KEY`` (see
  ``_authenticate_admin_request``, cloned from ``crush_lu.api_admin_sync``).
- Lives outside ``i18n_patterns`` so Functions can hit ``/api/admin/...``
  without a language prefix.
- Returns ``JsonResponse({"processed": N, ...}, status=202)``.
- Enqueues user-facing notifications via ``django.tasks`` (runs through the
  configured TASKS backend — ImmediateBackend in dev, DatabaseBackend +
  ``manage.py db_worker`` in production).

The work lives *here*, not in the Function, because (a) Python/Django stays
close to its ORM + model invariants, and (b) the Function layer should be a
thin scheduler. The contact-sync pattern in production already does this.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Bearer-token authentication (identical to api_admin_sync)
# -------------------------------------------------------------------------

def _authenticate_admin_request(request) -> bool:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    token = auth_header.replace("Bearer ", "", 1)
    expected = getattr(settings, "ADMIN_API_KEY", None)
    if not expected:
        logger.error("ADMIN_API_KEY not configured; rejecting admin request")
        return False
    return secrets.compare_digest(token, expected)


def _unauthorized(request) -> JsonResponse:
    logger.warning(
        "Unauthorized hybrid admin endpoint call from %s",
        request.META.get("REMOTE_ADDR"),
    )
    return JsonResponse({"error": "Unauthorized"}, status=401)


def _hybrid_disabled() -> JsonResponse:
    return JsonResponse(
        {"skipped": True, "reason": "HYBRID_COACH_SYSTEM_ENABLED=False"},
        status=200,
    )


FALLBACK_TOKEN_TTL = timedelta(days=30)


# -------------------------------------------------------------------------
# SLA sweep (Phase 3) — offer self-booking to users whose SLA breached.
# -------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["POST"])
def sla_sweep(request):
    """POST /api/admin/hybrid-coach-sla-sweep/

    For each pending submission where:
      * sla_deadline has passed,
      * fallback hasn't been offered yet,
      * the submission isn't paused,
      * the assigned coach opted into hybrid features,

    set ``fallback_offered_at``, mint a ``booking_token`` (30-day TTL), append
    a ``fallback_offered`` entry to ``system_actions``, and enqueue the email.

    Idempotent on repeat calls (the ``fallback_offered_at IS NULL`` filter
    prevents double offers).
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    from .models import ProfileSubmission
    from .tasks import send_sla_fallback_email_task

    if not getattr(settings, "HYBRID_COACH_SYSTEM_ENABLED", False):
        logger.info("[sla_sweep] HYBRID_COACH_SYSTEM_ENABLED=False — skipping")
        return _hybrid_disabled()

    host = _resolve_email_host(request)
    now = timezone.now()

    candidates = (
        ProfileSubmission.objects.filter(
            status="pending",
            sla_deadline__lte=now,
            sla_deadline__isnull=False,
            fallback_offered_at__isnull=True,
            booking_token__isnull=True,
            coach__hybrid_features_enabled=True,
            is_paused=False,
        )
        .select_related("coach__user", "profile__user")
    )

    processed = 0
    failed = 0
    with transaction.atomic():
        locked_ids = list(
            ProfileSubmission.objects.filter(pk__in=candidates.values("pk"))
            .select_for_update(skip_locked=True)
            .values_list("pk", flat=True)
        )
        submissions = (
            ProfileSubmission.objects.filter(pk__in=locked_ids)
            .select_related("coach__user", "profile__user")
        )
        for sub in submissions:
            # Nested atomic = savepoint. We persist fallback_offered_at +
            # booking_token AND enqueue the email together — if enqueue raises,
            # the savepoint rolls back, so the submission stays eligible for
            # the next sweep instead of being permanently excluded by the
            # `fallback_offered_at__isnull=True` filter with no email sent.
            try:
                with transaction.atomic():
                    sub.fallback_offered_at = now
                    sub.booking_token = uuid.uuid4()
                    sub.booking_token_expires_at = now + FALLBACK_TOKEN_TTL
                    sub.log_system_action(
                        "fallback_offered",
                        actor="system",
                        reason="sla_breach",
                        sla_deadline=(
                            sub.sla_deadline.isoformat()
                            if sub.sla_deadline
                            else None
                        ),
                    )
                    sub.save(
                        update_fields=[
                            "fallback_offered_at",
                            "booking_token",
                            "booking_token_expires_at",
                            "system_actions",
                        ]
                    )
                    send_sla_fallback_email_task.enqueue(
                        submission_id=sub.pk,
                        host=host,
                        is_secure=request.is_secure(),
                    )
                processed += 1
            except Exception:  # noqa: BLE001
                logger.exception("[sla_sweep] Failed on submission %s", sub.pk)
                failed += 1

    logger.info("[sla_sweep] processed=%d failed=%d", processed, failed)
    return JsonResponse(
        {"processed": processed, "failed": failed, "timestamp": now.isoformat()},
        status=202,
    )


def _resolve_email_host(request) -> str:
    """Pick the host for links in system-generated emails.

    Prefers the incoming request host when it looks like a real domain (so
    Function→``test.crush.lu`` produces links back to staging naturally).
    Falls back to ``STAGING_MODE`` — same slot-sticky env var the rest of the
    codebase uses — then to the first ``ALLOWED_HOSTS`` entry containing
    ``crush.lu``.
    """
    import os

    req_host = request.get_host()
    if req_host and "azurewebsites" not in req_host and "localhost" not in req_host:
        return req_host
    if os.environ.get("STAGING_MODE", "").lower() in ("true", "1", "yes"):
        return "test.crush.lu"
    for allowed in getattr(settings, "ALLOWED_HOSTS", []) or []:
        if allowed and "crush.lu" in allowed:
            return allowed.lstrip(".")
    return "crush.lu"


# -------------------------------------------------------------------------
# Pre-screening invites (reuses existing management command)
# -------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["POST"])
def pre_screening_invites(request):
    """POST /api/admin/pre-screening-invites/

    Thin wrapper around the existing ``send_pre_screening_invites`` command so
    a Function timer can drive it on the desired hourly cadence. All logic
    (the 1h invite / 4h push / 24h reminder windows) stays in the command so
    it remains usable from a dev shell.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    from io import StringIO

    from django.core.management import CommandError, call_command

    started = timezone.now()
    buffer = StringIO()
    try:
        call_command("send_pre_screening_invites", stdout=buffer, stderr=buffer)
    except CommandError:
        # CodeQL: don't echo the raw exception message to the caller — leaks
        # stack traces / internal paths. The failure is fully captured in logs
        # with stack via logger.exception; callers only need the error code.
        logger.exception("[pre_screening_invites] Command error")
        return JsonResponse({"error": "command_error"}, status=500)
    except Exception:  # noqa: BLE001
        logger.exception("[pre_screening_invites] Unhandled error")
        return JsonResponse({"error": "internal_error"}, status=500)

    took_ms = int((timezone.now() - started).total_seconds() * 1000)
    logger.info("[pre_screening_invites] took_ms=%d", took_ms)
    return JsonResponse(
        {"ok": True, "took_ms": took_ms}, status=202
    )
