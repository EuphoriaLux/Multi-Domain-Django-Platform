"""
Crush.lu Coach Push Notification API Endpoints
Handles subscription management for Crush Coaches.
Completely separate from user push API to avoid conflicts.
"""

import json
import logging
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .decorators import crush_login_required
from .models import CrushCoach, CoachPushSubscription
from .coach_notifications import send_coach_test_notification

logger = logging.getLogger(__name__)


def get_coach_or_error(request):
    """
    Helper to get the CrushCoach object for the current user.
    Returns (coach, None) on success or (None, JsonResponse) on error.
    """
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
        return coach, None
    except CrushCoach.DoesNotExist:
        return None, JsonResponse({
            'success': False,
            'error': 'You are not an active coach'
        }, status=403)


@crush_login_required
@require_http_methods(["GET"])
def get_vapid_public_key(request):
    """
    Return the VAPID public key for push subscription.
    Same key as user push - the difference is in the subscription storage.
    """
    coach, error = get_coach_or_error(request)
    if error:
        return error

    if not hasattr(settings, 'VAPID_PUBLIC_KEY') or not settings.VAPID_PUBLIC_KEY:
        return JsonResponse({
            'success': False,
            'error': 'Push notifications not configured'
        }, status=500)

    return JsonResponse({
        'success': True,
        'publicKey': settings.VAPID_PUBLIC_KEY
    })


@crush_login_required
@csrf_exempt
@require_http_methods(["POST"])
def subscribe_push(request):
    """
    Subscribe coach to push notifications.
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
    coach, error = get_coach_or_error(request)
    if error:
        return error

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
    subscription, created = CoachPushSubscription.objects.update_or_create(
        coach=coach,
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
    logger.info(f"Coach push subscription {action} for {coach.user.username}")

    return JsonResponse({
        'success': True,
        'message': f'Subscription {action} successfully',
        'subscriptionId': subscription.id
    })


@crush_login_required
@csrf_exempt
@require_http_methods(["POST"])
def unsubscribe_push(request):
    """
    Unsubscribe coach from push notifications.
    Expects JSON body with endpoint to remove.

    Request body:
    {
        "endpoint": "https://..."
    }
    """
    coach, error = get_coach_or_error(request)
    if error:
        return error

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
    deleted_count, _ = CoachPushSubscription.objects.filter(
        coach=coach,
        endpoint=data['endpoint']
    ).delete()

    if deleted_count > 0:
        logger.info(f"Coach push subscription removed for {coach.user.username}")
        return JsonResponse({
            'success': True,
            'message': 'Subscription removed successfully'
        })
    else:
        return JsonResponse({
            'success': False,
            'error': 'Subscription not found'
        }, status=404)


@crush_login_required
@require_http_methods(["GET"])
def list_subscriptions(request):
    """
    List all push subscriptions for the current coach.
    """
    coach, error = get_coach_or_error(request)
    if error:
        return error

    subscriptions = CoachPushSubscription.objects.filter(coach=coach)

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
                    'newSubmissions': sub.notify_new_submissions,
                    'screeningReminders': sub.notify_screening_reminders,
                    'userResponses': sub.notify_user_responses,
                    'systemAlerts': sub.notify_system_alerts,
                }
            }
            for sub in subscriptions
        ]
    })


@crush_login_required
@csrf_exempt
@require_http_methods(["POST"])
def update_subscription_preferences(request):
    """
    Update notification preferences for a coach subscription.

    Request body:
    {
        "subscriptionId": 123,
        "preferences": {
            "newSubmissions": true,
            "screeningReminders": true,
            "userResponses": false,
            "systemAlerts": true
        }
    }
    """
    coach, error = get_coach_or_error(request)
    if error:
        return error

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
        subscription = CoachPushSubscription.objects.get(
            id=subscription_id,
            coach=coach
        )
    except CoachPushSubscription.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Subscription not found'
        }, status=404)

    # Update preferences
    if 'newSubmissions' in preferences:
        subscription.notify_new_submissions = preferences['newSubmissions']
    if 'screeningReminders' in preferences:
        subscription.notify_screening_reminders = preferences['screeningReminders']
    if 'userResponses' in preferences:
        subscription.notify_user_responses = preferences['userResponses']
    if 'systemAlerts' in preferences:
        subscription.notify_system_alerts = preferences['systemAlerts']

    subscription.save()

    return JsonResponse({
        'success': True,
        'message': 'Preferences updated successfully'
    })


@crush_login_required
@csrf_exempt
@require_http_methods(["POST"])
def send_test_push(request):
    """
    Send a test push notification to verify coach push setup.
    """
    coach, error = get_coach_or_error(request)
    if error:
        return error

    result = send_coach_test_notification(coach)

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
