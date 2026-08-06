"""
Admin API endpoint for triggering Outlook contact sync
Called by Azure Function on scheduled basis

Secured with Bearer token authentication
"""

import logging
import threading
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from crush_lu.api_admin_auth import authenticate_admin_request as _authenticate_admin_request
from crush_lu.services.graph_contacts import GraphContactsService, is_sync_enabled

logger = logging.getLogger(__name__)


def _validated_service():
    """
    Build a GraphContactsService and prove it can authenticate.

    Both endpoints below hand their work to a background thread and answer 202.
    That answer is a lie if the service cannot even start, so the failure modes
    that are knowable up front -- missing credentials, a broken client secret,
    no msal package -- are surfaced synchronously as a 500 instead.

    Raises:
        Exception: if the service cannot be constructed or cannot get a token.
    """
    service = GraphContactsService()
    service.get_access_token()
    return service


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

    # Prove the job can actually start before promising 202. Building the
    # service validates credentials and acquiring a token exercises MSAL and
    # Graph auth; doing that inside the thread would report "started" for a run
    # that dies immediately, which is the same 200-while-doing-nothing silence
    # that hid the dead timers on the sibling function app.
    try:
        service = _validated_service()
    except Exception as e:
        logger.error(f"Contact sync cannot start: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Outlook contact sync is not operational'
        }, status=500)

    # Run sync in background thread to avoid App Service request timeout (230s)
    def _run_sync():
        try:
            import django
            django.db.connections.close_all()
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
    Delete the Outlook contacts this service created.

    Use with caution - this removes every contact in the shared mailbox that
    carries the 'Crush.lu' category, whether or not the database still tracks
    it. Contacts a human filed in noreply@crush.lu are left alone unless the
    request explicitly opts in with {"include_foreign": true}.

    Authentication: Bearer token via Authorization header

    Runs in a background thread and returns 202 immediately; the resulting
    counts are logged rather than returned.

    Response:
    {
        "success": true,
        "message": "Deletion started in background",
        "include_foreign": false,
        "timestamp": "2026-01-29T17:00:00Z"
    }
    """
    import json

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

    # Opt-in escape hatch for wiping contacts we did not create. Off by default:
    # noreply@crush.lu is a shared mailbox and this endpoint is reachable with
    # nothing but the bearer token.
    # Compared with `is True` rather than coerced: bool("false") and bool("0")
    # are both True in Python, so a caller sending a stringified flag from a
    # templated env var or form value would silently opt into the destructive
    # path. Only a real JSON `true` enables it.
    include_foreign = False
    if request.body:
        try:
            include_foreign = (
                json.loads(request.body).get('include_foreign', False) is True
            )
        except (ValueError, AttributeError):
            pass

    # Run in a background thread, like sync_contacts_endpoint above and for the
    # same reason: the deletion paces itself between contacts to stay inside the
    # per-mailbox request budget, which on a large mailbox exceeds the App
    # Service 230s request ceiling on its own -- the caller would lose the
    # result and the deletion would be left half-done.
    # Same reasoning as the sync endpoint: an operator running a *destructive*
    # cleanup must not be told it started when the credentials are broken and
    # nothing will be touched.
    try:
        service = _validated_service()
    except Exception as e:
        logger.error(f"Contact deletion cannot start: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Outlook contact sync is not operational'
        }, status=500)

    def _run_delete():
        try:
            import django
            django.db.connections.close_all()
            logger.warning(
                f"Deleting Outlook contacts via admin API (including orphaned; "
                f"include_foreign={include_foreign})"
            )
            stats = service.delete_all_contacts_from_outlook(
                include_foreign=include_foreign
            )
            logger.info(
                f"Contact deletion completed: "
                f"total={stats['total']}, deleted={stats['deleted']}, "
                f"errors={stats['errors']}, skipped={stats.get('skipped', 0)}"
            )
        except Exception as e:
            logger.error(f"Error during contact deletion: {e}", exc_info=True)

    thread = threading.Thread(target=_run_delete, daemon=True)
    thread.start()

    return JsonResponse({
        'success': True,
        'message': 'Deletion started in background',
        'include_foreign': include_foreign,
        'timestamp': timezone.now().isoformat()
    }, status=202)


@csrf_exempt
@require_http_methods(["GET"])
def sync_contacts_health(request):
    """
    Health check endpoint for contact sync service.

    Requires admin API key authentication to prevent information disclosure.

    Returns:
        JSON response with service status and configuration
    """
    from django.utils import timezone

    if not _authenticate_admin_request(request):
        return JsonResponse({
            'success': False,
            'error': 'Unauthorized'
        }, status=401)

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
