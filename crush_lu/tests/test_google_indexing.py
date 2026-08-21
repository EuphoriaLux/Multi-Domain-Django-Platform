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
    build_event_list_urls,
    get_google_indexing_credentials,
    get_google_indexing_service,
    notify_event_indexing,
    notify_url_indexing,
)

User = get_user_model()


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

    def test_build_event_list_urls(self):
        """Directory URLs are generated for all 3 supported languages."""
        urls = build_event_list_urls()
        self.assertEqual(len(urls), 3)
        self.assertIn("https://crush.lu/en/events/", urls)
        self.assertIn("https://crush.lu/fr/events/", urls)
        self.assertIn("https://crush.lu/de/events/", urls)

    @override_settings(GOOGLE_INDEXING_ENABLED=False)
    def test_disabled_by_settings(self):
        """Service respects GOOGLE_INDEXING_ENABLED=False."""
        self.assertIsNone(get_google_indexing_service())
        self.assertIsNone(notify_url_indexing("https://crush.lu/en/events/"))
        self.assertEqual(notify_event_indexing(self.event), [])

    @patch("crush_lu.services.google_indexing.get_google_indexing_service")
    def test_notify_url_indexing_success(self, mock_get_service):
        """notify_url_indexing sends URL_UPDATED payload and returns response."""
        mock_service = MagicMock()
        mock_publish = MagicMock()
        mock_publish.execute.return_value = {
            "urlNotificationMetadata": {"url": "https://crush.lu/en/events/15/"}
        }
        mock_service.urlNotifications.return_value.publish.return_value = mock_publish
        mock_get_service.return_value = mock_service

        resp = notify_url_indexing("https://crush.lu/en/events/15/", action="URL_UPDATED")

        self.assertIsNotNone(resp)
        self.assertEqual(
            resp["urlNotificationMetadata"]["url"], "https://crush.lu/en/events/15/"
        )
        mock_service.urlNotifications().publish.assert_called_once_with(
            body={"url": "https://crush.lu/en/events/15/", "type": "URL_UPDATED"}
        )

    @patch("crush_lu.services.google_indexing.get_google_indexing_service")
    def test_notify_url_indexing_handles_exception_gracefully(self, mock_get_service):
        """API errors do not raise exceptions and return None."""
        mock_service = MagicMock()
        mock_service.urlNotifications.return_value.publish.return_value.execute.side_effect = (
            Exception("API quota exceeded")
        )
        mock_get_service.return_value = mock_service

        resp = notify_url_indexing("https://crush.lu/en/events/15/", action="URL_UPDATED")
        self.assertIsNone(resp)

    @patch("crush_lu.services.google_indexing.notify_url_indexing")
    @patch("crush_lu.services.google_indexing.get_google_indexing_service")
    def test_notify_event_indexing_dispatches_all_urls(
        self, mock_get_service, mock_notify_url
    ):
        """notify_event_indexing pings 3 event language URLs + 3 list URLs on update."""
        mock_get_service.return_value = MagicMock()
        mock_notify_url.return_value = {"status": "ok"}

        results = notify_event_indexing(self.event, action="URL_UPDATED", include_event_list=True)

        self.assertEqual(len(results), 6)  # 3 detail + 3 directory URLs
        self.assertEqual(mock_notify_url.call_count, 6)


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
    def test_signal_fires_on_save_commit(self, mock_notify_event):
        """Creating/updating an event triggers notify_event_indexing on commit."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.title = "Updated Speed Dating Title"
            self.event.save()

        mock_notify_event.assert_called()

    @patch("crush_lu.services.google_indexing.notify_url_indexing")
    def test_signal_fires_on_delete_commit(self, mock_notify_url):
        """Deleting an event triggers URL_DELETED notification on commit."""
        event_id = self.event.id
        with self.captureOnCommitCallbacks(execute=True):
            self.event.delete()

        mock_notify_url.assert_called()
        calls = [c[1]["action"] for c in mock_notify_url.call_args_list]
        self.assertTrue(all(action == "URL_DELETED" for action in calls))

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_admin_action_ping_google_indexing(self, mock_notify_event):
        """MeetupEventAdmin action pings Google Indexing and shows success message."""
        mock_notify_event.return_value = [{"status": "ok"}]
        request = MagicMock()

        queryset = MeetupEvent.objects.filter(pk=self.event.pk)
        self.admin.ping_google_indexing(request, queryset)

        mock_notify_event.assert_called_once_with(
            self.event, action="URL_UPDATED", include_event_list=True
        )

    def test_management_command_dry_run(self):
        """Management command runs cleanly with --dry-run flag."""
        # Should execute with code 0 without raising exceptions
        call_command("ping_google_indexing", "--event", str(self.event.id), "--dry-run")
        call_command("ping_google_indexing", "--all", "--dry-run")
        call_command(
            "ping_google_indexing",
            "--url",
            "https://crush.lu/en/events/",
            "--dry-run",
        )
