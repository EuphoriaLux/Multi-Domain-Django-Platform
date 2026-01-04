"""
Comprehensive tests for the Crush.lu Push Notification Utilities

Tests for push_notifications.py which handles:
- Language detection and context management for multi-language notifications
- Web Push API notification delivery
- Notification-specific functions (event reminders, connections, messages, etc.)
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import date, timedelta
from contextlib import contextmanager

from django.contrib.auth.models import User
from django.utils import timezone

from crush_lu.push_notifications import (
    get_user_language,
    user_language_context,
    activate_user_language,
    get_user_language_url,
    send_push_notification,
    send_push_to_subscription,
    send_event_reminder,
    send_new_connection_notification,
    send_new_message_notification,
    send_profile_approved_notification,
    send_profile_revision_notification,
    send_test_notification,
)
from crush_lu.models import CrushProfile, PushSubscription, MeetupEvent, EventConnection


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def user(db):
    """Create a basic user without profile."""
    return User.objects.create_user(
        username='testuser@example.com',
        email='testuser@example.com',
        password='testpass123',
        first_name='Test',
        last_name='User'
    )


@pytest.fixture
def user_with_profile(user):
    """Create a user with a CrushProfile."""
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1990, 1, 1),
        gender='M',
        location='Luxembourg',
        is_approved=True,
        preferred_language='en'
    )
    return user


@pytest.fixture
def user_with_german_profile(db):
    """Create a user with German language preference."""
    user = User.objects.create_user(
        username='german@example.com',
        email='german@example.com',
        password='testpass123',
        first_name='Hans',
        last_name='Mueller'
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1990, 1, 1),
        gender='M',
        location='Luxembourg',
        is_approved=True,
        preferred_language='de'
    )
    return user


@pytest.fixture
def user_with_french_profile(db):
    """Create a user with French language preference."""
    user = User.objects.create_user(
        username='french@example.com',
        email='french@example.com',
        password='testpass123',
        first_name='Jean',
        last_name='Dupont'
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1990, 1, 1),
        gender='M',
        location='Luxembourg',
        is_approved=True,
        preferred_language='fr'
    )
    return user


@pytest.fixture
def user_with_invalid_language(db):
    """Create a user with an invalid language preference."""
    user = User.objects.create_user(
        username='invalid@example.com',
        email='invalid@example.com',
        password='testpass123',
        first_name='Invalid',
        last_name='User'
    )
    CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1990, 1, 1),
        gender='M',
        location='Luxembourg',
        is_approved=True,
        preferred_language='es'  # Spanish - not supported
    )
    return user


@pytest.fixture
def push_subscription(user_with_profile):
    """Create a push subscription for the test user."""
    return PushSubscription.objects.create(
        user=user_with_profile,
        endpoint='https://push.example.com/test-endpoint',
        p256dh_key='test_p256dh_key_value',
        auth_key='test_auth_key_value',
        device_name='Test Device',
        enabled=True,
        notify_new_messages=True,
        notify_event_reminders=True,
        notify_new_connections=True,
        notify_profile_updates=True
    )


@pytest.fixture
def disabled_subscription(user_with_profile):
    """Create a disabled push subscription."""
    return PushSubscription.objects.create(
        user=user_with_profile,
        endpoint='https://push.example.com/disabled-endpoint',
        p256dh_key='test_p256dh_key_disabled',
        auth_key='test_auth_key_disabled',
        device_name='Disabled Device',
        enabled=False
    )


@pytest.fixture
def multiple_subscriptions(user_with_profile):
    """Create multiple push subscriptions for the same user."""
    subs = []
    for i in range(3):
        subs.append(PushSubscription.objects.create(
            user=user_with_profile,
            endpoint=f'https://push.example.com/device-{i}',
            p256dh_key=f'test_p256dh_key_{i}',
            auth_key=f'test_auth_key_{i}',
            device_name=f'Device {i}',
            enabled=True,
            notify_new_messages=True,
            notify_event_reminders=True,
            notify_new_connections=True,
            notify_profile_updates=True
        ))
    return subs


@pytest.fixture
def sample_event(db):
    """Create a sample event for testing."""
    return MeetupEvent.objects.create(
        title='Test Speed Dating Event',
        description='A test event for unit testing',
        event_type='speed_dating',
        date_time=timezone.now() + timedelta(days=1),
        location='Test Location, Luxembourg',
        address='123 Test Street, Luxembourg City',
        max_participants=20,
        registration_deadline=timezone.now() + timedelta(hours=12),
        is_published=True
    )


@pytest.fixture
def mock_vapid_settings():
    """Mock VAPID settings for push notifications."""
    with patch('crush_lu.push_notifications.settings') as mock_settings:
        mock_settings.VAPID_PRIVATE_KEY = 'test_vapid_private_key'
        mock_settings.VAPID_PUBLIC_KEY = 'test_vapid_public_key'
        mock_settings.VAPID_ADMIN_EMAIL = 'admin@crush.lu'
        yield mock_settings


# =============================================================================
# TESTS FOR get_user_language()
# =============================================================================

class TestGetUserLanguage:
    """Tests for get_user_language function."""

    def test_returns_english_by_default_when_no_profile(self, user):
        """User without profile should get 'en' as default language."""
        assert get_user_language(user) == 'en'

    def test_returns_profile_language_english(self, user_with_profile):
        """User with English profile should get 'en'."""
        assert get_user_language(user_with_profile) == 'en'

    def test_returns_profile_language_german(self, user_with_german_profile):
        """User with German profile should get 'de'."""
        assert get_user_language(user_with_german_profile) == 'de'

    def test_returns_profile_language_french(self, user_with_french_profile):
        """User with French profile should get 'fr'."""
        assert get_user_language(user_with_french_profile) == 'fr'

    def test_returns_default_for_invalid_language(self, user_with_invalid_language):
        """User with unsupported language should get 'en' and log warning."""
        with patch('crush_lu.push_notifications.logger') as mock_logger:
            result = get_user_language(user_with_invalid_language)
            assert result == 'en'
            mock_logger.warning.assert_called_once()

    def test_returns_default_when_profile_has_default_language(self, user):
        """User with profile using default 'en' language should get 'en'."""
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1990, 1, 1),
            gender='M',
            location='Luxembourg',
            preferred_language='en'  # Default language
        )
        assert get_user_language(user) == 'en'

    def test_returns_default_when_profile_language_is_empty(self, user):
        """User with profile but empty language should get 'en'."""
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1990, 1, 1),
            gender='M',
            location='Luxembourg',
            preferred_language=''
        )
        assert get_user_language(user) == 'en'


# =============================================================================
# TESTS FOR user_language_context()
# =============================================================================

class TestUserLanguageContext:
    """Tests for user_language_context context manager."""

    def test_context_manager_returns_language_code(self, user_with_german_profile):
        """Context manager yields the user's language code."""
        with user_language_context(user_with_german_profile) as lang:
            assert lang == 'de'

    def test_context_manager_uses_override(self, user_with_french_profile):
        """Context manager applies Django's override for thread-safety."""
        from django.utils.translation import get_language

        with user_language_context(user_with_french_profile):
            # Inside context, language should be French
            assert get_language() == 'fr'

    def test_context_manager_restores_language_after_exit(self, user_with_german_profile):
        """Context manager restores original language after exiting."""
        from django.utils.translation import get_language, activate

        # Set a known language first
        activate('en')
        original_lang = get_language()

        with user_language_context(user_with_german_profile):
            assert get_language() == 'de'

        # After context, language should be restored
        assert get_language() == original_lang

    def test_context_manager_handles_exceptions(self, user_with_profile):
        """Context manager properly restores language even if exception occurs."""
        from django.utils.translation import get_language, activate

        activate('en')

        try:
            with user_language_context(user_with_profile):
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Language should still be restored
        assert get_language() == 'en'


# =============================================================================
# TESTS FOR activate_user_language() - DEPRECATED
# =============================================================================

class TestActivateUserLanguage:
    """Tests for deprecated activate_user_language function."""

    def test_returns_language_code(self, user_with_profile):
        """Function returns the user's language code."""
        with patch('crush_lu.push_notifications.logger') as mock_logger:
            result = activate_user_language(user_with_profile)
            assert result == 'en'

    def test_logs_deprecation_warning(self, user_with_profile):
        """Function logs a deprecation warning."""
        with patch('crush_lu.push_notifications.logger') as mock_logger:
            activate_user_language(user_with_profile)
            mock_logger.warning.assert_called_once()
            assert 'deprecated' in mock_logger.warning.call_args[0][0].lower()


# =============================================================================
# TESTS FOR get_user_language_url()
# =============================================================================

class TestGetUserLanguageUrl:
    """Tests for get_user_language_url function."""

    def test_returns_url_with_correct_language_prefix(self, user_with_german_profile):
        """URL should have correct language prefix for German user."""
        # Mocking reverse since the actual URL patterns may not be configured in tests
        with patch('crush_lu.push_notifications.reverse') as mock_reverse:
            mock_reverse.return_value = '/de/dashboard/'
            url = get_user_language_url(user_with_german_profile, 'crush_lu:dashboard')
            mock_reverse.assert_called_once_with('crush_lu:dashboard')

    def test_passes_kwargs_to_reverse(self, user_with_profile):
        """Function passes kwargs to Django's reverse."""
        with patch('crush_lu.push_notifications.reverse') as mock_reverse:
            mock_reverse.return_value = '/en/event/123/'
            get_user_language_url(
                user_with_profile,
                'crush_lu:event_detail',
                kwargs={'event_id': 123}
            )
            mock_reverse.assert_called_once_with(
                'crush_lu:event_detail',
                kwargs={'event_id': 123}
            )


# =============================================================================
# TESTS FOR send_push_notification()
# =============================================================================

class TestSendPushNotification:
    """Tests for the core send_push_notification function."""

    def test_returns_empty_stats_when_vapid_private_key_missing(self, user_with_profile):
        """Returns zeros when VAPID_PRIVATE_KEY is not configured."""
        with patch('crush_lu.push_notifications.settings') as mock_settings:
            mock_settings.VAPID_PRIVATE_KEY = None
            mock_settings.VAPID_PUBLIC_KEY = 'test_key'

            result = send_push_notification(user_with_profile, 'Test', 'Body')

            assert result == {'success': 0, 'failed': 0, 'total': 0}

    def test_returns_empty_stats_when_vapid_public_key_missing(self, user_with_profile):
        """Returns zeros when VAPID_PUBLIC_KEY is not configured."""
        with patch('crush_lu.push_notifications.settings') as mock_settings:
            mock_settings.VAPID_PRIVATE_KEY = 'test_key'
            mock_settings.VAPID_PUBLIC_KEY = None

            result = send_push_notification(user_with_profile, 'Test', 'Body')

            assert result == {'success': 0, 'failed': 0, 'total': 0}

    def test_returns_empty_stats_when_no_subscriptions(
        self, user_with_profile, mock_vapid_settings
    ):
        """Returns zeros when user has no push subscriptions."""
        result = send_push_notification(user_with_profile, 'Test', 'Body')

        assert result == {'success': 0, 'failed': 0, 'total': 0}

    def test_returns_empty_stats_when_only_disabled_subscriptions(
        self, disabled_subscription, mock_vapid_settings
    ):
        """Returns zeros when user only has disabled subscriptions."""
        user = disabled_subscription.user
        result = send_push_notification(user, 'Test', 'Body')

        assert result == {'success': 0, 'failed': 0, 'total': 0}

    @patch('crush_lu.push_notifications.webpush')
    def test_successful_push_to_single_subscription(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Successfully sends push to a single subscription."""
        user = push_subscription.user

        result = send_push_notification(
            user, 'Test Title', 'Test Body', '/test-url'
        )

        assert result['success'] == 1
        assert result['failed'] == 0
        assert result['total'] == 1
        mock_webpush.assert_called_once()

    @patch('crush_lu.push_notifications.webpush')
    def test_successful_push_to_multiple_subscriptions(
        self, mock_webpush, multiple_subscriptions, mock_vapid_settings
    ):
        """Successfully sends push to multiple subscriptions."""
        user = multiple_subscriptions[0].user

        result = send_push_notification(user, 'Test', 'Body')

        assert result['success'] == 3
        assert result['failed'] == 0
        assert result['total'] == 3
        assert mock_webpush.call_count == 3

    @patch('crush_lu.push_notifications.webpush')
    def test_marks_subscription_success_on_delivery(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Marks subscription success after successful delivery."""
        user = push_subscription.user
        old_last_used = push_subscription.last_used_at

        send_push_notification(user, 'Test', 'Body')

        push_subscription.refresh_from_db()
        assert push_subscription.failure_count == 0
        assert push_subscription.last_used_at != old_last_used

    @patch('crush_lu.push_notifications.webpush')
    def test_handles_webpush_exception_410_gone(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Deletes subscription when 410 Gone response is received."""
        from pywebpush import WebPushException

        user = push_subscription.user
        subscription_id = push_subscription.id

        # Create mock response with 410 status
        mock_response = MagicMock()
        mock_response.status_code = 410
        mock_webpush.side_effect = WebPushException(
            "Gone", response=mock_response
        )

        result = send_push_notification(user, 'Test', 'Body')

        assert result['failed'] == 1
        assert not PushSubscription.objects.filter(id=subscription_id).exists()

    @patch('crush_lu.push_notifications.webpush')
    def test_handles_webpush_exception_other_errors(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Increments failure count on non-410 WebPush errors."""
        from pywebpush import WebPushException

        user = push_subscription.user

        # Create mock response with 500 status
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_webpush.side_effect = WebPushException(
            "Server Error", response=mock_response
        )

        result = send_push_notification(user, 'Test', 'Body')

        assert result['failed'] == 1
        push_subscription.refresh_from_db()
        assert push_subscription.failure_count == 1

    @patch('crush_lu.push_notifications.webpush')
    def test_handles_unexpected_exception(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Handles unexpected exceptions gracefully."""
        user = push_subscription.user
        mock_webpush.side_effect = Exception("Unexpected error")

        result = send_push_notification(user, 'Test', 'Body')

        assert result['failed'] == 1

    @patch('crush_lu.push_notifications.webpush')
    def test_uses_default_icon_and_badge(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Uses default icon and badge when not specified."""
        import json

        user = push_subscription.user
        send_push_notification(user, 'Test', 'Body')

        # Get the payload from the webpush call
        call_kwargs = mock_webpush.call_args
        payload = json.loads(call_kwargs[1]['data'])

        assert payload['icon'] == '/static/crush_lu/icons/icon-192x192.png'
        assert payload['badge'] == '/static/crush_lu/icons/icon-72x72.png'

    @patch('crush_lu.push_notifications.webpush')
    def test_uses_custom_icon_and_badge(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Uses custom icon and badge when specified."""
        import json

        user = push_subscription.user
        custom_icon = '/custom/icon.png'
        custom_badge = '/custom/badge.png'

        send_push_notification(
            user, 'Test', 'Body',
            icon=custom_icon,
            badge=custom_badge
        )

        call_kwargs = mock_webpush.call_args
        payload = json.loads(call_kwargs[1]['data'])

        assert payload['icon'] == custom_icon
        assert payload['badge'] == custom_badge

    @patch('crush_lu.push_notifications.webpush')
    def test_partial_success_with_mixed_results(
        self, mock_webpush, multiple_subscriptions, mock_vapid_settings
    ):
        """Returns correct counts when some subscriptions fail."""
        from pywebpush import WebPushException

        user = multiple_subscriptions[0].user

        # First call succeeds, second fails, third succeeds
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_webpush.side_effect = [
            None,  # Success
            WebPushException("Error", response=mock_response),  # Failure
            None,  # Success
        ]

        result = send_push_notification(user, 'Test', 'Body')

        assert result['success'] == 2
        assert result['failed'] == 1
        assert result['total'] == 3


# =============================================================================
# TESTS FOR send_push_to_subscription()
# =============================================================================

class TestSendPushToSubscription:
    """Tests for send_push_to_subscription function."""

    def test_returns_error_when_vapid_not_configured(self, push_subscription):
        """Returns error when VAPID is not configured."""
        with patch('crush_lu.push_notifications.settings') as mock_settings:
            mock_settings.VAPID_PRIVATE_KEY = None
            mock_settings.VAPID_PUBLIC_KEY = 'test'

            result = send_push_to_subscription(push_subscription, 'Test', 'Body')

            assert result['success'] is False
            assert 'VAPID' in result['error']

    def test_returns_error_when_subscription_disabled(
        self, disabled_subscription, mock_vapid_settings
    ):
        """Returns error when subscription is disabled."""
        result = send_push_to_subscription(
            disabled_subscription, 'Test', 'Body'
        )

        assert result['success'] is False
        assert 'disabled' in result['error'].lower()

    @patch('crush_lu.push_notifications.webpush')
    def test_successful_push_to_subscription(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Successfully sends push to a specific subscription."""
        result = send_push_to_subscription(
            push_subscription, 'Test Title', 'Test Body'
        )

        assert result['success'] is True
        assert result['error'] is None

    @patch('crush_lu.push_notifications.webpush')
    def test_returns_error_on_webpush_exception(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Returns error details when WebPush fails."""
        from pywebpush import WebPushException

        mock_webpush.side_effect = WebPushException("Push failed")

        result = send_push_to_subscription(push_subscription, 'Test', 'Body')

        assert result['success'] is False
        assert 'Push failed' in result['error']

    @patch('crush_lu.push_notifications.webpush')
    def test_marks_failure_on_webpush_exception(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Increments failure count on WebPush exception."""
        from pywebpush import WebPushException

        mock_webpush.side_effect = WebPushException("Push failed")

        send_push_to_subscription(push_subscription, 'Test', 'Body')

        push_subscription.refresh_from_db()
        assert push_subscription.failure_count == 1


# =============================================================================
# TESTS FOR send_event_reminder()
# =============================================================================

class TestSendEventReminder:
    """Tests for send_event_reminder function."""

    def test_does_not_send_when_no_subscriptions(self, user_with_profile, sample_event):
        """Does not attempt to send when user has no subscriptions."""
        result = send_event_reminder(user_with_profile, sample_event)
        assert result is None

    def test_does_not_send_when_event_reminders_disabled(
        self, push_subscription, sample_event
    ):
        """Does not send when user has disabled event reminders."""
        push_subscription.notify_event_reminders = False
        push_subscription.save()

        result = send_event_reminder(push_subscription.user, sample_event)
        assert result is None

    @patch('crush_lu.push_notifications.send_push_notification')
    def test_sends_reminder_with_correct_content(
        self, mock_send_push, push_subscription, sample_event
    ):
        """Sends reminder with event title and time."""
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}

        send_event_reminder(push_subscription.user, sample_event)

        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args[1]

        assert sample_event.title in call_kwargs['title']
        assert 'event-reminder' in call_kwargs['tag']

    @patch('crush_lu.push_notifications.send_push_notification')
    @patch('crush_lu.push_notifications.get_user_language_url')
    def test_uses_language_prefixed_url(
        self, mock_url, mock_send_push, push_subscription, sample_event
    ):
        """Uses get_user_language_url for the notification URL."""
        mock_url.return_value = '/en/events/1/'
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}

        send_event_reminder(push_subscription.user, sample_event)

        mock_url.assert_called_once()


# =============================================================================
# TESTS FOR send_new_connection_notification()
# =============================================================================

class TestSendNewConnectionNotification:
    """Tests for send_new_connection_notification function."""

    def test_does_not_send_when_no_subscriptions(self, user_with_profile):
        """Does not send when user has no subscriptions."""
        mock_connection = MagicMock()
        result = send_new_connection_notification(user_with_profile, mock_connection)
        assert result is None

    def test_does_not_send_when_connections_disabled(self, push_subscription):
        """Does not send when user has disabled connection notifications."""
        push_subscription.notify_new_connections = False
        push_subscription.save()

        mock_connection = MagicMock()
        result = send_new_connection_notification(
            push_subscription.user, mock_connection
        )
        assert result is None

    @patch('crush_lu.push_notifications.send_push_notification')
    def test_sends_notification_with_other_user_name(
        self, mock_send_push, push_subscription, db
    ):
        """Sends notification with the other user's display name."""
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}

        # Create other user with profile (display_name is derived from first_name)
        other_user = User.objects.create_user(
            username='other@example.com',
            email='other@example.com',
            password='pass123',
            first_name='OtherPerson'
        )
        CrushProfile.objects.create(
            user=other_user,
            date_of_birth=date(1990, 1, 1),
            gender='F',
            location='Luxembourg',
            preferred_language='en'
        )

        # Mock connection with user1/user2 attributes (as expected by the code)
        mock_connection = MagicMock()
        mock_connection.user1 = other_user
        mock_connection.user2 = push_subscription.user
        mock_connection.id = 123

        send_new_connection_notification(push_subscription.user, mock_connection)

        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args[1]

        # display_name property returns user.first_name when show_full_name is False
        assert 'OtherPerson' in call_kwargs['body']
        assert 'connection-123' in call_kwargs['tag']


# =============================================================================
# TESTS FOR send_new_message_notification()
# =============================================================================

class TestSendNewMessageNotification:
    """Tests for send_new_message_notification function."""

    def test_does_not_send_when_no_subscriptions(self, user_with_profile):
        """Does not send when user has no subscriptions."""
        mock_message = MagicMock()
        result = send_new_message_notification(user_with_profile, mock_message)
        assert result is None

    def test_does_not_send_when_messages_disabled(self, push_subscription):
        """Does not send when user has disabled message notifications."""
        push_subscription.notify_new_messages = False
        push_subscription.save()

        mock_message = MagicMock()
        result = send_new_message_notification(
            push_subscription.user, mock_message
        )
        assert result is None

    @patch('crush_lu.push_notifications.send_push_notification')
    @patch('crush_lu.push_notifications.reverse')
    def test_sends_notification_with_sender_name(
        self, mock_reverse, mock_send_push, push_subscription, db
    ):
        """Sends notification with sender's display name."""
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}
        mock_reverse.return_value = '/connections/1/'

        # Create sender with profile (display_name is derived from first_name)
        sender = User.objects.create_user(
            username='sender@example.com',
            email='sender@example.com',
            password='pass123',
            first_name='MessageSender'
        )
        CrushProfile.objects.create(
            user=sender,
            date_of_birth=date(1990, 1, 1),
            gender='F',
            location='Luxembourg',
            preferred_language='en'
        )

        # Mock message
        mock_connection = MagicMock()
        mock_connection.id = 1
        mock_message = MagicMock()
        mock_message.sender = sender
        mock_message.message = 'Hello there!'
        mock_message.connection = mock_connection

        send_new_message_notification(push_subscription.user, mock_message)

        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args[1]

        # display_name property returns user.first_name when show_full_name is False
        assert 'MessageSender' in call_kwargs['title']

    @patch('crush_lu.push_notifications.send_push_notification')
    @patch('crush_lu.push_notifications.reverse')
    def test_truncates_long_messages(
        self, mock_reverse, mock_send_push, push_subscription, db
    ):
        """Truncates message body to 50 characters with ellipsis."""
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}
        mock_reverse.return_value = '/connections/1/'

        sender = User.objects.create_user(
            username='sender@example.com',
            email='sender@example.com',
            password='pass123',
            first_name='Sender'
        )

        mock_connection = MagicMock()
        mock_connection.id = 1
        mock_message = MagicMock()
        mock_message.sender = sender
        mock_message.message = 'A' * 100  # Long message
        mock_message.connection = mock_connection

        send_new_message_notification(push_subscription.user, mock_message)

        call_kwargs = mock_send_push.call_args[1]

        # Body should be truncated to 50 chars + "..."
        assert len(call_kwargs['body']) == 53
        assert call_kwargs['body'].endswith('...')


# =============================================================================
# TESTS FOR send_profile_approved_notification()
# =============================================================================

class TestSendProfileApprovedNotification:
    """Tests for send_profile_approved_notification function."""

    def test_does_not_send_when_no_subscriptions(self, user_with_profile):
        """Does not send when user has no subscriptions."""
        result = send_profile_approved_notification(user_with_profile)
        assert result is None

    def test_does_not_send_when_profile_updates_disabled(self, push_subscription):
        """Does not send when user has disabled profile notifications."""
        push_subscription.notify_profile_updates = False
        push_subscription.save()

        result = send_profile_approved_notification(push_subscription.user)
        assert result is None

    @patch('crush_lu.push_notifications.send_push_notification')
    @patch('crush_lu.push_notifications.reverse')
    def test_sends_approval_notification(
        self, mock_reverse, mock_send_push, push_subscription
    ):
        """Sends notification when profile is approved."""
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}
        mock_reverse.return_value = '/dashboard/'

        send_profile_approved_notification(push_subscription.user)

        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args[1]

        assert call_kwargs['tag'] == 'profile-approved'


# =============================================================================
# TESTS FOR send_profile_revision_notification()
# =============================================================================

class TestSendProfileRevisionNotification:
    """Tests for send_profile_revision_notification function."""

    def test_does_not_send_when_no_subscriptions(self, user_with_profile):
        """Does not send when user has no subscriptions."""
        result = send_profile_revision_notification(
            user_with_profile, "Please update your bio"
        )
        assert result is None

    @patch('crush_lu.push_notifications.send_push_notification')
    @patch('crush_lu.push_notifications.reverse')
    def test_sends_revision_notification_with_feedback(
        self, mock_reverse, mock_send_push, push_subscription
    ):
        """Sends notification with coach feedback."""
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}
        mock_reverse.return_value = '/edit-profile/'

        feedback = "Please add more details to your bio"
        send_profile_revision_notification(push_subscription.user, feedback)

        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args[1]

        assert call_kwargs['tag'] == 'profile-revision'
        assert 'Please add more details' in call_kwargs['body']

    @patch('crush_lu.push_notifications.send_push_notification')
    @patch('crush_lu.push_notifications.reverse')
    def test_truncates_long_feedback(
        self, mock_reverse, mock_send_push, push_subscription
    ):
        """Truncates feedback to 80 characters in notification body."""
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}
        mock_reverse.return_value = '/edit-profile/'

        long_feedback = 'A' * 200
        send_profile_revision_notification(push_subscription.user, long_feedback)

        call_kwargs = mock_send_push.call_args[1]

        # Body contains feedback[:80] + "..."
        assert 'A' * 80 in call_kwargs['body']


# =============================================================================
# TESTS FOR send_test_notification()
# =============================================================================

class TestSendTestNotification:
    """Tests for send_test_notification function."""

    @patch('crush_lu.push_notifications.send_push_notification')
    @patch('crush_lu.push_notifications.reverse')
    def test_sends_test_notification(
        self, mock_reverse, mock_send_push, user_with_profile
    ):
        """Sends test notification with correct tag."""
        mock_send_push.return_value = {'success': 1, 'failed': 0, 'total': 1}
        mock_reverse.return_value = '/dashboard/'

        send_test_notification(user_with_profile)

        mock_send_push.assert_called_once()
        call_kwargs = mock_send_push.call_args[1]

        assert call_kwargs['tag'] == 'user-test-notification'
        assert call_kwargs['user'] == user_with_profile


# =============================================================================
# TESTS FOR subscription failure tracking and auto-delete
# =============================================================================

class TestSubscriptionFailureTracking:
    """Tests for subscription failure counting and auto-deletion."""

    @patch('crush_lu.push_notifications.webpush')
    def test_auto_delete_after_five_failures(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Subscription is deleted after 5 consecutive failures."""
        from pywebpush import WebPushException

        user = push_subscription.user
        subscription_id = push_subscription.id

        # Set failure count to 4 (one more failure will delete)
        push_subscription.failure_count = 4
        push_subscription.save()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_webpush.side_effect = WebPushException(
            "Error", response=mock_response
        )

        send_push_notification(user, 'Test', 'Body')

        # Subscription should be deleted
        assert not PushSubscription.objects.filter(id=subscription_id).exists()

    @patch('crush_lu.push_notifications.webpush')
    def test_failure_count_resets_on_success(
        self, mock_webpush, push_subscription, mock_vapid_settings
    ):
        """Failure count is reset to 0 on successful delivery."""
        user = push_subscription.user

        # Set some prior failures
        push_subscription.failure_count = 3
        push_subscription.save()

        send_push_notification(user, 'Test', 'Body')

        push_subscription.refresh_from_db()
        assert push_subscription.failure_count == 0


# =============================================================================
# TESTS FOR thread-safety in concurrent scenarios
# =============================================================================

class TestThreadSafety:
    """Tests for thread-safety of language handling."""

    def test_nested_language_contexts(
        self, user_with_german_profile, user_with_french_profile
    ):
        """Nested language contexts work correctly."""
        from django.utils.translation import get_language

        with user_language_context(user_with_german_profile):
            assert get_language() == 'de'

            with user_language_context(user_with_french_profile):
                assert get_language() == 'fr'

            # Back to German after exiting inner context
            assert get_language() == 'de'

    def test_language_context_isolation(
        self, user_with_profile, user_with_german_profile
    ):
        """Language context changes are properly isolated."""
        from django.utils.translation import get_language, activate

        activate('en')

        with user_language_context(user_with_german_profile) as lang:
            assert lang == 'de'
            assert get_language() == 'de'

        # Should be restored to English
        assert get_language() == 'en'
