"""
Crush.lu Coach Push Notification Utilities
Handles Web Push API notifications for Crush Coaches.
Completely separate from user notifications to avoid conflicts.
"""

import json
import logging
from django.conf import settings
from django.urls import reverse
from pywebpush import webpush, WebPushException
from .models import CoachPushSubscription, CrushCoach

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

    return send_coach_push_notification(
        coach=coach,
        title="New Profile to Review",
        body=f"{user_name} submitted a profile for review",
        url=reverse('crush_lu:coach_review_profile', args=[submission.id]),
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

    return send_coach_push_notification(
        coach=coach,
        title="Profile Revision Submitted",
        body=f"{user_name} updated their profile based on your feedback",
        url=reverse('crush_lu:coach_review_profile', args=[submission.id]),
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

    return send_coach_push_notification(
        coach=coach,
        title="Pending Screening Call",
        body=f"Don't forget to schedule a call with {user_name}",
        url=reverse('crush_lu:coach_review_profile', args=[submission.id]),
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
    return send_coach_push_notification(
        coach=coach,
        title="Test Notification",
        body="Your coach notifications are working correctly!",
        url='/coach/dashboard/',
        tag="test-notification"
    )
