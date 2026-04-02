# crush_lu/notification_service.py
"""
Unified Notification Service for Crush.lu

Sends push and email as independent channels based on user preferences:
1. If user has active push subscriptions with preference enabled, send push
2. If user has email preference enabled, send email
Both channels are attempted independently — push success does not suppress email.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any

from django.http import HttpRequest

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """
    Notification types with unique identifiers.

    The preference_key property maps to the suffix in both PushSubscription (notify_*)
    and EmailPreference (email_*) fields for preference checking.

    IMPORTANT: Each enum member MUST have a unique value to avoid Python's enum aliasing
    behavior where members with the same value become aliases of the first member.
    """
    PROFILE_APPROVED = 'profile_approved'
    PROFILE_REVISION = 'profile_revision'
    PROFILE_REJECTED = 'profile_rejected'
    PROFILE_RECONTACT = 'profile_recontact'
    NEW_MESSAGE = 'new_message'
    NEW_CONNECTION = 'new_connection'
    CONNECTION_ACCEPTED = 'connection_accepted'
    EVENT_REMINDER = 'event_reminder'
    EVENT_REGISTRATION = 'event_registration'
    EVENT_WAITLIST = 'event_waitlist'
    SPARK_COACH_ASSIGNMENT = 'spark_coach_assignment'
    SPARK_RECIPIENT_ASSIGNED = 'spark_recipient_assigned'
    SPARK_JOURNEY_READY = 'spark_journey_ready'
    SPARK_COMPLETED = 'spark_completed'

    @property
    def preference_key(self) -> str:
        """
        Map notification type to the preference field suffix.
        Multiple notification types can share a preference key.
        """
        preference_mapping = {
            'profile_approved': 'profile_updates',
            'profile_revision': 'profile_updates',
            'profile_rejected': 'profile_updates',
            'profile_recontact': 'profile_updates',
            'new_message': 'new_messages',
            'new_connection': 'new_connections',
            'connection_accepted': 'new_connections',
            'event_reminder': 'event_reminders',
            'event_registration': 'event_reminders',
            'event_waitlist': 'event_reminders',
            'spark_coach_assignment': 'event_reminders',
            'spark_recipient_assigned': 'new_connections',
            'spark_journey_ready': 'new_connections',
            'spark_completed': 'new_connections',
        }
        return preference_mapping.get(self.value, self.value)


@dataclass
class NotificationResult:
    """
    Result of notification delivery attempt.
    Tracks what was attempted and what succeeded.
    """
    # Push notification results
    push_attempted: bool = False
    push_success_count: int = 0
    push_failed_count: int = 0

    # Email notification results
    email_attempted: bool = False
    email_sent: bool = False
    email_skipped_reason: Optional[str] = None  # 'user_unsubscribed', 'no_email'

    # Errors for debugging
    errors: list = field(default_factory=list)

    @property
    def push_success(self) -> bool:
        """Returns True if at least one push notification succeeded."""
        return self.push_success_count > 0

    @property
    def any_delivered(self) -> bool:
        """Returns True if notification was delivered via any channel."""
        return self.push_success or self.email_sent


class NotificationService:
    """
    Unified notification service with independent push and email channels based on user preferences.

    Usage:
        result = NotificationService.notify(
            user=user,
            notification_type=NotificationType.PROFILE_APPROVED,
            context={'profile': profile, 'coach_notes': notes},
            request=request
        )
    """

    @staticmethod
    def notify(
        user,
        notification_type: NotificationType,
        context: dict,
        request: Optional[HttpRequest] = None
    ) -> NotificationResult:
        """
        Send notification via independent push and email channels.

        Both channels are attempted based on user preferences — push success
        does not suppress email delivery.

        Args:
            user: Django User object (recipient)
            notification_type: Type of notification to send
            context: Dict with notification-specific data (profile, message, event, etc.)
            request: Optional Django request for URL generation

        Returns:
            NotificationResult with delivery status
        """
        result = NotificationResult()
        preference_key = notification_type.preference_key

        # --- Push channel (independent) ---
        try:
            from .models import PushSubscription
            push_filter = {f'notify_{preference_key}': True}
            push_subscriptions = PushSubscription.objects.filter(
                user=user,
                enabled=True,
                **push_filter
            )
            if push_subscriptions.exists():
                result.push_attempted = True
                try:
                    push_result = NotificationService._send_push(user, notification_type, context)
                    result.push_success_count = push_result.get('success', 0)
                    result.push_failed_count = push_result.get('failed', 0)
                except Exception as e:
                    logger.error(f"Error sending push to {user.username}: {e}")
                    result.errors.append(f"Push error: {e}")
                    result.push_failed_count = 1
        except Exception as e:
            logger.error(f"Error checking push subscriptions: {e}")
            result.errors.append(f"Push check error: {e}")

        # --- Email channel (independent) ---
        try:
            from .email_helpers import can_send_email
            if can_send_email(user, preference_key):
                result.email_attempted = True
                email_sent = NotificationService._send_email(
                    user, notification_type, context, request
                )
                result.email_sent = email_sent
                if email_sent:
                    logger.info(
                        f"Email sent to {user.email} ({notification_type.name})"
                    )
            else:
                result.email_skipped_reason = 'user_unsubscribed'
                logger.info(
                    f"Email skipped for {user.email} ({notification_type.name}): "
                    f"user unsubscribed"
                )
        except Exception as e:
            logger.error(f"Error sending email to {user.email}: {e}")
            result.errors.append(f"Email error: {e}")

        return result

    @staticmethod
    def _send_push(user, notification_type: NotificationType, context: dict) -> dict:
        """
        Route to appropriate push notification function.

        Returns:
            dict: {'success': int, 'failed': int, 'total': int}
        """
        from . import push_notifications

        try:
            if notification_type == NotificationType.PROFILE_APPROVED:
                return push_notifications.send_profile_approved_notification(user) or {}

            elif notification_type == NotificationType.PROFILE_REVISION:
                feedback = context.get('feedback', context.get('coach_notes', ''))
                return push_notifications.send_profile_revision_notification(user, feedback) or {}

            elif notification_type == NotificationType.PROFILE_RECONTACT:
                return push_notifications.send_profile_recontact_notification(user) or {}

            elif notification_type == NotificationType.NEW_MESSAGE:
                message = context.get('message')
                if message:
                    return push_notifications.send_new_message_notification(user, message) or {}

            elif notification_type in (NotificationType.NEW_CONNECTION, NotificationType.CONNECTION_ACCEPTED):
                connection = context.get('connection')
                if connection:
                    return push_notifications.send_new_connection_notification(user, connection) or {}

            elif notification_type == NotificationType.EVENT_REMINDER:
                event = context.get('event')
                if event:
                    return push_notifications.send_event_reminder(user, event) or {}

            # For types without specific push functions, use generic
            return {'success': 0, 'failed': 0, 'total': 0}

        except Exception as e:
            logger.error(f"Push notification error for {notification_type.name}: {e}")
            return {'success': 0, 'failed': 1, 'total': 1}

    @staticmethod
    def _send_email(
        user,
        notification_type: NotificationType,
        context: dict,
        request: Optional[HttpRequest]
    ) -> bool:
        """
        Route to appropriate email function.

        Returns:
            bool: True if email was sent successfully
        """
        from . import email_helpers

        try:
            if notification_type == NotificationType.PROFILE_APPROVED:
                profile = context.get('profile')
                coach_notes = context.get('coach_notes')
                if profile and request:
                    result = email_helpers.send_profile_approved_notification(
                        profile, request, coach_notes=coach_notes
                    )
                    return result == 1

            elif notification_type == NotificationType.PROFILE_REVISION:
                profile = context.get('profile')
                feedback = context.get('feedback', context.get('coach_notes', ''))
                if profile and request:
                    result = email_helpers.send_profile_revision_request(
                        profile, request, feedback=feedback
                    )
                    return result == 1

            elif notification_type == NotificationType.PROFILE_REJECTED:
                profile = context.get('profile')
                feedback = context.get('feedback', context.get('coach_notes', ''))
                if profile and request:
                    result = email_helpers.send_profile_rejected_notification(
                        profile, request, reason=feedback
                    )
                    return result == 1

            elif notification_type == NotificationType.PROFILE_RECONTACT:
                profile = context.get('profile')
                coach = context.get('coach')
                if profile and coach and request:
                    result = email_helpers.send_profile_recontact_notification(
                        profile, coach, request
                    )
                    return result == 1

            elif notification_type == NotificationType.NEW_MESSAGE:
                message = context.get('message')
                if message and request:
                    result = email_helpers.send_new_message_notification(
                        user, message, request
                    )
                    return result == 1

            elif notification_type == NotificationType.NEW_CONNECTION:
                connection = context.get('connection')
                requester = context.get('requester')
                if connection and request:
                    result = email_helpers.send_new_connection_request_notification(
                        user, connection, requester, request
                    )
                    return result == 1

            elif notification_type == NotificationType.CONNECTION_ACCEPTED:
                connection = context.get('connection')
                accepter = context.get('accepter')
                if connection and request:
                    result = email_helpers.send_connection_accepted_notification(
                        user, connection, accepter, request
                    )
                    return result == 1

            elif notification_type == NotificationType.EVENT_REMINDER:
                registration = context.get('registration')
                days_until = context.get('days_until', 1)
                if registration and request:
                    result = email_helpers.send_event_reminder(
                        registration, request, days_until_event=days_until
                    )
                    return result == 1

            elif notification_type == NotificationType.EVENT_REGISTRATION:
                registration = context.get('registration')
                if registration and request:
                    result = email_helpers.send_event_registration_confirmation(
                        registration, request
                    )
                    return result == 1

            elif notification_type == NotificationType.EVENT_WAITLIST:
                registration = context.get('registration')
                if registration and request:
                    result = email_helpers.send_event_waitlist_notification(
                        registration, request
                    )
                    return result == 1

            logger.warning(
                f"No email handler for {notification_type.name}, skipping email"
            )
            return False

        except AttributeError as e:
            # Email helper function doesn't exist yet
            logger.warning(
                f"Email helper not found for {notification_type.name}: {e}"
            )
            return False
        except Exception as e:
            logger.error(f"Email error for {notification_type.name}: {e}")
            return False


# Convenience functions for common notification types
def notify_profile_approved(user, profile, coach_notes: str = None, request=None) -> NotificationResult:
    """Send profile approved notification."""
    return NotificationService.notify(
        user=user,
        notification_type=NotificationType.PROFILE_APPROVED,
        context={'profile': profile, 'coach_notes': coach_notes},
        request=request
    )


def notify_profile_revision(user, profile, feedback: str, request=None) -> NotificationResult:
    """Send profile revision request notification."""
    return NotificationService.notify(
        user=user,
        notification_type=NotificationType.PROFILE_REVISION,
        context={'profile': profile, 'feedback': feedback},
        request=request
    )


def notify_profile_rejected(user, profile, feedback: str, request=None) -> NotificationResult:
    """Send profile rejected notification."""
    return NotificationService.notify(
        user=user,
        notification_type=NotificationType.PROFILE_REJECTED,
        context={'profile': profile, 'feedback': feedback},
        request=request
    )


def notify_new_message(recipient, message, request=None) -> NotificationResult:
    """Send new message notification."""
    return NotificationService.notify(
        user=recipient,
        notification_type=NotificationType.NEW_MESSAGE,
        context={'message': message},
        request=request
    )


def notify_new_connection(recipient, connection, requester, request=None) -> NotificationResult:
    """Send new connection request notification."""
    return NotificationService.notify(
        user=recipient,
        notification_type=NotificationType.NEW_CONNECTION,
        context={'connection': connection, 'requester': requester},
        request=request
    )


def notify_connection_accepted(recipient, connection, accepter, request=None) -> NotificationResult:
    """Send connection accepted notification."""
    return NotificationService.notify(
        user=recipient,
        notification_type=NotificationType.CONNECTION_ACCEPTED,
        context={'connection': connection, 'accepter': accepter},
        request=request
    )


def notify_event_reminder(user, registration, event, days_until: int = 1, request=None) -> NotificationResult:
    """Send event reminder notification."""
    return NotificationService.notify(
        user=user,
        notification_type=NotificationType.EVENT_REMINDER,
        context={
            'registration': registration,
            'event': event,
            'days_until': days_until
        },
        request=request
    )


def notify_profile_recontact(user, profile, coach, request=None) -> NotificationResult:
    """Send notification when coach needs user to recontact them."""
    return NotificationService.notify(
        user=user,
        notification_type=NotificationType.PROFILE_RECONTACT,
        context={'profile': profile, 'coach': coach},
        request=request
    )
