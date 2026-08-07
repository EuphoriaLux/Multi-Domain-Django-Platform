"""
Admin API endpoints for the event email lifecycle.

Invoked by the ``EventReminders``, ``EventRecaps`` and ``EventFeedback`` Azure
Function timer triggers in ``azure-functions/hybrid-maintenance/``. Mirrors the
thin wrapper pattern in ``api_admin_metrics``:

- Requires a Bearer token matching ``settings.ADMIN_API_KEY`` (shared helper in
  ``crush_lu.api_admin_auth``).
- Lives outside ``i18n_patterns`` so the Function can hit stable
  ``/api/admin/event-*/`` paths with no language prefix.
- Delegates to the management commands so all logic stays in one place and
  remains runnable from a dev shell.

Until this module existed the three ``send_event_*`` commands had **no**
scheduler at all — not a Function timer, not a GitHub cron, not an endpoint —
so an event's reminder, recap and feedback emails only ever went out if someone
typed the command by hand. The 2026-07-29 event shipped with no reminder and a
recap that was sent manually 20 hours after it ended.
"""

from __future__ import annotations

import logging
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


def _run(request, label: str, command: str, **command_kwargs) -> JsonResponse:
    """Authenticate, run ``command``, and answer 202 — the shared body.

    The three endpoints below differ only in which command they call, so the
    boilerplate (auth, output capture, error mapping, log line) lives here.
    Exceptions are never echoed to the caller — they can carry internal paths —
    but the full stack is captured via ``logger.exception``.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    started = timezone.now()
    buffer = StringIO()
    try:
        call_command(command, stdout=buffer, stderr=buffer, **command_kwargs)
    except CommandError:
        logger.exception("[%s] Command error", label)
        return JsonResponse({"error": "command_error"}, status=500)
    except Exception:  # noqa: BLE001
        logger.exception("[%s] Unhandled error", label)
        return JsonResponse({"error": "internal_error"}, status=500)

    logger.info("[%s] completed: %s", label, buffer.getvalue().strip())
    return JsonResponse(
        {"status": "ok", "timestamp": started.isoformat()},
        status=202,
    )


@csrf_exempt
@require_http_methods(["POST"])
def event_reminders_sweep(request):
    """POST /api/admin/event-reminders/

    Send the day-before reminder to confirmed registrants. Idempotent per
    registration (the command tracks what it has already sent), so a retried
    Function invocation never double-mails. Invoked daily by the
    ``EventReminders`` Azure Function timer.
    """
    return _run(request, "event_reminders", "send_event_reminders")


@csrf_exempt
@require_http_methods(["POST"])
def event_recaps_sweep(request):
    """POST /api/admin/event-recaps/

    Send the 24h post-event recap to attendees. Idempotent via
    ``EventRegistration.recap_sent_at``.

    Invoked **hourly**, deliberately. The command only targets events whose
    ``end_time`` falls between ``now - lookback_hours`` (36h) and
    ``now - min_hours_after_end`` (24h) — a 12-hour window. A daily timer can
    drift far enough into that window to miss an event entirely depending on
    what time it ended; an hourly one fires within an hour of the event
    becoming eligible and the idempotency flag absorbs the extra runs.
    """
    return _run(request, "event_recaps", "send_event_recaps")


@csrf_exempt
@require_http_methods(["POST"])
def event_feedback_sweep(request):
    """POST /api/admin/event-feedback/

    Send the post-event feedback survey to attendees. Idempotent via
    ``EventRegistration.feedback_request_sent_at``. Invoked daily by the
    ``EventFeedback`` Azure Function timer — its 48h lookback is wide enough
    that a daily cadence cannot miss an event.
    """
    return _run(request, "event_feedback", "send_event_feedback_requests")


@csrf_exempt
@require_http_methods(["POST"])
def echo_lu_sync_sweep(request):
    """POST /api/admin/echo-sync/

    Reconcile published events with echo.lu, Luxembourg's national events
    portal. Invoked hourly by the ``EchoLuSync`` Azure Function timer.

    A sweep is needed even though a post_save receiver already mirrors most
    edits live, because three transitions reach echo.lu no other way:

    - an event simply *ending* — nothing saves the row when it does, so the
      finished listing would sit on the portal until someone touched it;
    - a failed write — the receiver's task swallows API errors (an echo.lu
      outage must not fail an event edit), and this is what retries them;
    - anything changed by a path that bypasses signals, chiefly the admin's
      bulk actions, which use ``queryset.update()``.

    Idempotent: the service fingerprints each payload and skips events whose
    listing already matches, so the hourly cadence costs one hash per event
    and no API calls in the steady state.
    """
    return _run(request, "echo_lu_sync", "sync_events_to_echo")
