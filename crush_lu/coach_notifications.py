"""
Crush.lu Coach Push Notification Utilities
Handles Web Push API notifications for Crush Coaches.
Completely separate from user notifications to avoid conflicts.
"""

import json
import logging
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext as _
from pywebpush import webpush, WebPushException
from .models import CoachPushSubscription, CrushCoach
from .push_notifications import user_language_context

logger = logging.getLogger(__name__)


def send_coach_push_notification(coach, title, body, url='/', tag='coach-notification', icon=None, badge=None):
    """
    Send a push notification to all of a coach's subscribed devices.

    Args:
        coach: CrushCoach object
        title: Notification title
        body: Notification message body
        url: URL to open when notification is clicked (default: '/')
        tag: Notification tag for grouping (default: 'coach-notification')
        icon: Icon URL (default: Crush.lu logo)
        badge: Badge icon URL (default: Crush.lu badge)

    Returns:
        dict: {
            'success': int,  # Number of successful sends
            'failed': int,   # Number of failed sends
            'total': int     # Total subscriptions attempted
        }
    """

    # Validate VAPID configuration
    if not hasattr(settings, 'VAPID_PRIVATE_KEY') or not settings.VAPID_PRIVATE_KEY:
        logger.error("VAPID_PRIVATE_KEY not configured in settings")
        return {'success': 0, 'failed': 0, 'total': 0}

    if not hasattr(settings, 'VAPID_PUBLIC_KEY') or not settings.VAPID_PUBLIC_KEY:
        logger.error("VAPID_PUBLIC_KEY not configured in settings")
        return {'success': 0, 'failed': 0, 'total': 0}

    # Get all active subscriptions for this coach
    subscriptions = CoachPushSubscription.objects.filter(coach=coach, enabled=True)

    if not subscriptions.exists():
        logger.info(f"No active push subscriptions for coach {coach.user.username}")
        return {'success': 0, 'failed': 0, 'total': 0}

    # Prepare notification payload
    payload = {
        'title': title,
        'body': body,
        'url': url,
        'tag': tag,
        'icon': icon or '/static/crush_lu/icons/icon-192x192.png',
        'badge': badge or '/static/crush_lu/icons/icon-72x72.png',
    }

    # Track results
    success_count = 0
    failed_count = 0

    # Send to each subscription
    for subscription in subscriptions:
        try:
            # Prepare subscription info for pywebpush
            subscription_info = {
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh_key,
                    "auth": subscription.auth_key
                }
            }

            # Send the push notification
            webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload),
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={
                    "sub": f"mailto:{settings.VAPID_ADMIN_EMAIL}"
                }
            )

            # Mark success
            subscription.mark_success()
            success_count += 1
            logger.info(f"Coach push notification sent to {coach.user.username} ({subscription.device_name})")

        except WebPushException as e:
            # Handle push errors (expired subscription, etc.)
            logger.warning(f"Coach WebPush failed for {coach.user.username}: {e}")
            # 410 Gone = subscription permanently invalid, delete immediately
            if e.response is not None and e.response.status_code == 410:
                logger.info(f"Deleting expired coach subscription for {coach.user.username} ({subscription.device_name})")
                subscription.delete()
            else:
                subscription.mark_failure()  # Auto-deletes after 5 failures
            failed_count += 1

        except Exception as e:
            # Catch-all for unexpected errors
            logger.error(f"Unexpected error sending coach push to {coach.user.username}: {e}")
            failed_count += 1

    return {
        'success': success_count,
        'failed': failed_count,
        'total': subscriptions.count()
    }


def send_coach_push_to_subscription(subscription, title, body, url='/', tag='coach-notification', icon=None, badge=None):
    """
    Send a push notification to a specific coach subscription (single device).

    Args:
        subscription: CoachPushSubscription object
        title: Notification title
        body: Notification message body
        url: URL to open when notification is clicked (default: '/')
        tag: Notification tag for grouping (default: 'coach-notification')
        icon: Icon URL (default: Crush.lu logo)
        badge: Badge icon URL (default: Crush.lu badge)

    Returns:
        dict: {
            'success': bool,  # Whether send was successful
            'error': str      # Error message if failed
        }
    """
    # Validate VAPID configuration
    if not hasattr(settings, 'VAPID_PRIVATE_KEY') or not settings.VAPID_PRIVATE_KEY:
        logger.error("VAPID_PRIVATE_KEY not configured in settings")
        return {'success': False, 'error': 'VAPID not configured'}

    if not hasattr(settings, 'VAPID_PUBLIC_KEY') or not settings.VAPID_PUBLIC_KEY:
        logger.error("VAPID_PUBLIC_KEY not configured in settings")
        return {'success': False, 'error': 'VAPID not configured'}

    # Check if subscription is enabled
    if not subscription.enabled:
        return {'success': False, 'error': 'Subscription is disabled'}

    # Prepare notification payload
    payload = {
        'title': title,
        'body': body,
        'url': url,
        'tag': tag,
        'icon': icon or '/static/crush_lu/icons/icon-192x192.png',
        'badge': badge or '/static/crush_lu/icons/icon-72x72.png',
    }

    try:
        # Prepare subscription info for pywebpush
        subscription_info = {
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.p256dh_key,
                "auth": subscription.auth_key
            }
        }

        # Send the push notification
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={
                "sub": f"mailto:{settings.VAPID_ADMIN_EMAIL}"
            }
        )

        # Mark success
        subscription.mark_success()
        logger.info(f"Coach push notification sent to {subscription.coach.user.username} ({subscription.device_name})")
        return {'success': True, 'error': None}

    except WebPushException as e:
        # Handle push errors (expired subscription, etc.)
        logger.warning(f"Coach WebPush failed for {subscription.coach.user.username}: {e}")
        subscription.mark_failure()
        return {'success': False, 'error': str(e)}

    except Exception as e:
        # Catch-all for unexpected errors
        logger.error(f"Unexpected error sending coach push to {subscription.coach.user.username}: {e}")
        return {'success': False, 'error': str(e)}


def notify_coach_new_submission(coach, submission):
    """
    Notify a coach when a new profile is assigned for review.

    Args:
        coach: CrushCoach object
        submission: ProfileSubmission object
    """
    # Check if coach has notifications enabled for new submissions
    subscriptions = CoachPushSubscription.objects.filter(
        coach=coach,
        enabled=True,
        notify_new_submissions=True
    )

    if not subscriptions.exists():
        logger.info(f"Coach {coach.user.username} has new submission notifications disabled")
        return {'success': 0, 'failed': 0, 'total': 0}

    profile = submission.profile
    user_name = profile.user.first_name or profile.user.username

    # Use context manager for thread-safe language activation
    with user_language_context(coach.user):
        title = _("New Profile to Review")
        body = _("%(name)s submitted a profile for review") % {'name': user_name}
        url = reverse('crush_lu:coach_review_profile', args=[submission.id])

    return send_coach_push_notification(
        coach=coach,
        title=title,
        body=body,
        url=url,
        tag=f"new-submission-{submission.id}"
    )


def notify_coach_user_revision(coach, submission):
    """
    Notify a coach when a user submits a revision after feedback.

    Args:
        coach: CrushCoach object
        submission: ProfileSubmission object
    """
    # Check if coach has notifications enabled for user responses
    subscriptions = CoachPushSubscription.objects.filter(
        coach=coach,
        enabled=True,
        notify_user_responses=True
    )

    if not subscriptions.exists():
        logger.info(f"Coach {coach.user.username} has user response notifications disabled")
        return {'success': 0, 'failed': 0, 'total': 0}

    profile = submission.profile
    user_name = profile.user.first_name or profile.user.username

    # Use context manager for thread-safe language activation
    with user_language_context(coach.user):
        title = _("Profile Revision Submitted")
        body = _("%(name)s updated their profile based on your feedback") % {'name': user_name}
        url = reverse('crush_lu:coach_review_profile', args=[submission.id])

    return send_coach_push_notification(
        coach=coach,
        title=title,
        body=body,
        url=url,
        tag=f"revision-{submission.id}"
    )


def notify_coach_screening_reminder(coach, submission):
    """
    Remind a coach about a pending screening call.

    Args:
        coach: CrushCoach object
        submission: ProfileSubmission object
    """
    # Check if coach has notifications enabled for screening reminders
    subscriptions = CoachPushSubscription.objects.filter(
        coach=coach,
        enabled=True,
        notify_screening_reminders=True
    )

    if not subscriptions.exists():
        logger.info(f"Coach {coach.user.username} has screening reminder notifications disabled")
        return {'success': 0, 'failed': 0, 'total': 0}

    profile = submission.profile
    user_name = profile.user.first_name or profile.user.username

    # Use context manager for thread-safe language activation
    with user_language_context(coach.user):
        title = _("Pending Screening Call")
        body = _("Don't forget to schedule a call with %(name)s") % {'name': user_name}
        url = reverse('crush_lu:coach_review_profile', args=[submission.id])

    return send_coach_push_notification(
        coach=coach,
        title=title,
        body=body,
        url=url,
        tag=f"screening-reminder-{submission.id}"
    )


def notify_coach_system_alert(coach, title, message, url='/coach/dashboard/'):
    """
    Send a system/admin alert to a coach.

    Args:
        coach: CrushCoach object
        title: Alert title
        message: Alert message
        url: URL to navigate to
    """
    # Check if coach has notifications enabled for system alerts
    subscriptions = CoachPushSubscription.objects.filter(
        coach=coach,
        enabled=True,
        notify_system_alerts=True
    )

    if not subscriptions.exists():
        logger.info(f"Coach {coach.user.username} has system alert notifications disabled")
        return {'success': 0, 'failed': 0, 'total': 0}

    return send_coach_push_notification(
        coach=coach,
        title=title,
        body=message,
        url=url,
        tag="system-alert"
    )


def send_coach_test_notification(coach):
    """
    Send a test notification to verify coach push setup.

    Args:
        coach: CrushCoach object

    Returns:
        dict: Push result with success/failed counts
    """
    # Use context manager for thread-safe language activation
    with user_language_context(coach.user):
        title = _("Test Notification")
        body = _("Your coach notifications are working correctly!")

    return send_coach_push_notification(
        coach=coach,
        title=title,
        body=body,
        url='/coach/dashboard/',
        tag="coach-test-notification"
    )
