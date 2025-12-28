"""
Tests for the Crush.lu Unified Notification Service

Tests the "push first, email fallback" notification strategy.
"""
import pytest
from unittest.mock import patch, MagicMock
from django.contrib.auth.models import User

from crush_lu.notification_service import (
    NotificationService,
    NotificationType,
    NotificationResult,
    notify_profile_approved,
    notify_profile_revision,
    notify_new_message,
    notify_new_connection,
    notify_connection_accepted,
)
from crush_lu.models import PushSubscription, EmailPreference, CrushProfile


@pytest.fixture
def user_with_profile(db):
    """Create a user with a CrushProfile."""
    user = User.objects.create_user(
        username='testuser@example.com',
        email='testuser@example.com',
        password='testpass123',
        first_name='Test',
        last_name='User'
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth='1990-01-01',
        gender='male',
        location='Luxembourg',
        is_approved=True
    )
    return user


@pytest.fixture
def user_with_push_subscription(user_with_profile):
    """Create a user with an active push subscription."""
    PushSubscription.objects.create(
        user=user_with_profile,
        endpoint='https://push.example.com/test',
        p256dh_key='test_p256dh_key',
        auth_key='test_auth_key',
        device_name='Test Device',
        enabled=True,
        notify_new_messages=True,
        notify_event_reminders=True,
        notify_new_connections=True,
        notify_profile_updates=True
    )
    return user_with_profile


@pytest.fixture
def user_with_email_preferences(user_with_profile):
    """Create a user with email preferences."""
    EmailPreference.objects.create(
        user=user_with_profile,
        email_profile_updates=True,
        email_event_reminders=True,
        email_new_connections=True,
        email_new_messages=True
    )
    return user_with_profile


class TestNotificationResult:
    """Tests for NotificationResult dataclass."""

    def test_push_success_property(self):
        """push_success returns True when at least one push succeeded."""
        result = NotificationResult(push_success_count=1)
        assert result.push_success is True

        result = NotificationResult(push_success_count=0)
        assert result.push_success is False

    def test_any_delivered_property(self):
        """any_delivered returns True when push or email succeeded."""
        # Push succeeded
        result = NotificationResult(push_success_count=1)
        assert result.any_delivered is True

        # Email sent
        result = NotificationResult(email_sent=True)
        assert result.any_delivered is True

        # Both
        result = NotificationResult(push_success_count=1, email_sent=True)
        assert result.any_delivered is True

        # Neither
        result = NotificationResult()
        assert result.any_delivered is False


class TestNotificationServicePushFirst:
    """Tests for push-first notification strategy."""

    @patch('crush_lu.notification_service.NotificationService._send_push')
    @patch('crush_lu.notification_service.NotificationService._send_email')
    def test_push_only_when_subscribed_and_succeeds(
        self, mock_email, mock_push, user_with_push_subscription
    ):
        """User with push subscription receives push, no email when push succeeds."""
        mock_push.return_value = {'success': 1, 'failed': 0, 'total': 1}

        result = NotificationService.notify(
            user=user_with_push_subscription,
            notification_type=NotificationType.PROFILE_APPROVED,
            context={'profile': user_with_push_subscription.crushprofile}
        )

        assert result.push_attempted is True
        assert result.push_success is True
        assert result.email_sent is False
        assert result.email_skipped_reason == 'push_succeeded'
        mock_push.assert_called_once()
        mock_email.assert_not_called()

    @patch('crush_lu.notification_service.NotificationService._send_push')
    @patch('crush_lu.notification_service.NotificationService._send_email')
    def test_email_fallback_when_push_fails(
        self, mock_email, mock_push, user_with_push_subscription
    ):
        """When all push attempts fail, fall back to email."""
        mock_push.return_value = {'success': 0, 'failed': 1, 'total': 1}
        mock_email.return_value = True

        result = NotificationService.notify(
            user=user_with_push_subscription,
            notification_type=NotificationType.PROFILE_APPROVED,
            context={'profile': user_with_push_subscription.crushprofile}
        )

        assert result.push_attempted is True
        assert result.push_success is False
        assert result.email_sent is True
        mock_push.assert_called_once()
        mock_email.assert_called_once()

    @patch('crush_lu.notification_service.NotificationService._send_email')
    def test_email_only_when_no_push_subscription(
        self, mock_email, user_with_profile
    ):
        """User without push subscription receives email directly."""
        mock_email.return_value = True

        result = NotificationService.notify(
            user=user_with_profile,
            notification_type=NotificationType.PROFILE_APPROVED,
            context={'profile': user_with_profile.crushprofile}
        )

        assert result.push_attempted is False
        assert result.email_sent is True
        mock_email.assert_called_once()


class TestNotificationServicePreferences:
    """Tests for respecting user notification preferences."""

    @patch('crush_lu.email_helpers.can_send_email')
    def test_no_notification_when_email_unsubscribed(
        self, mock_can_send, user_with_profile
    ):
        """No notification when user has disabled both channels."""
        mock_can_send.return_value = False

        result = NotificationService.notify(
            user=user_with_profile,
            notification_type=NotificationType.PROFILE_APPROVED,
            context={'profile': user_with_profile.crushprofile}
        )

        assert result.push_attempted is False
        assert result.email_sent is False
        assert result.email_skipped_reason == 'user_unsubscribed'

    def test_push_respects_specific_preference(self, user_with_push_subscription):
        """Push subscription with specific notification type disabled."""
        # Disable profile updates for push
        sub = user_with_push_subscription.push_subscriptions.first()
        sub.notify_profile_updates = False
        sub.save()

        with patch('crush_lu.notification_service.NotificationService._send_email') as mock_email:
            mock_email.return_value = True

            result = NotificationService.notify(
                user=user_with_push_subscription,
                notification_type=NotificationType.PROFILE_APPROVED,
                context={'profile': user_with_push_subscription.crushprofile}
            )

            # No push attempted because preference is disabled
            assert result.push_attempted is False
            # Falls back to email
            mock_email.assert_called_once()


class TestNotificationTypes:
    """Tests for different notification types."""

    @patch('crush_lu.push_notifications.send_push_notification')
    def test_profile_approved_push_routing(
        self, mock_send_push, user_with_push_subscription
    ):
        """Profile approved notification calls the underlying push function."""
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}

        result = NotificationService._send_push(
            user_with_push_subscription,
            NotificationType.PROFILE_APPROVED,
            {'profile': user_with_push_subscription.crushprofile}
        )

        # Verify send_push_notification was called with correct title
        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args
        assert call_kwargs[1]['user'] == user_with_push_subscription
        assert 'approved' in call_kwargs[1]['title'].lower()

    @patch('crush_lu.push_notifications.send_push_notification')
    def test_profile_revision_push_routing(
        self, mock_send_push, user_with_push_subscription
    ):
        """Profile revision notification calls the underlying push function."""
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}

        result = NotificationService._send_push(
            user_with_push_subscription,
            NotificationType.PROFILE_REVISION,
            {'feedback': 'Please update your bio'}
        )

        # Verify send_push_notification was called with feedback in body
        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args
        assert call_kwargs[1]['user'] == user_with_push_subscription
        assert 'update' in call_kwargs[1]['title'].lower() or 'revision' in call_kwargs[1]['title'].lower()


class TestConvenienceFunctions:
    """Tests for convenience wrapper functions."""

    @patch.object(NotificationService, 'notify')
    def test_notify_profile_approved(self, mock_notify, user_with_profile):
        """notify_profile_approved calls NotificationService correctly."""
        mock_notify.return_value = NotificationResult()

        notify_profile_approved(
            user=user_with_profile,
            profile=user_with_profile.crushprofile,
            coach_notes='Great profile!',
            request=None
        )

        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert call_args.kwargs['user'] == user_with_profile
        assert call_args.kwargs['notification_type'] == NotificationType.PROFILE_APPROVED
        assert call_args.kwargs['context']['coach_notes'] == 'Great profile!'

    @patch.object(NotificationService, 'notify')
    def test_notify_new_message(self, mock_notify, user_with_profile):
        """notify_new_message calls NotificationService correctly."""
        mock_notify.return_value = NotificationResult()
        mock_message = MagicMock()

        notify_new_message(
            recipient=user_with_profile,
            message=mock_message,
            request=None
        )

        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        assert call_args.kwargs['user'] == user_with_profile
        assert call_args.kwargs['notification_type'] == NotificationType.NEW_MESSAGE
        assert call_args.kwargs['context']['message'] == mock_message


class TestErrorHandling:
    """Tests for error handling in notification service."""

    @patch('crush_lu.notification_service.NotificationService._send_push')
    def test_push_exception_handled(self, mock_push, user_with_push_subscription):
        """Exceptions during push don't crash the service."""
        mock_push.side_effect = Exception('Push service error')

        result = NotificationService.notify(
            user=user_with_push_subscription,
            notification_type=NotificationType.PROFILE_APPROVED,
            context={'profile': user_with_push_subscription.crushprofile}
        )

        # Should still try email fallback
        assert result.push_attempted is True
        assert 'Push service error' in str(result.errors) or result.push_failed_count == 1

    @patch('crush_lu.notification_service.NotificationService._send_email')
    def test_email_exception_handled(self, mock_email, user_with_profile):
        """Exceptions during email don't crash the service."""
        mock_email.side_effect = Exception('Email service error')

        result = NotificationService.notify(
            user=user_with_profile,
            notification_type=NotificationType.PROFILE_APPROVED,
            context={'profile': user_with_profile.crushprofile}
        )

        # Should record the error
        assert len(result.errors) > 0 or result.email_sent is False
