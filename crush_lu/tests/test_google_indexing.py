"""
Unit and integration tests for Google Search Indexing API integration in Crush.lu.
"""

import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.admin.events import MeetupEventAdmin
from crush_lu.models import EventRegistration, MeetupEvent
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
        """should_index_event mirrors what event_detail serves an anonymous Googlebot."""
        self.assertTrue(should_index_event(self.event))

        self.event.is_published = False
        self.assertFalse(should_index_event(self.event))

        self.event.is_published = True
        self.event.is_private_invitation = True
        self.assertFalse(should_index_event(self.event))

    def test_cancelled_published_event_stays_indexable(self):
        """A cancelled but published page still 200s with EventCancelled markup.

        Declaring it URL_DELETED would be API misuse: Google re-crawls, finds the
        page alive, and the cancellation never reaches the SERP.
        """
        self.event.is_cancelled = True
        self.assertTrue(should_index_event(self.event))

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
    def test_notify_url_indexing_passes_max_allowed_time_and_caps_timeout(
        self, mock_get_session
    ):
        """notify_url_indexing caps socket timeout to max_allowed_time and passes max_allowed_time."""
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        mock_session.post.return_value = mock_resp
        mock_get_session.return_value = mock_session

        resp = notify_url_indexing(
            "https://crush.lu/en/events/15/",
            action="URL_UPDATED",
            timeout=5.0,
            max_allowed_time=2.5,
        )

        self.assertIsNotNone(resp)
        mock_session.post.assert_called_once_with(
            "https://indexing.googleapis.com/v3/urlNotifications:publish",
            json={"url": "https://crush.lu/en/events/15/", "type": "URL_UPDATED"},
            timeout=2.5,
            max_allowed_time=2.5,
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

    @patch("crush_lu.services.google_indexing.notify_url_indexing")
    @patch("crush_lu.services.google_indexing.get_google_indexing_session")
    def test_notify_events_indexing_counts_intra_event_deferrals(
        self, mock_get_session, mock_notify_url
    ):
        """URLs skipped part-way through the *last* event still count as deferred."""
        mock_get_session.return_value = MagicMock()

        # First URL consumes the whole budget; the other two are never attempted.
        def _burn_budget(*args, **kwargs):
            time.sleep(0.05)
            return {"status": "ok"}

        mock_notify_url.side_effect = _burn_budget

        res = notify_events_indexing(
            [self.event], action="URL_UPDATED", max_budget_seconds=0.01
        )

        self.assertEqual(res["total_expected"], 3)
        self.assertEqual(res["success_count"], 1)
        self.assertEqual(res["deferred_count"], 2)


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
        self._retire_fixture_indexing_callbacks()
        self.site = AdminSite()
        self.admin = MeetupEventAdmin(MeetupEvent, self.site)

    @staticmethod
    def _retire_fixture_indexing_callbacks():
        """Mark fixture-created events' queued indexing callbacks as already spent.

        `captureOnCommitCallbacks` executes only the callbacks registered inside
        its own block, and never removes any of them from
        `connection.run_on_commit`; a Django `TestCase` never commits either. So
        a model created in `setUp`, or between blocks, leaves a live callback
        queued for the whole test — and since the scheduler coalesces per event
        and transaction, that stale callback would swallow the notification the
        test is asserting on. These belong to fixture setup, not to the
        behaviour under test.
        """
        from django.db import connection

        for entry in connection.run_on_commit:
            callback = entry[1]
            if hasattr(callback, "_indexing_event_pk"):
                callback._indexing_done = True

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_fires_on_published_event_save(self, mock_notify_event):
        """Updating a published active event triggers notify_event_indexing with URL_UPDATED."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.title = "Updated Speed Dating Title"
            self.event.save()

        mock_notify_event.assert_called_once()
        self.assertEqual(mock_notify_event.call_args[0][0], self.event)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_UPDATED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_skips_on_wallet_class_nested_save(self, mock_notify_event):
        """Saving with update_fields containing only internal fields skips signal."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.save(update_fields=["google_wallet_event_class_id"])

        mock_notify_event.assert_not_called()

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_fires_url_deleted_for_draft(self, mock_notify_event):
        """Saving an unpublished event sends URL_DELETED (the page 404s)."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.is_published = False
            self.event.save()

        mock_notify_event.assert_called_once()
        self.assertEqual(mock_notify_event.call_args[0][0], self.event)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_DELETED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_fires_url_updated_for_cancelled(self, mock_notify_event):
        """Cancelling a published event refreshes the page instead of deleting it."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.is_cancelled = True
            self.event.save()

        mock_notify_event.assert_called_once()
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_UPDATED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_fires_url_deleted_for_private_invitation(self, mock_notify_event):
        """Saving a private invitation event sends URL_DELETED to protect private URLs."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.is_private_invitation = True
            self.event.save()

        mock_notify_event.assert_called_once()
        self.assertEqual(mock_notify_event.call_args[0][0], self.event)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_DELETED")

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

        mock_notify_event.assert_called_once()
        self.assertEqual(mock_notify_event.call_args[0][0], self.event)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_UPDATED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_signal_fires_on_expanded_fields_save(self, mock_notify_event):
        """Saving with update_fields for registration_fee, max_participants, etc. triggers indexing update."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.registration_fee = 35.00
            self.event.save(update_fields=["registration_fee"])

        mock_notify_event.assert_called_once()
        self.assertEqual(mock_notify_event.call_args[0][0], self.event)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_UPDATED")

    @patch("crush_lu.services.google_indexing.notify_urls_indexing")
    def test_bulk_delete_shares_post_commit_budget_and_is_savepoint_safe(
        self, mock_notify_urls
    ):
        """Bulk deleting multiple events inside one transaction executes with shared deadline context."""
        event2 = MeetupEvent.objects.create(
            title="Second Event",
            description="Second Event Description",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=10),
            registration_deadline=timezone.now() + timedelta(days=9),
            location="Luxembourg City",
            is_published=True,
        )
        self._retire_fixture_indexing_callbacks()

        with self.captureOnCommitCallbacks(execute=True):
            self.event.delete()
            event2.delete()

        self.assertEqual(mock_notify_urls.call_count, 2)
        deadline1 = mock_notify_urls.call_args_list[0][1]["deadline"]
        deadline2 = mock_notify_urls.call_args_list[1][1]["deadline"]
        self.assertEqual(deadline1, deadline2)
        self.assertEqual(mock_notify_urls.call_args_list[0][1]["action"], "URL_DELETED")
        self.assertEqual(mock_notify_urls.call_args_list[1][1]["action"], "URL_DELETED")

    @patch("crush_lu.services.google_indexing.notify_urls_indexing")
    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_savepoint_rollback_discards_indexing_callbacks(
        self, mock_notify_event, mock_notify_urls
    ):
        """Rolling back an inner atomic block does not execute rolled-back on_commit callbacks."""
        with self.captureOnCommitCallbacks(execute=True):
            self.event.title = "Committed Outer Change"
            self.event.save()

            try:
                with transaction.atomic():
                    self.event.is_published = False
                    self.event.save()
                    raise ValueError("Simulated savepoint rollback")
            except ValueError:
                pass

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_UPDATED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_registration_save_and_delete_triggers_indexing_notification(
        self, mock_notify_event
    ):
        """EventRegistration status change or deletion notifies indexing to update capacity/availability."""
        user = User.objects.create_user(
            username="attendee1", email="attendee1@example.com"
        )

        # 1. Registration creation
        with self.captureOnCommitCallbacks(execute=True):
            reg = EventRegistration.objects.create(
                event=self.event,
                user=user,
                status="confirmed",
            )

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[0][0], self.event)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_UPDATED")

        # 2. Status change (confirmed -> cancelled)
        mock_notify_event.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            reg.status = "cancelled"
            reg.save(update_fields=["status"])

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[0][0], self.event)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_UPDATED")

        # 3. Check-in (confirmed -> attended) stays within seat-holding set -> 0 indexing notifications
        with self.captureOnCommitCallbacks(execute=True):
            reg.status = "confirmed"
            reg.save()
        mock_notify_event.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            reg.status = "attended"
            reg.save(update_fields=["status"])

        self.assertEqual(mock_notify_event.call_count, 0)

        # 4. Non-status update does not trigger indexing
        mock_notify_event.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            reg.special_requests = "Vegetarian please"
            reg.save(update_fields=["special_requests"])

        self.assertEqual(mock_notify_event.call_count, 0)

        # 5. Deleting a seat-holding registration triggers indexing
        with self.captureOnCommitCallbacks(execute=True):
            reg.status = "confirmed"
            reg.save()
        mock_notify_event.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            reg.delete()

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[0][0], self.event)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_UPDATED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_coaches_m2m_changed_triggers_indexing(self, mock_notify_event):
        """Adding coaches to an event updates schema.org performer and triggers indexing."""
        from crush_lu.models import CrushCoach

        coach_user = User.objects.create_user(
            username="coach_user",
            email="coach@example.com",
            first_name="Taylor",
            last_name="Coach",
        )
        coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.event.coaches.add(coach)

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[0][0], self.event)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_UPDATED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_expired_deadline_resets_on_subsequent_batches_outside_http_request(
        self, mock_notify_event
    ):
        """In non-request processes (e.g. CLI/shell), a previous batch's expired deadline is reset."""
        from crush_lu.signals import _get_indexing_context

        ctx = _get_indexing_context()
        # Simulate a spent batch. Outside a request there is no worker timeout
        # to protect, so the window must roll over rather than go silent.
        ctx.deadline = time.monotonic() - 100.0
        ctx.in_request = False
        ctx.session = "old_session"

        with self.captureOnCommitCallbacks(execute=True):
            self.event.title = "Fresh Batch Title"
            self.event.save()

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertGreater(ctx.deadline, time.monotonic())

    def test_cleanup_indexing_context_on_request_finished(self):
        """cleanup_indexing_thread_state clears the window and releases request scope."""
        from crush_lu.signals import (
            _get_indexing_context,
            cleanup_indexing_thread_state,
        )

        ctx = _get_indexing_context()
        ctx.deadline = time.monotonic() + 10.0
        ctx.session = "dummy_session"
        ctx.in_request = True

        cleanup_indexing_thread_state()

        self.assertIsNone(ctx.deadline)
        self.assertIsNone(ctx.session)
        self.assertFalse(ctx.in_request)

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_exhausted_budget_skips_inside_a_request(self, mock_notify_event):
        """Inside an HTTP request a spent window must stop, not roll over."""
        from crush_lu.signals import _get_indexing_context

        ctx = _get_indexing_context()
        ctx.deadline = time.monotonic() - 1.0
        ctx.session = None
        ctx.in_request = True
        self.addCleanup(setattr, ctx, "in_request", False)

        with self.captureOnCommitCallbacks(execute=True):
            self.event.title = "Should Not Be Indexed"
            self.event.save()

        mock_notify_event.assert_not_called()

    @patch("crush_lu.management.commands.ping_google_indexing.notify_event_indexing")
    def test_management_command_single_event_eligibility(self, mock_notify_event):
        """Single event ping command sends URL_DELETED for unpublished events unless --delete is passed."""
        self.event.is_published = False
        self.event.save()

        mock_notify_event.return_value = [{"ok": 1}, {"ok": 2}, {"ok": 3}]
        call_command("ping_google_indexing", "--event", str(self.event.id))
        mock_notify_event.assert_called_with(self.event, action="URL_DELETED")

    @patch("crush_lu.management.commands.ping_google_indexing.notify_event_indexing")
    def test_management_command_all_includes_in_progress_events(
        self, mock_notify_event
    ):
        """ping_google_indexing --all includes currently in-progress events (end_time >= now)."""
        now = timezone.now()
        # Started 30 mins ago, duration 120 mins -> ends 90 mins in the future
        in_progress = MeetupEvent.objects.create(
            title="In-Progress Event",
            description="Testing live lookback inclusion.",
            event_type="speed_dating",
            date_time=now - timedelta(minutes=30),
            duration_minutes=120,
            location="Luxembourg City",
            registration_deadline=now - timedelta(hours=1),
            is_published=True,
        )

        mock_notify_event.return_value = [{"ok": 1}, {"ok": 2}, {"ok": 3}]
        call_command("ping_google_indexing", "--all")
        called_events = [call.args[0] for call in mock_notify_event.call_args_list]
        self.assertIn(in_progress, called_events)
        self.assertIn(self.event, called_events)

    @patch("crush_lu.management.commands.ping_google_indexing.notify_event_indexing")
    def test_management_command_errors_when_nothing_was_delivered(
        self, mock_notify_event
    ):
        """A run that delivers zero notifications must exit nonzero, not look healthy."""
        from django.core.management.base import CommandError

        mock_notify_event.return_value = []
        with self.assertRaises(CommandError):
            call_command("ping_google_indexing", "--event", str(self.event.id))

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_coaches_set_coalesces_into_one_notification(self, mock_notify_event):
        """coaches.set() fires post_remove then post_add; that is still one page change."""
        from crush_lu.models import CrushCoach

        coaches = []
        for i in range(2):
            coach_user = User.objects.create_user(
                username=f"set_coach_{i}",
                email=f"set_coach_{i}@example.com",
                first_name=f"Coach{i}",
            )
            coaches.append(CrushCoach.objects.create(user=coach_user, is_active=True))

        with self.captureOnCommitCallbacks(execute=True):
            self.event.coaches.add(coaches[0])
        mock_notify_event.reset_mock()

        # Swaps one coach for the other -> post_remove + post_add in one call.
        with self.captureOnCommitCallbacks(execute=True):
            self.event.coaches.set([coaches[1]])

        self.assertEqual(mock_notify_event.call_count, 1)

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_reverse_coach_clear_notifies_affected_events(self, mock_notify_event):
        """coach.assigned_events.clear() sends pk_set=None; the IDs come from pre_clear."""
        from crush_lu.models import CrushCoach

        coach_user = User.objects.create_user(
            username="clearing_coach",
            email="clearing_coach@example.com",
            first_name="Robin",
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)

        with self.captureOnCommitCallbacks(execute=True):
            coach.assigned_events.add(self.event)
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            coach.assigned_events.clear()

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[0][0].pk, self.event.pk)

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_reverse_coach_add_notifies_each_distinct_event(self, mock_notify_event):
        """Coalescing is per event: two reverse adds for two events are two pings."""
        from crush_lu.models import CrushCoach

        coach_user = User.objects.create_user(
            username="multi_coach", email="multi_coach@example.com", first_name="Sam"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        event2 = MeetupEvent.objects.create(
            title="Second Assigned Event",
            description="Second event for the same coach.",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=8),
            location="Luxembourg City",
            registration_deadline=timezone.now() + timedelta(days=7),
            is_published=True,
        )
        self._retire_fixture_indexing_callbacks()
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            coach.assigned_events.add(self.event)
            coach.assigned_events.add(event2)

        notified = {call.args[0].pk for call in mock_notify_event.call_args_list}
        self.assertEqual(notified, {self.event.pk, event2.pk})

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_many_triggers_for_one_event_coalesce(self, mock_notify_event):
        """Raising capacity promotes N waitlisted rows; that is still one page change."""
        users = [
            User.objects.create_user(
                username=f"coalesce_{i}", email=f"coalesce_{i}@example.com"
            )
            for i in range(3)
        ]
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            for user in users:
                EventRegistration.objects.create(
                    event=self.event, user=user, status="confirmed"
                )
            self.event.title = "Capacity Raised"
            self.event.save()

        self.assertEqual(mock_notify_event.call_count, 1)

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_coalescing_keeps_the_more_permissive_trigger(self, mock_notify_event):
        """A registration ping must not swallow the URL_DELETED an unpublish needs."""
        user = User.objects.create_user(
            username="permissive", email="permissive@example.com"
        )
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            # skip_if_ineligible=True lands first...
            EventRegistration.objects.create(
                event=self.event, user=user, status="confirmed"
            )
            # ...and the event save that follows still needs its URL_DELETED.
            self.event.is_published = False
            self.event.save()

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_DELETED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_foreign_commit_callback_does_not_reopen_the_budget(
        self, mock_notify_event
    ):
        """Another receiver's slow on_commit work is not idleness.

        Production's ImmediateBackend runs the echo.lu enqueue inline, so a real
        drain interleaves foreign HTTP work between two indexing callbacks.
        """
        from django.db import transaction as db_transaction

        from crush_lu.signals import _get_indexing_context

        ctx = _get_indexing_context()
        ctx.deadline = None
        ctx.session = None
        ctx.in_request = True
        self.addCleanup(setattr, ctx, "in_request", False)

        event2 = MeetupEvent.objects.create(
            title="Interleave Event Two",
            description="Second event for the interleaving test.",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=9),
            location="Luxembourg City",
            registration_deadline=timezone.now() + timedelta(days=8),
            is_published=True,
        )
        self._retire_fixture_indexing_callbacks()

        deadlines = []
        mock_notify_event.side_effect = lambda *a, **kw: (
            deadlines.append(kw.get("deadline")),
            [],
        )[1]

        with self.captureOnCommitCallbacks(execute=True):
            self.event.title = "Interleave Event One Updated"
            self.event.save()
            # A foreign callback, registered between ours, that takes real time.
            db_transaction.on_commit(lambda: time.sleep(1.2))
            event2.title = "Interleave Event Two Updated"
            event2.save()

        self.assertEqual(len(deadlines), 2)
        self.assertEqual(deadlines[0], deadlines[1])

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_coach_deactivation_notifies_assigned_events(self, mock_notify_event):
        """A single-record is_active flip changes the published performer list."""
        from crush_lu.models import CrushCoach

        coach_user = User.objects.create_user(
            username="solo_coach", email="solo_coach@example.com", first_name="Jo"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        with self.captureOnCommitCallbacks(execute=True):
            self.event.coaches.add(coach)
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            coach.is_active = False
            coach.save()

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[0][0].pk, self.event.pk)

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_coach_rename_notifies_assigned_events(self, mock_notify_event):
        """performer names come from the coach's User.first_name."""
        from crush_lu.models import CrushCoach

        coach_user = User.objects.create_user(
            username="rename_coach",
            email="rename_coach@example.com",
            first_name="Chris",
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        with self.captureOnCommitCallbacks(execute=True):
            self.event.coaches.add(coach)
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            coach_user.first_name = "Christina"
            coach_user.save(update_fields=["first_name"])

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[0][0].pk, self.event.pk)

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_login_style_user_save_does_not_notify(self, mock_notify_event):
        """last_login writes must not cost a query or a ping."""
        from crush_lu.models import CrushCoach

        coach_user = User.objects.create_user(
            username="login_coach", email="login_coach@example.com", first_name="Robin"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        with self.captureOnCommitCallbacks(execute=True):
            self.event.coaches.add(coach)
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            coach_user.last_login = timezone.now()
            coach_user.save(update_fields=["last_login"])

        mock_notify_event.assert_not_called()

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_creating_a_draft_sends_nothing(self, mock_notify_event):
        """is_published defaults to False; a draft has no public URL to delete."""
        with self.captureOnCommitCallbacks(execute=True):
            MeetupEvent.objects.create(
                title="Fresh Draft",
                description="Not published yet.",
                event_type="speed_dating",
                date_time=timezone.now() + timedelta(days=11),
                location="Luxembourg City",
                registration_deadline=timezone.now() + timedelta(days=10),
            )

        mock_notify_event.assert_not_called()

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_creating_a_published_event_notifies(self, mock_notify_event):
        """A create that is public straight away still gets its URL_UPDATED."""
        with self.captureOnCommitCallbacks(execute=True):
            MeetupEvent.objects.create(
                title="Born Public",
                description="Published on creation.",
                event_type="speed_dating",
                date_time=timezone.now() + timedelta(days=12),
                location="Luxembourg City",
                registration_deadline=timezone.now() + timedelta(days=11),
                is_published=True,
            )

        mock_notify_event.assert_called_once()
        self.assertEqual(mock_notify_event.call_args[1]["action"], "URL_UPDATED")

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_reassigning_a_registration_notifies_both_events(self, mock_notify_event):
        """The event FK is editable in the admin; a seat move changes two pages."""
        event2 = MeetupEvent.objects.create(
            title="Destination Event",
            description="Receives the moved seat.",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=13),
            location="Luxembourg City",
            registration_deadline=timezone.now() + timedelta(days=12),
            is_published=True,
        )
        user = User.objects.create_user(username="mover", email="mover@example.com")
        reg = EventRegistration.objects.create(
            event=self.event, user=user, status="confirmed"
        )
        self._retire_fixture_indexing_callbacks()
        mock_notify_event.reset_mock()

        # Status untouched: only the event changes.
        with self.captureOnCommitCallbacks(execute=True):
            reg.event = event2
            reg.save(update_fields=["event"])

        notified = {call.args[0].pk for call in mock_notify_event.call_args_list}
        self.assertEqual(notified, {self.event.pk, event2.pk})

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_deleting_a_coach_notifies_assigned_events(self, mock_notify_event):
        """The delete cascade strips the m2m rows without emitting m2m_changed."""
        from crush_lu.models import CrushCoach

        coach_user = User.objects.create_user(
            username="doomed_coach",
            email="doomed_coach@example.com",
            first_name="Sam",
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        with self.captureOnCommitCallbacks(execute=True):
            self.event.coaches.add(coach)
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            coach.delete()

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[0][0].pk, self.event.pk)

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_upgrade_from_a_nested_savepoint_does_not_leak(self, mock_notify_event):
        """A rolled-back event save must not re-arm an outer registration ping.

        The permissive upgrade mutates a callback the trigger does not own, so it
        is only taken when the current savepoint stack is a subset of the pending
        callback's. Here it is not: the event save is nested deeper and rolls
        back, and the surviving registration callback must stay strict — the
        event is invitation-only, so it has no public URL to delete.
        """
        private_event = MeetupEvent.objects.create(
            title="Nested Savepoint Private Event",
            description="Invitation only.",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=14),
            location="Luxembourg City",
            registration_deadline=timezone.now() + timedelta(days=13),
            is_published=True,
            is_private_invitation=True,
        )
        user = User.objects.create_user(username="nested", email="nested@example.com")
        self._retire_fixture_indexing_callbacks()
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            # Strict trigger, registered at the outer level.
            EventRegistration.objects.create(
                event=private_event, user=user, status="confirmed"
            )
            try:
                with transaction.atomic():
                    private_event.title = "Rolled Back Title"
                    private_event.save()
                    raise ValueError("Simulated savepoint rollback")
            except ValueError:
                pass

        mock_notify_event.assert_not_called()

    def test_request_start_anchors_a_wall_clock_deadline(self):
        """The window can never outrun the request it belongs to."""
        from crush_lu.signals import (
            INDEXING_REQUEST_WALL_CLOCK_SECONDS,
            _get_indexing_context,
            cleanup_indexing_thread_state,
            open_indexing_request_scope,
        )

        self.addCleanup(cleanup_indexing_thread_state)
        # Called directly rather than via request_started.send(): that signal
        # also drives django.db.close_old_connections, which would tear down the
        # TestCase's transaction and fail every test after this one.
        open_indexing_request_scope()

        ctx = _get_indexing_context()
        self.assertTrue(ctx.in_request)
        self.assertIsNotNone(ctx.request_deadline)
        self.assertLessEqual(
            ctx.request_deadline,
            time.monotonic() + INDEXING_REQUEST_WALL_CLOCK_SECONDS,
        )

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_window_cannot_outlive_the_request_wall_clock(self, mock_notify_event):
        """A window opened late is truncated to the request's remaining headroom."""
        from crush_lu.signals import _get_indexing_context

        ctx = _get_indexing_context()
        ctx.deadline = None
        ctx.session = None
        ctx.in_request = True
        # The request is already past its wall-clock ceiling.
        ctx.request_deadline = time.monotonic() - 1.0
        self.addCleanup(setattr, ctx, "request_deadline", None)
        self.addCleanup(setattr, ctx, "in_request", False)

        with self.captureOnCommitCallbacks(execute=True):
            self.event.title = "Too Late To Index"
            self.event.save()

        mock_notify_event.assert_not_called()

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_rolled_back_coach_change_does_not_suppress_the_next_one(
        self, mock_notify_event
    ):
        """A discarded coach change must not leave a claim that mutes the next one."""
        from crush_lu.models import CrushCoach

        coach_user = User.objects.create_user(
            username="rollback_coach",
            email="rollback_coach@example.com",
            first_name="Alex",
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self.event.coaches.add(coach)
                    raise ValueError("Simulated savepoint rollback")
            except ValueError:
                pass

            # Same in-memory instances, committed this time.
            self.event.coaches.add(coach)

        self.assertEqual(mock_notify_event.call_count, 1)
        self.assertEqual(mock_notify_event.call_args[0][0].pk, self.event.pk)

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_registration_on_ineligible_event_is_not_notified(self, mock_notify_event):
        """A seat change on an invitation-only event must not spend indexing quota."""
        private_event = MeetupEvent.objects.create(
            title="Private Event",
            description="Invitation only.",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=4),
            location="Luxembourg City",
            max_participants=10,
            registration_deadline=timezone.now() + timedelta(days=2),
            is_published=True,
            is_private_invitation=True,
        )
        user = User.objects.create_user(
            username="private_attendee", email="private@example.com"
        )
        self._retire_fixture_indexing_callbacks()
        mock_notify_event.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            EventRegistration.objects.create(
                event=private_event, user=user, status="confirmed"
            )

        mock_notify_event.assert_not_called()

    @patch("crush_lu.services.google_indexing.notify_event_indexing")
    def test_slow_callback_does_not_reopen_the_budget(self, mock_notify_event):
        """Time spent waiting on Google is not idleness, so the deadline holds."""
        from crush_lu.signals import _get_indexing_context

        ctx = _get_indexing_context()
        ctx.deadline = None
        ctx.session = None
        ctx.last_active = 0.0

        event2 = MeetupEvent.objects.create(
            title="Budget Event Two",
            description="Second event for the shared budget.",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=6),
            location="Luxembourg City",
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
        )
        self._retire_fixture_indexing_callbacks()

        deadlines = []

        def _slow(*args, **kwargs):
            deadlines.append(kwargs.get("deadline"))
            if len(deadlines) == 1:
                # Longer than INDEXING_IDLE_RESET_SECONDS: stamping last_active
                # on entry used to make the *next* callback read this as an idle
                # gap and hand it a whole fresh budget.
                time.sleep(1.2)
            return []

        mock_notify_event.side_effect = _slow

        with self.captureOnCommitCallbacks(execute=True):
            self.event.title = "Budget Event One Updated"
            self.event.save()
            event2.title = "Budget Event Two Updated"
            event2.save()

        self.assertEqual(len(deadlines), 2)
        self.assertEqual(deadlines[0], deadlines[1])

    def test_management_command_errors_on_missing_event(self):
        """A typo or deleted event must not exit 0."""
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("ping_google_indexing", "--event", "99999999", "--dry-run")

    @patch("crush_lu.management.commands.ping_google_indexing.notify_event_indexing")
    def test_management_command_all_includes_cancelled_public_events(
        self, mock_notify_event
    ):
        """Cancelled pages are indexable now, so a backfill has to reach them."""
        cancelled = MeetupEvent.objects.create(
            title="Cancelled But Public",
            description="Still returns 200 with EventCancelled.",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=4),
            location="Luxembourg City",
            registration_deadline=timezone.now() + timedelta(days=2),
            is_published=True,
            is_cancelled=True,
        )
        mock_notify_event.return_value = [{"ok": 1}, {"ok": 2}, {"ok": 3}]

        call_command("ping_google_indexing", "--all")

        called = [call.args[0] for call in mock_notify_event.call_args_list]
        self.assertIn(cancelled, called)

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
