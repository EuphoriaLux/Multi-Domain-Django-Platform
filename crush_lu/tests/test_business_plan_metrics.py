"""business_plan_metrics — paid seats counted from immutable rows (#847).

The report must count paid seats from PAID ``PaymentTransaction`` rows (what
#827 moved ``weekly_kpis`` and ``admin_views`` to), never from the mutable
``EventRegistration.payment_confirmed`` flag: issuing cancellation credit
clears the flag, so a cancellation that releases a seat used to retroactively
shrink a past month's "paid" figure.

Run with: pytest crush_lu/tests/test_business_plan_metrics.py -v
"""

import json
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.services.credits import issue_cancellation_credits

User = get_user_model()

FEE = Decimal("15.50")


def _aware(d, hour=18):
    """Timezone-aware datetime at a given hour on date ``d``."""
    return timezone.make_aware(datetime.combine(d, time(hour, 0)))


class BusinessPlanMetricsFixture(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "crush.lu", "name": "Crush.lu"}
        )

    def setUp(self):
        self.member = User.objects.create_user(
            username="member@crush.lu", email="member@crush.lu", password="x"
        )

    # -- builders ---------------------------------------------------------

    def _event(self, day, *, title="Speed Dating Luxembourg"):
        return MeetupEvent.objects.create(
            title=title,
            description="d",
            event_type="speed_dating",
            location="Luxembourg City",
            address="1 Grand Rue",
            date_time=_aware(day),
            registration_deadline=_aware(day) - timedelta(hours=24),
            registration_fee=FEE,
            max_participants=10,
            is_published=True,
        )

    def _paid_seat(self, event, user, *, provider=PaymentTransaction.Provider.SUMUP):
        """A confirmed seat plus its captured payment behind it."""
        registration = EventRegistration.objects.create(
            event=event,
            user=user,
            status="confirmed",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )
        self._tx(
            registration,
            provider=provider,
            # Shaped like the real thing: create_sumup_event_checkout writes
            # CRUSH-EVT-<registration id>-<hex>, and the registration id in
            # that string is what identifies the seat once the FK is gone.
            reference=f"CRUSH-EVT-{registration.pk}-{uuid.uuid4().hex[:6]}",
        )
        return registration

    def _tx(
        self,
        registration,
        *,
        provider=PaymentTransaction.Provider.SUMUP,
        status=PaymentTransaction.Status.PAID,
        reference,
        amount=FEE,
    ):
        return PaymentTransaction.objects.create(
            transaction_reference=reference,
            provider=provider,
            sumup_checkout_id=(
                "" if provider == PaymentTransaction.Provider.CREDIT else reference
            ),
            amount=amount,
            currency="EUR",
            status=status,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
        )

    def _cancel_with_credit(self, registration):
        """The state the member-cancellation flow leaves behind: seat
        released, credit issued, payment flags cleared — the PAID row kept.

        The credit moment is pinned >48h before the event (the fixture's
        events are dated in the past, so "now" would read as a late
        cancellation and issue nothing).
        """
        registration.status = "cancelled"
        registration.save()
        credits = issue_cancellation_credits(
            registration,
            moment=registration.event.date_time - timedelta(hours=100),
        )
        registration.refresh_from_db()
        assert credits, "fixture expected a full credit cancellation"
        assert not registration.payment_confirmed

    # -- report runners ----------------------------------------------------

    def _report(self, since, until):
        out = StringIO()
        call_command(
            "business_plan_metrics",
            "--json",
            "--since",
            since.isoformat(),
            "--until",
            until.isoformat(),
            stdout=out,
        )
        return json.loads(out.getvalue())

    def _monthly(self, since, until):
        out = StringIO()
        call_command(
            "business_plan_metrics",
            "--monthly",
            "--json",
            "--since",
            since.isoformat(),
            "--until",
            until.isoformat(),
            stdout=out,
        )
        return {
            row["month"]: row
            for row in json.loads(out.getvalue())["monthly"]
        }


class MainReportPaidSeatsTests(BusinessPlanMetricsFixture):
    """Section 6 (Event Activity) — ``event_activity.paid_registrations``."""

    def test_paid_seat_survives_a_seat_releasing_cancellation(self):
        event = self._event(date(2026, 6, 10))
        registration = self._paid_seat(event, self.member)

        report = self._report(date(2026, 6, 1), date(2026, 7, 1))
        self.assertEqual(report["event_activity"]["paid_registrations"], 1)

        # The member cancels more than 48h out: credit is issued and the
        # mutable payment flags are released — the exact state that used to
        # retroactively shrink this figure when the report was re-run.
        self._cancel_with_credit(registration)

        report = self._report(date(2026, 6, 1), date(2026, 7, 1))
        self.assertEqual(report["event_activity"]["paid_registrations"], 1)

    def test_one_seat_with_several_attempts_counts_once(self):
        event = self._event(date(2026, 6, 10))
        registration = self._paid_seat(event, self.member)
        # A declined card attempt before the capture, plus a cash (MANUAL)
        # capture — and a re-booked cycle reusing the same registration row.
        # Still one paid seat, not three transactions.
        self._tx(
            registration,
            status=PaymentTransaction.Status.FAILED,
            reference=f"CRUSH-EVT-{registration.pk}-declined",
        )
        self._tx(
            registration,
            provider=PaymentTransaction.Provider.MANUAL,
            reference=f"CRUSH-EVT-{registration.pk}-cash",
        )

        report = self._report(date(2026, 6, 1), date(2026, 7, 1))
        self.assertEqual(report["event_activity"]["paid_registrations"], 1)

    def test_credit_funded_seat_is_not_a_new_paid_seat(self):
        # A credit redemption confirms the seat (flag True) but moves no new
        # money: the cash behind it was already counted at first capture, so
        # counting it again would book one payment twice.
        event = self._event(date(2026, 6, 10))
        self._paid_seat(event, self.member, provider=PaymentTransaction.Provider.CREDIT)

        report = self._report(date(2026, 6, 1), date(2026, 7, 1))
        self.assertEqual(report["event_activity"]["paid_registrations"], 0)


class MonthlyPaidSeatsTests(BusinessPlanMetricsFixture):
    """``--monthly`` breakdown — the ``paid_reg`` column."""

    def test_cancellation_does_not_shrink_the_event_month(self):
        may_event = self._event(date(2026, 5, 14), title="May Event")
        registration = self._paid_seat(may_event, self.member)

        monthly = self._monthly(date(2026, 5, 1), date(2026, 7, 1))
        self.assertEqual(monthly["2026-05"]["paid_reg"], 1)

        # The cancellation happens later (say, July) — the May figure must
        # not move when the offline report is re-run afterwards.
        self._cancel_with_credit(registration)

        monthly = self._monthly(date(2026, 5, 1), date(2026, 7, 1))
        self.assertEqual(monthly["2026-05"]["paid_reg"], 1)

    def test_second_cycle_seat_counted_where_the_cash_lands(self):
        # Cycle 1: pays cash in May, cancels, credit issued. Cycle 2: re-books
        # in June using that credit — no new money, so June stays at the one
        # genuinely new cash seat (the other member's).
        may_event = self._event(date(2026, 5, 14), title="May Event")
        june_event = self._event(date(2026, 6, 11), title="June Event")

        first_seat = self._paid_seat(may_event, self.member)
        self._cancel_with_credit(first_seat)

        self._paid_seat(june_event, self.member, provider=PaymentTransaction.Provider.CREDIT)
        other = User.objects.create_user(
            username="other@crush.lu", email="other@crush.lu", password="x"
        )
        self._paid_seat(june_event, other)

        monthly = self._monthly(date(2026, 5, 1), date(2026, 7, 1))
        self.assertEqual(monthly["2026-05"]["paid_reg"], 1)
        self.assertEqual(monthly["2026-06"]["paid_reg"], 1)


class DeletedAccountPaidSeatsTests(BusinessPlanMetricsFixture):
    """A deleted account must not shrink a past month either.

    ``views_account.py`` deletes a departing member's EventRegistration rows,
    and ``PaymentTransaction.event_registration`` is SET_NULL — so attributing
    seats through ``event_registration__event`` drops those captured payments
    exactly the way the ``payment_confirmed`` flag used to. The money was
    taken; the seat was sold; the report must still say so. Attribution runs
    through the immutable ``PaymentTransaction.event`` instead.
    """

    def test_paid_seat_survives_the_registration_being_deleted(self):
        event = self._event(date(2026, 5, 14))
        registration = self._paid_seat(event, self.member)

        registration.delete()

        report = self._report(date(2026, 5, 1), date(2026, 6, 1))
        self.assertEqual(report["event_activity"]["paid_registrations"], 1)

    def test_several_deleted_accounts_do_not_collapse_into_one_seat(self):
        """The NULL group trap: every orphaned row shares event_registration
        _id=NULL, so a DISTINCT over that column reports a whole event's worth
        of departed members as a single paid seat."""
        event = self._event(date(2026, 5, 14))
        for i in range(3):
            user = User.objects.create_user(
                username=f"gone{i}@crush.lu", email=f"gone{i}@crush.lu", password="x"
            )
            self._paid_seat(event, user).delete()

        report = self._report(date(2026, 5, 1), date(2026, 6, 1))
        self.assertEqual(report["event_activity"]["paid_registrations"], 3)

    def test_deleted_account_still_counts_in_its_own_month(self):
        may_event = self._event(date(2026, 5, 14), title="May Event")
        june_event = self._event(date(2026, 6, 11), title="June Event")
        self._paid_seat(may_event, self.member).delete()
        other = User.objects.create_user(
            username="other@crush.lu", email="other@crush.lu", password="x"
        )
        self._paid_seat(june_event, other)

        monthly = self._monthly(date(2026, 5, 1), date(2026, 7, 1))
        self.assertEqual(monthly["2026-05"]["paid_reg"], 1)
        self.assertEqual(monthly["2026-06"]["paid_reg"], 1)

    def test_reused_seat_with_two_captures_stays_one_after_deletion(self):
        """The mirror of ``test_one_seat_with_several_attempts_counts_once``.

        A rebooking cycle leaves a card capture and a later cash capture on
        one registration; the linked branch dedupes them by registration and
        reports one seat. Deleting the member orphans both rows, and counting
        orphans as transactions would make that seat silently become two —
        the report jumping up on a departure rather than staying put.
        """
        event = self._event(date(2026, 5, 14))
        registration = self._paid_seat(event, self.member)
        self._tx(
            registration,
            provider=PaymentTransaction.Provider.MANUAL,
            reference=f"CRUSH-EVT-{registration.pk}-cash",
        )

        before = self._report(date(2026, 5, 1), date(2026, 6, 1))
        self.assertEqual(before["event_activity"]["paid_registrations"], 1)

        registration.delete()

        after = self._report(date(2026, 5, 1), date(2026, 6, 1))
        self.assertEqual(after["event_activity"]["paid_registrations"], 1)

    def test_two_departed_members_on_one_event_still_count_separately(self):
        """Deduping orphans by (event, member) must not over-merge: two
        different people who both left are two seats, not one."""
        event = self._event(date(2026, 5, 14))
        for i in range(2):
            user = User.objects.create_user(
                username=f"left{i}@crush.lu", email=f"left{i}@crush.lu", password="x"
            )
            registration = self._paid_seat(event, user)
            self._tx(
                registration,
                provider=PaymentTransaction.Provider.MANUAL,
                reference=f"CRUSH-EVT-{registration.pk}-cash",
            )
            registration.delete()

        report = self._report(date(2026, 5, 1), date(2026, 6, 1))
        self.assertEqual(report["event_activity"]["paid_registrations"], 2)

    def test_staff_opened_checkouts_do_not_collapse_after_deletion(self):
        """Seat identity cannot be the payer.

        `create_sumup_event_checkout` lets staff open a card checkout on a
        member's behalf and stores the *staff* requester in
        `PaymentTransaction.user` (the repo pins this in
        `StaffOpenedPaymentTransactionAdminTests`). Keying orphaned captures on
        the user would report a whole event's staff-sold seats as one the
        moment those members left.
        """
        event = self._event(date(2026, 5, 14))
        staff = User.objects.create_user(
            username="staff@crush.lu", email="staff@crush.lu", password="x",
            is_staff=True,
        )
        for i in range(3):
            buyer = User.objects.create_user(
                username=f"buyer{i}@crush.lu", email=f"buyer{i}@crush.lu", password="x"
            )
            registration = EventRegistration.objects.create(
                event=event, user=buyer, status="confirmed",
                payment_confirmed=True, payment_date=timezone.now(),
            )
            tx = self._tx(
                registration,
                reference=f"CRUSH-EVT-{registration.pk}-{uuid.uuid4().hex[:6]}",
            )
            # Staff opened it: payer is staff, buyer is the member.
            tx.user = staff
            tx.save(update_fields=["user"])
            registration.delete()

        report = self._report(date(2026, 5, 1), date(2026, 6, 1))
        self.assertEqual(report["event_activity"]["paid_registrations"], 3)
