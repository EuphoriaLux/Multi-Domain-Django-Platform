"""The "Are you still coming?" check for unpaid cash-at-the-door seats.

Run with: pytest crush_lu/tests/test_event_attendance_check.py -v
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent
from crush_lu.models.profiles import UserDataConsent
from crush_lu.services.attendance_check import awaits_attendance_answer
from crush_lu.views_coach import _coach_roster_rows

User = get_user_model()


def _event(title, days_ahead, fee="10.00", **extra):
    day = timezone.localdate() + timedelta(days=days_ahead)
    start = timezone.make_aware(
        datetime.combine(day, time(19, 0)), timezone.get_current_timezone()
    )
    return MeetupEvent.objects.create(
        title=title,
        description="d",
        event_type="mixer",
        date_time=start,
        location="Luxembourg",
        address="1 Test Street",
        max_participants=20,
        registration_deadline=timezone.now() + timedelta(hours=1),
        is_published=True,
        registration_fee=Decimal(fee),
        **extra,
    )


def _check_mails():
    return [m for m in mail.outbox if "still coming" in m.subject]


class AttendanceCheckBase(TestCase):
    def setUp(self):
        cache.clear()
        self._n = 0

    def make_reg(self, event, status="pending", **extra):
        self._n += 1
        user = User.objects.create_user(
            username=f"u{self._n}@example.com",
            email=f"u{self._n}@example.com",
            password="x",
            first_name="Mem",
        )
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Lux",
            verification_status="verified",
            completion_status="step4",
        )
        return EventRegistration.objects.create(
            event=event, user=user, status=status, **extra
        )

    def run_command(self, *args):
        call_command("send_event_reminders", *args, verbosity=0)


class CommandSelectionTests(AttendanceCheckBase):
    def test_only_day_plus_two_paid_pending_unpaid_unasked(self):
        target = _event("Target", 2)
        target_reg = self.make_reg(target)
        self.make_reg(target, status="confirmed", payment_confirmed=True)
        self.make_reg(target, status="confirmed")
        self.make_reg(target, payment_confirmed=True)
        self.make_reg(target, attendance_check_sent_at=timezone.now())
        self.make_reg(target, status="waitlist")
        self.make_reg(_event("Free", 2, fee="0.00"))
        self.make_reg(_event("Cancelled", 2, is_cancelled=True))
        self.make_reg(_event("Tomorrow", 1))
        self.make_reg(_event("Day3", 3))

        self.run_command()

        # target_reg plus the one that was pre-stamped
        self.assertEqual(
            EventRegistration.objects.filter(
                attendance_check_sent_at__isnull=False
            ).count(),
            2,
        )
        target_reg.refresh_from_db()
        self.assertIsNotNone(target_reg.attendance_check_sent_at)
        sent = _check_mails()
        self.assertEqual(len(sent), 1)
        self.assertIn("Target", sent[0].subject)

    def test_second_run_sends_nothing(self):
        self.make_reg(_event("Target", 2))
        self.run_command()
        self.assertEqual(len(_check_mails()), 1)
        self.run_command()
        self.assertEqual(len(_check_mails()), 1)

    def test_dry_run_does_not_send_or_stamp(self):
        reg = self.make_reg(_event("Target", 2))
        self.run_command("--dry-run")
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_check_sent_at)
        self.assertFalse(_check_mails())

    def test_failure_is_not_stamped_so_next_sweep_retries(self):
        reg = self.make_reg(_event("Target", 2))
        with mock.patch(
            "crush_lu.email_helpers.send_event_attendance_check",
            side_effect=RuntimeError("graph down"),
        ):
            self.run_command()
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_check_sent_at)

    def test_a_swallowed_send_failure_is_not_stamped(self):
        reg = self.make_reg(_event("Target", 2))
        with mock.patch(
            "crush_lu.email_helpers.send_event_attendance_check", return_value=0
        ):
            self.run_command()
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_check_sent_at)

    def test_a_failing_reminder_pass_does_not_skip_the_check(self):
        from crush_lu.management.commands.send_event_reminders import Command

        reg = self.make_reg(_event("Target", 2))
        with mock.patch.object(
            Command, "_send_reminders", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.run_command()
        reg.refresh_from_db()
        self.assertIsNotNone(reg.attendance_check_sent_at)

    def test_hours_before_mode_skips_the_check(self):
        reg = self.make_reg(_event("Target", 2))
        self.run_command("--hours-before", "2")
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_check_sent_at)


class EmailContentTests(AttendanceCheckBase):
    def _html(self):
        msg = _check_mails()[0]
        return msg.alternatives[0][0] if msg.alternatives else msg.body

    def test_email_invites_to_the_app_and_cannot_answer_itself(self):
        event = _event("Target", 2)
        self.make_reg(event)
        self.run_command()
        html = self._html()
        self.assertIn(f"/events/{event.id}/#attendance-check", html)
        self.assertIn(f"/events/{event.id}/cancel/", html)
        self.assertIn("Confirm in the app", html)
        self.assertIn("release my seat", html)
        # No link in the email records an answer.
        self.assertNotIn("/attending/", html)

    def test_cash_reminder_only_when_paused(self):
        self.make_reg(_event("Target", 2))
        with mock.patch(
            "crush_lu.services.event_payments.online_payments_paused",
            return_value=True,
        ):
            self.run_command()
        self.assertIn("in cash at the door", self._html())

    def test_no_cash_reminder_when_not_paused(self):
        self.make_reg(_event("Target", 2))
        with mock.patch(
            "crush_lu.services.event_payments.online_payments_paused",
            return_value=False,
        ):
            self.run_command()
        self.assertNotIn("in cash at the door", self._html())


@override_settings(ALLOWED_HOSTS=["*"])
class SignedInConfirmationTests(AttendanceCheckBase):
    """The answer is only ever given signed in, on the event page or dashboard."""

    def asked(self, event=None, **extra):
        reg = self.make_reg(event or _event("Target", 2), **extra)
        EventRegistration.objects.filter(pk=reg.pk).update(
            attendance_check_sent_at=timezone.now()
        )
        reg.refresh_from_db()
        return reg

    def confirm(self, reg, **data):
        self.client.force_login(reg.user)
        return self.client.post(
            f"/en/events/{reg.event_id}/attending/", data, HTTP_HOST="crush.lu"
        )

    def test_member_confirms_on_the_event_page(self):
        reg = self.asked()
        self.client.force_login(reg.user)
        page = self.client.get(
            f"/en/events/{reg.event_id}/", HTTP_HOST="crush.lu"
        ).content.decode()
        self.assertIn('id="attendance-check"', page)
        self.assertIn(f"/en/events/{reg.event_id}/attending/", page)

        response = self.confirm(reg)
        self.assertEqual(response.status_code, 302)
        reg.refresh_from_db()
        first = reg.attendance_confirmed_at
        self.assertIsNotNone(first)
        self.confirm(reg)
        reg.refresh_from_db()
        self.assertEqual(reg.attendance_confirmed_at, first)

        page = self.client.get(
            f"/en/events/{reg.event_id}/", HTTP_HOST="crush.lu"
        ).content.decode()
        self.assertNotIn('id="attendance-check"', page)

    def test_dashboard_offers_the_answer(self):
        reg = self.asked()
        self.client.force_login(reg.user)
        page = self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")
        self.assertEqual(page.status_code, 200)
        self.assertIn(f"/en/events/{reg.event_id}/attending/", page.content.decode())

    def test_anonymous_and_get_requests_never_answer(self):
        reg = self.asked()
        url = f"/en/events/{reg.event_id}/attending/"
        self.client.post(url, HTTP_HOST="crush.lu")
        self.client.force_login(reg.user)
        self.assertNotEqual(self.client.get(url, HTTP_HOST="crush.lu").status_code, 200)
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_confirmed_at)

    def test_only_the_members_own_registration(self):
        reg = self.asked()
        other = self.make_reg(_event("Other", 3))
        self.client.force_login(other.user)
        response = self.client.post(
            f"/en/events/{reg.event_id}/attending/", HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 404)
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_confirmed_at)

    def test_not_asked_cancelled_or_past_seats_are_not_marked(self):
        not_asked = self.make_reg(_event("Target", 2))
        cancelled = self.asked(status="cancelled")
        past = self.asked(event=_event("Old", -1))
        for reg in (not_asked, cancelled, past):
            self.confirm(reg)
            reg.refresh_from_db()
            self.assertIsNone(reg.attendance_confirmed_at)
            self.assertFalse(awaits_attendance_answer(reg))

    def test_next_only_accepts_a_same_site_path(self):
        reg = self.asked()
        response = self.confirm(reg, next="https://evil.example/")
        self.assertNotIn("evil.example", response["Location"])

    def test_rescheduling_clears_the_check(self):
        event = _event("Target", 2)
        reg = self.make_reg(event)
        EventRegistration.objects.filter(pk=reg.pk).update(
            attendance_check_sent_at=timezone.now(),
            attendance_confirmed_at=timezone.now(),
        )
        event.date_time = event.date_time + timedelta(days=7)
        event.save()
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_check_sent_at)
        self.assertIsNone(reg.attendance_confirmed_at)


class CoachRosterMarkerTests(AttendanceCheckBase):
    def test_markers(self):
        event = _event("Target", 2)
        coming = self.make_reg(event, attendance_confirmed_at=timezone.now())
        asked = self.make_reg(event, attendance_check_sent_at=timezone.now())
        plain = self.make_reg(event)
        paid = self.make_reg(
            event,
            status="confirmed",
            payment_confirmed=True,
            attendance_confirmed_at=timezone.now(),
        )
        rows = {
            r["registration"].pk: r["attendance_check"]
            for r in _coach_roster_rows([coming, asked, plain, paid], event)
        }
        self.assertEqual(rows[coming.pk], "Coming ✓")
        self.assertEqual(rows[asked.pk], "Asked, no answer")
        self.assertEqual(rows[plain.pk], "")
        self.assertEqual(rows[paid.pk], "")

    def test_template_renders_marker(self):
        event = _event("Target", 2)
        reg = self.make_reg(event, attendance_confirmed_at=timezone.now())
        html = render_to_string(
            "crush_lu/components/coach_event_roster.html",
            {"rows": _coach_roster_rows([reg], event)},
        )
        self.assertIn("data-attendance-check", html)
        self.assertIn("Coming ✓", html)
