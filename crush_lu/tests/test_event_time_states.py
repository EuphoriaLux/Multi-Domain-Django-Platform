"""Regression tests for upcoming/live/past event state handling."""

from datetime import date, timedelta
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
)
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()


def _grant_crush_access(user):
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"verified": True, "primary": True},
    )


def _make_event(title, start, *, duration_minutes=120):
    return MeetupEvent.objects.create(
        title=title,
        description="Time-state regression event",
        event_type="mixer",
        date_time=start,
        duration_minutes=duration_minutes,
        location="Luxembourg",
        address="1 Test Street",
        max_participants=1,
        registration_deadline=start - timedelta(hours=1),
        is_published=True,
    )


class MeetupEventLiveStateTests(TestCase):
    def test_is_live_boundaries(self):
        now = timezone.now()

        with patch("crush_lu.models.events.timezone.now", return_value=now):
            before = MeetupEvent(
                date_time=now + timedelta(seconds=1), duration_minutes=120
            )
            at_start = MeetupEvent(date_time=now, duration_minutes=120)
            mid_event = MeetupEvent(
                date_time=now - timedelta(minutes=30), duration_minutes=120
            )
            at_end = MeetupEvent(
                date_time=now - timedelta(minutes=120), duration_minutes=120
            )
            after = MeetupEvent(
                date_time=now - timedelta(minutes=121), duration_minutes=120
            )

            self.assertFalse(before.is_live)
            self.assertTrue(at_start.is_live)
            self.assertTrue(mid_event.is_live)
            self.assertFalse(at_end.is_live)
            self.assertFalse(after.is_live)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class EventCancellationTimeGateTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="cancel-time@example.com",
            email="cancel-time@example.com",
            password="testpass123",
        )
        self.waitlisted_user = User.objects.create_user(
            username="wait-time@example.com",
            email="wait-time@example.com",
            password="testpass123",
        )
        for user, gender in ((self.user, "M"), (self.waitlisted_user, "F")):
            CrushProfile.objects.create(
                user=user,
                date_of_birth=date(1995, 1, 1),
                gender=gender,
                location="Luxembourg",
                is_approved=True,
            )
            _grant_crush_access(user)
        self.client.force_login(self.user)

    def _registration_pair(self, event, *, status="confirmed"):
        registration = EventRegistration.objects.create(
            event=event, user=self.user, status=status
        )
        waitlisted = EventRegistration.objects.create(
            event=event, user=self.waitlisted_user, status="waitlist"
        )
        return registration, waitlisted

    def _cancel(self, event):
        return self.client.post(
            reverse("crush_lu:event_cancel", args=[event.id]),
            HTTP_HOST="crush.lu",
        )

    @patch("crush_lu.views_events.send_event_registration_confirmation")
    @patch("crush_lu.views_events.send_event_cancellation_confirmation")
    @patch("crush_lu.views_events._promote_from_waitlist")
    def test_attended_registration_is_refused_without_promotion_or_email(
        self, promote, cancellation_email, promotion_email
    ):
        event = _make_event("Attended event", timezone.now() + timedelta(days=1))
        registration, _waitlisted = self._registration_pair(event, status="attended")

        response = self._cancel(event)

        self.assertRedirects(
            response,
            reverse("crush_lu:event_detail", args=[event.id]),
            fetch_redirect_response=False,
        )
        registration.refresh_from_db()
        self.assertEqual(registration.status, "attended")
        promote.assert_not_called()
        cancellation_email.assert_not_called()
        promotion_email.assert_not_called()

    @patch("crush_lu.views_events.send_event_registration_confirmation")
    @patch("crush_lu.views_events.send_event_cancellation_confirmation")
    @patch("crush_lu.views_events._promote_from_waitlist")
    def test_live_registration_is_refused_without_promotion_or_email(
        self, promote, cancellation_email, promotion_email
    ):
        event = _make_event("Live event", timezone.now() - timedelta(hours=1))
        registration, waitlisted = self._registration_pair(event)

        response = self._cancel(event)

        self.assertRedirects(
            response,
            reverse("crush_lu:event_detail", args=[event.id]),
            fetch_redirect_response=False,
        )
        registration.refresh_from_db()
        waitlisted.refresh_from_db()
        self.assertEqual(registration.status, "confirmed")
        self.assertEqual(waitlisted.status, "waitlist")
        promote.assert_not_called()
        cancellation_email.assert_not_called()
        promotion_email.assert_not_called()

    @patch("crush_lu.views_events.send_event_registration_confirmation")
    @patch("crush_lu.views_events.send_event_cancellation_confirmation")
    def test_upcoming_cancellation_promotes_waitlist(
        self, cancellation_email, promotion_email
    ):
        event = _make_event("Upcoming event", timezone.now() + timedelta(days=1))
        registration, waitlisted = self._registration_pair(event)

        response = self._cancel(event)

        self.assertRedirects(
            response,
            reverse("crush_lu:dashboard"),
            fetch_redirect_response=False,
        )
        registration.refresh_from_db()
        waitlisted.refresh_from_db()
        self.assertEqual(registration.status, "cancelled")
        self.assertEqual(waitlisted.status, "confirmed")
        cancellation_email.assert_called_once()
        promotion_email.assert_called_once()

    def test_pending_registration_can_cancel_from_my_events(self):
        event = _make_event("Pending event", timezone.now() + timedelta(days=1))
        registration = EventRegistration.objects.create(
            event=event,
            user=self.user,
            status="pending",
        )

        response = self.client.get(
            reverse("crush_lu:my_events"), HTTP_HOST="crush.lu"
        )

        self.assertEqual(response.status_code, 200)
        entry = next(
            item
            for item in response.context["upcoming_registrations"]
            if item["registration"] == registration
        )
        self.assertTrue(entry["can_cancel"])

    @patch("crush_lu.views_events.send_event_registration_confirmation")
    @patch("crush_lu.views_events.send_event_cancellation_confirmation")
    @patch("crush_lu.views_events._promote_from_waitlist")
    def test_past_registration_is_refused_without_promotion_or_email(
        self, promote, cancellation_email, promotion_email
    ):
        event = _make_event("Past event", timezone.now() - timedelta(hours=3))
        registration, waitlisted = self._registration_pair(event)

        response = self._cancel(event)

        self.assertRedirects(
            response,
            reverse("crush_lu:event_detail", args=[event.id]),
            fetch_redirect_response=False,
        )
        registration.refresh_from_db()
        waitlisted.refresh_from_db()
        self.assertEqual(registration.status, "confirmed")
        self.assertEqual(waitlisted.status, "waitlist")
        promote.assert_not_called()
        cancellation_email.assert_not_called()
        promotion_email.assert_not_called()


@override_settings(ROOT_URLCONF="azureproject.urls_crush", SITE_ID=1)
class LiveEventDiscoveryTests(TestCase):
    def setUp(self):
        Site.objects.update_or_create(
            id=1, defaults={"domain": "crush.lu", "name": "Crush.lu"}
        )
        self.user = User.objects.create_user(
            username="live-state@example.com",
            email="live-state@example.com",
            password="testpass123",
            first_name="Live",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="M",
            location="Luxembourg",
            verification_status="pending",
        )
        _grant_crush_access(self.user)
        self.live_event = _make_event(
            "Long Live Discovery Event",
            timezone.now() - timedelta(hours=25),
            duration_minutes=26 * 60,
        )
        EventRegistration.objects.create(
            event=self.live_event, user=self.user, status="confirmed"
        )

    def test_coach_event_list_keeps_live_event_upcoming(self):
        coach_user = User.objects.create_user(
            username="live-coach@example.com",
            email="live-coach@example.com",
            password="testpass123",
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)
        _grant_crush_access(coach_user)
        client = Client()
        client.force_login(coach_user)

        response = client.get(
            reverse("crush_lu:coach_event_list"), HTTP_HOST="crush.lu"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.live_event, response.context["upcoming_events"])
        self.assertNotIn(self.live_event, response.context["past_events"])

    @patch("crush_lu.views_coach._attach_registration_stats")
    def test_coach_event_list_limits_stats_to_ten_past_events(self, attach_stats):
        coach_user = User.objects.create_user(
            username="bounded-coach@example.com",
            email="bounded-coach@example.com",
            password="testpass123",
        )
        CrushCoach.objects.create(user=coach_user, is_active=True)
        _grant_crush_access(coach_user)
        for index in range(12):
            _make_event(
                f"Historical event {index}",
                timezone.now() - timedelta(days=8 + index),
            )
        client = Client()
        client.force_login(coach_user)

        response = client.get(
            reverse("crush_lu:coach_event_list"), HTTP_HOST="crush.lu"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["past_events"]), 10)
        self.assertEqual(len(attach_stats.call_args_list[1].args[0]), 10)

    def test_wallet_pass_returns_live_event(self):
        from crush_lu.wallet_pass import get_next_event_for_pass

        event_info = get_next_event_for_pass(self.profile)

        self.assertIsNotNone(event_info)
        self.assertEqual(event_info["title"], self.live_event.title)

    def test_verification_path_stays_event_during_live_window(self):
        from crush_lu.views import _verification_path_context

        context = _verification_path_context(self.profile, self.user)

        self.assertEqual(context["chosen_path"], "event")

    def test_profile_submitted_teaser_includes_live_event(self):
        client = Client()
        client.force_login(self.user)

        response = client.get(
            reverse("crush_lu:profile_submitted"), HTTP_HOST="crush.lu"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["next_event"], self.live_event)

    def test_sitemap_keeps_future_query_lazy_and_filters_recently_ended(self):
        from django.db.models import QuerySet

        from crush_lu.sitemaps import CrushEventSitemap

        now = timezone.now()
        ended_event = _make_event("Ended sitemap event", now - timedelta(hours=3))
        future_event = _make_event("Future sitemap event", now + timedelta(days=1))

        items = CrushEventSitemap().items()

        self.assertIsInstance(items, QuerySet)
        events = list(items)
        self.assertIn(self.live_event, events)
        self.assertIn(future_event, events)
        self.assertNotIn(ended_event, events)

    def test_event_list_fills_past_after_many_overlapping_live_events(self):
        now = timezone.now()
        for index in range(50):
            _make_event(
                f"Overlapping live event {index}",
                now - timedelta(hours=25),
                duration_minutes=26 * 60,
            )
        older_past = _make_event("Older past event", now - timedelta(days=8))

        response = self.client.get(
            reverse("crush_lu:event_list"), HTTP_HOST="crush.lu"
        )

        self.assertEqual(response.status_code, 200)
        visible_past = [
            event for event, _attended in response.context["past_events_with_attendance"]
        ]
        self.assertIn(older_past, visible_past)


class UpcomingRegistrantSegmentTests(TestCase):
    def test_excludes_recently_ended_event_and_keeps_long_live_event(self):
        from crush_lu.admin.user_segments import get_segment_definitions

        now = timezone.now()
        ended_user = User.objects.create_user(
            username="ended-segment@example.com",
            email="ended-segment@example.com",
            password="testpass123",
        )
        live_user = User.objects.create_user(
            username="live-segment@example.com",
            email="live-segment@example.com",
            password="testpass123",
        )
        ended_profile = CrushProfile.objects.create(
            user=ended_user,
            verification_status="verified",
        )
        live_profile = CrushProfile.objects.create(
            user=live_user,
            verification_status="verified",
        )
        ended_event = _make_event("Ended segment event", now - timedelta(hours=3))
        long_live_event = _make_event(
            "Long live segment event",
            now - timedelta(hours=25),
            duration_minutes=26 * 60,
        )
        EventRegistration.objects.create(
            event=ended_event,
            user=ended_user,
            status="confirmed",
        )
        EventRegistration.objects.create(
            event=long_live_event,
            user=live_user,
            status="confirmed",
        )

        segment = next(
            item
            for item in get_segment_definitions()["event_engagement"]["segments"]
            if item["key"] == "event_upcoming_registrants"
        )

        self.assertNotIn(ended_profile, segment["queryset"])
        self.assertIn(live_profile, segment["queryset"])
