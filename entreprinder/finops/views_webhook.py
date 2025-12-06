# entreprinder/finops/views_webhook.py
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
import os
import io


@csrf_exempt
@require_http_methods(["POST"])
def trigger_cost_sync(request):
    """Webhook endpoint to trigger cost data sync"""
    sync_token = request.headers.get('X-Sync-Token')
    expected_token = os.getenv('SECRET_SYNC_TOKEN') or getattr(settings, 'SECRET_SYNC_TOKEN', None)

    if not expected_token:
        return JsonResponse({
            'success': False,
            'error': 'Sync token not configured on server'
        }, status=500)

    if sync_token != expected_token:
        return JsonResponse({
            'success': False,
            'error': 'Invalid sync token'
        }, status=403)

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

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e),
            'message': 'Cost sync failed'
        }, status=500)


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

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)
