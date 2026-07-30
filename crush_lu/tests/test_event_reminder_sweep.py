"""The scheduled event-reminder sweep.

Three defects the EventReminders timer would otherwise have shipped with, all
invisible until the command ran unattended:

1. `NotificationService._send_email` gated the reminder email on
   `registration and request`. A management command has no request, so the
   only path that ever runs unattended sent a push and an in-app row and
   nothing to the inbox.
2. `send_event_reminder` passed no `domain`, and `get_domain_email_config`
   falls back to the **PowerUp** sender when it has neither a request nor a
   domain — so a Crush reminder would leave from the wrong brand.
3. Nothing recorded that a reminder had been sent, so a catch-up or retried
   invocation re-notified everyone.

Run with: pytest crush_lu/tests/test_event_reminder_sweep.py -v
"""

from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

User = get_user_model()


class _ReminderFixture(TestCase):
    def setUp(self):
        from crush_lu.models import CrushProfile, EventRegistration, MeetupEvent

        self.event = MeetupEvent.objects.create(
            title="Reminder Test",
            description="Tomorrow",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=1, hours=2),
            location="Luxembourg",
            address="1 Test Street",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(hours=1),
            is_published=True,
        )
        self.user = User.objects.create_user(
            username="r@example.com",
            email="r@example.com",
            password="testpass123",
            first_name="Rem",
        )
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
        )
        self.reg = EventRegistration.objects.create(
            event=self.event, user=self.user, status="confirmed"
        )


class ReminderEmailReachesRequestlessCallersTests(_ReminderFixture):
    def test_command_sends_the_email_without_a_request(self):
        with mock.patch(
            "crush_lu.email_helpers.send_event_reminder", return_value=1
        ) as helper:
            call_command("send_event_reminders", "--event-id", str(self.event.id))
        helper.assert_called_once()
        self.assertEqual(helper.call_args[0][0].pk, self.reg.pk)

    def test_notification_service_no_longer_requires_a_request(self):
        from crush_lu.notification_service import notify_event_reminder

        with mock.patch(
            "crush_lu.email_helpers.send_event_reminder", return_value=1
        ) as helper:
            notify_event_reminder(
                user=self.user,
                registration=self.reg,
                event=self.event,
                days_until=1,
                request=None,
            )
        helper.assert_called_once()


class ReminderSenderDomainTests(_ReminderFixture):
    def test_reminder_email_uses_the_crush_sender_without_a_request(self):
        from crush_lu import email_helpers

        with mock.patch.object(
            email_helpers, "send_domain_email", return_value=1
        ) as send:
            email_helpers.send_event_reminder(self.reg, None, days_until_event=1)
        self.assertEqual(send.call_args.kwargs["domain"], "crush.lu")

    def test_feedback_and_confirmation_also_pin_the_crush_sender(self):
        """The same latent bug lived in every request-less event helper."""
        from crush_lu import email_helpers

        for fn in (
            email_helpers.send_event_feedback_request,
            email_helpers.send_event_registration_confirmation,
        ):
            with self.subTest(helper=fn.__name__):
                with mock.patch.object(
                    email_helpers, "send_domain_email", return_value=1
                ) as send:
                    fn(self.reg)
                self.assertEqual(send.call_args.kwargs["domain"], "crush.lu")


class ReminderIdempotencyTests(_ReminderFixture):
    def _run(self, *extra):
        with mock.patch(
            "crush_lu.email_helpers.send_event_reminder", return_value=1
        ) as helper:
            call_command(
                "send_event_reminders", "--event-id", str(self.event.id), *extra
            )
        return helper

    def test_first_run_stamps_reminder_sent_at(self):
        self._run()
        self.reg.refresh_from_db()
        self.assertIsNotNone(self.reg.reminder_sent_at)

    def test_second_run_does_not_resend(self):
        self._run()
        self.assertEqual(self._run().call_count, 0, "a retry must not re-notify")

    def test_force_resends(self):
        self._run()
        self.assertEqual(self._run("--force").call_count, 1)

    def test_hours_before_mode_is_independent_of_the_day_stamp(self):
        """`--hours-before` is a different, same-day reminder: a day-before
        send must not suppress it, and it must not consume the stamp."""
        self._run()
        self.reg.refresh_from_db()
        stamped_at = self.reg.reminder_sent_at

        from crush_lu.models import MeetupEvent

        MeetupEvent.objects.filter(pk=self.event.pk).update(
            date_time=timezone.now() + timedelta(hours=2)
        )
        with mock.patch(
            "crush_lu.email_helpers.send_event_reminder", return_value=1
        ) as helper:
            call_command(
                "send_event_reminders",
                "--event-id",
                str(self.event.id),
                "--hours-before",
                "2",
            )
        self.assertEqual(helper.call_count, 1, "same-day nudge must still go out")
        self.reg.refresh_from_db()
        self.assertEqual(self.reg.reminder_sent_at, stamped_at)

    def test_dry_run_does_not_stamp(self):
        self._run("--dry-run")
        self.reg.refresh_from_db()
        self.assertIsNone(self.reg.reminder_sent_at)
