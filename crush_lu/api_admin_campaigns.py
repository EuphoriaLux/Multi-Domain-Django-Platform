"""
Admin API endpoint for multi-channel campaign dispatch.

Invoked by the ``CampaignDispatch`` Azure Function timer trigger in
``azure-functions/hybrid-maintenance/`` every 5 minutes. Mirrors the thin
wrapper pattern in ``api_admin_metrics``:

- Requires a Bearer token matching ``settings.ADMIN_API_KEY`` (shared helper
  in ``crush_lu.api_admin_auth``).
- Lives outside ``i18n_patterns`` so the Function can hit a stable
  ``/api/admin/campaigns/dispatch/`` path with no language prefix.
- Delegates to ``crush_lu.services.campaigns.dispatch_campaigns`` — the same
  logic the ``dispatch_campaigns`` management command runs — which keeps each
  tick bounded (per-channel limits + wall-clock budget) so the request stays
  well inside the gunicorn timeout.
- Gated by ``settings.CAMPAIGN_DISPATCH_ENABLED`` (default off) so the
  feature stays dormant per environment until explicitly enabled.
"""
from __future__ import annotations

import logging
from io import StringIO

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from crush_lu.api_admin_auth import authenticate_admin_request as _authenticate_admin_request
from crush_lu.api_admin_auth import unauthorized as _unauthorized

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["POST"])
def dispatch_campaigns_endpoint(request):
    """POST /api/admin/campaigns/dispatch/

    Run one bounded campaign dispatch tick: promote due scheduled campaigns
    to sending and continue in-flight ones in per-channel batches. Safe to
    retry or overlap — campaigns are claimed via a heartbeat and every
    channel tracks per-recipient state, so no recipient is contacted twice.
    """
    if not _authenticate_admin_request(request):
        return _unauthorized(request)

    if not getattr(settings, "CAMPAIGN_DISPATCH_ENABLED", False):
        return JsonResponse(
            {"skipped": True, "reason": "CAMPAIGN_DISPATCH_ENABLED is off"},
            status=200,
        )

    from crush_lu.services.campaigns import dispatch_campaigns

    started = timezone.now()
    buffer = StringIO()
    try:
        summary = dispatch_campaigns(stdout=buffer)
    except Exception:  # noqa: BLE001
        logger.exception("[campaign_dispatch] Unhandled error")
        return JsonResponse({"error": "internal_error"}, status=500)

    log_output = buffer.getvalue().strip()
    if log_output:
        logger.info("[campaign_dispatch] %s", log_output)
    return JsonResponse(
        {
            "status": "ok",
            "timestamp": started.isoformat(),
            "promoted": summary["promoted"],
            "campaigns": summary["campaigns"],
        },
        status=202,
    )
