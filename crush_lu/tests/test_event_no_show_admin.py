"""
The MeetupEvent admin action that records no-shows for every event type.

Only the QuizEvent admin could record no-shows, so mixers and speed dating
kept theirs as seat holders: event 27, a free mixer, still had 14
never-checked-in rows ``confirmed`` a day after it ended (prod, 2026-10-11).
The new action shares ``services.no_shows`` with the quiz action; these tests
pin what it changes, what it leaves alone and which events it refuses.

Requests use literal paths on the crush.lu host, so its middleware picks the
urlconf as in production (AGENTS.md: `reverse()` resolves against the default
urlconf, not the host's).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.sites.models import Site
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent

User = get_user_model()

HOST = "crush.lu"
EVENTS = "/crush-admin/crush_lu/meetupevent/"
ACTION = "mark_unattended_as_no_show"


def make_event(title, start, **fields):
    fields = {
        "description": "No-show admin fixture event",
        "event_type": "mixer",
        "duration_minutes": 120,
        "location": "Luxembourg",
        "address": "1 Test Street",
        "max_participants": 20,
        "registration_deadline": start - timedelta(hours=1),
        "is_published": True,
        **fields,
    }
    return MeetupEvent.objects.create(title=title, date_time=start, **fields)


class MeetupEventNoShowActionTests(TestCase):
    def setUp(self):
        # Recycled SQLite primary keys share one rate-limit counter.
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.superuser = User.objects.create_superuser(
            username="no_show_admin",
            email="no-show-admin@example.com",
            password="pw12345678",
        )
        self.client = Client(HTTP_HOST=HOST)
        self.client.force_login(self.superuser)
        self.now = timezone.now()
        self._users = 0

    def _register(self, event, status, **fields):
        self._users += 1
        user = User.objects.create_user(
            username=f"member{self._users}",
            email=f"member{self._users}@example.com",
            password="pw12345678",
        )
        return EventRegistration.objects.create(
            event=event, user=user, status=status, **fields
        )

    def _attended(self, event):
        return self._register(
            event, "attended", checked_in_at=event.date_time + timedelta(minutes=5)
        )

    def _run(self, *events):
        response = self.client.post(
            EVENTS,
            {"action": ACTION, "_selected_action": [event.pk for event in events]},
        )
        self.assertEqual(response.status_code, 302)
        return [str(message) for message in get_messages(response.wsgi_request)]

    def _status(self, registration):
        registration.refresh_from_db()
        return registration.status

    def test_ended_mixer_marks_unchecked_in_seat_holders_as_no_show(self):
        event = make_event("Ended mixer", self.now - timedelta(days=1))
        attended = self._attended(event)
        confirmed = self._register(event, "confirmed")
        # A cash-at-the-door seat that never arrived is a no-show too.
        pending = self._register(event, "pending")
        cancelled = self._register(event, "cancelled")
        waitlist = self._register(event, "waitlist")
        applied = self._register(event, "applied")
        # Scanned at the door, then edited back to "confirmed": they were there.
        scanned = self._register(
            event, "confirmed", checked_in_at=event.date_time + timedelta(minutes=9)
        )

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "no_show")
        self.assertEqual(self._status(pending), "no_show")
        self.assertEqual(self._status(attended), "attended")
        self.assertEqual(self._status(cancelled), "cancelled")
        self.assertEqual(self._status(waitlist), "waitlist")
        self.assertEqual(self._status(applied), "applied")
        self.assertEqual(self._status(scanned), "confirmed")
        self.assertIn(f"'{event}': marked 2 registration(s) as No Show.", messages)

    def test_no_show_does_not_promote_the_waitlist(self):
        # Full event: marking the no-show frees a seat on paper, and a
        # cancellation of the same seat would hand it to the waitlist.
        event = make_event(
            "Full mixer", self.now - timedelta(days=1), max_participants=2
        )
        self._attended(event)
        no_show = self._register(event, "confirmed")
        waiting = self._register(event, "waitlist")

        # Promotion runs on commit, which a TestCase never reaches on its own.
        with self.captureOnCommitCallbacks(execute=True):
            self._run(event)

        self.assertEqual(self._status(no_show), "no_show")
        self.assertEqual(self._status(waiting), "waitlist")
        self.assertFalse(
            EventRegistration.objects.filter(
                event=event, waitlist_promoted_at__isnull=False
            ).exists()
        )
        self.assertEqual(mail.outbox, [])

    def test_future_event_is_refused(self):
        event = make_event("Next mixer", self.now + timedelta(days=3))
        confirmed = self._register(event, "confirmed")

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "confirmed")
        self.assertIn(f"'{event}' skipped: it has not ended yet.", messages)

    def test_live_event_is_refused(self):
        # Started, not ended: late arrivals can still check in.
        event = make_event("Live mixer", self.now - timedelta(minutes=30))
        self._attended(event)
        confirmed = self._register(event, "confirmed")

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "confirmed")
        self.assertIn(f"'{event}' skipped: it has not ended yet.", messages)

    def test_cancelled_event_is_refused(self):
        # Cancelling leaves registrations "confirmed"; nobody missed anything.
        event = make_event(
            "Cancelled mixer", self.now - timedelta(days=1), is_cancelled=True
        )
        confirmed = self._register(event, "confirmed")

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "confirmed")
        self.assertIn(
            f"'{event}' skipped: it was cancelled, so nobody missed it.",
            messages,
        )

    def test_event_without_any_check_in_is_refused(self):
        # The scanner was never used, so the whole roster would read absent.
        event = make_event("Unscanned mixer", self.now - timedelta(days=1))
        confirmed = self._register(event, "confirmed")

        messages = self._run(event)

        self.assertEqual(self._status(confirmed), "confirmed")
        self.assertIn(
            f"'{event}' skipped: nobody was checked in, so attendance "
            "was never recorded.",
            messages,
        )

    def test_mixed_selection_marks_only_the_eligible_event(self):
        ended = make_event(
            "Ended speed dating",
            self.now - timedelta(days=2),
            event_type="speed_dating",
        )
        self._attended(ended)
        missed = self._register(ended, "confirmed")
        upcoming = make_event("Upcoming mixer", self.now + timedelta(days=1))
        booked = self._register(upcoming, "confirmed")

        self._run(ended, upcoming)

        self.assertEqual(self._status(missed), "no_show")
        self.assertEqual(self._status(booked), "confirmed")
