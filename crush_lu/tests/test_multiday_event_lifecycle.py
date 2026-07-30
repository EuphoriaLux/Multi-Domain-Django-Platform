"""Multi-day events must still get their lifecycle emails, and unpublished
events must not get waitlist promotions.

Both found by review on #742:

- `send_event_recaps` / `send_event_feedback_requests` pre-filtered starts to
  `lookback - 1 day`, but `duration_minutes` is capped at 7 days, so an event
  that *ended* inside the window could have *started* long before the
  prefilter and was dropped before the precise `end_time` check ever ran.
- `accepts_waitlist_promotion` checked cancelled but not published, so an
  unpublished event could confirm a waitlisted member and email them for an
  event `event_detail` rejects and `my_events` hides.

Run with: pytest crush_lu/tests/test_multiday_event_lifecycle.py -v
"""

from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

User = get_user_model()


class MultiDayEventLifecycleTests(TestCase):
    """A 4-day event that ended 26h ago: eligible by end_time, but its start is
    5 days back — well outside the old one-day allowance."""

    def setUp(self):
        from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent

        now = timezone.now()
        self.event = MeetupEvent.objects.create(
            title="Four Day Retreat",
            description="Long one",
            event_type="mixer",
            date_time=now - timedelta(days=5, hours=2),
            duration_minutes=4 * 24 * 60,
            location="Luxembourg",
            address="1 Test Street",
            max_participants=20,
            registration_deadline=now - timedelta(days=6),
            is_published=True,
        )
        # Ends 26h ago — inside both the recap (24h..36h) and feedback (48h)
        # windows.
        self.assertLess(self.event.end_time, now - timedelta(hours=24))
        self.assertGreater(self.event.end_time, now - timedelta(hours=36))

        self.user = User.objects.create_user(
            username="m@example.com", email="m@example.com", password="testpass123"
        )
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
        )
        self.reg = EventRegistration.objects.create(
            event=self.event, user=self.user, status="attended"
        )

    def test_recap_reaches_a_multiday_event(self):
        with mock.patch(
            "crush_lu.management.commands.send_event_recaps.send_event_recap",
            return_value=1,
        ) as send:
            call_command("send_event_recaps", "--event-id", str(self.event.id))
        self.assertEqual(send.call_count, 1)

    def test_feedback_reaches_a_multiday_event(self):
        with mock.patch(
            "crush_lu.management.commands.send_event_feedback_requests"
            ".send_event_feedback_request",
            return_value=1,
        ) as send:
            call_command(
                "send_event_feedback_requests", "--event-id", str(self.event.id)
            )
        self.assertEqual(send.call_count, 1)

    def test_lookback_cutoff_stays_bounded_by_the_duration_ceiling(self):
        """The widened scan is bounded, not a full-table sweep: it reaches back
        exactly the maximum permitted duration and no further."""
        from crush_lu.models import MeetupEvent

        now = timezone.now()
        cutoff = MeetupEvent.live_lookback_cutoff(now)
        self.assertEqual(
            cutoff, now - timedelta(minutes=MeetupEvent.MAX_DURATION_MINUTES)
        )
        self.assertLess(
            cutoff,
            now - timedelta(days=6),
            "must reach past the 7-day ceiling so no ended event is dropped",
        )


class UnpublishedEventPromotionTests(TestCase):
    def setUp(self):
        from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent

        self.event = MeetupEvent.objects.create(
            title="Unpublished",
            description="Draft",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="1 Test Street",
            max_participants=1,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=False,
        )

        def member(name):
            u = User.objects.create_user(
                username=name, email=name, password="testpass123"
            )
            CrushProfile.objects.create(
                user=u,
                date_of_birth=date(1995, 1, 1),
                gender="F",
                location="Luxembourg",
            )
            return u

        self.holder = EventRegistration.objects.create(
            event=self.event, user=member("h@example.com"), status="confirmed"
        )
        self.waiting = EventRegistration.objects.create(
            event=self.event, user=member("w@example.com"), status="waitlist"
        )

    def test_unpublished_event_rejects_promotion(self):
        self.assertFalse(self.event.accepts_waitlist_promotion)

    def test_capacity_increase_on_unpublished_event_does_not_promote(self):
        self.event.max_participants = 5
        self.event.save()
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "waitlist")

    def test_cancellation_on_unpublished_event_does_not_promote(self):
        self.holder.status = "cancelled"
        with self.captureOnCommitCallbacks(execute=True):
            self.holder.save()
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "waitlist")

    def test_publishing_it_restores_promotion(self):
        """Control: the guard is about publication state, not a blanket block."""
        from crush_lu.models import MeetupEvent

        MeetupEvent.objects.filter(pk=self.event.pk).update(is_published=True)
        self.event.refresh_from_db()
        self.assertTrue(self.event.accepts_waitlist_promotion)

        self.event.max_participants = 5
        self.event.save()
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")
