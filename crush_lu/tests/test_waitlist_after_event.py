"""A waitlist spot that never turned into a seat reads "No spot" once the event ends.

Nothing moves a ``waitlist`` (or still-open curated ``applied``) row on after
its event: promotion stops at the start and the stored status stays as it is.
Before this, the coach's member overview showed "Waitlist" on events months in
the past, gave curated "applied"/"not_selected" rows no badge at all, and the
member's own event page still promised "We'll email you if a spot opens up".

Run with: pytest crush_lu/tests/test_waitlist_after_event.py -v
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import CrushCoach, EventRegistration
from crush_lu.templatetags.registration_status_tags import registration_tone
from crush_lu.tests.test_phase_post_event import (
    CRUSH_LU_URL_SETTINGS,
    _make_event,
    _make_profile,
    _make_user,
    _SiteSetup,
)


def _past_event(**overrides):
    return _make_event(
        date_time=timezone.now() - timedelta(days=5),
        registration_deadline=timezone.now() - timedelta(days=6),
        **overrides,
    )


class MissedOutPropertyTests(_SiteSetup, TestCase):
    def setUp(self):
        self.user = _make_user("missed_prop")

    def _reg(self, event, status):
        return EventRegistration.objects.create(
            event=event, user=self.user, status=status
        )

    def test_waitlist_on_a_finished_event_missed_out(self):
        self.assertTrue(self._reg(_past_event(), "waitlist").missed_out)

    def test_open_application_on_a_finished_event_missed_out(self):
        self.assertTrue(self._reg(_past_event(), "applied").missed_out)

    def test_waitlist_on_an_upcoming_event_is_still_waiting(self):
        self.assertFalse(self._reg(_make_event(), "waitlist").missed_out)

    def test_waitlist_while_the_event_is_running_is_still_waiting(self):
        # Started an hour ago, still on (end_time is the cut-off, like My Events).
        event = _make_event(
            date_time=timezone.now() - timedelta(hours=1),
            registration_deadline=timezone.now() - timedelta(hours=2),
            duration_minutes=180,
        )
        self.assertFalse(self._reg(event, "waitlist").missed_out)

    def test_other_statuses_on_a_finished_event_are_untouched(self):
        for status in ("attended", "no_show", "cancelled", "not_selected"):
            with self.subTest(status=status):
                reg = EventRegistration(
                    event=_past_event(), user=self.user, status=status
                )
                self.assertFalse(reg.missed_out)

    def test_tone_is_missed_not_waitlist(self):
        event = _past_event()
        reg = self._reg(event, "waitlist")
        self.assertEqual(registration_tone(reg, event), "missed")

    def test_tone_stays_waitlist_before_the_event(self):
        event = _make_event()
        reg = self._reg(event, "waitlist")
        self.assertEqual(registration_tone(reg, event), "waitlist")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class MemberPagesTests(_SiteSetup, TestCase):
    def setUp(self):
        cache.clear()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = _make_user("missed_member")
        _make_profile(self.user)
        self.client.force_login(self.user)
        self.event = _past_event(title="Gone evening")
        EventRegistration.objects.create(
            event=self.event, user=self.user, status="waitlist"
        )

    def test_event_page_no_longer_promises_an_email(self):
        page = self.client.get(f"/en/events/{self.event.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "You&#x27;re on the waitlist")
        self.assertNotContains(page, "if a spot opens up")
        self.assertContains(page, "No spot opened up")

    def test_my_events_labels_it_no_spot(self):
        page = self.client.get("/en/my-events/")
        self.assertEqual(page.status_code, 200)
        (entry,) = page.context["past_registrations"]
        self.assertTrue(entry["no_spot"])
        self.assertContains(page, "No spot")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachMemberOverviewTests(_SiteSetup, TestCase):
    def setUp(self):
        cache.clear()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        coach_user = _make_user("missed_coach")
        CrushCoach.objects.create(user=coach_user, bio="coach", is_active=True)
        self.client.force_login(coach_user)
        self.member = _make_user("missed_target")
        _make_profile(self.member)

    def _overview(self):
        response = self.client.get(f"/en/coach/member/{self.member.pk}/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _register(self, event, status):
        EventRegistration.objects.create(event=event, user=self.member, status=status)

    def test_past_waitlist_shows_no_spot_not_waitlist(self):
        self._register(_past_event(title="Past mixer"), "waitlist")
        html = self._overview()
        self.assertIn("No spot", html)
        self.assertIn("Was on the waitlist", html)
        self.assertNotIn(">Waitlist<", html)

    def test_upcoming_waitlist_still_shows_waitlist(self):
        self._register(_make_event(title="Next mixer"), "waitlist")
        html = self._overview()
        self.assertIn(">Waitlist<", html)
        self.assertNotIn("No spot", html)

    def test_curated_statuses_get_a_badge(self):
        self._register(_make_event(title="Applied evening"), "applied")
        self._register(_past_event(title="Turned down evening"), "not_selected")
        html = self._overview()
        self.assertIn(">Applied<", html)
        self.assertIn(">Not selected<", html)


class CloseFinishedWaitlistsTests(_SiteSetup, TestCase):
    """The after-event sweep stores what the pages above only display."""

    def setUp(self):
        from django.core import mail

        mail.outbox = []
        self.n = 0

    def _reg(self, event, status):
        self.n += 1
        user = _make_user(f"sweep_{self.n}")
        return EventRegistration.objects.create(event=event, user=user, status=status)

    def _status(self, reg):
        reg.refresh_from_db()
        return reg.status

    def test_finished_event_waitlist_and_applications_are_closed(self):
        from django.core import mail

        from crush_lu.services.waitlist_closing import close_finished_waitlists

        event = _past_event()
        waiting = self._reg(event, "waitlist")
        applied = self._reg(event, "applied")
        attended = self._reg(event, "attended")

        counts = close_finished_waitlists()

        self.assertEqual(counts, {"events": 1, "waitlist_closed": 1, "not_selected": 1})
        self.assertEqual(self._status(waiting), "waitlist_closed")
        self.assertEqual(self._status(applied), "not_selected")
        self.assertEqual(self._status(attended), "attended")
        # Silent: nobody is mailed "you were not picked" after the evening.
        self.assertEqual(mail.outbox, [])

    def test_closed_row_reads_missed_out(self):
        from crush_lu.services.waitlist_closing import close_finished_waitlists

        reg = self._reg(_past_event(), "waitlist")
        close_finished_waitlists()
        reg.refresh_from_db()
        self.assertTrue(reg.missed_out)
        self.assertEqual(registration_tone(reg, reg.event), "missed")

    def test_upcoming_and_running_events_are_left_alone(self):
        from crush_lu.services.waitlist_closing import close_finished_waitlists

        upcoming = self._reg(_make_event(), "waitlist")
        running = self._reg(
            _make_event(
                date_time=timezone.now() - timedelta(hours=1),
                registration_deadline=timezone.now() - timedelta(hours=2),
                duration_minutes=180,
            ),
            "waitlist",
        )
        self.assertEqual(close_finished_waitlists()["events"], 0)
        self.assertEqual(self._status(upcoming), "waitlist")
        self.assertEqual(self._status(running), "waitlist")

    def test_backlog_is_left_for_the_manual_run(self):
        from crush_lu.services.waitlist_closing import close_finished_waitlists

        old = self._reg(
            _make_event(
                date_time=timezone.now() - timedelta(days=120),
                registration_deadline=timezone.now() - timedelta(days=121),
            ),
            "waitlist",
        )
        self.assertEqual(close_finished_waitlists()["waitlist_closed"], 0)
        self.assertEqual(self._status(old), "waitlist")

        self.assertEqual(close_finished_waitlists(lookback=None)["waitlist_closed"], 1)
        self.assertEqual(self._status(old), "waitlist_closed")

    def test_dry_run_writes_nothing(self):
        from crush_lu.services.waitlist_closing import close_finished_waitlists

        reg = self._reg(_past_event(), "waitlist")
        counts = close_finished_waitlists(dry_run=True)
        self.assertEqual(counts["waitlist_closed"], 1)
        self.assertEqual(self._status(reg), "waitlist")

    def test_command_dry_run_lists_events_and_all_past_closes_backlog(self):
        from io import StringIO

        from django.core.management import call_command

        event = _make_event(
            title="Karaoke mixer",
            date_time=timezone.now() - timedelta(days=120),
            registration_deadline=timezone.now() - timedelta(days=121),
        )
        reg = self._reg(event, "waitlist")

        out = StringIO()
        call_command("close_finished_waitlists", "--all-past", "--dry-run", stdout=out)
        self.assertIn("Karaoke mixer", out.getvalue())
        self.assertIn("Would close", out.getvalue())
        self.assertEqual(self._status(reg), "waitlist")

        call_command("close_finished_waitlists", "--all-past", stdout=StringIO())
        self.assertEqual(self._status(reg), "waitlist_closed")

    @override_settings(ADMIN_API_KEY="test-admin-api-key", **CRUSH_LU_URL_SETTINGS)
    def test_hourly_recaps_endpoint_closes_them(self):
        from django.test import Client

        reg = self._reg(_past_event(), "waitlist")
        response = Client(HTTP_HOST="crush.lu").post(
            "/api/admin/event-recaps/",
            HTTP_AUTHORIZATION="Bearer test-admin-api-key",
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self._status(reg), "waitlist_closed")


class CoachRosterStatusTests(_SiteSetup, TestCase):
    def test_closed_waitlist_has_its_own_roster_bucket_and_badge(self):
        from crush_lu.views_coach import ROSTER_BUCKET_ORDER, _roster_status

        self.assertIn("waitlist_closed", ROSTER_BUCKET_ORDER)
        event = _past_event()
        reg = EventRegistration.objects.create(
            event=event, user=_make_user("roster_closed"), status="waitlist_closed"
        )
        label, tone = _roster_status(reg, event, started=True)
        self.assertEqual((str(label), tone), ("Waitlist closed", "gray"))
