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
from io import StringIO

from django.core.management import CommandError, call_command
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from crush_lu.api_admin_auth import authenticate_admin_request as _authenticate_admin_request
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
