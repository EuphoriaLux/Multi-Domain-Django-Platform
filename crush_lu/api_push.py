"""
Crush.lu Push Notification API Endpoints
Handles subscription management and notification delivery
"""

import json
import logging
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import PushSubscription
from .push_notifications import send_test_notification

logger = logging.getLogger(__name__)


@login_required
@require_http_methods(["GET"])
def get_vapid_public_key(request):
    """
    Return the VAPID public key for push subscription.
    This is needed by the frontend to subscribe to push notifications.
    """
    if not hasattr(settings, 'VAPID_PUBLIC_KEY') or not settings.VAPID_PUBLIC_KEY:
        return JsonResponse({
            'success': False,
            'error': 'Push notifications not configured'
        }, status=500)

    return JsonResponse({
        'success': True,
        'publicKey': settings.VAPID_PUBLIC_KEY
    })


@login_required
@csrf_exempt  # Push subscriptions use their own authentication
@require_http_methods(["POST"])
def subscribe_push(request):
    """
    Subscribe user to push notifications.
    Expects JSON body with subscription data from browser's PushManager.

    Request body:
    {
        "endpoint": "https://...",
        "keys": {
            "p256dh": "...",
            "auth": "..."
        },
        "userAgent": "...",  # optional
        "deviceName": "..."  # optional
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

    # Create or update subscription
    subscription, created = PushSubscription.objects.update_or_create(
        user=request.user,
        endpoint=data['endpoint'],
        defaults={
            'p256dh_key': keys['p256dh'],
            'auth_key': keys['auth'],
            'user_agent': data.get('userAgent', ''),
            'device_name': data.get('deviceName', ''),
            'enabled': True,
            'failure_count': 0,
        }
    )

    action = 'created' if created else 'updated'
    logger.info(f"Push subscription {action} for {request.user.username}")

    return JsonResponse({
        'success': True,
        'message': f'Subscription {action} successfully',
        'subscriptionId': subscription.id
    })


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def unsubscribe_push(request):
    """
    Unsubscribe user from push notifications.
    Expects JSON body with endpoint to remove.

    Request body:
    {
        "endpoint": "https://..."
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
        return JsonResponse({
            'success': True,
            'message': 'Subscription removed successfully'
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
    from .models import CrushProfile

    try:
        profile = request.user.crushprofile
        profile.is_pwa_user = True
        profile.last_pwa_visit = timezone.now()
        profile.save(update_fields=['is_pwa_user', 'last_pwa_visit'])

        logger.info(f"Marked {request.user.username} as PWA user")

        return JsonResponse({
            'success': True,
            'message': 'PWA status updated',
            'isPwaUser': True
        })

    except CrushProfile.DoesNotExist:
        logger.warning(f"No CrushProfile for user {request.user.username}")
        return JsonResponse({
            'success': False,
            'error': 'Profile not found. Please complete your profile first.'
        }, status=404)

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
    from .models import CrushProfile

    try:
        profile = request.user.crushprofile
        return JsonResponse({
            'success': True,
            'isPwaUser': profile.is_pwa_user,
            'lastPwaVisit': profile.last_pwa_visit.isoformat() if profile.last_pwa_visit else None
        })

    except CrushProfile.DoesNotExist:
        return JsonResponse({
            'success': True,
            'isPwaUser': False,
            'lastPwaVisit': None
        })
