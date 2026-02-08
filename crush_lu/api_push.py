"""
Crush.lu Push Notification API Endpoints
Handles subscription management and notification delivery
"""

import json
import logging
import re
import os
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from .models import PushSubscription, CoachPushSubscription, CrushCoach
from .push_notifications import send_test_notification

logger = logging.getLogger(__name__)


@login_required
@require_http_methods(["GET"])
def get_vapid_public_key(request):
    """
    Return the VAPID public key for push subscription.
    This is needed by the frontend to subscribe to push notifications.

    VAPID public key should be a base64url-encoded P-256 ECDSA public key,
    typically 87 characters long.
    """
    if not hasattr(settings, 'VAPID_PUBLIC_KEY') or not settings.VAPID_PUBLIC_KEY:
        logger.error("VAPID_PUBLIC_KEY not configured - push notifications unavailable")
        return JsonResponse({
            'success': False,
            'error': 'Push notifications not configured on server'
        }, status=503)

    vapid_key = settings.VAPID_PUBLIC_KEY

    # Basic validation: VAPID public keys should be ~87 chars base64url
    if len(vapid_key) < 80 or len(vapid_key) > 100:
        logger.error(f"VAPID_PUBLIC_KEY has invalid length: {len(vapid_key)} (expected ~87)")
        return JsonResponse({
            'success': False,
            'error': 'Push notifications misconfigured on server'
        }, status=503)

    # Check for valid base64url characters
    if not re.match(r'^[A-Za-z0-9_-]+$', vapid_key):
        logger.error("VAPID_PUBLIC_KEY contains invalid characters")
        return JsonResponse({
            'success': False,
            'error': 'Push notifications misconfigured on server'
        }, status=503)

    return JsonResponse({
        'success': True,
        'publicKey': vapid_key
    })


@login_required
@csrf_exempt  # Push subscriptions use their own authentication
@require_http_methods(["POST"])
def subscribe_push(request):
    """
    Subscribe user to push notifications.
    Uses device fingerprint for stable device identification across sessions.

    When a fingerprint matches an existing subscription, UPDATE that subscription
    with the new endpoint instead of creating a duplicate.

    Request body:
    {
        "endpoint": "https://...",
        "keys": {
            "p256dh": "...",
            "auth": "..."
        },
        "userAgent": "...",  # optional
        "deviceName": "...",  # optional
        "deviceFingerprint": "..."  # optional but recommended
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON'
        }, status=400)

    # Validate required fields
    if 'endpoint' not in data or 'keys' not in data:
        return JsonResponse({
            'success': False,
            'error': 'Missing endpoint or keys'
        }, status=400)

    keys = data['keys']
    if 'p256dh' not in keys or 'auth' not in keys:
        return JsonResponse({
            'success': False,
            'error': 'Missing p256dh or auth keys'
        }, status=400)

    fingerprint = data.get('deviceFingerprint', '')
    subscription = None
    created = False

    # Strategy 1: Try to find by fingerprint first (stable identifier)
    # This handles the case where the endpoint changed (e.g., after logout/login)
    if fingerprint:
        existing_by_fingerprint = PushSubscription.objects.filter(
            user=request.user,
            device_fingerprint=fingerprint
        ).first()

        if existing_by_fingerprint:
            # Same physical device, update endpoint and keys
            existing_by_fingerprint.endpoint = data['endpoint']
            existing_by_fingerprint.p256dh_key = keys['p256dh']
            existing_by_fingerprint.auth_key = keys['auth']
            existing_by_fingerprint.user_agent = data.get('userAgent', '')
            existing_by_fingerprint.device_name = data.get('deviceName', '')
            existing_by_fingerprint.enabled = True
            existing_by_fingerprint.failure_count = 0
            existing_by_fingerprint.save()
            subscription = existing_by_fingerprint
            created = False

            # Clean up any stale subscriptions with the same endpoint from other fingerprints
            PushSubscription.objects.filter(
                user=request.user,
                endpoint=data['endpoint']
            ).exclude(id=subscription.id).delete()

    # Strategy 2: Fall back to endpoint-based matching (backwards compatibility)
    if not subscription:
        subscription, created = PushSubscription.objects.update_or_create(
            user=request.user,
            endpoint=data['endpoint'],
            defaults={
                'p256dh_key': keys['p256dh'],
                'auth_key': keys['auth'],
                'user_agent': data.get('userAgent', ''),
                'device_name': data.get('deviceName', ''),
                'device_fingerprint': fingerprint,
                'enabled': True,
                'failure_count': 0,
            }
        )

    action = 'created' if created else 'updated'
    fingerprint_info = f" (fingerprint: {fingerprint[:8]}...)" if fingerprint else ""
    logger.info(f"Push subscription {action} for {request.user.username}{fingerprint_info}")

    return JsonResponse({
        'success': True,
        'message': f'Subscription {action} successfully',
        'subscriptionId': subscription.id,
        'fingerprint': fingerprint
    })


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def refresh_subscription(request):
    """
    Refresh a push subscription when browser changes endpoint.
    Called by service worker pushsubscriptionchange event.

    Matches old subscription by endpoint or device fingerprint,
    updates with new endpoint/keys.

    Supports both PushSubscription (users) and CoachPushSubscription (coaches).
    """
    try:
        data = json.loads(request.body)
        old_endpoint = data.get('oldEndpoint')
        new_subscription_data = data.get('subscription', {})

        if not new_subscription_data.get('endpoint'):
            return JsonResponse({
                'success': False,
                'message': 'New subscription endpoint required'
            }, status=400)

        new_endpoint = new_subscription_data['endpoint']
        new_keys = new_subscription_data.get('keys', {})
        p256dh = new_keys.get('p256dh')
        auth = new_keys.get('auth')

        if not p256dh or not auth:
            return JsonResponse({
                'success': False,
                'message': 'Subscription keys (p256dh, auth) required'
            }, status=400)

        # Strategy 1: Find by old endpoint (most reliable)
        subscription = None
        if old_endpoint:
            # Try user subscriptions first
            subscription = PushSubscription.objects.filter(
                user=request.user,
                endpoint=old_endpoint
            ).first()

            # Try coach subscriptions if user is a coach
            if not subscription and hasattr(request.user, 'crush_coach'):
                subscription = CoachPushSubscription.objects.filter(
                    coach=request.user.crush_coach,
                    endpoint=old_endpoint
                ).first()

        # Strategy 2: If old endpoint not found, this is likely a fresh re-subscribe
        # Don't create duplicate - just update the subscribe endpoint to handle it
        if not subscription:
            # Check if new endpoint already exists (browser re-subscribed without our knowledge)
            subscription = PushSubscription.objects.filter(
                user=request.user,
                endpoint=new_endpoint
            ).first()

            if not subscription and hasattr(request.user, 'crush_coach'):
                subscription = CoachPushSubscription.objects.filter(
                    coach=request.user.crush_coach,
                    endpoint=new_endpoint
                ).first()

            if subscription:
                # Update keys and reset failure count
                subscription.p256dh_key = p256dh
                subscription.auth_key = auth
                subscription.failure_count = 0
                subscription.updated_at = timezone.now()
                subscription.save()

                return JsonResponse({
                    'success': True,
                    'message': 'Subscription already exists, keys updated'
                })

        if subscription:
            # Update endpoint and keys
            subscription.endpoint = new_endpoint
            subscription.p256dh_key = p256dh
            subscription.auth_key = auth
            subscription.failure_count = 0  # Reset failures
            subscription.updated_at = timezone.now()
            subscription.save()

            return JsonResponse({
                'success': True,
                'message': 'Subscription refreshed successfully'
            })
        else:
            # No existing subscription found - shouldn't happen in normal flow
            # but handle gracefully by creating new subscription
            return JsonResponse({
                'success': False,
                'message': 'Original subscription not found. Please re-subscribe manually.',
                'action': 'resubscribe'
            }, status=404)

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Invalid JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Error refreshing push subscription: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': 'Server error refreshing subscription'
        }, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def validate_subscription(request):
    """
    Check if a push subscription endpoint is still valid in our database.
    Used by frontend health checks.

    Supports both PushSubscription (users) and CoachPushSubscription (coaches).
    """
    try:
        data = json.loads(request.body)
        endpoint = data.get('endpoint')

        if not endpoint:
            return JsonResponse({
                'success': False,
                'message': 'Endpoint required'
            }, status=400)

        # Check if subscription exists and is enabled (try user first, then coach)
        subscription = PushSubscription.objects.filter(
            user=request.user,
            endpoint=endpoint,
            enabled=True
        ).first()

        if not subscription and hasattr(request.user, 'crush_coach'):
            subscription = CoachPushSubscription.objects.filter(
                coach=request.user.crush_coach,
                endpoint=endpoint,
                enabled=True
            ).first()

        if subscription:
            # Check if subscription has high failure count (may be dead)
            if subscription.failure_count >= 3:
                return JsonResponse({
                    'success': True,
                    'valid': False,
                    'reason': 'high_failure_count',
                    'message': 'Subscription may be expired (multiple send failures)'
                })

            # Check if subscription is very old (>90 days) - may be stale
            age_days = (timezone.now() - subscription.created_at).days
            if age_days > 90:
                return JsonResponse({
                    'success': True,
                    'valid': True,
                    'warning': 'old_subscription',
                    'age_days': age_days,
                    'message': f'Subscription is {age_days} days old, consider refreshing'
                })

            return JsonResponse({
                'success': True,
                'valid': True,
                'message': 'Subscription is healthy'
            })
        else:
            return JsonResponse({
                'success': True,
                'valid': False,
                'reason': 'not_found',
                'message': 'Subscription not found or disabled'
            })

    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Invalid JSON'
        }, status=400)
    except Exception as e:
        logger.error(f"Error validating push subscription: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'message': 'Server error'
        }, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def unsubscribe_push(request):
    """
    Unsubscribe user from push notifications.
    Expects JSON body with endpoint to remove.

    Request body:
    {
        "endpoint": "https://...",
        "deviceFingerprint": "..."  # optional but recommended
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON'
        }, status=400)

    if 'endpoint' not in data:
        return JsonResponse({
            'success': False,
            'error': 'Missing endpoint'
        }, status=400)

    # Delete subscription
    deleted_count, _ = PushSubscription.objects.filter(
        user=request.user,
        endpoint=data['endpoint']
    ).delete()

    if deleted_count > 0:
        logger.info(f"Push subscription removed for {request.user.username}")

        # Check if coach push subscription exists with same endpoint OR fingerprint
        # If so, tell frontend to keep the browser subscription active
        keep_browser_subscription = False
        try:
            coach = CrushCoach.objects.get(user=request.user, is_active=True)
            fingerprint = data.get('deviceFingerprint', '')

            # Build query: match by endpoint OR by fingerprint (if provided)
            query = Q(endpoint=data['endpoint'])
            if fingerprint:
                query = query | Q(device_fingerprint=fingerprint)

            keep_browser_subscription = CoachPushSubscription.objects.filter(
                coach=coach
            ).filter(query).exists()
        except CrushCoach.DoesNotExist:
            pass

        return JsonResponse({
            'success': True,
            'message': 'Subscription removed successfully',
            'keep_browser_subscription': keep_browser_subscription
        })
    else:
        return JsonResponse({
            'success': False,
            'error': 'Subscription not found'
        }, status=404)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def delete_push_subscription(request):
    """
    Delete a specific push subscription by ID.
    Allows users to remove subscriptions from devices they no longer have access to.

    Request body:
    {
        "subscription_id": 123
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON'
        }, status=400)

    subscription_id = data.get('subscription_id')

    if not subscription_id:
        return JsonResponse({
            'success': False,
            'error': 'subscription_id required'
        }, status=400)

    # Delete subscription - must belong to current user
    deleted_count, _ = PushSubscription.objects.filter(
        id=subscription_id,
        user=request.user
    ).delete()

    if deleted_count > 0:
        logger.info(f"Push subscription {subscription_id} deleted for {request.user.username}")
        return JsonResponse({
            'success': True,
            'message': 'Subscription deleted successfully'
        })
    else:
        return JsonResponse({
            'success': False,
            'error': 'Subscription not found'
        }, status=404)


@login_required
@require_http_methods(["GET"])
def list_subscriptions(request):
    """
    List all push subscriptions for the current user.
    """
    subscriptions = PushSubscription.objects.filter(user=request.user)

    return JsonResponse({
        'success': True,
        'subscriptions': [
            {
                'id': sub.id,
                'deviceName': sub.device_name or 'Unknown Device',
                'enabled': sub.enabled,
                'createdAt': sub.created_at.isoformat(),
                'lastUsedAt': sub.last_used_at.isoformat() if sub.last_used_at else None,
                'preferences': {
                    'newMessages': sub.notify_new_messages,
                    'eventReminders': sub.notify_event_reminders,
                    'newConnections': sub.notify_new_connections,
                    'profileUpdates': sub.notify_profile_updates,
                }
            }
            for sub in subscriptions
        ]
    })


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def update_subscription_preferences(request):
    """
    Update notification preferences for a subscription.

    Request body:
    {
        "subscriptionId": 123,
        "preferences": {
            "newMessages": true,
            "eventReminders": true,
            "newConnections": false,
            "profileUpdates": true
        }
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON'
        }, status=400)

    subscription_id = data.get('subscriptionId')
    preferences = data.get('preferences', {})

    if not subscription_id:
        return JsonResponse({
            'success': False,
            'error': 'Missing subscriptionId'
        }, status=400)

    try:
        subscription = PushSubscription.objects.get(
            id=subscription_id,
            user=request.user
        )
    except PushSubscription.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Subscription not found'
        }, status=404)

    # Update preferences
    if 'newMessages' in preferences:
        subscription.notify_new_messages = preferences['newMessages']
    if 'eventReminders' in preferences:
        subscription.notify_event_reminders = preferences['eventReminders']
    if 'newConnections' in preferences:
        subscription.notify_new_connections = preferences['newConnections']
    if 'profileUpdates' in preferences:
        subscription.notify_profile_updates = preferences['profileUpdates']

    subscription.save()

    return JsonResponse({
        'success': True,
        'message': 'Preferences updated successfully'
    })


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def send_test_push(request):
    """
    Send a test push notification to verify everything is working.
    """
    result = send_test_notification(request.user)

    if result['success'] > 0:
        return JsonResponse({
            'success': True,
            'message': f'Test notification sent to {result["success"]} device(s)',
            'details': result
        })
    else:
        return JsonResponse({
            'success': False,
            'error': 'No active subscriptions or notification failed',
            'details': result
        }, status=400)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def mark_pwa_user(request):
    """
    Mark the current user as a PWA user.

    Called when the PWA is installed on the user's device.
    This enables push notification prompts in the UI.

    Response:
    {
        "success": true,
        "message": "PWA status updated",
        "isPwaUser": true
    }
    """
    from django.utils import timezone
    from .models import UserActivity

    try:
        # Get or create UserActivity record for tracking PWA usage
        activity, created = UserActivity.objects.get_or_create(
            user=request.user,
            defaults={'last_seen': timezone.now()}
        )
        activity.is_pwa_user = True
        activity.last_pwa_visit = timezone.now()
        activity.save(update_fields=['is_pwa_user', 'last_pwa_visit'])

        logger.info(f"Marked {request.user.username} as PWA user")

        return JsonResponse({
            'success': True,
            'message': 'PWA status updated',
            'isPwaUser': True
        })

    except Exception as e:
        logger.error(f"Error marking PWA user: {e}")
        return JsonResponse({
            'success': False,
            'error': 'Failed to update PWA status'
        }, status=500)


@login_required
@require_http_methods(["GET"])
def get_pwa_status(request):
    """
    Get the current user's PWA status.

    Response:
    {
        "success": true,
        "isPwaUser": true,
        "lastPwaVisit": "2024-01-15T10:30:00Z"
    }
    """
    from .models import UserActivity

    try:
        activity = UserActivity.objects.get(user=request.user)
        return JsonResponse({
            'success': True,
            'isPwaUser': activity.is_pwa_user,
            'lastPwaVisit': activity.last_pwa_visit.isoformat() if activity.last_pwa_visit else None
        })

    except UserActivity.DoesNotExist:
        return JsonResponse({
            'success': True,
            'isPwaUser': False,
            'lastPwaVisit': None
        })


@csrf_exempt
@require_http_methods(["POST"])
def run_subscription_health_check(request):
    """
    Endpoint for Azure Logic App or external scheduler.
    Protected by Azure App Service authentication or secret token.

    This endpoint runs the push subscription health check management command
    to identify and clean up stale/failing subscriptions.
    """
    # Verify request comes from trusted source
    auth_header = request.headers.get('Authorization')
    expected_token = os.getenv('HEALTH_CHECK_SECRET_TOKEN')

    if not expected_token or auth_header != f'Bearer {expected_token}':
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        # Call management command with cleanup and 90-day threshold
        call_command(
            'check_push_subscription_health',
            '--cleanup',
            '--age-threshold', '90',
            '--include-coaches'
        )
        return JsonResponse({
            'success': True,
            'message': 'Health check completed successfully'
        })
    except Exception as e:
        logger.error(f"Error running health check: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'An error occurred during health check'
        }, status=500)
