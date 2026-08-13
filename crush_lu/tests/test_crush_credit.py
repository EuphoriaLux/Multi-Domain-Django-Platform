"""Crush Credit — issuing on cancellation, the resale clause, and redemption.

The policy these pin down (approved 2026-08-13, replacing the cash-refund
Terms §7.3):

* cancel more than 48h out, having paid  → 100% credit
* cancel inside 48h                      → nothing, unless the seat is resold
  from the waitlist before the event starts, and then 50%
* no-show                                → nothing
* Crush.lu cancels the event             → a premium credit, flagged as still
                                           eligible for a cash refund on request

The load-bearing test in this file is
``ReRegistrationAfterCreditTests`` — Trap 1, the ``payment_confirmed``
double-dip. ``_admitted_status`` hands a "confirmed" seat to any registration
carrying ``payment_confirmed``, and re-registration reuses the same row, so a
member credited for a seat they still appear to have paid for would hold the
money's worth AND walk back into the same event free.

Run with: pytest crush_lu/tests/test_crush_credit.py -v
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models.credits import CreditRedemption, CrushCredit, add_months
from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushProfile, UserDataConsent
from crush_lu.services.credits import (
    available_credit_cents,
    credit_paid_registrations_for_cancelled_event,
    issue_credit,
    redeem_for_registration,
)

User = get_user_model()

FEE = Decimal("15.50")
FEE_CENTS = 1550


class CreditFixture(TestCase):
    """One paid event, one member holding a paid seat."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "crush.lu", "name": "Crush.lu"}
        )

    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = self._user("member@crush.lu")
        self.event = self._event()
        self.registration = self._paid_registration(self.event, self.user)

    # -- builders ---------------------------------------------------------

    def _user(self, email, gender="F"):
        user = User.objects.create_user(
            username=email, email=email, password="password123"
        )
        UserDataConsent.objects.update_or_create(
            user=user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender=gender,
            location="Luxembourg",
            verification_status="verified",
            completion_status="step4",
        )
        return user

    def _event(self, *, hours_away=72, fee=FEE, max_participants=1, **overrides):
        kwargs = dict(
            title="Speed Dating Luxembourg",
            description="The first paid week",
            event_type="speed_dating",
            location="Luxembourg City",
            address="10 Grand Rue",
            date_time=timezone.now() + timedelta(hours=hours_away),
            registration_deadline=timezone.now() + timedelta(hours=hours_away - 1),
            registration_fee=fee,
            max_participants=max_participants,
            is_published=True,
        )
        kwargs.update(overrides)
        return MeetupEvent.objects.create(**kwargs)

    def _registration(self, event, user, status="pending"):
        return EventRegistration.objects.create(event=event, user=user, status=status)

    def _paid_registration(self, event, user, *, amount=None):
        """A confirmed seat with the captured payment behind it."""
        registration = EventRegistration.objects.create(
            event=event,
            user=user,
            status="confirmed",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )
        PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-EVT-{registration.pk}-testref",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=f"CHK{registration.pk}",
            amount=amount if amount is not None else event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=user,
            event_registration=registration,
        )
        return registration

    def _cancel(self, user, event):
        """POST the member-facing cancel view, running its on_commit hooks.

        ``captureOnCommitCallbacks`` matters here: the signal path promotes
        on commit, and ``TestCase`` wraps every test in a transaction that
        never commits, so without it half of the promotion behaviour under
        test would never run at all.
        """
        self.client.force_login(user)
        url = reverse("crush_lu:event_cancel", kwargs={"event_id": event.pk})
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url)

    def _credits(self, user):
        return CrushCredit.objects.filter(user=user).order_by("id")


# ---------------------------------------------------------------------------
# 1 + 6: issuing on a member cancellation
# ---------------------------------------------------------------------------


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CancellationCreditTests(CreditFixture):
    def test_cancel_more_than_48h_out_issues_full_credit(self):
        """AC1: 100% credit, and ``payment_confirmed`` is released with it."""
        self._cancel(self.user, self.event)

        credits = list(self._credits(self.user))
        self.assertEqual(len(credits), 1)
        credit = credits[0]
        self.assertEqual(credit.amount_cents, FEE_CENTS)
        self.assertEqual(credit.reason, CrushCredit.Reason.MEMBER_CANCELLATION)
        self.assertEqual(credit.currency, "EUR")
        self.assertEqual(credit.status, CrushCredit.Status.ACTIVE)
        self.assertEqual(credit.source_registration_id, self.registration.pk)
        self.assertIsNotNone(credit.source_payment_id)
        self.assertFalse(credit.cash_refund_eligible)

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "cancelled")
        # Trap 1. Without this the member holds the credit AND is re-admitted
        # to the same event free.
        self.assertFalse(self.registration.payment_confirmed)
        self.assertIsNone(self.registration.payment_date)

    def test_credit_expires_six_calendar_months_out(self):
        self._cancel(self.user, self.event)
        credit = self._credits(self.user).get()
        self.assertEqual(credit.expires_at, add_months(credit.issued_at, 6))

    def test_credit_is_valued_at_what_was_paid_not_the_current_fee(self):
        """The fee is admin-editable; the credit is what the member paid."""
        PaymentTransaction.objects.filter(event_registration=self.registration).update(
            amount=Decimal("12.00")
        )
        self.event.registration_fee = Decimal("25.00")
        self.event.save(update_fields=["registration_fee"])

        self._cancel(self.user, self.event)
        self.assertEqual(self._credits(self.user).get().amount_cents, 1200)

    def test_cancel_inside_48h_issues_nothing_at_cancellation_time(self):
        """AC3: nothing up front — only the resale clause can pay out now."""
        event = self._event(hours_away=30)
        registration = self._paid_registration(event, self.user)

        self._cancel(self.user, event)

        self.assertEqual(self._credits(self.user).count(), 0)
        registration.refresh_from_db()
        self.assertEqual(registration.status, "cancelled")
        # Still ours: nothing has been given back, so the money is still
        # consideration for that seat. This is also the state the resale
        # clause reads later.
        self.assertTrue(registration.payment_confirmed)

    def test_unpaid_cancellation_is_unchanged(self):
        """AC6: a free/unpaid seat cancels exactly as it always did."""
        event = self._event()
        registration = self._registration(event, self.user, status="pending")

        response = self._cancel(self.user, event)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._credits(self.user).count(), 0)
        registration.refresh_from_db()
        self.assertEqual(registration.status, "cancelled")

    def test_free_event_issues_no_credit_even_if_flagged_paid(self):
        """A €0.00 seat has no value to give back."""
        event = self._event(fee=Decimal("0.00"))
        registration = EventRegistration.objects.create(
            event=event,
            user=self.user,
            status="confirmed",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )

        self._cancel(self.user, event)

        self.assertEqual(self._credits(self.user).count(), 0)
        registration.refresh_from_db()
        self.assertEqual(registration.status, "cancelled")

    def test_cancelling_twice_issues_one_credit(self):
        self._cancel(self.user, self.event)
        self._cancel(self.user, self.event)
        self.assertEqual(self._credits(self.user).count(), 1)


# ---------------------------------------------------------------------------
# 2: Trap 1 — the payment_confirmed double-dip
# ---------------------------------------------------------------------------


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ReRegistrationAfterCreditTests(CreditFixture):
    """AC2. The expensive one.

    ``_admitted_status`` returns "confirmed" for any registration with
    ``payment_confirmed=True``, and re-registration reuses the same row. If
    issuing credit did not clear the flag, a member could take €15.50 of credit
    and immediately re-register for the very same event without paying — and so
    could anyone promoted off the waitlist, because promotion admits through
    the same function.
    """

    def test_admitted_status_charges_again_after_credit(self):
        from crush_lu.views_events import _admitted_status

        self._cancel(self.user, self.event)
        self.registration.refresh_from_db()

        self.assertEqual(
            _admitted_status(self.event, self.registration),
            "pending",
            "a credited registration must be asked to pay again, not admitted "
            "confirmed for free",
        )

    def test_re_registering_for_the_same_event_is_charged(self):
        self._cancel(self.user, self.event)
        self.assertEqual(self._credits(self.user).get().amount_cents, FEE_CENTS)

        self.client.force_login(self.user)
        url = reverse("crush_lu:event_register", kwargs={"event_id": self.event.pk})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {"confirm_registration": "on"})

        self.registration.refresh_from_db()
        self.assertNotEqual(
            self.registration.status,
            "confirmed",
            "re-registration after a credit must not hand back a paid seat",
        )
        self.assertFalse(self.registration.payment_confirmed)

    def test_credit_is_not_double_spent_by_re_registering(self):
        """The seat comes back only by spending the credit that was issued."""
        self._cancel(self.user, self.event)
        self.registration.refresh_from_db()
        self.registration.status = "pending"
        self.registration.save(update_fields=["status"])

        self.assertEqual(available_credit_cents(self.user), FEE_CENTS)
        redeem_for_registration(self.user, self.registration, FEE_CENTS)
        self.assertEqual(available_credit_cents(self.user), 0)


# ---------------------------------------------------------------------------
# 4 + 5: the resale clause
# ---------------------------------------------------------------------------


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ResaleClauseTests(CreditFixture):
    def setUp(self):
        super().setUp()
        # A late-running event with one seat, held by the paid member.
        self.event = self._event(hours_away=30, max_participants=1)
        self.registration = self._paid_registration(self.event, self.user)

    def test_late_cancellation_whose_seat_is_resold_earns_fifty_percent(self):
        """AC4: exactly one 50% credit when the waitlist takes the seat."""
        waiter = self._user("waiter@crush.lu", gender="F")
        waitlisted = self._registration(self.event, waiter, status="waitlist")

        self._cancel(self.user, self.event)

        waitlisted.refresh_from_db()
        self.assertIn(waitlisted.status, ("pending", "confirmed"))

        credits = list(self._credits(self.user))
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0].amount_cents, FEE_CENTS // 2)
        self.assertEqual(credits[0].reason, CrushCredit.Reason.SEAT_RESOLD)
        # Same release as the 100% path: the member has been paid something
        # back, so the seat must not still read as paid for.
        self.registration.refresh_from_db()
        self.assertFalse(self.registration.payment_confirmed)

    def test_a_second_promotion_does_not_issue_a_second_credit(self):
        """AC4, second half. At most once per cancelled seat."""
        from crush_lu.services.credits import maybe_issue_resale_credit

        waiter = self._user("waiter@crush.lu", gender="F")
        self._registration(self.event, waiter, status="waitlist")
        self._cancel(self.user, self.event)
        self.assertEqual(self._credits(self.user).count(), 1)

        # Re-run the hook against another promoted seat, as a second
        # cancellation on the same event would.
        other = self._user("other@crush.lu", gender="F")
        promoted_again = self._registration(self.event, other, status="confirmed")
        self.registration.refresh_from_db()
        maybe_issue_resale_credit(self.registration, promoted_again)

        self.assertEqual(self._credits(self.user).count(), 1)

    def test_empty_waitlist_earns_nothing(self):
        """AC5, first half: no waitlist, no promotion, no credit."""
        self._cancel(self.user, self.event)

        self.assertEqual(self._credits(self.user).count(), 0)
        self.registration.refresh_from_db()
        self.assertTrue(self.registration.payment_confirmed)

    def test_gender_blocked_waitlist_earns_nothing(self):
        """AC5, second half.

        A gender-capped event whose whole waitlist sits in a pool that is
        already at its ceiling cannot promote anybody. The seat is genuinely
        not resold, so no credit is due — this is correct behaviour, not a
        defect (see the 2026-08-11 event 14 worked example).
        """
        event = self._event(
            hours_away=30,
            max_participants=2,
            max_participants_m=1,
            max_participants_f=1,
            max_participants_nb=0,
        )
        self.assertTrue(event.gender_limits_active)

        female_holder = self._paid_registration(event, self.user)
        male_user = self._user("male-holder@crush.lu", gender="M")
        self._registration(event, male_user, status="confirmed")

        # The whole waitlist is male, and the male pool is already full.
        blocked = self._user("male-waiter@crush.lu", gender="M")
        blocked_reg = self._registration(event, blocked, status="waitlist")

        self._cancel(self.user, event)

        blocked_reg.refresh_from_db()
        self.assertEqual(blocked_reg.status, "waitlist")
        self.assertEqual(self._credits(self.user).count(), 0)
        female_holder.refresh_from_db()
        self.assertTrue(female_holder.payment_confirmed)

    def test_promotion_after_the_event_started_earns_nothing(self):
        """ "Before the event starts" is part of the clause."""
        from crush_lu.services.credits import maybe_issue_resale_credit

        self.registration.status = "cancelled"
        self.registration.save(update_fields=["status"])
        MeetupEvent.objects.filter(pk=self.event.pk).update(
            date_time=timezone.now() - timedelta(minutes=5)
        )
        self.registration.refresh_from_db()

        other = self._user("late@crush.lu")
        promoted = self._registration(self.event, other, status="confirmed")

        self.assertIsNone(maybe_issue_resale_credit(self.registration, promoted))
        self.assertEqual(self._credits(self.user).count(), 0)

    def test_a_full_credit_cancellation_cannot_also_earn_the_resale_share(self):
        """A >48h cancellation is settled in full; the seat cannot pay twice."""
        from crush_lu.services.credits import maybe_issue_resale_credit

        early_event = self._event(hours_away=72, max_participants=1)
        early_reg = self._paid_registration(early_event, self.user)
        waiter = self._user("early-waiter@crush.lu")
        self._registration(early_event, waiter, status="waitlist")

        self._cancel(self.user, early_event)

        credits = list(self._credits(self.user))
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0].reason, CrushCredit.Reason.MEMBER_CANCELLATION)

        early_reg.refresh_from_db()
        promoted = self._registration(
            early_event, self._user("second@crush.lu"), status="confirmed"
        )
        self.assertIsNone(maybe_issue_resale_credit(early_reg, promoted))
        self.assertEqual(self._credits(self.user).count(), 1)

    def test_signal_path_credits_an_admin_driven_late_cancellation(self):
        """Promotion also fires from the admin/shell, and the clause holds."""
        waiter = self._user("shell-waiter@crush.lu", gender="F")
        waitlisted = self._registration(self.event, waiter, status="waitlist")

        with self.captureOnCommitCallbacks(execute=True):
            self.registration.status = "cancelled"
            self.registration.save()

        waitlisted.refresh_from_db()
        self.assertIn(waitlisted.status, ("pending", "confirmed"))
        credits = list(self._credits(self.user))
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0].reason, CrushCredit.Reason.SEAT_RESOLD)


# ---------------------------------------------------------------------------
# 7 + 8 + 9: redemption at checkout
# ---------------------------------------------------------------------------


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CreditCheckoutTests(CreditFixture):
    def setUp(self):
        super().setUp()
        self.buyer = self._user("buyer@crush.lu")
        self.target = self._event(hours_away=96, max_participants=10)
        self.seat = self._registration(self.target, self.buyer, status="pending")
        self.url = reverse(
            "sumup_create_event_checkout", kwargs={"registration_id": self.seat.pk}
        )

    def _grant(self, cents, **kwargs):
        return issue_credit(self.buyer, cents, CrushCredit.Reason.GOODWILL, **kwargs)

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_sufficient_credit_skips_sumup_entirely(self, mock_create_checkout):
        """AC7. No provider call, seat confirmed, ledger balanced."""
        self._grant(FEE_CENTS)
        self.client.force_login(self.buyer)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["paid_with_credit"])
        mock_create_checkout.assert_not_called()

        tx = PaymentTransaction.objects.get(event_registration=self.seat)
        self.assertEqual(tx.provider, PaymentTransaction.Provider.CREDIT)
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(tx.purpose, PaymentTransaction.Purpose.EVENT_REGISTRATION)
        self.assertEqual(tx.amount, FEE)
        # No SumUp identifiers: sumup_checkout_status and the reconciliation
        # sweep must never go hunting a checkout that was never opened.
        self.assertEqual(tx.sumup_checkout_id, "")
        self.assertTrue(tx.transaction_reference.startswith("CRUSH-CREDIT-"))

        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "confirmed")
        self.assertTrue(self.seat.payment_confirmed)
        self.assertTrue(self.seat.checkin_token, "a paid seat must be scannable")

        redemptions = CreditRedemption.objects.filter(event_registration=self.seat)
        self.assertEqual(
            sum(r.amount_cents for r in redemptions),
            FEE_CENTS,
            "redemptions must add up to exactly the price",
        )
        self.assertEqual(available_credit_cents(self.buyer), 0)

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_insufficient_credit_charges_the_full_price(self, mock_create_checkout):
        """AC8. v1 is whole-seat-or-nothing: no split credit/card payment."""
        mock_create_checkout.return_value = {"id": "CHK_PART", "status": "PENDING"}
        self._grant(FEE_CENTS - 1)
        self.client.force_login(self.buyer)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checkout_id"], "CHK_PART")
        mock_create_checkout.assert_called_once()
        self.assertEqual(mock_create_checkout.call_args.kwargs["amount"], float(FEE))

        self.assertFalse(CreditRedemption.objects.exists())
        self.assertEqual(
            available_credit_cents(self.buyer),
            FEE_CENTS - 1,
            "a credit that cannot cover the seat must be left untouched",
        )

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_expired_credit_cannot_be_redeemed(self, mock_create_checkout):
        """AC9. The clock is the authority, not the status flag."""
        mock_create_checkout.return_value = {"id": "CHK_EXP", "status": "PENDING"}
        stale = self._grant(FEE_CENTS)
        CrushCredit.objects.filter(pk=stale.pk).update(
            issued_at=timezone.now() - timedelta(days=400),
            expires_at=timezone.now() - timedelta(days=220),
        )

        self.assertEqual(available_credit_cents(self.buyer), 0)

        self.client.force_login(self.buyer)
        response = self.client.post(self.url)

        self.assertEqual(response.json()["checkout_id"], "CHK_EXP")
        mock_create_checkout.assert_called_once()
        self.assertFalse(CreditRedemption.objects.exists())

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_one_credit_is_spent_across_several_seats(self, mock_create_checkout):
        """The €20 event-cancellation premium must not be worth €20 forever.

        Partial consumption of a *single* credit is not split payment — it is
        one credit spanning purchases, and the whole ledger formula depends on
        it.
        """
        self._grant(2000)
        self.client.force_login(self.buyer)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url)

        self.assertEqual(available_credit_cents(self.buyer), 2000 - FEE_CENTS)
        credit = CrushCredit.objects.get(user=self.buyer)
        self.assertEqual(credit.remaining_cents, 450)
        self.assertEqual(
            credit.status,
            CrushCredit.Status.ACTIVE,
            "a partly spent credit stays spendable",
        )
        mock_create_checkout.assert_not_called()

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_credit_is_spent_oldest_expiry_first(self, mock_create_checkout):
        soon = self._grant(1000)
        CrushCredit.objects.filter(pk=soon.pk).update(
            expires_at=timezone.now() + timedelta(days=5)
        )
        later = self._grant(1000)
        CrushCredit.objects.filter(pk=later.pk).update(
            expires_at=timezone.now() + timedelta(days=200)
        )
        self.client.force_login(self.buyer)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url)

        self.assertEqual(
            CreditRedemption.objects.get(credit_id=soon.pk).amount_cents,
            1000,
            "the soonest-expiring credit must be spent first, and in full",
        )
        self.assertEqual(
            CreditRedemption.objects.get(credit_id=later.pk).amount_cents, 550
        )
        soon.refresh_from_db()
        self.assertEqual(soon.status, CrushCredit.Status.CONSUMED)
        mock_create_checkout.assert_not_called()

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_a_fully_consumed_credit_is_not_offered_again(self, mock_create_checkout):
        mock_create_checkout.return_value = {"id": "CHK_AGAIN", "status": "PENDING"}
        self._grant(FEE_CENTS)
        self.client.force_login(self.buyer)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(self.url)

        second_seat = self._registration(
            self._event(hours_away=120, max_participants=10),
            self.buyer,
            status="pending",
        )
        response = self.client.post(
            reverse(
                "sumup_create_event_checkout",
                kwargs={"registration_id": second_seat.pk},
            )
        )

        self.assertEqual(response.json()["checkout_id"], "CHK_AGAIN")
        mock_create_checkout.assert_called_once()


# ---------------------------------------------------------------------------
# 10: Crush.lu cancels the event
# ---------------------------------------------------------------------------


class EventCancelledCreditTests(CreditFixture):
    def test_every_paid_seat_gets_premium_credit_and_the_refund_flag(self):
        """AC10. Premium value, plus the cash-refund entitlement recorded."""
        event = self._event(hours_away=100, max_participants=10)
        paid_a = self._paid_registration(event, self._user("a@crush.lu"))
        paid_b = self._paid_registration(event, self._user("b@crush.lu"))
        unpaid = self._registration(event, self._user("c@crush.lu"), status="pending")

        issued = credit_paid_registrations_for_cancelled_event(event)

        self.assertEqual(len(issued), 2)
        for credit in issued:
            self.assertEqual(credit.amount_cents, 2000)
            self.assertEqual(credit.reason, CrushCredit.Reason.EVENT_CANCELLED)
            self.assertTrue(
                credit.cash_refund_eligible,
                "when the organiser cancels, a cash refund on request is not "
                "optional under Luxembourg consumer guidance",
            )

        for registration in (paid_a, paid_b):
            registration.refresh_from_db()
            self.assertFalse(registration.payment_confirmed)

        self.assertFalse(
            CrushCredit.objects.filter(source_registration=unpaid).exists(),
            "an unpaid seat is owed nothing",
        )

    def test_running_it_twice_credits_nobody_twice(self):
        event = self._event(hours_away=100, max_participants=10)
        self._paid_registration(event, self._user("a@crush.lu"))

        credit_paid_registrations_for_cancelled_event(event)
        again = credit_paid_registrations_for_cancelled_event(event)

        self.assertEqual(again, [])
        self.assertEqual(CrushCredit.objects.count(), 1)

    @override_settings(CRUSH_CREDIT_EVENT_CANCELLED_PREMIUM_CENTS=3000)
    def test_the_premium_is_configurable(self):
        event = self._event(hours_away=100, max_participants=10)
        self._paid_registration(event, self._user("a@crush.lu"))

        issued = credit_paid_registrations_for_cancelled_event(event)

        self.assertEqual(issued[0].amount_cents, 3000)


# ---------------------------------------------------------------------------
# The ledger itself
# ---------------------------------------------------------------------------


class LedgerTests(CreditFixture):
    def test_balance_is_derived_never_stored(self):
        """No model in this feature may carry a balance field."""
        for model in (CrushCredit, CreditRedemption, EventRegistration, CrushProfile):
            names = {f.name for f in model._meta.get_fields() if hasattr(f, "name")}
            self.assertFalse(
                {"balance", "credit_balance", "balance_cents"} & names,
                f"{model.__name__} must not store a balance — it drifts",
            )

    def test_void_and_expired_credit_is_not_spendable(self):
        active = issue_credit(self.user, 1000, CrushCredit.Reason.GOODWILL)
        voided = issue_credit(self.user, 5000, CrushCredit.Reason.GOODWILL)
        CrushCredit.objects.filter(pk=voided.pk).update(status=CrushCredit.Status.VOID)
        stale = issue_credit(self.user, 7000, CrushCredit.Reason.GOODWILL)
        CrushCredit.objects.filter(pk=stale.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )

        self.assertEqual(available_credit_cents(self.user), active.amount_cents)

    def test_zero_value_credit_is_not_issued(self):
        self.assertIsNone(issue_credit(self.user, 0, CrushCredit.Reason.GOODWILL))
        self.assertEqual(CrushCredit.objects.count(), 0)

    def test_goodwill_credit_does_not_strip_a_paid_seat(self):
        """Goodwill is *additional* money, not a return of a payment.

        Releasing ``payment_confirmed`` here would be the mirror of Trap 1:
        taking a seat away from someone whose money we still hold.
        """
        issue_credit(
            self.user,
            1000,
            CrushCredit.Reason.GOODWILL,
            registration=self.registration,
        )

        self.registration.refresh_from_db()
        self.assertTrue(self.registration.payment_confirmed)
        self.assertIsNotNone(self.registration.payment_date)

    def test_redemption_refuses_to_partially_spend_a_short_balance(self):
        issue_credit(self.user, 1000, CrushCredit.Reason.GOODWILL)

        self.assertIsNone(
            redeem_for_registration(self.user, self.registration, FEE_CENTS)
        )
        self.assertFalse(CreditRedemption.objects.exists())
        self.assertEqual(available_credit_cents(self.user), 1000)

    def test_add_months_clamps_to_a_real_day(self):
        from datetime import datetime, timezone as dt_timezone

        issued = datetime(2026, 8, 31, 12, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(add_months(issued, 6).date(), date(2027, 2, 28))

    def test_a_credit_may_state_its_own_expiry(self):
        stated = timezone.now() + timedelta(days=3)
        credit = CrushCredit.objects.create(
            user=self.user,
            amount_cents=500,
            reason=CrushCredit.Reason.GOODWILL,
            expires_at=stated,
        )
        self.assertEqual(credit.expires_at, stated)


class CreditRowsStayOutOfSumUpTests(CreditFixture):
    """A credit payment must never send anyone looking for a checkout.

    ``sumup_checkout_status`` and the reconciliation sweep both walk
    ``PaymentTransaction`` rows. A credit payment has no checkout at SumUp and
    never did, so a SumUp-shaped row would have an operator — or ``--sync`` —
    chasing an id that was never issued.
    """

    def _credit_tx(self):
        return PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-CREDIT-{self.registration.pk}-abc123",
            provider=PaymentTransaction.Provider.CREDIT,
            sumup_checkout_id="",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

    @patch("crush_lu.services.sumup.SumUpClient.get_checkout")
    def test_status_command_never_asks_sumup_about_a_credit_row(self, mock_get):
        from io import StringIO

        from django.core.management import call_command

        tx = self._credit_tx()
        out = StringIO()
        call_command(
            "sumup_checkout_status", reference=tx.transaction_reference, stdout=out
        )

        mock_get.assert_not_called()
        self.assertIn("Crush Credit", out.getvalue())

    def test_the_admin_recheck_action_skips_credit_rows(self):
        """It filters on ``sumup_checkout_id``, which a credit row leaves empty."""
        tx = self._credit_tx()
        self.assertEqual(tx.sumup_checkout_id, "")


class CreditLockOrderTests(TestCase):
    """Trap 2, pinned structurally because it cannot be pinned at runtime.

    SQLite ignores ``select_for_update`` both locally and in CI, so no test can
    provoke the deadlock. What can be checked is the source: the credit path
    must take ``PaymentTransaction`` before ``EventRegistration`` like every
    other money path, and ``CrushCredit`` last of all.
    """

    def _lock_sequence(self, func):
        import inspect
        import re

        src = inspect.getsource(func)
        return re.findall(r"(\w+)\.objects\s*\.?\s*select_for_update", src)

    def test_checkout_still_locks_transaction_before_registration(self):
        from crush_lu.views_payments import create_sumup_event_checkout

        seq = self._lock_sequence(create_sumup_event_checkout)
        self.assertEqual(seq[:2], ["PaymentTransaction", "EventRegistration"])

    def test_redemption_locks_only_crush_credit(self):
        from crush_lu.services.credits import redeem_for_registration

        self.assertEqual(
            self._lock_sequence(redeem_for_registration),
            ["CrushCredit"],
            "redemption runs with the payment and registration locks already "
            "held; taking either of them again here would invert the order",
        )

    def test_the_credit_service_never_locks_payments_or_profiles(self):
        import inspect

        from crush_lu.services import credits

        src = inspect.getsource(credits)
        for forbidden in (
            "PaymentTransaction.objects.select_for_update",
            "CrushProfile.objects.select_for_update",
        ):
            self.assertNotIn(
                forbidden,
                src,
                "event_cancel calls in here holding MeetupEvent and "
                "EventRegistration; locking a payment at that point is the "
                "ABBA the checkout path is commented to death about",
            )
