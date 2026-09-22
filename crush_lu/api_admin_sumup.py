"""
Admin API endpoint for the SumUp Tier-2 refund reconciliation sweep.

Invoked daily by the ``SumUpReconciliation`` Azure Function timer in
``azure-functions/hybrid-maintenance/``. Same thin-wrapper shape as
``api_admin_campaigns``:

- Bearer token matching ``settings.ADMIN_API_KEY`` (``crush_lu.api_admin_auth``).
- Outside ``i18n_patterns`` so the Function hits a stable, unprefixed
  ``/api/admin/sumup-reconciliation/`` path.
- Gated by ``settings.SUMUP_RECONCILIATION_ENABLED`` (default off): when off it
  answers ``200 {"skipped": true, ...}`` and runs nothing.

It runs ``reconcile_sumup_payments``' sweep inline with the command's defaults
(30-day window, partial refunds left for a human, no dry run) plus a wall-clock
budget, and reports the sweep's counters. It OBSERVES refunds a human already
took in the SumUp dashboard or on a terminal; it never issues one. Only the
read-side client calls are reachable from here, and a structural test pins
that.

Contract: ai-memory-hub/policies/sumup-tier2-refund-automation-contract.md
"""

from __future__ import annotations

import logging
from io import StringIO

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from crush_lu.api_admin_auth import (
    authenticate_admin_request as _authenticate_admin_request,
)
from crush_lu.api_admin_auth import unauthorized as _unauthorized

logger = logging.getLogger(__name__)

# Contract §6.2: the window filters on when the member PAID, not on when the
# refund happened, so it must stay at the command's 30-day default.
RECONCILIATION_DAYS = 30

# Contract §6.3: the App Service front end abandons a request at ~230 s and
# leaves no request telemetry. Stop starting new rows after this many seconds;
# each row costs at most two SumUp reads with a 10 s timeout each, so the
# request still ends well inside the Function's 110 s HTTP timeout.
RECONCILIATION_BUDGET_SECONDS = 80

_FLAG = "SUMUP_RECONCILIATION_ENABLED"


@csrf_exempt
@require_http_methods(["POST"])
def sumup_reconciliation_endpoint(request):
    """POST /api/admin/sumup-reconciliation/

    Run one bounded reconciliation sweep over recent PAID SumUp payments.
    Safe to retry or overlap: only PAID rows are selected, and each write
    re-checks the row under ``select_for_update`` and skips it if it is no
    longer PAID, so a refund is applied exactly once.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    if not getattr(settings, _FLAG, False):
        logger.warning("[sumup_reconciliation] skipped: %s is off", _FLAG)
        return JsonResponse(
            {"skipped": True, "reason": f"{_FLAG} is off"},
            status=200,
        )

    from crush_lu.management.commands.reconcile_sumup_payments import Command

    started = timezone.now()
    buffer = StringIO()
    command = Command(stdout=buffer, stderr=buffer, no_color=True)
    try:
        # include_partial stays False: a partial refund is a human decision
        # (contract §3, §9.8). batch_delay is left at the command default.
        counters = command.run_sweep(
            days=RECONCILIATION_DAYS,
            dry_run=False,
            include_partial=False,
            quiet=True,
            budget_seconds=RECONCILIATION_BUDGET_SECONDS,
        )
    except Exception:  # noqa: BLE001
        logger.exception("[sumup_reconciliation] Unhandled error")
        return JsonResponse({"error": "internal_error"}, status=500)

    body = {
        "status": "ok",
        "timestamp": started.isoformat(),
        "checked": counters["checked"],
        "reconciled": counters["reconciled"],
        "partial": counters["partial"],
        "errors": counters["errors"],
        "unchecked": counters["unchecked"],
    }
    # One structured line, queryable in App Insights without a database.
    # Counts only — no references, emails or payloads (contract §7.3).
    needs_attention = body["partial"] or body["errors"] or body["unchecked"]
    logger.log(
        logging.WARNING if needs_attention else logging.INFO,
        "[sumup_reconciliation] checked=%s reconciled=%s partial=%s errors=%s "
        "unchecked=%s",
        body["checked"],
        body["reconciled"],
        body["partial"],
        body["errors"],
        body["unchecked"],
    )
    return JsonResponse(body, status=202)
