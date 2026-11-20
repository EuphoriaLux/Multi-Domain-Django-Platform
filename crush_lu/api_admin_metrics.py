"""
Admin API endpoint for the weekly KPI digest.

Invoked by the ``WeeklyKPIs`` Azure Function timer trigger in
``azure-functions/hybrid-maintenance/`` on Monday mornings. Mirrors the thin
wrapper pattern in ``api_admin_hybrid.pre_screening_invites``:

- Requires a Bearer token matching ``settings.ADMIN_API_KEY`` (shared helper in
  ``crush_lu.api_admin_auth``).
- Lives outside ``i18n_patterns`` so the Function can hit a stable
  ``/api/admin/weekly-kpis/`` path with no language prefix.
- Delegates to the ``send_weekly_kpis`` management command so all logic stays in
  one place and remains runnable from a dev shell.
"""

from __future__ import annotations

import logging
import time
from io import StringIO

from django.core.management import CommandError, call_command
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from crush_lu.api_admin_auth import (
    authenticate_admin_request as _authenticate_admin_request,
)
from crush_lu.api_admin_auth import unauthorized as _unauthorized

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["POST"])
def weekly_kpis_sweep(request):
    """POST /api/admin/weekly-kpis/

    Compute + persist the snapshot for the last completed ISO week and email the
    digest to ``settings.WEEKLY_KPI_RECIPIENTS``. Idempotent for a given week
    (the snapshot is ``update_or_create``d), so a retried Function invocation
    just refreshes the same row and re-sends the email.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    started = timezone.now()
    buffer = StringIO()
    try:
        call_command("send_weekly_kpis", stdout=buffer, stderr=buffer)
    except CommandError:
        # Don't echo the raw exception to the caller (may leak internal paths);
        # the full stack is captured in logs via logger.exception.
        logger.exception("[weekly_kpis] Command error")
        return JsonResponse({"error": "command_error"}, status=500)
    except Exception:  # noqa: BLE001
        logger.exception("[weekly_kpis] Unhandled error")
        return JsonResponse({"error": "internal_error"}, status=500)

    logger.info("[weekly_kpis] completed: %s", buffer.getvalue().strip())
    return JsonResponse(
        {"status": "ok", "timestamp": started.isoformat()},
        status=202,
    )


@csrf_exempt
@require_http_methods(["POST"])
def rotate_connect_questions_sweep(request):
    """POST /api/admin/rotate-connect-questions/

    Ensure the current ISO week's Crush Connect question set exists. Idempotent —
    the set is built once per week (deterministic weighted pick), so a retried
    Function invocation is a no-op.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    started = timezone.now()
    buffer = StringIO()
    try:
        call_command("rotate_connect_questions", stdout=buffer, stderr=buffer)
    except CommandError:
        logger.exception("[rotate_connect_questions] Command error")
        return JsonResponse({"error": "command_error"}, status=500)
    except Exception:  # noqa: BLE001
        logger.exception("[rotate_connect_questions] Unhandled error")
        return JsonResponse({"error": "internal_error"}, status=500)

    logger.info("[rotate_connect_questions] completed: %s", buffer.getvalue().strip())
    return JsonResponse(
        {"status": "ok", "timestamp": started.isoformat()},
        status=202,
    )


# Matches send_profile_reminders.SEND_TIME_BUDGET_SECONDS (Function HTTP timeout 60s).
REMINDER_SWEEP_BUDGET_SECONDS = 55
# One Graph send can block up to 30s; below that, starting a run is pointless.
VERIFICATION_REMINDER_MIN_SECONDS = 31


@csrf_exempt
@require_http_methods(["POST"])
def profile_reminders_sweep(request):
    """POST /api/admin/profile-reminders/

    Send the 24h/72h/7d profile-completion reminder emails. Idempotent per
    user per reminder type — a ``ProfileReminder`` row (unique on
    ``(user, reminder_type)``) records each send, so a retried Function
    invocation never re-sends the same reminder. Invoked daily by the
    ``ProfileReminders`` Azure Function timer.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    started = timezone.now()
    started_monotonic = time.monotonic()
    buffer = StringIO()
    try:
        call_command("send_profile_reminders", stdout=buffer, stderr=buffer)
    except CommandError:
        logger.exception("[profile_reminders] Command error")
        return JsonResponse({"error": "command_error"}, status=500)
    except Exception:  # noqa: BLE001
        logger.exception("[profile_reminders] Unhandled error")
        return JsonResponse({"error": "internal_error"}, status=500)

    logger.info("[profile_reminders] completed: %s", buffer.getvalue().strip())

    # Verification reminders for submitted, not yet verified members ride on
    # the same daily timer, within what is left of the request's 55s budget.
    # A failure here is logged and does not fail the profile-reminder sweep.
    left = REMINDER_SWEEP_BUDGET_SECONDS - (time.monotonic() - started_monotonic)
    if left > VERIFICATION_REMINDER_MIN_SECONDS:
        vbuffer = StringIO()
        try:
            call_command(
                "send_verification_reminders",
                time_budget=left,
                stdout=vbuffer,
                stderr=vbuffer,
            )
            logger.info(
                "[verification_reminders] completed: %s", vbuffer.getvalue().strip()
            )
        except Exception:  # noqa: BLE001
            logger.exception("[verification_reminders] error")
    else:
        logger.info("[verification_reminders] skipped: no time budget left")
    return JsonResponse(
        {"status": "ok", "timestamp": started.isoformat()},
        status=202,
    )


@csrf_exempt
@require_http_methods(["POST"])
def gdpr_retention_sweep(request):
    """POST /api/admin/gdpr-retention/

    Apply the GDPR data-minimization retention sweep (PhoneOTP, CallAttempt,
    DailyUserActivity — see the ``gdpr_retention_cleanup`` command). Runs with
    ``--apply`` here; the command alone is dry-run by default. Invoked weekly
    by the ``GdprRetention`` Azure Function timer.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    started = timezone.now()
    buffer = StringIO()
    try:
        call_command(
            "gdpr_retention_cleanup",
            **{"apply": True},
            stdout=buffer,
            stderr=buffer,
        )
    except CommandError:
        logger.exception("[gdpr_retention] Command error")
        return JsonResponse({"error": "command_error"}, status=500)
    except Exception:  # noqa: BLE001
        logger.exception("[gdpr_retention] Unhandled error")
        return JsonResponse({"error": "internal_error"}, status=500)

    logger.info("[gdpr_retention] completed: %s", buffer.getvalue().strip())
    return JsonResponse(
        {"status": "ok", "timestamp": started.isoformat()},
        status=202,
    )
