"""
Crush.lu Push Notification Utilities
Handles Web Push API notifications for PWA users
"""

import json
import logging
from contextlib import contextmanager
from django.conf import settings
from django.urls import reverse
from django.utils.translation import override, gettext as _
from pywebpush import webpush, WebPushException
from .models import PushSubscription

logger = logging.getLogger(__name__)


def get_user_language(user):
    """
    Get user's preferred language from their CrushProfile.

    Args:
        user: Django User object

    Returns:
        Language code ('en', 'de', 'fr') - defaults to 'en'
    """
    if hasattr(user, 'crushprofile') and user.crushprofile:
        profile_lang = getattr(user.crushprofile, 'preferred_language', None)
        if profile_lang and profile_lang in ['en', 'de', 'fr']:
            return profile_lang
        elif profile_lang:
            logger.warning(f"User {user.id} has invalid language: {profile_lang}, using 'en'")
    return 'en'


@contextmanager
def user_language_context(user):
    """
    Context manager for user's preferred language.

    Uses override() to ensure thread-safety in production environments
    where Gunicorn workers handle multiple requests per thread.

    Usage:
        with user_language_context(user):
            title = _("Hello")
            url = reverse('crush_lu:dashboard')

    Args:
        user: Django User object

    Yields:
        Language code that was activated
    """
    lang = get_user_language(user)
    with override(lang):
        yield lang


def activate_user_language(user):
    """
    DEPRECATED: Use user_language_context() context manager instead.

    This function mutates thread-local state without cleanup, which can cause
    language leakage between requests in production (Gunicorn workers).

    Kept for backwards compatibility but should be replaced with:
        with user_language_context(user):
            # your code here

    Args:
        user: Django User object

    Returns:
        Language code that was activated
    """
    lang = get_user_language(user)
    # Note: This still uses override internally but returns immediately,
    # so the context is not properly managed. Use user_language_context instead.
    logger.warning("activate_user_language() is deprecated, use user_language_context() instead")
    return lang


def get_user_language_url(user, url_name, **kwargs):
    """
    Get a language-prefixed URL based on user's preferred language.

    Uses override() context manager for thread-safety.

    Args:
        user: Django User object
        url_name: The URL name to reverse (e.g., 'crush_lu:dashboard')
        **kwargs: Additional arguments for reverse()
    """
    with user_language_context(user):
        return reverse(url_name, **kwargs)


def send_push_notification(user, title, body, url='/', tag='crush-notification', icon=None, badge=None):
    """
    Send a push notification to all of a user's subscribed devices.

    Args:
        user: Django User object
        title: Notification title
        body: Notification message body
        url: URL to open when notification is clicked (default: '/')
        tag: Notification tag for grouping (default: 'crush-notification')
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

    # Get all active subscriptions for this user
    subscriptions = PushSubscription.objects.filter(user=user, enabled=True)

    if not subscriptions.exists():
        logger.info(f"No active push subscriptions for user {user.username}")
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
            logger.info(f"Push notification sent to {user.username} ({subscription.device_name})")

        except WebPushException as e:
            # Handle push errors (expired subscription, etc.)
            logger.warning(f"WebPush failed for {user.username}: {e}")
            subscription.mark_failure()  # Auto-deletes after 5 failures
            failed_count += 1

        except Exception as e:
            # Catch-all for unexpected errors
            logger.error(f"Unexpected error sending push to {user.username}: {e}")
            failed_count += 1

    return {
        'success': success_count,
        'failed': failed_count,
        'total': subscriptions.count()
    }


def send_event_reminder(user, event):
    """
    Send event reminder notification.

    Args:
        user: Django User object
        event: MeetupEvent object
    """
    # Check if user wants event reminders
    subscriptions = user.push_subscriptions.filter(enabled=True, notify_event_reminders=True)
    if not subscriptions.exists():
        return

    # Use context manager for thread-safe language activation
    with user_language_context(user):
        title = _("Event Tomorrow: %(event_title)s") % {'event_title': event.title}
        body = _("Don't forget! %(event_title)s starts at %(time)s. See you there!") % {
            'event_title': event.title,
            'time': event.date_time.strftime('%H:%M')
        }
        url = reverse('crush_lu:event_detail', kwargs={'event_id': event.id})

    return send_push_notification(
        user=user,
        title=title,
        body=body,
        url=url,
        tag=f'event-reminder-{event.id}'
    )


def send_new_connection_notification(user, connection):
    """
    Send notification when someone wants to connect.

    Args:
        user: Django User object (recipient)
        connection: EventConnection object
    """
    # Check if user wants connection notifications
    subscriptions = user.push_subscriptions.filter(enabled=True, notify_new_connections=True)
    if not subscriptions.exists():
        return

    # Get the other user's display name
    other_user = connection.user1 if connection.user2 == user else connection.user2
    display_name = other_user.crushprofile.display_name if hasattr(other_user, 'crushprofile') else other_user.first_name

    # Use context manager for thread-safe language activation
    with user_language_context(user):
        title = _("New Connection Request!")
        body = _("%(name)s wants to connect with you!") % {'name': display_name}
        url = reverse('crush_lu:my_connections')

    return send_push_notification(
        user=user,
        title=title,
        body=body,
        url=url,
        tag=f'connection-{connection.id}'
    )


def send_new_message_notification(user, message):
    """
    Send notification for new connection message.

    Args:
        user: Django User object (recipient)
        message: ConnectionMessage object
    """
    # Check if user wants message notifications
    subscriptions = user.push_subscriptions.filter(enabled=True, notify_new_messages=True)
    if not subscriptions.exists():
        return

    sender = message.sender
    display_name = sender.crushprofile.display_name if hasattr(sender, 'crushprofile') else sender.first_name

    # Truncate message for notification
    preview = message.message[:50] + "..." if len(message.message) > 50 else message.message

    # Use context manager for thread-safe language activation
    with user_language_context(user):
        title = _("New message from %(name)s") % {'name': display_name}
        body = preview
        url = reverse('crush_lu:connection_detail', kwargs={'connection_id': message.connection.id})

    return send_push_notification(
        user=user,
        title=title,
        body=body,
        url=url,
        tag=f'message-{message.connection.id}'
    )


def send_profile_approved_notification(user):
    """
    Send notification when profile is approved by coach.

    Args:
        user: Django User object
    """
    # Check if user wants profile update notifications
    subscriptions = user.push_subscriptions.filter(enabled=True, notify_profile_updates=True)
    if not subscriptions.exists():
        return

    # Use context manager for thread-safe language activation
    with user_language_context(user):
        title = _("Profile Approved!")
        body = _("Your Crush.lu profile has been approved! You can now register for events.")
        url = reverse('crush_lu:dashboard')

    return send_push_notification(
        user=user,
        title=title,
        body=body,
        url=url,
        tag='profile-approved'
    )


def send_profile_revision_notification(user, feedback):
    """
    Send notification when coach requests profile revisions.

    Args:
        user: Django User object
        feedback: Coach feedback text
    """
    # Check if user wants profile update notifications
    subscriptions = user.push_subscriptions.filter(enabled=True, notify_profile_updates=True)
    if not subscriptions.exists():
        return

    # Use context manager for thread-safe language activation
    with user_language_context(user):
        title = _("Profile Update Needed")
        body = _("Your Crush Coach has some feedback: %(feedback)s...") % {'feedback': feedback[:80]}
        url = reverse('crush_lu:edit_profile')

    return send_push_notification(
        user=user,
        title=title,
        body=body,
        url=url,
        tag='profile-revision'
    )


def send_test_notification(user):
    """
    Send a test notification to verify push is working.

    Args:
        user: Django User object
    """
    # Use context manager for thread-safe language activation
    with user_language_context(user):
        title = _("Test Notification")
        body = _("Push notifications are working! You'll receive updates about events, messages, and connections.")
        url = reverse('crush_lu:dashboard')

    return send_push_notification(
        user=user,
        title=title,
        body=body,
        url=url,
        tag='test-notification'
    )
