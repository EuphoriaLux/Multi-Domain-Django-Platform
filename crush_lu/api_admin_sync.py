"""
Admin API endpoint for triggering Outlook contact sync
Called by Azure Function on scheduled basis

Secured with Bearer token authentication
"""

import json
import logging
import secrets
import threading
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from crush_lu.services.graph_contacts import GraphContactsService, is_sync_enabled

logger = logging.getLogger(__name__)


def _authenticate_admin_request(request) -> bool:
    """
    Authenticate admin API request using Bearer token.

    Returns:
        bool: True if authenticated, False otherwise
    """
    auth_header = request.headers.get('Authorization', '')

    if not auth_header.startswith('Bearer '):
        return False

    token = auth_header.replace('Bearer ', '', 1)
    expected_token = getattr(settings, 'ADMIN_API_KEY', None)

    if not expected_token:
        logger.error("ADMIN_API_KEY not configured in settings")
        return False

    return secrets.compare_digest(token, expected_token)


@csrf_exempt
@require_http_methods(["POST"])
def sync_contacts_endpoint(request):
    """
    Trigger full Outlook contact sync.

    Called by Azure Function on scheduled basis to ensure contacts stay in sync.

    Authentication: Bearer token via Authorization header

    Request body:
    {
        "command": "sync_contacts_to_outlook",
        "args": [],
        "options": {}
    }

    Response:
    {
        "success": true,
        "stats": {
            "total": 150,
            "synced": 145,
            "skipped": 5,
            "errors": 0
        },
        "timestamp": "2025-01-29T12:00:00Z"
    }
    """
    from django.utils import timezone

    # Authenticate request
    if not _authenticate_admin_request(request):
        logger.warning(f"Unauthorized contact sync attempt from {request.META.get('REMOTE_ADDR')}")
        return JsonResponse({
            'success': False,
            'error': 'Unauthorized'
        }, status=401)

    # Check if sync is enabled
    if not is_sync_enabled():
        logger.warning("Contact sync endpoint called but OUTLOOK_CONTACT_SYNC_ENABLED is not true")
        return JsonResponse({
            'success': False,
            'error': 'Outlook contact sync is not enabled for this environment'
        }, status=503)

    # Run sync in background thread to avoid App Service request timeout (230s)
    def _run_sync():
        try:
            import django
            django.db.connections.close_all()
            service = GraphContactsService()
            logger.info("Starting scheduled Outlook contact sync via admin API (background)")
            stats = service.sync_all_profiles(dry_run=False)
            logger.info(
                f"Scheduled contact sync completed: "
                f"total={stats['total']}, synced={stats['synced']}, "
                f"skipped={stats['skipped']}, errors={stats['errors']}"
            )
        except Exception as e:
            logger.error(f"Error during scheduled contact sync: {e}", exc_info=True)

    thread = threading.Thread(target=_run_sync, daemon=True)
    thread.start()

    return JsonResponse({
        'success': True,
        'message': 'Sync started in background',
        'timestamp': timezone.now().isoformat()
    }, status=202)


@csrf_exempt
@require_http_methods(["POST"])
def delete_all_contacts_endpoint(request):
    """
    Delete all synced Outlook contacts.

    Use with caution - this removes all contacts that have outlook_contact_id.

    Authentication: Bearer token via Authorization header

    Response:
    {
        "success": true,
        "stats": {
            "total": 161,
            "deleted": 161,
            "errors": 0
        },
        "timestamp": "2026-01-29T17:00:00Z"
    }
    """
    from django.utils import timezone

    # Authenticate request
    if not _authenticate_admin_request(request):
        logger.warning(f"Unauthorized delete attempt from {request.META.get('REMOTE_ADDR')}")
        return JsonResponse({
            'success': False,
            'error': 'Unauthorized'
        }, status=401)

    # Check if sync is enabled
    if not is_sync_enabled():
        logger.warning("Delete endpoint called but OUTLOOK_CONTACT_SYNC_ENABLED is not true")
        return JsonResponse({
            'success': False,
            'error': 'Outlook contact sync is not enabled for this environment'
        }, status=503)

    try:
        # Initialize service
        service = GraphContactsService()

        # Delete all contacts directly from Outlook (not just database-tracked ones)
        logger.warning("Deleting ALL Outlook contacts via admin API (including orphaned)")
        stats = service.delete_all_contacts_from_outlook()

        logger.info(
            f"Contact deletion completed: "
            f"total={stats['total']}, deleted={stats['deleted']}, "
            f"errors={stats['errors']}"
        )

        return JsonResponse({
            'success': True,
            'stats': stats,
            'timestamp': timezone.now().isoformat()
        })

    except Exception as e:
        logger.error(f"Error during contact deletion: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'An error occurred during contact deletion'
        }, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def sync_contacts_health(request):
    """
    Health check endpoint for contact sync service.

    Returns:
        JSON response with service status and configuration
    """
    from django.utils import timezone
    # Check if API key is configured
    has_api_key = bool(getattr(settings, 'ADMIN_API_KEY', None))

    # Check if sync is enabled
    sync_enabled = is_sync_enabled()

    # Check Graph API credentials
    import os
    has_tenant = os.getenv('GRAPH_TENANT_ID') or getattr(settings, 'GRAPH_TENANT_ID', None)
    has_client = os.getenv('GRAPH_CLIENT_ID') or getattr(settings, 'GRAPH_CLIENT_ID', None)
    has_secret = os.getenv('GRAPH_CLIENT_SECRET') or getattr(settings, 'GRAPH_CLIENT_SECRET', None)

    has_credentials = bool(has_tenant and has_client and has_secret)

    # Determine overall health
    is_healthy = sync_enabled and has_credentials and has_api_key

    return JsonResponse({
        'status': 'healthy' if is_healthy else 'degraded',
        'sync_enabled': sync_enabled,
        'has_credentials': has_credentials,
        'has_api_key': has_api_key,
        'timestamp': timezone.now().isoformat()
    })
