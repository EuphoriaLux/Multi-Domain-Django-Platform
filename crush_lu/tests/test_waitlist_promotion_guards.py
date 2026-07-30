"""Waitlist promotion guards.

Two defects found while reviewing the 2026-07-29 event:

1. ``promote_waitlist_on_capacity_increase`` fires on *any* ``MeetupEvent``
   save and never checked the date, so opening a finished event in the admin
   and pressing Save would confirm its leftover waitlist and email each of
   them a registration confirmation for a party that already happened.
   Marking attendees ``no_show`` makes this *more* likely, because no_show
   rows leave ``get_confirmed_count()`` and the event reads as having room.

2. Only the member-facing cancel view promoted from the waitlist. A status
   flipped to ``cancelled`` anywhere else — admin, shell, coach — freed a seat
   nobody was offered.

Run with: pytest crush_lu/tests/test_waitlist_promotion_guards.py -v
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

User = get_user_model()


class _WaitlistFixture(TestCase):
    """One event with a confirmed seat-holder and one waitlisted candidate."""

    def _make_event(self, **overrides):
        from crush_lu.models import MeetupEvent

        kwargs = dict(
            title="Guard Test",
            description="Testing waitlist promotion guards",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="123 Test Street",
            max_participants=1,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
        )
        kwargs.update(overrides)
        return MeetupEvent.objects.create(**kwargs)

    def _user(self, username, gender="F"):
        from crush_lu.models import CrushProfile

        user = User.objects.create_user(
            username=username, email=username, password="testpass123"
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender=gender,
            location="Luxembourg",
        )
        return user

    def _register(self, event, user, status):
        from crush_lu.models import EventRegistration

        return EventRegistration.objects.create(event=event, user=user, status=status)

    def _save_running_commit_hooks(self, obj, **attrs):
        """Save ``obj`` and actually run its ``on_commit`` callbacks.

        The cancellation promotion is deferred to ``on_commit`` on purpose — it
        must count seats against a *durable* cancellation, and the confirmation
        email must not go out if the transaction rolls back. Django's
        ``TestCase`` wraps every test in a transaction that never commits, so
        without ``captureOnCommitCallbacks`` the promotion never runs and the
        "must not promote" assertions below would all pass vacuously.
        """
        for key, value in attrs.items():
            setattr(obj, key, value)
        with self.captureOnCommitCallbacks(execute=True):
            obj.save()


class AcceptsWaitlistPromotionTests(_WaitlistFixture):
    """The single definition the two signals and the cancel view all share."""

    def test_future_published_event_accepts(self):
        self.assertTrue(self._make_event().accepts_waitlist_promotion)

    def test_past_event_rejects(self):
        event = self._make_event(date_time=timezone.now() - timedelta(hours=1))
        self.assertFalse(event.accepts_waitlist_promotion)

    def test_cancelled_event_rejects(self):
        event = self._make_event(is_cancelled=True)
        self.assertFalse(event.accepts_waitlist_promotion)

    def test_event_starting_right_now_rejects(self):
        """Boundary: promotion stops the moment the event starts, matching the
        'already started' branch in event_cancel."""
        event = self._make_event(date_time=timezone.now())
        self.assertFalse(event.accepts_waitlist_promotion)


class CapacityIncreaseGuardTests(_WaitlistFixture):
    def setUp(self):
        self.event = self._make_event()
        self.holder = self._register(
            self.event, self._user("a@example.com"), "confirmed"
        )
        self.waiting = self._register(
            self.event, self._user("b@example.com"), "waitlist"
        )

    def _save_event(self):
        self.event.save()
        self.waiting.refresh_from_db()

    def test_capacity_increase_still_promotes_for_a_future_event(self):
        """The feature itself must keep working — this is the control."""
        self.event.max_participants = 5
        self._save_event()
        self.assertEqual(self.waiting.status, "confirmed")

    def test_saving_a_past_event_does_not_promote(self):
        self.event.date_time = timezone.now() - timedelta(days=1)
        self.event.max_participants = 5
        self._save_event()
        self.assertEqual(self.waiting.status, "waitlist")

    def test_saving_a_cancelled_event_does_not_promote(self):
        self.event.is_cancelled = True
        self.event.max_participants = 5
        self._save_event()
        self.assertEqual(self.waiting.status, "waitlist")

    def test_past_event_with_free_seats_does_not_promote(self):
        """The exact production shape: an event that has ended, whose seats
        freed up because attendees were marked no_show afterwards."""
        from crush_lu.models import EventRegistration

        self.event.date_time = timezone.now() - timedelta(days=1)
        self.event.save()
        EventRegistration.objects.filter(pk=self.holder.pk).update(status="no_show")
        self.assertFalse(self.event.is_full)  # a seat really is free

        self._save_event()
        self.assertEqual(self.waiting.status, "waitlist")


class CancellationPromotionTests(_WaitlistFixture):
    def setUp(self):
        self.event = self._make_event()
        self.holder = self._register(
            self.event, self._user("a@example.com"), "confirmed"
        )
        self.waiting = self._register(
            self.event, self._user("b@example.com"), "waitlist"
        )

    def test_admin_side_cancellation_promotes_one(self):
        self._save_running_commit_hooks(self.holder, status="cancelled")
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")

    def test_promotes_exactly_one_per_cancellation(self):
        second = self._register(self.event, self._user("c@example.com"), "waitlist")
        # Widen capacity without saving the event — that would promote via the
        # capacity signal and confuse what this test is measuring.
        from crush_lu.models import MeetupEvent

        MeetupEvent.objects.filter(pk=self.event.pk).update(max_participants=5)

        self._save_running_commit_hooks(self.holder, status="cancelled")

        self.waiting.refresh_from_db()
        second.refresh_from_db()
        promoted = [r for r in (self.waiting, second) if r.status == "confirmed"]
        self.assertEqual(len(promoted), 1, "one cancellation frees exactly one seat")

    def test_resaving_an_already_cancelled_row_does_not_promote_again(self):
        self._save_running_commit_hooks(self.holder, status="cancelled")
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "confirmed")

        second = self._register(self.event, self._user("c@example.com"), "waitlist")
        from crush_lu.models import MeetupEvent

        MeetupEvent.objects.filter(pk=self.event.pk).update(max_participants=5)

        self._save_running_commit_hooks(self.holder, special_requests="edited later")

        second.refresh_from_db()
        self.assertEqual(
            second.status,
            "waitlist",
            "a later save of an already-cancelled row must not free another seat",
        )

    def test_flag_suppresses_promotion_for_the_member_cancel_path(self):
        """views_events promotes explicitly; the signal must stand down or the
        seat is handed out twice."""
        self._save_running_commit_hooks(
            self.holder, status="cancelled", _waitlist_promotion_handled=True
        )
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "waitlist")

    def test_past_event_cancellation_does_not_promote(self):
        from crush_lu.models import MeetupEvent

        MeetupEvent.objects.filter(pk=self.event.pk).update(
            date_time=timezone.now() - timedelta(days=1)
        )
        self.holder.refresh_from_db()
        self._save_running_commit_hooks(self.holder, status="cancelled")
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "waitlist")

    def test_bulk_update_does_not_promote(self):
        """QuerySet.update() bypasses signals — the documented behaviour that
        keeps post-event no-show tidying from resurrecting the waitlist."""
        from crush_lu.models import EventRegistration

        with self.captureOnCommitCallbacks(execute=True):
            EventRegistration.objects.filter(pk=self.holder.pk).update(
                status="cancelled"
            )
        self.waiting.refresh_from_db()
        self.assertEqual(self.waiting.status, "waitlist")


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class MemberCancelPathTests(_WaitlistFixture):
    """End-to-end through the view, to prove the flag prevents a double promote
    rather than disabling promotion altogether."""

    def setUp(self):
        from crush_lu.models import UserDataConsent

        self.event = self._make_event(max_participants=2)
        self.member = self._user("member@example.com")
        self.holder = self._register(self.event, self.member, "confirmed")
        self.first = self._register(
            self.event, self._user("w1@example.com"), "waitlist"
        )
        self.second = self._register(
            self.event, self._user("w2@example.com"), "waitlist"
        )
        # ConsentMiddleware 302s every urls_crush view without this.
        UserDataConsent.objects.filter(user=self.member).update(
            crushlu_consent_given=True
        )

    def test_member_cancellation_promotes_exactly_one(self):
        from django.test import Client
        from django.urls import reverse

        client = Client(HTTP_HOST="crush.lu")
        client.force_login(self.member)
        url = reverse("crush_lu:event_cancel", kwargs={"event_id": self.event.id})
        with self.captureOnCommitCallbacks(execute=True):
            resp = client.post(url, follow=True)

        self.holder.refresh_from_db()
        self.assertEqual(
            self.holder.status,
            "cancelled",
            f"POST {url} -> {resp.status_code}, chain={resp.redirect_chain}",
        )

        self.first.refresh_from_db()
        self.second.refresh_from_db()
        promoted = [r for r in (self.first, self.second) if r.status == "confirmed"]
        self.assertEqual(
            len(promoted), 1, "the view and the signal must not both promote"
        )
