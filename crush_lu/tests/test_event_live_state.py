"""
Tests for the "live" event state (events happening right now).

Covers:
- MeetupEvent.is_live boundary behaviour (start/end edges).
- The public home() view surfacing in-progress events in `upcoming_events`.

Run with: python manage.py test crush_lu.tests.test_event_live_state
"""
from datetime import datetime, timedelta, timezone as py_timezone
from unittest import mock

from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import MeetupEvent


class EventIsLiveTests(TestCase):
    """Boundary behaviour of MeetupEvent.is_live."""

    def setUp(self):
        # Fixed, timezone-aware start so boundaries are deterministic.
        self.start = datetime(2030, 6, 1, 20, 0, 0, tzinfo=py_timezone.utc)
        self.event = MeetupEvent.objects.create(
            title="Live State Event",
            description="A test event",
            event_type="mixer",
            date_time=self.start,
            location="Luxembourg City",
            address="123 Test Street",
            max_participants=20,
            duration_minutes=60,  # ends at self.start + 60 min
            registration_deadline=self.start - timedelta(hours=1),
            is_published=True,
        )

    def _at(self, moment):
        """Patch the `now` used inside the model to a fixed instant."""
        return mock.patch(
            "crush_lu.models.events.timezone.now", return_value=moment
        )

    def test_before_start_is_not_live(self):
        with self._at(self.start - timedelta(seconds=1)):
            self.assertFalse(self.event.is_live)

    def test_exact_start_is_live(self):
        with self._at(self.start):
            self.assertTrue(self.event.is_live)

    def test_mid_event_is_live(self):
        with self._at(self.start + timedelta(minutes=30)):
            self.assertTrue(self.event.is_live)

    def test_exact_end_is_not_live(self):
        # end_time is exclusive: at exactly end_time the event is over.
        with self._at(self.start + timedelta(minutes=60)):
            self.assertFalse(self.event.is_live)

    def test_after_end_is_not_live(self):
        with self._at(self.start + timedelta(minutes=61)):
            self.assertFalse(self.event.is_live)

    def test_cancelled_event_is_not_live(self):
        # A cancelled event is never "live", even mid-window: its published
        # detail page stays reachable, so the banner/badge must stay hidden.
        self.event.is_cancelled = True
        self.event.save(update_fields=["is_cancelled"])
        with self._at(self.start + timedelta(minutes=30)):
            self.assertFalse(self.event.is_live)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class HomeLiveEventTests(TestCase):
    """The public landing page must include in-progress ("live") events."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

    def setUp(self):
        self.client = Client()

    def _make_event(self, *, date_time, **overrides):
        params = dict(
            title="Home Live Event",
            description="A test event",
            event_type="mixer",
            date_time=date_time,
            location="Luxembourg City",
            address="123 Test Street",
            max_participants=20,
            duration_minutes=60,
            registration_deadline=date_time - timedelta(hours=1),
            is_published=True,
            is_cancelled=False,
        )
        params.update(overrides)
        return MeetupEvent.objects.create(**params)

    def test_live_event_appears_in_upcoming(self):
        now = timezone.now()
        # Started 10 min ago, 60 min long -> still in progress.
        live = self._make_event(date_time=now - timedelta(minutes=10))

        response = self.client.get(reverse("crush_lu:home"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(live, list(response.context["upcoming_events"]))

    def test_long_running_live_event_appears(self):
        now = timezone.now()
        # Started 30h ago but runs for 48h -> still live, yet beyond a fixed
        # 24h cutoff. The duration-derived cutoff must still surface it.
        live = self._make_event(
            date_time=now - timedelta(hours=30),
            duration_minutes=48 * 60,
        )

        response = self.client.get(reverse("crush_lu:home"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(live, list(response.context["upcoming_events"]))

    def test_ended_event_excluded_from_upcoming(self):
        now = timezone.now()
        # Started 3h ago, 60 min long -> ended 2h ago. Within the 24h ORM
        # cutoff, so it is fetched but must be dropped by the end_time filter.
        ended = self._make_event(date_time=now - timedelta(hours=3))

        response = self.client.get(reverse("crush_lu:home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(ended, list(response.context["upcoming_events"]))


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class EventDetailLiveBannerTests(TestCase):
    """The event detail page renders the "happening now" banner while live."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

    def setUp(self):
        self.client = Client()

    def test_live_event_shows_banner(self):
        now = timezone.now()
        event = MeetupEvent.objects.create(
            title="Detail Live Event",
            description="A test event",
            event_type="mixer",
            date_time=now - timedelta(minutes=10),  # in progress
            location="Luxembourg City",
            address="123 Test Street",
            max_participants=20,
            duration_minutes=60,
            registration_deadline=now - timedelta(hours=1),
            is_published=True,
            is_cancelled=False,
        )

        url = reverse("crush_lu:event_detail", kwargs={"event_id": event.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This event is happening now")
