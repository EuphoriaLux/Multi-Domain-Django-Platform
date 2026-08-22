"""
Unit and integration tests for Google Search Indexing API integration in Crush.lu.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.admin.events import MeetupEventAdmin
from crush_lu.models import MeetupEvent
from crush_lu.services.google_indexing import (
    build_event_indexing_urls,
    get_google_indexing_credentials,
    get_google_indexing_session,
    is_indexing_enabled,
    notify_event_indexing,
    notify_events_indexing,
    notify_url_indexing,
    notify_urls_indexing,
    should_index_event,
)

User = get_user_model()


@override_settings(GOOGLE_INDEXING_ENABLED=True)
class GoogleIndexingServiceTests(TestCase):
    """Test URL builders, credentials loading, and Google Indexing API service methods."""

    def setUp(self):
        self.event = MeetupEvent.objects.create(
            title="Test Singles Mixer",
            description="A test mixer event description.",
            event_type="singles_mixer",
            date_time=timezone.now() + timedelta(days=5),
            location="Luxembourg City",
            address_street="Rue du Nord",
            address_number="12",
            address_town="Luxembourg",
            address_postcode="2229",
            max_participants=30,
            registration_deadline=timezone.now() + timedelta(days=2),
            is_published=True,
        )

    def test_build_event_indexing_urls(self):
        """URLs are generated for all 3 supported languages."""
        urls = build_event_indexing_urls(self.event)
        self.assertEqual(len(urls), 3)
        self.assertIn(f"https://crush.lu/en/events/{self.event.id}/", urls)
        self.assertIn(f"https://crush.lu/fr/events/{self.event.id}/", urls)
        self.assertIn(f"https://crush.lu/de/events/{self.event.id}/", urls)

    def test_build_event_indexing_urls_empty_when_no_pk(self):
        """Unsaved event returns empty URL list without error."""
        unsaved = MeetupEvent()
        urls = build_event_indexing_urls(unsaved)
        self.assertEqual(urls, [])

    def test_should_index_event_logic(self):
        """should_index_event returns True only for published, non-cancelled, non-private events."""
        self.assertTrue(should_index_event(self.event))

        self.event.is_published = False
        self.assertFalse(should_index_event(self.event))

        self.event.is_published = True
        self.event.is_cancelled = True
        self.assertFalse(should_index_event(self.event))

        self.event.is_cancelled = False
        self.event.is_private_invitation = True
        self.assertFalse(should_index_event(self.event))

    @override_settings(GOOGLE_INDEXING_ENABLED=False)
    def test_disabled_by_settings(self):
        """Service respects GOOGLE_INDEXING_ENABLED=False."""
        self.assertFalse(is_indexing_enabled())
        self.assertIsNone(get_google_indexing_session())
        self.assertIsNone(notify_url_indexing("https://crush.lu/en/events/15/"))
        self.assertEqual(notify_event_indexing(self.event), [])
        self.assertEqual(notify_events_indexing([self.event])["success_count"], 0)
        self.assertEqual(notify_urls_indexing(["https://crush.lu/en/events/15/"]), [])

    @override_settings(
        GOOGLE_INDEXING_KEY_JSON='{"type": "service_account", "client_email": "test@crush.iam.gserviceaccount.com"}'
    )
    @patch("google.oauth2.service_account.Credentials.from_service_account_info")
    def test_get_google_indexing_credentials_from_json(self, mock_from_info):
        """Credentials load successfully from GOOGLE_INDEXING_KEY_JSON setting."""
        mock_from_info.return_value = MagicMock()
        creds = get_google_indexing_credentials()
        self.assertIsNotNone(creds)
        mock_from_info.assert_called_once()

    @patch("crush_lu.services.google_indexing.get_google_indexing_session")
    def test_notify_url_indexing_success(self, mock_get_session):
        """notify_url_indexing sends URL_UPDATED payload with timeout and returns JSON response."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "urlNotificationMetadata": {"url": "https://crush.lu/en/events/15/"}
        }
        mock_session.post.return_value = mock_resp
        mock_get_session.return_value = mock_session

        resp = notify_url_indexing(
            "https://crush.lu/en/events/15/", action="URL_UPDATED", timeout=5
        )

        self.assertIsNotNone(resp)
        self.assertEqual(
            resp["urlNotificationMetadata"]["url"], "https://crush.lu/en/events/15/"
        )
        mock_session.post.assert_called_once_with(
            "https://indexing.googleapis.com/v3/urlNotifications:publish",
            json={"url": "https://crush.lu/en/events/15/", "type": "URL_UPDATED"},
            timeout=5,
        )

    @patch("crush_lu.services.google_indexing.get_google_indexing_session")
    def test_notify_url_indexing_handles_exception_gracefully(self, mock_get_session):
        """API errors and timeouts do not raise exceptions and return None."""
        mock_session = MagicMock()
        mock_session.post.side_effect = Exception("Network timeout")
        mock_get_session.return_value = mock_session

        resp = notify_url_indexing(
            "https://crush.lu/en/events/15/", action="URL_UPDATED"
        )
        self.assertIsNone(resp)

    @patch("crush_lu.services.google_indexing.notify_url_indexing")
    @patch("crush_lu.services.google_indexing.get_google_indexing_session")
    def test_notify_event_indexing_dispatches_detail_urls(
        self, mock_get_session, mock_notify_url
    ):
        """notify_event_indexing pings the 3 language detail URLs."""
        mock_get_session.return_value = MagicMock()
        mock_notify_url.return_value = {"status": "ok"}

        results = notify_event_indexing(self.event, action="URL_UPDATED")

        self.assertEqual(len(results), 3)  # 3 detail language URLs
        self.assertEqual(mock_notify_url.call_count, 3)

    @patch("crush_lu.services.google_indexing.notify_url_indexing")
    @patch("crush_lu.services.google_indexing.get_google_indexing_session")
    def test_notify_events_indexing_batch_budget(
        self, mock_get_session, mock_notify_url
    ):
        """notify_events_indexing batches multiple events within a single session and deadline."""
        mock_get_session.return_value = MagicMock()
        mock_notify_url.return_value = {"status": "ok"}

        res = notify_events_indexing(
            [self.event], action="URL_UPDATED", max_budget_seconds=5.0
        )

        self.assertEqual(res["success_count"], 3)
        self.assertEqual(res["total_expected"], 3)
        self.assertEqual(res["deferred_count"], 0)


@override_settings(GOOGLE_INDEXING_ENABLED=True)
class GoogleIndexingSignalAndAdminTests(TestCase):
    """Test signal handlers, admin actions, and management command."""

    def setUp(self):
        self.event = MeetupEvent.objects.create(
            title="Signal Test Event",
            description="Testing signals.",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=3),
            location="Luxembourg City",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=1),
            is_published=True,
        )
        self.site = AdminSite()
        self.admin = MeetupEventAdmin(MeetupEvent, self.site)

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_fires_on_published_event_save(self, mock_notify_event):
        """Updating a published active event triggers notify_event_indexing with URL_UPDATED."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.title = "Updated Speed Dating Title"
            self.event.save()

        mock_notify_event.assert_called_once_with(self.event, action="URL_UPDATED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_skips_on_wallet_class_nested_save(self, mock_notify_event):
        """Saving with update_fields containing only internal fields skips signal."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.save(update_fields=["google_wallet_event_class_id"])

        mock_notify_event.assert_not_called()

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_fires_url_deleted_for_draft_or_cancelled(self, mock_notify_event):
        """Saving an unpublished or cancelled event sends URL_DELETED."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.is_published = False
            self.event.save()

        mock_notify_event.assert_called_once_with(self.event, action="URL_DELETED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_fires_url_deleted_for_private_invitation(self, mock_notify_event):
        """Saving a private invitation event sends URL_DELETED to protect private URLs."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.is_private_invitation = True
            self.event.save()

        mock_notify_event.assert_called_once_with(self.event, action="URL_DELETED")

    @patch("crush_lu.services.google_indexing.notify_urls_indexing")
    def test_signal_fires_on_delete_commit(self, mock_notify_urls):
        """Deleting an event triggers notify_urls_indexing with URL_DELETED on commit."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.delete()

        mock_notify_urls.assert_called()
        self.assertEqual(mock_notify_urls.call_args[1]["action"], "URL_DELETED")

    @patch("crush_lu.services.google_indexing.notify_events_indexing")
    def test_admin_publish_action_notifies_google(self, mock_notify_events):
        """publish_events bulk action notifies Google Indexing with batch helper."""
        self.event.is_published = False
        self.event.address_street = "Grand-Rue"
        self.event.address_number = "1"
        self.event.address_town = "Luxembourg"
        self.event.address_postcode = "1661"
        self.event.canton = "Luxembourg"
        self.event.save()

        request = MagicMock()
        queryset = MeetupEvent.objects.filter(pk=self.event.pk)
        self.admin.publish_events(request, queryset)

        mock_notify_events.assert_called()

    @patch("crush_lu.services.google_indexing.notify_events_indexing")
    def test_admin_unpublish_action_notifies_google(self, mock_notify_events):
        """unpublish_events bulk action notifies Google Indexing with URL_DELETED."""
        request = MagicMock()
        queryset = MeetupEvent.objects.filter(pk=self.event.pk)
        self.admin.unpublish_events(request, queryset)

        mock_notify_events.assert_called()

    @patch("crush_lu.services.google_indexing.notify_events_indexing")
    def test_admin_cancel_action_notifies_google(self, mock_notify_events):
        """cancel_events bulk action notifies Google Indexing with URL_DELETED."""
        request = MagicMock()
        queryset = MeetupEvent.objects.filter(pk=self.event.pk)
        self.admin.cancel_events(request, queryset)

        mock_notify_events.assert_called()

    @patch("crush_lu.services.google_indexing.notify_events_indexing")
    def test_admin_action_ping_google_indexing(self, mock_notify_events):
        """MeetupEventAdmin ping_google_indexing action reports success."""
        mock_notify_events.return_value = {
            "success_count": 3,
            "total_expected": 3,
            "deferred_count": 0,
        }
        request = MagicMock()

        queryset = MeetupEvent.objects.filter(pk=self.event.pk)
        self.admin.ping_google_indexing(request, queryset)

        mock_notify_events.assert_called_once()

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_fires_on_duration_minutes_save(self, mock_notify_event):
        """Saving with update_fields=['duration_minutes'] triggers indexing update."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.duration_minutes = 150
            self.event.save(update_fields=["duration_minutes"])

        mock_notify_event.assert_called_once_with(self.event, action="URL_UPDATED")

    @patch("crush_lu.management.commands.ping_google_indexing.notify_event_indexing")
    def test_management_command_single_event_eligibility(self, mock_notify_event):
        """Single event ping command sends URL_DELETED for unpublished events unless --delete is passed."""
        self.event.is_published = False
        self.event.save()

        call_command("ping_google_indexing", "--event", str(self.event.id))
        mock_notify_event.assert_called_with(self.event, action="URL_DELETED")

    def test_management_command_dry_run_and_execution(self):
        """Management command runs cleanly with --dry-run flag and execution."""
        call_command("ping_google_indexing", "--event", str(self.event.id), "--dry-run")
        call_command("ping_google_indexing", "--all", "--dry-run")
        call_command(
            "ping_google_indexing",
            "--url",
            "https://crush.lu/en/events/15/",
            "--dry-run",
        )
