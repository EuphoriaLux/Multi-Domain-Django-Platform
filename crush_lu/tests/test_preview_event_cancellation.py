"""The cancellation preview, and the two rules it exists to keep apart.

Every paid transaction on a cancelled event reads ``paid``, whether the member
was still coming or had dropped out weeks earlier — ``REFUNDED`` is never set by
anything. So the obvious query, the one written under time pressure, cannot tell
the two apart, and both directions of getting it wrong cost real money:

  * refunding a self-cancelled member who is owed nothing, or
  * retaining money from one who is owed a full refund under ToS §7.3.

The second is the easier mistake, because "they cancelled, so they get nothing"
sounds like a policy and is only the <24h row of a four-row table. A member who
dropped out three days ahead is owed the whole fee.

Asserted here: live seats get the full amount with no timing test (Crush.lu
cancelled, §7.3 last row), self-cancelled seats get graded by timing, and the
grading lands on the right side of both boundaries.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.tests.test_profile_edit_connect_card import _make_member


def _make_event(title="Quiz Night", *, days_from_now=1, fee="15.50"):
    when = timezone.now() + timedelta(days=days_from_now)
    return MeetupEvent.objects.create(
        title=title,
        description="x",
        event_type="mixer",
        date_time=when,
        location="Luxembourg",
        address="1 Test St",
        max_participants=20,
        duration_minutes=160,
        registration_fee=Decimal(fee),
        registration_deadline=when - timedelta(hours=2),
        is_published=True,
    )


class PreviewEventCancellationTests(TestCase):
    def setUp(self):
        self.event = _make_event()

    def _register(self, email, status):
        return EventRegistration.objects.create(
            user=_make_member(email), event=self.event, status=status
        )

    def _pay(self, registration, amount="15.50", status=None):
        return PaymentTransaction.objects.create(
            user=registration.user,
            event_registration=registration,
            amount=Decimal(amount),
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            status=status or PaymentTransaction.Status.PAID,
            sumup_checkout_id=f"chk-{registration.pk}",
        )

    def _run(self, *args):
        out = StringIO()
        call_command(
            "preview_event_cancellation",
            "--event-id",
            str(self.event.pk),
            *args,
            stdout=out,
        )
        return out.getvalue()

    @staticmethod
    def _section(report, heading, until):
        """The slice of the report between two headings."""
        start = report.index(heading)
        return report[start : report.index(until, start)]

    # -- the split --------------------------------------------------------

    def _cancelled_at(self, registration, *, hours_before_event):
        """Backdate the cancellation. updated_at is auto_now, so .update()."""
        EventRegistration.objects.filter(pk=registration.pk).update(
            updated_at=self.event.date_time - timedelta(hours=hours_before_event)
        )
        return registration

    def test_the_two_groups_are_reported_apart(self):
        staying = self._register("staying@example.com", "confirmed")
        self._pay(staying)
        dropped = self._register("dropped@example.com", "cancelled")
        self._pay(dropped)

        report = self._run()
        owed = self._section(report, "REFUND OWED", "SELF-CANCELLED")
        graded = self._section(report, "SELF-CANCELLED", "AFTER YOU REFUND")

        self.assertIn("staying@example.com", owed)
        self.assertNotIn("dropped@example.com", owed)

        self.assertIn("dropped@example.com", graded)
        self.assertNotIn("staying@example.com", graded)

    def test_live_seats_get_the_full_fee_with_no_timing_test(self):
        """Crush.lu cancelled, so §7.3's last row applies: full, regardless."""
        for email in ("a@example.com", "b@example.com"):
            reg = self._pay(self._register(email, "confirmed")).event_registration
            # Even a seat touched minutes before the event is owed in full.
            self._cancelled_at(reg, hours_before_event=1)

        self.assertIn("TOTAL TO REFUND: 31.00 EUR", self._run())

    # -- ToS §7.3 grading -------------------------------------------------

    def test_self_cancel_more_than_48h_out_is_owed_the_full_fee(self):
        """The expensive misreading: "they cancelled" does not mean "no refund"."""
        self._pay(
            self._cancelled_at(
                self._register("early@example.com", "cancelled"),
                hours_before_event=60,
            )
        )

        report = self._run()
        self.assertIn("OWED UNDER §7.3 : 15.50 EUR", report)
        self.assertIn("RETAINED        : 0.00 EUR", report)

    def test_self_cancel_inside_48h_is_owed_half(self):
        self._pay(
            self._cancelled_at(
                self._register("late@example.com", "cancelled"),
                hours_before_event=30,
            )
        )

        report = self._run()
        self.assertIn("OWED UNDER §7.3 : 7.75 EUR", report)
        self.assertIn("RETAINED        : 7.75 EUR", report)

    def test_self_cancel_inside_24h_is_owed_nothing(self):
        self._pay(
            self._cancelled_at(
                self._register("verylate@example.com", "cancelled"),
                hours_before_event=6,
            )
        )

        report = self._run()
        self.assertIn("OWED UNDER §7.3 : 0.00 EUR", report)
        self.assertIn("RETAINED        : 15.50 EUR", report)

    def test_resale_warning_appears_only_when_money_is_owed(self):
        """Retaining an owed refund means being paid twice for one chair."""
        owed = self._cancelled_at(
            self._register("owed@example.com", "cancelled"), hours_before_event=60
        )
        self._pay(owed)
        self.assertIn("twice for one chair", self._run())

        PaymentTransaction.objects.all().delete()
        EventRegistration.objects.all().delete()
        self._pay(
            self._cancelled_at(
                self._register("nothing@example.com", "cancelled"),
                hours_before_event=2,
            )
        )
        self.assertNotIn("twice for one chair", self._run())

    def test_two_step_refund_reminder_is_shown(self):
        """Refunding in SumUp alone leaves the member admitted free."""
        self._pay(self._register("payer@example.com", "confirmed"))
        self.assertIn("payment_confirmed = False", self._run())

    def test_waitlist_and_pending_seats_count_as_live(self):
        """Neither has been told the event is off, so both need contacting."""
        self._register("waiting@example.com", "waitlist")
        self._register("unpaid@example.com", "pending")

        report = self._run()
        contacts = self._section(report, "2. WHO MUST BE TOLD", "3. MONEY")
        self.assertIn("waiting@example.com", contacts)
        self.assertIn("unpaid@example.com", contacts)
        self.assertIn("WHO MUST BE TOLD (2 people)", report)

    # -- what it tells you ------------------------------------------------

    def test_report_states_that_nobody_is_emailed(self):
        """The single most load-bearing line: the app notifies no one."""
        self._register("someone@example.com", "confirmed")
        self.assertIn("emails or notifies attendees: NO.", self._run())

    def test_emails_flag_lists_live_seats_only(self):
        self._register("live@example.com", "confirmed")
        self._register("dropped@example.com", "cancelled")

        addresses = self._run("--emails").strip()
        self.assertEqual(addresses, "live@example.com")

    def test_open_checkout_is_flagged_as_able_to_capture_late(self):
        reg = self._register("inflight@example.com", "pending")
        self._pay(reg, status=PaymentTransaction.Status.PENDING)

        report = self._run()
        self.assertIn("still PENDING", report)
        self.assertIn("inflight@example.com", report)

    def test_double_charge_is_flagged(self):
        reg = self._register("twice@example.com", "confirmed")
        self._pay(reg)
        self._pay(reg)

        report = self._run()
        self.assertIn("MORE THAN ONE paid", report)
        self.assertIn("31.00 EUR", report)

    def test_preview_changes_nothing(self):
        reg = self._register("safe@example.com", "confirmed")
        tx = self._pay(reg)

        self._run()

        self.event.refresh_from_db()
        reg.refresh_from_db()
        tx.refresh_from_db()
        self.assertFalse(self.event.is_cancelled)
        self.assertEqual(reg.status, "confirmed")
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
