"""
The dashboard's event surface: one next-event card, honest counts, and an
action strip — not the member's whole history.

The list this replaced rendered EVERY registration ever, ordered
``-event__date_time``. That put the most DISTANT future event first and mixed
years of past rows in below it at equal visual weight, so on a member with 14
registrations the list was 48% of the page and the event happening in six days
sat more than a screen down.

What is asserted here:

  * only the next live seat is rendered, and "next" means soonest;
  * past, cancelled, and finished-waitlist rows never appear;
  * the counts distinguish upcoming from attended and exclude cancellations;
  * an unpaid seat is surfaced at the top, where it can be acted on;
  * the strip disappears entirely when there is nothing to do.
"""

import re
from unittest import mock
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.services.event_lobby import CTA_PROMO_ONLY, PHASE_LIVE
from crush_lu.tests.test_profile_edit_connect_card import _make_member

# The bottom nav's Events tab, matched by its data-tab rather than by a bare
# URL search: the two count tiles also link to my_events, so a page-wide
# assertContains would pass without the tab ever changing.
_EVENTS_TAB = re.compile(
    r'<a\s+href="([^"]+)"(?:(?!</a>).)*?data-tab="events"', re.DOTALL
)


def _events_tab_href(response):
    match = _EVENTS_TAB.search(response.content.decode())
    assert match, "bottom-nav Events tab not found in the response"
    return match.group(1)


def _live_event(title="Happening now"):
    """An event that has started but not ended."""
    event = _make_event(title, days_from_now=0)
    event.date_time = timezone.now() - timedelta(minutes=30)
    event.duration_minutes = 180
    event.save(update_fields=["date_time", "duration_minutes"])
    return event


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
class DashboardNextEventTests(TestCase):
    def setUp(self):
        self.user = _make_member("events@example.com")
        self.client.login(username="events@example.com", password="testpass123")

    def _get(self):
        return self.client.get(reverse("crush_lu:dashboard"), HTTP_HOST="crush.lu")

    def _register(self, event, status):
        return EventRegistration.objects.create(
            user=self.user, event=event, status=status
        )

    # -- which single event gets the card ---------------------------------

    def test_next_is_the_soonest_upcoming_not_the_furthest(self):
        """The old list was reverse-chronological, so it led with the furthest."""
        self._register(_make_event("Far away", days_from_now=90), "confirmed")
        soon = _make_event("Six days out", days_from_now=6)
        self._register(soon, "confirmed")
        self._register(_make_event("Middle", days_from_now=30), "confirmed")

        response = self._get()
        self.assertEqual(response.context["next_registration"].event, soon)
        self.assertContains(response, "Six days out")
        # Exactly one card, so the other two are not on the page body. Their
        # titles do still appear in the header's upcoming-events dropdown,
        # which is a different surface and legitimately lists all of them.
        self.assertContains(response, "Next event", count=1)

    def test_history_is_not_rendered_on_the_dashboard(self):
        """Past rows belong to my_events, which splits upcoming from past."""
        self._register(
            _make_event("Attended last year", days_from_now=-300), "attended"
        )
        self._register(_make_event("No-show", days_from_now=-60), "confirmed")
        self._register(_make_event("Never got in", days_from_now=-20), "waitlist")
        self._register(_make_event("Coming up", days_from_now=4), "confirmed")

        response = self._get()
        self.assertContains(response, "Coming up")
        self.assertNotContains(response, "Attended last year")
        self.assertNotContains(response, "No-show")
        self.assertNotContains(response, "Never got in")

    def test_cancelled_seat_never_becomes_the_next_event(self):
        self._register(_make_event("Dropped out", days_from_now=3), "cancelled")
        kept = _make_event("Still going", days_from_now=10)
        self._register(kept, "confirmed")

        response = self._get()
        self.assertEqual(response.context["next_registration"].event, kept)
        self.assertNotContains(response, "Dropped out")

    def test_unpublished_or_cancelled_event_is_skipped(self):
        dead = _make_event("Called off", days_from_now=2)
        dead.is_cancelled = True
        dead.save(update_fields=["is_cancelled"])
        self._register(dead, "confirmed")

        hidden = _make_event("Not published", days_from_now=3)
        hidden.is_published = False
        hidden.save(update_fields=["is_published"])
        self._register(hidden, "confirmed")

        live = _make_event("Real one", days_from_now=9)
        self._register(live, "confirmed")

        response = self._get()
        self.assertEqual(response.context["next_registration"].event, live)
        self.assertEqual(response.context["upcoming_count"], 1)

    def test_event_running_right_now_still_counts_as_upcoming(self):
        """The cutoff is end_time — a live event has not passed."""
        event = _live_event()
        self._register(event, "confirmed")

        response = self._get()
        self.assertEqual(response.context["next_registration"].event, event)

    def test_started_event_offers_no_cancel_button(self):
        """event_cancel() rejects a started event, so the button cannot work.

        A seat is kept until end_time, so the next event can already be under
        way — status alone would still say "confirmed".
        """
        event = _live_event()
        self._register(event, "confirmed")

        response = self._get()
        self.assertFalse(response.context["next_registration"].can_cancel)
        self.assertNotContains(
            response, reverse("crush_lu:event_cancel", args=[event.id])
        )

    def test_unstarted_event_still_offers_cancel(self):
        event = _make_event("Next week", days_from_now=7)
        self._register(event, "confirmed")

        response = self._get()
        self.assertTrue(response.context["next_registration"].can_cancel)
        self.assertContains(response, reverse("crush_lu:event_cancel", args=[event.id]))

    def test_checked_in_member_sees_live_state_not_a_blank_card(self):
        """Checking in early flips the seat to "attended" while the event runs.

        That row is still the next event, but it carried no status pill, a
        "in 0 minutes" countdown, and a ticket for an event already joined.
        """
        event = _live_event()
        self._register(event, "attended")

        response = self._get()
        self.assertEqual(response.context["next_registration"].event, event)
        self.assertContains(response, "Live now")
        self.assertNotContains(response, "in 0&#160;minutes")
        self.assertNotContains(
            response, reverse("crush_lu:event_ticket", args=[event.id])
        )

    # -- counts -----------------------------------------------------------

    def test_counts_separate_upcoming_from_attended_and_drop_cancellations(self):
        for i in range(2):
            self._register(_make_event(f"Soon {i}", days_from_now=5 + i), "confirmed")
        for i in range(3):
            self._register(_make_event(f"Went {i}", days_from_now=-10 - i), "attended")
        self._register(_make_event("Scrapped", days_from_now=8), "cancelled")
        self._register(_make_event("Bailed", days_from_now=-40), "cancelled")

        context = self._get().context
        self.assertEqual(context["upcoming_count"], 2)
        self.assertEqual(context["attended_count"], 3)
        # The old single tile showed 7 here — every row including cancellations.

    def test_has_attended_event_still_tracks_attendance(self):
        self._register(_make_event("Only upcoming", days_from_now=5), "confirmed")
        self.assertFalse(self._get().context["has_attended_event"])

        self._register(_make_event("Went once", days_from_now=-5), "attended")
        self.assertTrue(self._get().context["has_attended_event"])

    # -- the action strip -------------------------------------------------

    def test_unpaid_seat_is_surfaced_with_a_way_to_pay(self):
        event = _make_event("Unpaid", days_from_now=7)
        self._register(event, "pending")

        response = self._get()
        self.assertEqual(
            [r.event for r in response.context["pending_payment_registrations"]],
            [event],
        )
        self.assertContains(response, "Payment due")
        self.assertContains(response, "Pay now")
        self.assertContains(response, reverse("crush_lu:my_events"))

    def test_a_past_unpaid_seat_is_not_chased(self):
        """Nothing to settle once the event is over — it just clutters."""
        self._register(_make_event("Long gone", days_from_now=-90), "pending")

        response = self._get()
        self.assertEqual(list(response.context["pending_payment_registrations"]), [])
        self.assertNotContains(response, "Pay now")

    def test_strip_is_absent_when_there_is_nothing_to_act_on(self):
        self._register(_make_event("All settled", days_from_now=6), "confirmed")

        response = self._get()
        self.assertEqual(list(response.context["pending_payment_registrations"]), [])
        self.assertEqual(list(response.context["post_event_actions"]), [])
        self.assertNotContains(response, "Pay now")

    def test_recent_attendance_keeps_its_my_crush_path(self):
        """The old list carried this on every attended row; the strip carries
        it only while the connection window is still open."""
        event = _make_event("Just happened", days_from_now=-1)
        self._register(event, "attended")

        response = self._get()
        self.assertEqual(
            [a["event"] for a in response.context["post_event_actions"]], [event]
        )
        self.assertTrue(response.context["post_event_actions"][0]["show_my_crush"])
        self.assertContains(
            response, reverse("crush_lu:event_attendees", args=[event.id])
        )

    def test_my_crush_waits_for_the_event_to_end(self):
        """connection_window_active only enforces the CLOSING deadline.

        Mid-event it is already true, but event_attendees redirects until the
        event ends — so gating on it alone rendered a CTA that could not open.
        """
        event = _live_event()
        self._register(event, "attended")

        actions = self._get().context["post_event_actions"]
        self.assertFalse(any(a["show_my_crush"] for a in actions))

    def test_a_row_with_no_usable_action_is_not_rendered(self):
        """lobby_cta() denies for reasons unrelated to timing.

        Coach exclusion, lost verification and revoked photo consent all return
        "promo_only", which carries no per-event signal and renders nothing.
        Deciding visibility before resolving the CTA left a titled card with no
        buttons, in a strip whose whole premise is vanishing when empty.
        """
        event = _make_event("Lobby open, nothing to do", days_from_now=-1)
        self._register(event, "attended")

        # The view imports these inside the function, so the source module is
        # the patch point.
        with mock.patch(
            "crush_lu.services.event_lobby.event_lobby_phase",
            return_value=PHASE_LIVE,
        ), mock.patch(
            "crush_lu.services.event_lobby.lobby_cta", return_value=CTA_PROMO_ONLY
        ):
            # Past the connection window, so My Crush is gone too.
            event.connection_window_hours = 0
            event.save(update_fields=["connection_window_hours"])
            response = self._get()

        self.assertEqual(list(response.context["post_event_actions"]), [])
        self.assertNotContains(response, "Lobby open, nothing to do")

    def test_live_quiz_keeps_a_direct_entry_point(self):
        """The removed list carried Join Quiz on attended rows."""
        event = _make_event("Quiz Night", days_from_now=0)
        event.event_type = "quiz_night"
        event.date_time = timezone.now() - timedelta(minutes=30)
        event.duration_minutes = 180
        event.save(update_fields=["event_type", "date_time", "duration_minutes"])
        self._register(event, "attended")

        response = self._get()
        self.assertTrue(response.context["post_event_actions"][0]["show_quiz"])
        self.assertContains(response, reverse("crush_lu:quiz_live", args=[event.id]))

    def test_old_attendance_drops_out_of_the_strip(self):
        """Once the connection window and the lobby have both closed there is
        nothing left to do, so the event becomes history."""
        event = _make_event("Ancient", days_from_now=-120)
        self._register(event, "attended")

        response = self._get()
        self.assertEqual(list(response.context["post_event_actions"]), [])
        self.assertNotContains(
            response, reverse("crush_lu:event_attendees", args=[event.id])
        )

    # -- the card itself --------------------------------------------------

    def test_confirmed_seat_offers_its_ticket(self):
        event = _make_event("Next week", days_from_now=7)
        self._register(event, "confirmed")

        response = self._get()
        self.assertContains(response, reverse("crush_lu:event_ticket", args=[event.id]))

    def test_waitlist_seat_offers_no_ticket(self):
        """There is no seat yet, so there is nothing to scan at the door."""
        event = _make_event("Hoping", days_from_now=7)
        self._register(event, "waitlist")

        response = self._get()
        self.assertEqual(response.context["next_registration"].event, event)
        self.assertNotContains(
            response, reverse("crush_lu:event_ticket", args=[event.id])
        )

    def test_member_with_no_upcoming_seat_is_pointed_at_the_catalogue(self):
        self._register(_make_event("Old news", days_from_now=-30), "attended")

        response = self._get()
        self.assertIsNone(response.context["next_registration"])
        self.assertContains(response, "No upcoming events yet")
        self.assertContains(response, reverse("crush_lu:event_list"))

    # -- navigation -------------------------------------------------------

    def test_events_tab_lands_on_my_events_once_the_member_holds_a_seat(self):
        """The tab's badge counts the member's own seats, so it must go there."""
        self.assertEqual(_events_tab_href(self._get()), reverse("crush_lu:event_list"))

        self._register(_make_event("Booked", days_from_now=5), "confirmed")
        self.assertEqual(_events_tab_href(self._get()), reverse("crush_lu:my_events"))

    def test_a_cancelled_event_does_not_route_the_tab_to_an_empty_page(self):
        """The count drives both the badge and the destination.

        my_events skips unpublished and cancelled events, so counting them sent
        a member with no live seat to a personal page with nothing on it.
        """
        event = _make_event("Called off", days_from_now=5)
        self._register(event, "confirmed")
        event.is_cancelled = True
        event.save(update_fields=["is_cancelled"])

        response = self._get()
        self.assertEqual(response.context["upcoming_events_count"], 0)
        self.assertEqual(_events_tab_href(response), reverse("crush_lu:event_list"))

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
