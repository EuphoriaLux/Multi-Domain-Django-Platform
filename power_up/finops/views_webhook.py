# power_up/finops/views_webhook.py
"""
Webhook endpoint for triggering cost data sync
Can be called by Azure Logic Apps, Azure Functions, or external schedulers

Security: Requires SECRET_SYNC_TOKEN in request headers
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.core.management import call_command
import json
import os
import io
import logging
import secrets

logger = logging.getLogger(__name__)


def _reject_invalid_sync_token(request):
    """Return an error response when the caller's sync token is unusable.

    Returns ``None`` when the request is authenticated, so callers can use it
    as an early-exit guard.
    """
    sync_token = request.headers.get('X-Sync-Token')
    expected_token = os.getenv('SECRET_SYNC_TOKEN') or getattr(settings, 'SECRET_SYNC_TOKEN', None)

    if not expected_token:
        return JsonResponse({
            'success': False,
            'error': 'Sync token not configured on server'
        }, status=500)

    if not sync_token or not secrets.compare_digest(sync_token, expected_token):
        return JsonResponse({
            'success': False,
            'error': 'Invalid sync token'
        }, status=403)

    return None


@csrf_exempt
@require_http_methods(["POST"])
def trigger_cost_sync(request):
    """Webhook endpoint to trigger cost data sync"""
    denied = _reject_invalid_sync_token(request)
    if denied is not None:
        return denied

    try:
        output_stream = io.StringIO()
        error_stream = io.StringIO()

        call_command('sync_daily_costs', stdout=output_stream, stderr=error_stream)

        output = output_stream.getvalue()
        errors = error_stream.getvalue()

        return JsonResponse({
            'success': True,
            'message': 'Cost sync completed successfully',
            'output': output,
            'errors': errors if errors else None
        })

    except Exception:
        logger.exception("Error during cost sync triggered via webhook")
        return JsonResponse({
            'success': False,
            'error': 'Internal server error during cost sync',
            'message': 'Cost sync failed'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def retail_price_sync_regions(request):
    """List the regions a caller must walk to assemble one day's snapshot.

    The scheduler fetches this instead of hard-coding the list, so the Function
    App and Django cannot silently disagree about which regions get captured.
    """
    denied = _reject_invalid_sync_token(request)
    if denied is not None:
        return denied

    from .retail_prices.connectors.azure import DEFAULT_EUROPEAN_REGIONS

    return JsonResponse({"success": True, "regions": list(DEFAULT_EUROPEAN_REGIONS)})


@csrf_exempt
@require_http_methods(["POST"])
def trigger_retail_price_sync(request):
    """Capture today's public Azure retail-price catalogue for ONE region.

    Deliberately single-region: the full European catalogue is ~200k rows over
    208 pages and cannot finish inside one App Service request, which the Azure
    load balancer cuts off at ~240s on Linux. Callers walk
    ``retail_price_sync_regions`` and post once per region.
    """
    denied = _reject_invalid_sync_token(request)
    if denied is not None:
        return denied

    from .retail_prices.connectors.azure import EUROPEAN_AZURE_REGIONS

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse(
            {"success": False, "error": "Request body must be valid JSON"},
            status=400,
        )
    if not isinstance(body, dict):
        return JsonResponse(
            {"success": False, "error": "Request body must be a JSON object"},
            status=400,
        )

    region = (body.get("region") or request.GET.get("region") or "").strip().lower()
    if not region:
        return JsonResponse(
            {
                "success": False,
                "error": "A 'region' is required; sync one region per request.",
            },
            status=400,
        )
    if region not in EUROPEAN_AZURE_REGIONS:
        return JsonResponse(
            {"success": False, "error": f"Unknown region '{region}'"},
            status=400,
        )

    try:
        output_stream = io.StringIO()
        error_stream = io.StringIO()
        call_command(
            "sync_retail_prices",
            currency="EUR",
            regions=[region],
            stdout=output_stream,
            stderr=error_stream,
        )
        return JsonResponse(
            {
                "success": True,
                "region": region,
                "message": f"Retail price snapshot completed for {region}",
                "output": output_stream.getvalue(),
                "errors": error_stream.getvalue() or None,
            }
        )
    except Exception:
        logger.exception(
            "Error during retail price sync triggered via webhook for %s", region
        )
        return JsonResponse(
            {
                "success": False,
                "region": region,
                "error": "Internal server error during retail price sync",
                "message": f"Retail price sync failed for {region}",
            },
            status=500,
        )


@require_http_methods(["GET"])
def sync_status(request):
    """Check status of cost exports (public endpoint)"""
    from .models import CostExport

    try:
        total_exports = CostExport.objects.count()
        completed_exports = CostExport.objects.filter(import_status='completed').count()
        failed_exports = CostExport.objects.filter(import_status='failed').count()
        processing_exports = CostExport.objects.filter(import_status='processing').count()

        latest_export = CostExport.objects.filter(import_status='completed').first()

        return JsonResponse({
            'success': True,
            'stats': {
                'total_exports': total_exports,
                'completed': completed_exports,
                'failed': failed_exports,
                'processing': processing_exports,
            },
            'latest_export': {
                'subscription': latest_export.subscription_name if latest_export else None,
                'billing_period_start': latest_export.billing_period_start if latest_export else None,
                'billing_period_end': latest_export.billing_period_end if latest_export else None,
                'records_imported': latest_export.records_imported if latest_export else 0,
                'import_completed_at': latest_export.import_completed_at if latest_export else None,
            } if latest_export else None
        })

    except Exception:
        logger.exception("Error while fetching sync status")
        return JsonResponse({
            'success': False,
            'error': 'Internal server error while fetching sync status'
        }, status=500)
