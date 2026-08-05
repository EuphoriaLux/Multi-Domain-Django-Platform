"""
Status badges and ticket CTAs on the dashboard's "Your Events" list.

Two defects these lock down (see crush_lu/templates/crush_lu/dashboard.html):

  1. ``pending`` had no badge branch at all, so an UNPAID seat rendered with no
     status while still offering "View Ticket" — visually identical to a paid
     one. The ticket itself is correct to keep (the seat is held and the door
     scans it; payment is often settled out of band, same rule as my_events) —
     what was missing is the member being told money is owed.

  2. The ticket CTA tested status but never the date. A no-show keeps
     ``confirmed`` forever, so an event months past kept rendering "Confirmed"
     plus a live ticket button. A seat the admin explicitly flipped to
     ``no_show`` reads "Missed" alongside it, the same word my_events uses.
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.tests.test_profile_edit_connect_card import _make_member


def _badge(label):
    """The rendered status pill, not a bare word.

    "Confirmed" and "Waitlist" also occur elsewhere on the dashboard, so a
    plain substring assertion passes for the wrong reason.
    """
    return f">{label}</span>"


def _make_event(title, *, days_from_now):
    when = timezone.now() + timedelta(days=days_from_now)
    return MeetupEvent.objects.create(
        title=title,
        description="x",
        event_type="mixer",
        date_time=when,
        location="Luxembourg",
        address="1 Test St",
        max_participants=20,
        duration_minutes=120,
        registration_deadline=when - timedelta(days=2),
        is_published=True,
    )


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DashboardEventStatusTests(TestCase):
    def setUp(self):
        self.user = _make_member("events@example.com")
        self.client.login(username="events@example.com", password="testpass123")

    def _get(self):
        return self.client.get(reverse("crush_lu:dashboard"), HTTP_HOST="crush.lu")

    def _register(self, event, status):
        return EventRegistration.objects.create(
            user=self.user, event=event, status=status
        )

    # -- is_past ----------------------------------------------------------

    def test_is_past_is_set_from_end_time_not_status(self):
        """A no-show keeps `confirmed`; only the clock can mark it past."""
        self._register(_make_event("Gone", days_from_now=-30), "confirmed")
        self._register(_make_event("Soon", days_from_now=5), "confirmed")

        by_title = {r.event.title: r for r in self._get().context["registrations"]}
        self.assertTrue(by_title["Gone"].is_past)
        self.assertFalse(by_title["Soon"].is_past)

    def test_event_ending_later_today_is_not_past(self):
        """The cutoff is end_time, not date_time — a live event is not past."""
        event = _make_event("Running now", days_from_now=0)
        event.date_time = timezone.now() - timedelta(minutes=30)
        event.duration_minutes = 180
        event.save(update_fields=["date_time", "duration_minutes"])
        self._register(event, "confirmed")

        registration = self._get().context["registrations"][0]
        self.assertFalse(registration.is_past)

    # -- unpaid seat ------------------------------------------------------

    def test_pending_seat_shows_payment_due_and_keeps_ticket(self):
        event = _make_event("Unpaid", days_from_now=7)
        self._register(event, "pending")

        response = self._get()
        self.assertContains(response, _badge("Payment due"))
        # The held seat stays scannable at the door.
        self.assertContains(response, reverse("crush_lu:event_ticket", args=[event.id]))

    # -- past events ------------------------------------------------------

    def test_past_confirmed_reads_missed_and_drops_the_ticket(self):
        event = _make_event("No-show", days_from_now=-60)
        self._register(event, "confirmed")

        response = self._get()
        self.assertContains(response, _badge("Missed"))
        self.assertNotContains(response, _badge("Confirmed"))
        self.assertNotContains(
            response, reverse("crush_lu:event_ticket", args=[event.id])
        )

    def test_past_no_show_reads_missed(self):
        """`no_show` is the same fact as an unscanned `confirmed`, said aloud.

        The admin action mark_unattended_as_no_show (crush_lu/admin/quiz.py)
        flips still-`confirmed` and `pending` seats to `no_show` once the host
        confirms nobody arrived. Falling through to "Past" here would tell the
        member less than the untouched row next to it, and my_events already
        calls this exact status "Missed" (views_events.py, entry.no_show).
        """
        event = _make_event("Marked absent", days_from_now=-60)
        self._register(event, "no_show")

        response = self._get()
        self.assertContains(response, _badge("Missed"))
        self.assertNotContains(response, _badge("Past"))
        self.assertNotContains(
            response, reverse("crush_lu:event_ticket", args=[event.id])
        )

    def test_past_pending_reads_past_and_drops_the_ticket(self):
        event = _make_event("Never paid", days_from_now=-45)
        self._register(event, "pending")

        response = self._get()
        self.assertContains(response, _badge("Past"))
        self.assertNotContains(response, _badge("Payment due"))
        self.assertNotContains(
            response, reverse("crush_lu:event_ticket", args=[event.id])
        )

    def test_past_waitlist_reads_past(self):
        """A waitlist spot that never came through stops looking live."""
        self._register(_make_event("Never got in", days_from_now=-20), "waitlist")

        response = self._get()
        self.assertContains(response, _badge("Past"))
        self.assertNotContains(response, _badge("Waitlist"))

    # -- statuses that already carried their own meaning ------------------

    def test_attended_and_cancelled_are_unchanged_by_the_past_branch(self):
        self._register(_make_event("Went", days_from_now=-10), "attended")
        self._register(_make_event("Dropped", days_from_now=-10), "cancelled")

        response = self._get()
        # The "Attended" pill wraps a checkmark SVG, so its label is not
        # flush against the closing tag the way the other badges are.
        self.assertContains(response, "Attended")
        self.assertContains(response, _badge("Cancelled"))
        self.assertNotContains(response, _badge("Missed"))

    def test_upcoming_confirmed_still_shows_ticket(self):
        event = _make_event("Next week", days_from_now=7)
        self._register(event, "confirmed")

        response = self._get()
        self.assertContains(response, _badge("Confirmed"))
        self.assertContains(response, reverse("crush_lu:event_ticket", args=[event.id]))

    # -- template hygiene -------------------------------------------------

    def test_no_template_comment_leaks_into_the_page(self):
        """Django's {# #} is single-line only.

        A {# #} spanning two lines is not a comment — it is literal text, and
        it renders to the member. Nothing in the events list should ever emit
        a brace-hash or an {% endcomment %} into the response.
        """
        self._register(_make_event("Upcoming", days_from_now=5), "pending")
        self._register(_make_event("Old", days_from_now=-30), "confirmed")

        html = self._get().content.decode()
        for leak in ("{#", "#}", "{% comment %}", "{% endcomment %}"):
            self.assertNotIn(leak, html)
