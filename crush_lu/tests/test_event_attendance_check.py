"""The "Are you still coming?" check for unpaid cash-at-the-door seats.

Run with: pytest crush_lu/tests/test_event_attendance_check.py -v
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.core.cache import cache
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent
from crush_lu.services.attendance_check import (
    ATTENDANCE_CONFIRM_SALT,
    make_attendance_token,
)
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
        CrushProfile.objects.create(
            user=user, date_of_birth=date(1995, 1, 1), gender="F", location="Lux"
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

    def test_email_has_confirm_and_cancel_links(self):
        event = _event("Target", 2)
        reg = self.make_reg(event)
        self.run_command()
        html = self._html()
        self.assertIn("/events/attending/", html)
        self.assertIn(f"/events/{event.id}/cancel/", html)
        self.assertIn("coming", html)
        self.assertIn("release my seat", html)
        token = html.split("/events/attending/")[1].split("/")[0].split('"')[0]
        self.assertEqual(
            signing.loads(token, salt=ATTENDANCE_CONFIRM_SALT)["r"], reg.pk
        )

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
class ConfirmEndpointTests(AttendanceCheckBase):
    def get(self, token, lang="en"):
        # The answer is recorded on POST (the button on the page). Named get
        # for the existing callers, which all exercise the recording path.
        return self.client.post(
            f"/{lang}/events/attending/{token}/", HTTP_HOST="crush.lu"
        )

    def test_opening_the_link_only_asks(self):
        """A mail scanner's GET must not mark the member as coming."""
        reg = self.make_reg(_event("Target", 2))
        resp = self.client.get(
            f"/en/events/attending/{make_attendance_token(reg)}/",
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Yes, I")
        self.assertContains(resp, f"/en/events/{reg.event_id}/cancel/")
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_confirmed_at)

    def test_confirm_sets_timestamp_and_is_idempotent(self):
        reg = self.make_reg(_event("Target", 2))
        resp = self.get(make_attendance_token(reg))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "see you there")
        reg.refresh_from_db()
        first = reg.attendance_confirmed_at
        self.assertIsNotNone(first)
        self.assertEqual(self.get(make_attendance_token(reg)).status_code, 200)
        reg.refresh_from_db()
        self.assertEqual(reg.attendance_confirmed_at, first)

    def test_tampered_token_is_friendly_not_500(self):
        reg = self.make_reg(_event("Target", 2))
        token = make_attendance_token(reg)
        for bad in (token[:-3] + "xyz", "garbage", "a:b:c"):
            self.assertEqual(self.get(bad).status_code, 400)
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_confirmed_at)

    def test_wrong_salt_rejected(self):
        reg = self.make_reg(_event("Target", 2))
        token = signing.dumps({"r": reg.pk}, salt="other")
        self.assertEqual(self.get(token).status_code, 400)

    def test_expired_token_rejected(self):
        reg = self.make_reg(_event("Target", 2))
        token = make_attendance_token(reg)
        later = timezone.now().timestamp() + 8 * 86400
        with mock.patch("time.time", return_value=later):
            resp = self.get(token)
        self.assertEqual(resp.status_code, 400)
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_confirmed_at)

    def test_cancelled_registration_refused(self):
        reg = self.make_reg(_event("Target", 2), status="cancelled")
        self.assertEqual(self.get(make_attendance_token(reg)).status_code, 400)
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_confirmed_at)

    def test_link_from_an_earlier_booking_does_not_answer_for_a_new_one(self):
        reg = self.make_reg(_event("Target", 2))
        old_token = make_attendance_token(reg)
        reg.registered_at = reg.registered_at + timedelta(minutes=5)
        reg.save(update_fields=["registered_at"])
        self.assertEqual(self.get(old_token).status_code, 400)
        reg.refresh_from_db()
        self.assertIsNone(reg.attendance_confirmed_at)

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

    def test_cancelled_event_refused(self):
        reg = self.make_reg(_event("Target", 2, is_cancelled=True))
        self.assertEqual(self.get(make_attendance_token(reg)).status_code, 400)

    def test_past_event_refused(self):
        reg = self.make_reg(_event("Old", -1))
        self.assertEqual(self.get(make_attendance_token(reg)).status_code, 400)

    def test_german_page_renders(self):
        reg = self.make_reg(_event("Target", 2))
        resp = self.get(make_attendance_token(reg), lang="de")
        self.assertContains(resp, "Danke, bis dann!")


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
