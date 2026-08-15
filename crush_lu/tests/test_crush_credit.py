"""Crush Credit — issuing on cancellation, the resale clause, and redemption.

The policy these pin down (approved 2026-08-13, replacing the cash-refund
Terms §7.3):

* cancel more than 48h out, having paid  → 100% credit
* cancel inside 48h                      → nothing, unless a waitlist replacement
                                           pays before the event starts, then 50%
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

import json
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import PermissionDenied
from django.db.models.deletion import ProtectedError
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
    funding_tranches,
    issue_cancellation_credits,
    issue_credit,
    maybe_issue_resale_credits,
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

    def _pay_replacement(self, registration):
        from crush_lu.views_payments import _apply_paid_checkout

        tx = PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-EVT-replacement-{registration.pk}",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=f"CHK-REPLACEMENT-{registration.pk}",
            amount=registration.event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
        )
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})


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
        # The POSITIVE transition, not just "not confirmed" — a re-registration
        # that silently failed would also leave the row 'cancelled' and pass a
        # bare not-equal assertion, so the most expensive trap in this feature
        # would be guarded by a test that never re-registered anybody.
        self.assertEqual(
            self.registration.status,
            "pending",
            "the cancelled row must be reused and asked to pay again",
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

    def _pay_replacement(self, registration):
        from crush_lu.views_payments import _apply_paid_checkout

        tx = PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-EVT-replacement-{registration.pk}",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=f"CHK-REPLACEMENT-{registration.pk}",
            amount=self.event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
        )
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})

    def test_late_cancellation_whose_seat_is_resold_earns_fifty_percent(self):
        """AC4: exactly one 50% credit when the waitlist takes the seat."""
        waiter = self._user("waiter@crush.lu", gender="F")
        waitlisted = self._registration(self.event, waiter, status="waitlist")

        self._cancel(self.user, self.event)

        waitlisted.refresh_from_db()
        self.assertIn(waitlisted.status, ("pending", "confirmed"))

        self.assertEqual(self._credits(self.user).count(), 0)
        self._pay_replacement(waitlisted)

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
        waiter = self._user("waiter@crush.lu", gender="F")
        self._registration(self.event, waiter, status="waitlist")
        self._cancel(self.user, self.event)
        promoted = EventRegistration.objects.get(event=self.event, user=waiter)
        self._pay_replacement(promoted)
        self.assertEqual(self._credits(self.user).count(), 1)

        # Re-run the hook against another promoted seat, as a second
        # cancellation on the same event would.
        other = self._user("other@crush.lu", gender="F")
        promoted_again = self._registration(self.event, other, status="confirmed")
        self.registration.refresh_from_db()
        maybe_issue_resale_credits(self.registration, promoted_again)

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
        self.registration.status = "cancelled"
        self.registration.save(update_fields=["status"])
        MeetupEvent.objects.filter(pk=self.event.pk).update(
            date_time=timezone.now() - timedelta(minutes=5)
        )
        self.registration.refresh_from_db()

        other = self._user("late@crush.lu")
        promoted = self._registration(self.event, other, status="confirmed")

        self.assertEqual(maybe_issue_resale_credits(self.registration, promoted), [])
        self.assertEqual(self._credits(self.user).count(), 0)

    def test_a_full_credit_cancellation_cannot_also_earn_the_resale_share(self):
        """A >48h cancellation is settled in full; the seat cannot pay twice."""
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
        self.assertEqual(maybe_issue_resale_credits(early_reg, promoted), [])
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
        self.assertEqual(self._credits(self.user).count(), 0)
        self.assertTrue(
            any(
                message.to == [self.user.email]
                and "Registration Cancelled" in message.subject
                and "If a replacement pays" in message.body
                for message in mail.outbox
            ),
            "staff-driven cancellation must explain the conditional resale remedy",
        )

        mail.outbox.clear()
        self._pay_replacement(waitlisted)
        credits = list(self._credits(self.user))
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0].reason, CrushCredit.Reason.SEAT_RESOLD)
        self.assertTrue(
            any(
                message.to == [self.user.email]
                and "We've added 7.75 EUR" in message.body
                for message in mail.outbox
            ),
            "the original attendee must hear when the promised share arrives",
        )


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

    def _post_credit(self, url=None):
        return self.client.post(
            url or self.url,
            data=json.dumps({"payment_method": "credit"}),
            content_type="application/json",
        )

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_sufficient_credit_skips_sumup_entirely(self, mock_create_checkout):
        """AC7. No provider call, seat confirmed, ledger balanced."""
        self._grant(FEE_CENTS)
        self.client.force_login(self.buyer)

        with self.captureOnCommitCallbacks(execute=True):
            response = self._post_credit()

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
    def test_insufficient_explicit_credit_does_not_fall_back_to_card(
        self, mock_create_checkout
    ):
        """An explicit credit choice never silently turns into a card charge."""
        self._grant(FEE_CENTS - 1)
        self.client.force_login(self.buyer)

        response = self._post_credit()

        self.assertEqual(response.status_code, 400)
        mock_create_checkout.assert_not_called()

        self.assertFalse(CreditRedemption.objects.exists())
        self.assertEqual(
            available_credit_cents(self.buyer),
            FEE_CENTS - 1,
            "a credit that cannot cover the seat must be left untouched",
        )

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_expired_credit_cannot_be_redeemed(self, mock_create_checkout):
        """AC9. The clock is the authority, not the status flag."""
        stale = self._grant(FEE_CENTS)
        CrushCredit.objects.filter(pk=stale.pk).update(
            issued_at=timezone.now() - timedelta(days=400),
            expires_at=timezone.now() - timedelta(days=220),
        )

        self.assertEqual(available_credit_cents(self.buyer), 0)

        self.client.force_login(self.buyer)
        response = self._post_credit()

        self.assertEqual(response.status_code, 400)
        mock_create_checkout.assert_not_called()
        self.assertFalse(CreditRedemption.objects.exists())

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_card_choice_leaves_available_credit_untouched(self, mock_create_checkout):
        mock_create_checkout.return_value = {"id": "CHK_CARD", "status": "PENDING"}
        self._grant(FEE_CENTS)
        self.client.force_login(self.buyer)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checkout_id"], "CHK_CARD")
        self.assertEqual(available_credit_cents(self.buyer), FEE_CENTS)
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
            self._post_credit()

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
            self._post_credit()

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
            self._post_credit()

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


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class LedgerIntegrityRegressionTests(CreditFixture):
    """Round 2 — everything the PR review found. Each test is one finding."""

    def _repay(self, registration, reference):
        """Simulate the member re-registering for the same event and paying.

        ``event_register`` reuses the cancelled row rather than making a new
        one, which is the whole reason these regressions exist.
        """
        registration.status = "confirmed"
        registration.payment_confirmed = True
        registration.payment_date = timezone.now()
        registration.save()
        return PaymentTransaction.objects.create(
            transaction_reference=reference,
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=reference,
            amount=registration.event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
        )

    def _settle_replacement(self, event, user, reference):
        from crush_lu.views_payments import _apply_paid_checkout

        replacement = EventRegistration.objects.get(event=event, user=user)
        tx = PaymentTransaction.objects.create(
            transaction_reference=reference,
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=reference,
            amount=event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=user,
            event_registration=replacement,
        )
        _apply_paid_checkout(tx, {"status": "PAID"})

    def test_a_second_pay_cancel_resell_cycle_is_credited_not_crashed(self):
        """Two payments, two resold seats, two 50% credits — and no 500.

        The original constraint keyed on (source_registration, reason), but
        ``event_register`` reuses the same row forever, so the second
        legitimate cycle collided and raised IntegrityError *inside*
        ``event_cancel``'s transaction — the member could not even cancel.
        """
        event = self._event(hours_away=30, max_participants=2)
        registration = self._paid_registration(event, self.user)

        first_waiter = self._user("w1@crush.lu", gender="F")
        self._registration(event, first_waiter, status="waitlist")
        self._cancel(self.user, event)
        self._settle_replacement(event, first_waiter, "CRUSH-EVT-replacement1")

        credits = list(self._credits(self.user))
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0].reason, CrushCredit.Reason.SEAT_RESOLD)

        # Second cycle: same row, brand new payment.
        registration.refresh_from_db()
        self._repay(registration, "CRUSH-EVT-cycle2")
        second_waiter = self._user("w2@crush.lu", gender="F")
        self._registration(event, second_waiter, status="waitlist")

        response = self._cancel(self.user, event)
        self._settle_replacement(event, second_waiter, "CRUSH-EVT-replacement2")

        self.assertEqual(
            response.status_code, 302, "the member must still be able to cancel"
        )
        resale = self._credits(self.user).filter(reason=CrushCredit.Reason.SEAT_RESOLD)
        self.assertEqual(
            resale.count(), 2, "a second paid seat that resold earns a second share"
        )

    def test_a_duplicate_resale_credit_for_one_payment_is_refused_quietly(self):
        """The constraint still guards the real case, and never propagates."""
        event = self._event(hours_away=30, max_participants=2)
        registration = self._paid_registration(event, self.user)
        promoted = self._registration(
            event, self._user("p@crush.lu"), status="confirmed"
        )
        promoted.payment_confirmed = True
        promoted.save(update_fields=["payment_confirmed"])
        registration.status = "cancelled"
        registration.save()

        first = maybe_issue_resale_credits(registration, promoted)
        self.assertTrue(first)

        # Force the guard open again without a new payment, as a concurrent
        # second promotion would.
        registration.payment_confirmed = True
        registration.save(update_fields=["payment_confirmed"])
        second = maybe_issue_resale_credits(registration, promoted)

        self.assertEqual(second, [], "one credit per captured payment")
        self.assertEqual(self._credits(self.user).count(), 1)

    def test_deleting_a_registration_does_not_refill_a_spent_credit(self):
        """CASCADE here silently handed the member their money back twice."""
        credit = issue_credit(self.user, 2000, CrushCredit.Reason.GOODWILL)
        seat = self._registration(
            self._event(hours_away=96, max_participants=5), self.user, status="pending"
        )
        redeem_for_registration(self.user, seat, FEE_CENTS)
        self.assertEqual(available_credit_cents(self.user), 450)

        seat.delete()

        self.assertEqual(
            CreditRedemption.objects.filter(credit=credit).count(),
            1,
            "a redemption must outlive the seat it was spent on",
        )
        self.assertEqual(available_credit_cents(self.user), 450)

    def test_account_merge_moves_credit_to_the_keeper(self):
        """Otherwise the merge quietly confiscates the member's balance."""
        from crush_lu.services.account_merge import merge_accounts

        duplicate = self._user("dupe@crush.lu")
        issue_credit(duplicate, 2000, CrushCredit.Reason.GOODWILL)
        keeper = self._user("keeper@crush.lu")

        merge_accounts(keeper, duplicate)

        self.assertEqual(available_credit_cents(keeper), 2000)
        self.assertEqual(available_credit_cents(duplicate), 0)

    def test_cancelling_a_credit_funded_seat_keeps_the_original_expiry(self):
        """Book-and-cancel must not be an unlimited expiry extension."""
        credit = issue_credit(self.user, FEE_CENTS, CrushCredit.Reason.GOODWILL)
        original_expiry = timezone.now() + timedelta(days=3)
        CrushCredit.objects.filter(pk=credit.pk).update(expires_at=original_expiry)

        event = self._event(hours_away=200, max_participants=5)
        seat = self._registration(event, self.user, status="pending")
        redeem_for_registration(self.user, seat, FEE_CENTS)
        seat.payment_confirmed = True
        seat.payment_date = timezone.now()
        seat.status = "confirmed"
        seat.save()
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-CREDIT-rollover",
            provider=PaymentTransaction.Provider.CREDIT,
            sumup_checkout_id="",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=seat,
            # The real checkout path always records which credits paid, and
            # that record is what carries the expiry forward — reading it off
            # the registration instead would pick up redemptions left behind by
            # any earlier booking of the same reused row.
            raw_response={
                "paid_with": "crush_credit",
                "redemptions": [{"credit_id": credit.pk, "amount_cents": FEE_CENTS}],
            },
        )

        self._cancel(self.user, event)

        reissued = self._credits(self.user).get(
            reason=CrushCredit.Reason.MEMBER_CANCELLATION
        )
        self.assertEqual(
            reissued.expires_at,
            original_expiry,
            "giving credit back must not restart its six months",
        )

    def test_a_credit_bought_seat_is_not_flagged_for_a_cash_refund(self):
        """Store credit must not become cash by way of a cancelled event."""
        event = self._event(hours_away=96, max_participants=5)
        seat = self._registration(event, self.user, status="confirmed")
        seat.payment_confirmed = True
        seat.save()
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-CREDIT-bought",
            provider=PaymentTransaction.Provider.CREDIT,
            sumup_checkout_id="",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=seat,
        )

        issued = credit_paid_registrations_for_cancelled_event(event)

        self.assertEqual(len(issued), 1)
        self.assertFalse(
            issued[0].cash_refund_eligible,
            "there is no SumUp capture behind this seat to refund",
        )

    def test_a_card_bought_seat_is_still_flagged_for_a_cash_refund(self):
        event = self._event(hours_away=96, max_participants=5)
        self._paid_registration(event, self._user("card@crush.lu"))

        issued = credit_paid_registrations_for_cancelled_event(event)

        self.assertTrue(issued[0].cash_refund_eligible)

    def test_the_event_cancellation_sweep_locks_each_registration(self):
        """Trap 2 territory: unlocked, two coaches double-credit one seat."""
        import inspect
        import re

        from crush_lu.services import credits

        src = inspect.getsource(credits.credit_paid_registrations_for_cancelled_event)
        self.assertTrue(
            re.search(r"EventRegistration\.objects\s*\.?\s*select_for_update", src),
            "each registration must be locked before it is credited",
        )


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CreditAdminSurfaceTests(CreditFixture):
    def test_partly_spent_credit_leaves_the_cash_refund_queue(self):
        """Otherwise staff refund cash on top of credit already spent."""
        from crush_lu.admin.credits import CashRefundQueueFilter

        credit = issue_credit(
            self.user,
            2000,
            CrushCredit.Reason.EVENT_CANCELLED,
            cash_refund_eligible=True,
        )
        # Django passes changelist params as lists; a bare string leaves
        # ``value()`` returning None and the filter silently becomes a no-op,
        # which would let this test pass without filtering anything.
        queue = CashRefundQueueFilter(
            None, {"refund_queue": ["open"]}, CrushCredit, None
        )
        self.assertEqual(queue.value(), "open")
        self.assertIn(credit, queue.queryset(None, CrushCredit.objects.all()))

        seat = self._registration(
            self._event(hours_away=96, max_participants=5), self.user, status="pending"
        )
        redeem_for_registration(self.user, seat, FEE_CENTS)

        self.assertNotIn(
            credit,
            queue.queryset(None, CrushCredit.objects.all()),
            "the member has taken the credit; the cash question is closed",
        )

    def test_void_withdraws_an_unspent_credit_but_refuses_a_spent_one(self):
        from crush_lu.admin import CrushCreditAdmin
        from crush_lu.admin.site import crush_admin_site

        unspent = issue_credit(self.user, 2000, CrushCredit.Reason.EVENT_CANCELLED)
        spent = issue_credit(self.user, 2000, CrushCredit.Reason.EVENT_CANCELLED)
        seat = self._registration(
            self._event(hours_away=96, max_participants=5), self.user, status="pending"
        )
        CreditRedemption.objects.create(
            credit=spent, event_registration=seat, amount_cents=500
        )

        admin_obj = CrushCreditAdmin(CrushCredit, crush_admin_site)
        staff = self._user("staff@crush.lu")
        staff.is_staff = True
        staff.is_superuser = True
        staff.save(update_fields=["is_staff", "is_superuser"])
        request = _admin_request(staff)
        admin_obj.void_credits(
            request, CrushCredit.objects.filter(pk__in=[unspent.pk, spent.pk])
        )

        unspent.refresh_from_db()
        spent.refresh_from_db()
        self.assertEqual(unspent.status, CrushCredit.Status.VOID)
        self.assertEqual(
            spent.status,
            CrushCredit.Status.ACTIVE,
            "value that has left the ledger cannot be taken back by a flag",
        )

    def test_goodwill_amount_converts_through_decimal(self):
        from crush_lu.admin.credits import GoodwillCreditForm

        form = GoodwillCreditForm(
            {"amount_eur": "20.15", "reason": "Door mix-up on 2026-08-12."}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            int(
                (form.cleaned_data["amount_eur"] * 100).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            ),
            2015,
        )

    def test_the_annotated_balance_matches_the_authoritative_read(self):
        from django.contrib.auth.models import User as AuthUser

        from crush_lu.admin.credits import annotate_credit_balance

        credit = issue_credit(self.user, 2000, CrushCredit.Reason.GOODWILL)
        seat = self._registration(
            self._event(hours_away=96, max_participants=5), self.user, status="pending"
        )
        # Two redemptions against one credit — the shape that makes a naive
        # join-and-group double-count.
        CreditRedemption.objects.create(
            credit=credit, event_registration=seat, amount_cents=300
        )
        CreditRedemption.objects.create(
            credit=credit, event_registration=seat, amount_cents=200
        )

        row = annotate_credit_balance(AuthUser.objects.filter(pk=self.user.pk)).get()
        derived = row._credit_issued - row._credit_redeemed

        self.assertEqual(derived, available_credit_cents(self.user))
        self.assertEqual(derived, 1500)


def _admin_request(user):
    """A request an admin action can call ``message_user`` on.

    ``message_user`` goes through the real messages framework, so the request
    needs a storage backend attached — a bare stub raises deep inside Django
    rather than in the test.
    """
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    request = RequestFactory().post("/crush-admin/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ReviewRoundThreeTests(CreditFixture):
    """Round 3 — the second pass of review findings."""

    def _credit_payment(self, registration, credit_ids):
        return PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-CREDIT-{registration.pk}-r3",
            provider=PaymentTransaction.Provider.CREDIT,
            sumup_checkout_id="",
            amount=registration.event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
            raw_response={
                "paid_with": "crush_credit",
                "redemptions": [
                    {"credit_id": cid, "amount_cents": FEE_CENTS} for cid in credit_ids
                ],
            },
        )

    @patch("crush_lu.views_payments.SumUpClient.deactivate_checkout")
    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_credit_is_not_spent_while_an_old_checkout_survives(
        self, mock_create_checkout, mock_deactivate
    ):
        """A checkout we could not kill may have just been paid.

        Spending credit then would confirm the seat, release the lock, and let
        the blocked webhook mark the card payment PAID — one seat, paid for in
        two currencies, only one of them refundable.
        """
        mock_deactivate.return_value = False
        mock_create_checkout.return_value = {"id": "CHK_SURVIVOR", "status": "PENDING"}
        buyer = self._user("survivor@crush.lu")
        target = self._event(hours_away=96, max_participants=10)
        seat = self._registration(target, buyer, status="pending")
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-old",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_OLD",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=buyer,
            event_registration=seat,
        )
        issue_credit(buyer, FEE_CENTS, CrushCredit.Reason.GOODWILL)
        self.client.force_login(buyer)

        response = self.client.post(
            reverse("sumup_create_event_checkout", kwargs={"registration_id": seat.pk})
        )

        self.assertEqual(response.status_code, 409)
        self.assertIsNone(response.json().get("checkout_id"))
        mock_create_checkout.assert_not_called()
        self.assertFalse(CreditRedemption.objects.exists())
        self.assertEqual(
            available_credit_cents(buyer),
            FEE_CENTS,
            "the balance must be untouched — that is the half reconciliation "
            "cannot repair afterwards",
        )

    def test_replacement_paid_before_late_original_capture_keeps_resale_claim(self):
        from crush_lu.views_payments import _apply_paid_checkout

        event = self._event(hours_away=30, max_participants=1)
        original = self._registration(event, self.user, status="pending")
        original_payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-original-pending",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-ORIGINAL-PENDING",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=original,
        )
        replacement_user = self._user("pending-capture-replacement@crush.lu")
        self._registration(event, replacement_user, status="waitlist")

        self._cancel(self.user, event)
        replacement = EventRegistration.objects.get(event=event, user=replacement_user)
        self.assertEqual(replacement.resale_source_payment_id, original_payment.pk)

        replacement_payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-replacement-first",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-REPLACEMENT-FIRST",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=replacement_user,
            event_registration=replacement,
        )
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(replacement_payment, {"status": "PAID"})
        replacement.refresh_from_db()
        self.assertEqual(
            replacement.resale_source_payment_id,
            original_payment.pk,
            "a replacement capture must retain the contingent claim while the "
            "original checkout is still pending",
        )

        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(original_payment, {"status": "PAID"})

        original.refresh_from_db()
        replacement.refresh_from_db()
        credit = self._credits(self.user).get()
        self.assertEqual(credit.reason, CrushCredit.Reason.SEAT_RESOLD)
        self.assertEqual(credit.amount_cents, FEE_CENTS // 2)
        self.assertFalse(original.payment_confirmed)
        self.assertIsNone(replacement.resale_source_payment_id)

    def test_stale_redemptions_on_a_reused_row_do_not_poison_a_card_credit(self):
        """``EventRegistration`` rows are reused; redemptions stay attached.

        A member who once bought this seat with credit, then re-registers and
        pays by CARD, must get a fresh six months — not the long-dead expiry of
        the credit they spent on the earlier booking.
        """
        event = self._event(hours_away=96, max_participants=5)
        seat = self._registration(event, self.user, status="pending")

        old_credit = issue_credit(self.user, FEE_CENTS, CrushCredit.Reason.GOODWILL)
        CrushCredit.objects.filter(pk=old_credit.pk).update(
            expires_at=timezone.now() - timedelta(days=30)
        )
        CreditRedemption.objects.create(
            credit=old_credit, event_registration=seat, amount_cents=FEE_CENTS
        )

        card_payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-reused-card",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_REUSED",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=seat,
        )

        self.assertEqual(
            funding_tranches(card_payment),
            [],
            "a card payment inherits nothing — real money went in",
        )

    def test_a_credit_payment_still_carries_its_expiry_forward(self):
        event = self._event(hours_away=96, max_participants=5)
        seat = self._registration(event, self.user, status="pending")
        credit = issue_credit(self.user, FEE_CENTS, CrushCredit.Reason.GOODWILL)
        soon = timezone.now() + timedelta(days=4)
        CrushCredit.objects.filter(pk=credit.pk).update(expires_at=soon)

        payment = self._credit_payment(seat, [credit.pk])

        self.assertEqual(funding_tranches(payment)[0][0].expires_at, soon)

    def test_a_member_cannot_cancel_into_a_worse_remedy_at_a_cancelled_event(self):
        """The organiser remedy is a premium plus cash; don't let it be lost."""
        event = self._event(hours_away=96, max_participants=5)
        registration = self._paid_registration(event, self.user)
        MeetupEvent.objects.filter(pk=event.pk).update(is_cancelled=True)

        self._cancel(self.user, event)

        registration.refresh_from_db()
        self.assertNotEqual(
            registration.status,
            "cancelled",
            "the seat must stay claimable by the event-cancellation sweep",
        )
        self.assertTrue(registration.payment_confirmed)
        self.assertEqual(self._credits(self.user).count(), 0)

        # ...and the sweep still finds and compensates them properly.
        issued = credit_paid_registrations_for_cancelled_event(event)
        self.assertEqual(len(issued), 1)
        self.assertEqual(issued[0].amount_cents, 2000)
        self.assertTrue(issued[0].cash_refund_eligible)

    @patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
    def test_deleting_a_crush_account_closes_out_the_credit(self, _delete_storage):
        """A banned account can never spend; leaving it active lies twice."""
        from crush_lu.views_account import delete_crushlu_profile_only

        credit = issue_credit(self.user, 2000, CrushCredit.Reason.GOODWILL)

        delete_crushlu_profile_only(self.user)

        credit.refresh_from_db()
        self.assertEqual(credit.status, CrushCredit.Status.VOID)
        self.assertIn("account deletion", credit.note)
        self.assertEqual(available_credit_cents(self.user), 0)

    @patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
    def test_account_deletion_preserves_an_unclaimed_cash_refund(self, _delete_storage):
        """Deleting a profile is not a waiver of an organiser cash remedy."""
        from crush_lu.views_account import delete_crushlu_profile_only

        credit = issue_credit(
            self.user,
            2000,
            CrushCredit.Reason.EVENT_CANCELLED,
            cash_refund_eligible=True,
        )

        delete_crushlu_profile_only(self.user)

        credit.refresh_from_db()
        self.assertEqual(credit.status, CrushCredit.Status.ACTIVE)
        self.assertTrue(credit.cash_refund_still_available)

    def test_the_data_export_includes_the_credit_ledger(self):
        """It promises all personal data, and this is money."""
        credit = issue_credit(
            self.user,
            2000,
            CrushCredit.Reason.EVENT_CANCELLED,
            cash_refund_eligible=True,
            note="internal coach note that must not be exported",
        )
        seat = self._registration(
            self._event(hours_away=96, max_participants=5), self.user, status="pending"
        )
        redeem_for_registration(self.user, seat, FEE_CENTS)

        self.client.force_login(self.user)
        response = self.client.get(reverse("crush_lu:export_user_data"))
        payload = json.loads(response.content)

        self.assertIn("crush_credit", payload)
        entry = payload["crush_credit"][0]
        self.assertEqual(entry["amount"], "20.00")
        self.assertEqual(entry["remaining"], "4.50")
        # False, not True: this credit has been partly spent, so the member has
        # taken the alternative and the cash offer is closed. Reporting the raw
        # issuance flag here told them otherwise — see
        # test_the_export_reports_effective_cash_availability.
        self.assertFalse(entry["cash_refund_available_on_request"])
        self.assertEqual(len(entry["spent_on"]), 1)
        self.assertEqual(entry["spent_on"][0]["amount"], "15.50")
        self.assertNotIn(
            "internal coach note",
            response.content.decode(),
            "the staff note is internal and can name other people",
        )
        self.assertEqual(credit.pk, CrushCredit.objects.get().pk)

    def test_a_late_canceller_is_compensated_when_the_event_is_then_cancelled(self):
        """The costs-are-committed argument dies with the evening.

        A late canceller keeps `payment_confirmed` on purpose so the resale
        clause can find them. An `.exclude(status="cancelled")` in the sweep
        therefore dropped exactly the people who had paid and received nothing.
        """
        event = self._event(hours_away=30, max_participants=5)
        late = self._paid_registration(event, self.user)
        self._cancel(self.user, event)

        late.refresh_from_db()
        self.assertEqual(late.status, "cancelled")
        self.assertTrue(late.payment_confirmed, "uncompensated, by construction")
        self.assertEqual(self._credits(self.user).count(), 0)

        issued = credit_paid_registrations_for_cancelled_event(event)

        self.assertEqual(len(issued), 1)
        self.assertEqual(issued[0].amount_cents, 2000)
        self.assertTrue(issued[0].cash_refund_eligible)

    def test_an_early_cancellation_from_the_admin_is_credited(self):
        """A coach cancelling a paid seat a week out compensates the member."""
        event = self._event(hours_away=200, max_participants=5)
        registration = self._paid_registration(event, self.user)

        with self.captureOnCommitCallbacks(execute=True):
            registration.status = "cancelled"
            registration.save()

        credits = list(self._credits(self.user))
        self.assertEqual(len(credits), 1)
        self.assertEqual(credits[0].reason, CrushCredit.Reason.MEMBER_CANCELLATION)
        self.assertEqual(credits[0].amount_cents, FEE_CENTS)
        registration.refresh_from_db()
        self.assertFalse(registration.payment_confirmed)
        self.assertTrue(
            any(
                message.to == [self.user.email]
                and "Registration Cancelled" in message.subject
                and "We've added 15.50 EUR" in message.body
                for message in mail.outbox
            )
        )

    def test_an_admin_cancellation_is_not_credited_twice(self):
        """The >48h payout and the resale share are mutually exclusive."""
        event = self._event(hours_away=200, max_participants=1)
        registration = self._paid_registration(event, self.user)
        self._registration(event, self._user("wait@crush.lu"), status="waitlist")

        with self.captureOnCommitCallbacks(execute=True):
            registration.status = "cancelled"
            registration.save()

        self.assertEqual(self._credits(self.user).count(), 1)

    def test_the_premium_never_credits_less_than_was_paid(self):
        """`registration_fee` is editable and uncapped; €20 is a floor."""
        event = self._event(hours_away=96, max_participants=5, fee=Decimal("30.00"))
        self._paid_registration(event, self._user("pricey@crush.lu"))

        issued = credit_paid_registrations_for_cancelled_event(event)

        self.assertEqual(
            issued[0].amount_cents,
            3000,
            "an organiser cancellation must never return less than was paid",
        )

    def test_the_export_reports_effective_cash_availability(self):
        """Not the issuance flag — the member must not be told a stale yes."""
        credit = issue_credit(
            self.user,
            2000,
            CrushCredit.Reason.EVENT_CANCELLED,
            cash_refund_eligible=True,
        )
        self.assertTrue(credit.cash_refund_still_available)

        seat = self._registration(
            self._event(hours_away=96, max_participants=5), self.user, status="pending"
        )
        redeem_for_registration(self.user, seat, FEE_CENTS)

        credit.refresh_from_db()
        self.assertFalse(
            credit.cash_refund_still_available,
            "the member took the credit; the cash offer is closed",
        )

        self.client.force_login(self.user)
        payload = json.loads(
            self.client.get(reverse("crush_lu:export_user_data")).content
        )
        self.assertFalse(payload["crush_credit"][0]["cash_refund_available_on_request"])

    def test_the_export_reports_unswept_expired_credit_as_unspendable(self):
        credit = issue_credit(self.user, 2000, CrushCredit.Reason.GOODWILL)
        CrushCredit.objects.filter(pk=credit.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        self.client.force_login(self.user)
        payload = json.loads(
            self.client.get(reverse("crush_lu:export_user_data")).content
        )

        entry = payload["crush_credit"][0]
        self.assertEqual(entry["status"], "Expired")
        self.assertEqual(entry["remaining"], "0.00")

    def test_merging_accounts_takes_credit_after_registrations(self):
        """Trap 2: redemption locks EventRegistration then CrushCredit.

        A merge that locked credit first and touched registrations afterwards
        is an ABBA against any member completing a credit checkout.
        """
        import inspect

        from crush_lu.services import account_merge

        src = inspect.getsource(account_merge.merge_accounts)
        self.assertLess(
            src.index("affected_registrations ="),
            src.index("CrushCredit.objects.filter(user=duplicate_user)"),
            "credit must be transferred AFTER registrations, never before",
        )

    def test_a_failed_goodwill_batch_credits_nobody(self):
        """All-or-nothing, so re-running the same selection is safe."""
        from crush_lu.admin import CrushCreditAdmin
        from crush_lu.admin.credits import issue_goodwill_credit
        from crush_lu.admin.site import crush_admin_site
        from django.contrib.auth.models import User as AuthUser

        first = self._user("batch1@crush.lu")
        second = self._user("batch2@crush.lu")
        staff = self._user("staff2@crush.lu")
        staff.is_staff = True
        staff.is_superuser = True
        staff.save(update_fields=["is_staff", "is_superuser"])
        request = _admin_request(staff)
        request.POST = {
            "apply": "1",
            "amount_eur": "20.00",
            "reason": "Door mix-up affecting both of them.",
        }
        admin_obj = CrushCreditAdmin(CrushCredit, crush_admin_site)
        admin_obj.has_change_permission = lambda _request: True

        with patch(
            "crush_lu.admin.credits.issue_credit",
            side_effect=[object(), RuntimeError("boom")],
        ):
            with self.assertRaises(RuntimeError):
                issue_goodwill_credit(
                    admin_obj,
                    request,
                    AuthUser.objects.filter(pk__in=[first.pk, second.pk]),
                )

        self.assertEqual(
            CrushCredit.objects.filter(user__in=[first, second]).count(),
            0,
            "a partial batch makes the obvious retry pay someone twice",
        )


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class FinalReviewRegressionTests(CreditFixture):
    """The outstanding review threads that close PR #827."""

    def test_unpublished_event_cancellation_still_compensates(self):
        event = self._event(hours_away=96, max_participants=5, is_published=False)
        registration = self._paid_registration(event, self.user)

        with self.captureOnCommitCallbacks(execute=True):
            registration.status = "cancelled"
            registration.save(update_fields=["status"])

        self.assertEqual(
            self._credits(self.user).get().reason,
            CrushCredit.Reason.MEMBER_CANCELLATION,
        )

    def test_late_sumup_capture_after_event_cancellation_issues_remedy(self):
        from crush_lu.views_payments import _apply_paid_checkout

        event = self._event(hours_away=96, max_participants=5, is_cancelled=True)
        registration = self._registration(event, self.user, status="pending")
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-late-capture",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-LATE-CAPTURE",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=registration,
        )

        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(payment, {"status": "PAID"})

        payment.refresh_from_db()
        registration.refresh_from_db()
        credit = self._credits(self.user).get()
        self.assertEqual(payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(registration.status, "pending", "the seat stays closed")
        self.assertFalse(registration.payment_confirmed)
        self.assertEqual(credit.amount_cents, 2000)
        self.assertTrue(credit.cash_refund_eligible)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("what happens next", mail.outbox[0].subject)

    def test_credit_funding_tranches_keep_their_individual_expiries(self):
        event = self._event(hours_away=96, max_participants=5)
        registration = self._registration(event, self.user, status="cancelled")
        registration.payment_confirmed = True
        registration.payment_date = timezone.now()
        registration.save(update_fields=["payment_confirmed", "payment_date"])

        soon = issue_credit(self.user, 1000, CrushCredit.Reason.GOODWILL)
        later = issue_credit(self.user, 1000, CrushCredit.Reason.GOODWILL)
        soon_expiry = timezone.now() + timedelta(days=10)
        later_expiry = timezone.now() + timedelta(days=90)
        CrushCredit.objects.filter(pk=soon.pk).update(expires_at=soon_expiry)
        CrushCredit.objects.filter(pk=later.pk).update(expires_at=later_expiry)
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-CREDIT-two-tranches",
            provider=PaymentTransaction.Provider.CREDIT,
            sumup_checkout_id="",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=registration,
            raw_response={
                "paid_with": "crush_credit",
                "redemptions": [
                    {"credit_id": soon.pk, "amount_cents": 1000},
                    {"credit_id": later.pk, "amount_cents": 550},
                ],
            },
        )

        issued = issue_cancellation_credits(registration)

        self.assertEqual(
            [(c.amount_cents, c.expires_at) for c in issued],
            [
                (1000, soon_expiry),
                (550, later_expiry),
            ],
        )
        self.assertEqual(
            [c.restored_from_credit_id for c in issued], [soon.pk, later.pk]
        )
        self.assertEqual(payment.issued_credits.count(), 2)

    def test_financial_rows_protect_against_hard_user_delete(self):
        issue_credit(self.user, 2000, CrushCredit.Reason.GOODWILL)
        with self.assertRaises(ProtectedError):
            self.user.delete()

        other = self._user("payment-retention@crush.lu")
        self._paid_registration(self._event(hours_away=96, max_participants=5), other)
        with self.assertRaises(ProtectedError):
            other.delete()

    def test_money_admin_actions_enforce_mutation_permissions(self):
        from crush_lu.admin import CrushCreditAdmin
        from crush_lu.admin.credits import issue_goodwill_credit
        from crush_lu.admin.site import crush_admin_site

        member = self._user("no-money-permission@crush.lu")
        request = _admin_request(member)
        credit_admin = CrushCreditAdmin(CrushCredit, crush_admin_site)
        credit = issue_credit(self.user, 1000, CrushCredit.Reason.GOODWILL)

        with self.assertRaises(PermissionDenied):
            credit_admin.void_credits(request, CrushCredit.objects.filter(pk=credit.pk))
        with self.assertRaises(PermissionDenied):
            issue_goodwill_credit(credit_admin, request, User.objects.all())

    def test_canonical_admin_cancellation_credits_and_emails(self):
        from crush_lu.admin.events import MeetupEventAdmin
        from crush_lu.admin.site import crush_admin_site

        event = self._event(hours_away=96, max_participants=5)
        self._paid_registration(event, self.user)
        admin_user = self._user("organiser@crush.lu")
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.save(update_fields=["is_staff", "is_superuser"])
        request = _admin_request(admin_user)
        admin_obj = MeetupEventAdmin(MeetupEvent, crush_admin_site)

        with patch("crush_lu.admin.events._enqueue_echo_sync"):
            admin_obj.cancel_events(request, MeetupEvent.objects.filter(pk=event.pk))

        event.refresh_from_db()
        self.assertTrue(event.is_cancelled)
        self.assertEqual(self._credits(self.user).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("what happens next", mail.outbox[0].subject)

    def test_admin_cancellation_emails_every_live_unpaid_registration(self):
        from crush_lu.admin.events import MeetupEventAdmin
        from crush_lu.admin.site import crush_admin_site

        free_event = self._event(
            hours_away=96,
            fee=Decimal("0.00"),
            max_participants=5,
        )
        pending_event = self._event(hours_away=97, max_participants=5)
        waitlist_event = self._event(hours_away=98, max_participants=1)
        registrations = [
            self._registration(free_event, self.user, status="confirmed"),
            self._registration(
                pending_event,
                self._user("unpaid-cancelled@crush.lu"),
                status="pending",
            ),
            self._registration(
                waitlist_event,
                self._user("waitlist-cancelled@crush.lu"),
                status="waitlist",
            ),
        ]
        staff = self._user("all-attendees-organiser@crush.lu")
        staff.is_staff = True
        staff.is_superuser = True
        staff.save(update_fields=["is_staff", "is_superuser"])
        admin_obj = MeetupEventAdmin(MeetupEvent, crush_admin_site)
        mail.outbox.clear()

        with patch("crush_lu.admin.events._enqueue_echo_sync"):
            admin_obj.cancel_events(
                _admin_request(staff),
                MeetupEvent.objects.filter(
                    pk__in=[free_event.pk, pending_event.pk, waitlist_event.pk]
                ),
            )

        self.assertEqual(CrushCredit.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 3)
        self.assertTrue(
            all(
                "No payment was recorded for this booking" in message.body
                for message in mail.outbox
            )
        )
        for registration in registrations:
            registration.refresh_from_db()
            self.assertIsNotNone(
                registration.organiser_cancellation_notified_at
            )

    def test_signal_cancellation_after_organiser_cancellation_uses_premium(self):
        event = self._event(hours_away=96, max_participants=5, is_cancelled=True)
        registration = self._paid_registration(event, self.user)

        with self.captureOnCommitCallbacks(execute=True):
            registration.status = "cancelled"
            registration.save(update_fields=["status"])

        credit = self._credits(self.user).get()
        self.assertEqual(credit.reason, CrushCredit.Reason.EVENT_CANCELLED)
        self.assertEqual(credit.amount_cents, 2000)
        self.assertTrue(credit.cash_refund_eligible)
        self.assertEqual(len(mail.outbox), 1)

    def test_organiser_cancellation_does_not_credit_a_free_registration(self):
        event = self._event(
            hours_away=96,
            fee=Decimal("0.00"),
            max_participants=5,
            is_cancelled=True,
        )
        EventRegistration.objects.create(
            event=event,
            user=self.user,
            status="confirmed",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )

        issued = credit_paid_registrations_for_cancelled_event(event)

        self.assertEqual(issued, [])
        self.assertEqual(self._credits(self.user).count(), 0)

    def test_unpaid_replacement_forwards_the_original_resale_claim(self):
        event = self._event(hours_away=30, max_participants=1)
        original = self._paid_registration(event, self.user)
        first = self._user("first-unpaid@crush.lu")
        second = self._user("second-payer@crush.lu")
        self._registration(event, first, status="waitlist")
        self._registration(event, second, status="waitlist")

        self._cancel(self.user, event)
        first_registration = EventRegistration.objects.get(event=event, user=first)
        self._cancel(first, event)
        second_registration = EventRegistration.objects.get(event=event, user=second)

        first_registration.refresh_from_db()
        self.assertEqual(first_registration.status, "cancelled")
        self.assertEqual(second_registration.resale_source_registration_id, original.pk)
        self.assertEqual(second_registration.resale_beneficiary_id, self.user.pk)

        self._pay_replacement(second_registration)

        credit = self._credits(self.user).get()
        self.assertEqual(credit.reason, CrushCredit.Reason.SEAT_RESOLD)
        self.assertEqual(credit.amount_cents, FEE_CENTS // 2)

    def test_later_direct_registrant_inherits_an_unclaimed_resale_claim(self):
        event = self._event(hours_away=30, max_participants=1)
        original = self._paid_registration(event, self.user)

        self._cancel(self.user, event)
        original.refresh_from_db()
        self.assertEqual(original.status, "cancelled")
        self.assertEqual(self._credits(self.user).count(), 0)

        replacement_user = self._user("direct-replacement@crush.lu")
        self.client.force_login(replacement_user)
        response = self.client.post(
            reverse("crush_lu:event_register", kwargs={"event_id": event.pk}),
            {"confirm_registration": "on"},
        )

        self.assertEqual(response.status_code, 302)
        replacement = EventRegistration.objects.get(
            event=event, user=replacement_user
        )
        self.assertEqual(replacement.status, "pending")
        self.assertEqual(replacement.resale_source_registration_id, original.pk)
        self.assertEqual(replacement.resale_beneficiary_id, self.user.pk)

        self._pay_replacement(replacement)

        credit = self._credits(self.user).get()
        self.assertEqual(credit.reason, CrushCredit.Reason.SEAT_RESOLD)
        self.assertEqual(credit.amount_cents, FEE_CENTS // 2)

    def test_abandoned_checkouts_do_not_prevent_event_deletion(self):
        for status in (
            PaymentTransaction.Status.PENDING,
            PaymentTransaction.Status.FAILED,
            PaymentTransaction.Status.CANCELLED,
        ):
            with self.subTest(status=status):
                event = self._event(hours_away=120, max_participants=5)
                registration = self._registration(
                    event,
                    self._user(f"abandoned-{status}@crush.lu"),
                    status="pending",
                )
                payment = PaymentTransaction.objects.create(
                    transaction_reference=f"CRUSH-EVT-abandoned-{status}",
                    provider=PaymentTransaction.Provider.SUMUP,
                    amount=event.registration_fee,
                    currency="EUR",
                    status=status,
                    purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                    user=registration.user,
                    event_registration=registration,
                )
                self.assertIsNone(payment.event_id)

                event.delete()

                payment.refresh_from_db()
                self.assertIsNone(payment.event_id)
                self.assertIsNone(payment.event_registration_id)

    def test_captured_payment_prevents_event_deletion(self):
        payment = self.registration.payment_transactions.get()

        with self.assertRaises(ProtectedError) as error:
            self.event.delete()

        self.assertIn(payment, error.exception.protected_objects)

    def test_staff_cannot_spend_a_members_credit(self):
        staff = self._user("staff-credit@crush.lu")
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        event = self._event(hours_away=96, max_participants=5)
        registration = self._registration(event, self.user, status="pending")
        issue_credit(self.user, FEE_CENTS, CrushCredit.Reason.GOODWILL)
        self.client.force_login(staff)

        response = self.client.post(
            reverse(
                "sumup_create_event_checkout",
                kwargs={"registration_id": registration.pk},
            ),
            data=json.dumps({"payment_method": "credit"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(available_credit_cents(self.user), FEE_CENTS)
        self.assertFalse(
            PaymentTransaction.objects.filter(
                event_registration=registration,
                provider=PaymentTransaction.Provider.CREDIT,
            ).exists()
        )

    def test_cash_refund_voids_the_source_payment_too(self):
        from crush_lu.services.credits import void_credit

        event = self._event(hours_away=96, max_participants=5, is_cancelled=True)
        registration = self._paid_registration(event, self.user)
        payment = registration.payment_transactions.get()
        credit = credit_paid_registrations_for_cancelled_event(event)[0]

        _credit, outcome = void_credit(
            credit.pk,
            note="Cash refund completed.",
            mark_source_payment_refunded=True,
        )

        payment.refresh_from_db()
        self.assertEqual(outcome, "voided")
        self.assertEqual(payment.status, PaymentTransaction.Status.REFUNDED)

    def test_paid_at_is_immutable_when_a_transaction_is_rechecked(self):
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-paid-at",
            provider=PaymentTransaction.Provider.SUMUP,
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
        )
        payment.status = PaymentTransaction.Status.PAID
        payment.save(update_fields=["status", "updated_at"])
        first_paid_at = payment.paid_at

        PaymentTransaction.objects.filter(pk=payment.pk).update(
            updated_at=timezone.now() + timedelta(days=2)
        )
        payment.refresh_from_db()

        self.assertEqual(payment.paid_at, first_paid_at)
        self.assertGreater(payment.updated_at, payment.paid_at)

    def test_credit_button_only_appears_when_the_whole_seat_is_covered(self):
        event = self._event(hours_away=96, max_participants=5)
        self._registration(event, self.user, status="pending")
        issue_credit(self.user, 100, CrushCredit.Reason.GOODWILL)
        self.client.force_login(self.user)
        url = reverse("crush_lu:event_detail", kwargs={"event_id": event.pk})

        response = self.client.get(url)
        self.assertNotContains(response, 'data-payment-method="credit"')

        issue_credit(self.user, FEE_CENTS - 100, CrushCredit.Reason.GOODWILL)
        response = self.client.get(url)
        self.assertContains(response, 'data-payment-method="credit"')

    def test_manual_payment_settles_a_pending_resale_claim(self):
        from django.contrib.admin.sites import AdminSite

        from crush_lu.admin.events import EventRegistrationAdmin

        event = self._event(hours_away=30, max_participants=1)
        original = self._paid_registration(event, self.user)
        replacement_user = self._user("manual-replacement@crush.lu")
        self._registration(event, replacement_user, status="waitlist")
        self._cancel(self.user, event)
        replacement = EventRegistration.objects.get(event=event, user=replacement_user)
        replacement.payment_confirmed = True
        staff = self._user("cash-coach@crush.lu")
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        request = _admin_request(staff)
        form = type("F", (), {"changed_data": ["payment_confirmed"]})()
        mail.outbox.clear()

        admin_obj = EventRegistrationAdmin(EventRegistration, AdminSite())
        with self.captureOnCommitCallbacks(execute=True):
            admin_obj.save_model(request, replacement, form, change=True)

        manual = PaymentTransaction.objects.get(
            event_registration=replacement,
            provider=PaymentTransaction.Provider.MANUAL,
        )
        original.refresh_from_db()
        replacement.refresh_from_db()
        self.assertEqual(manual.status, PaymentTransaction.Status.PAID)
        self.assertEqual(manual.paid_at, replacement.payment_date)
        self.assertFalse(original.payment_confirmed)
        self.assertIsNone(replacement.resale_source_payment_id)
        self.assertEqual(self._credits(self.user).get().amount_cents, FEE_CENTS // 2)
        self.assertTrue(
            any(
                message.to == [self.user.email]
                and "We've added 7.75 EUR" in message.body
                for message in mail.outbox
            )
        )

    def test_manual_payment_creates_a_new_capture_for_a_reused_registration(self):
        from django.contrib.admin.sites import AdminSite

        from crush_lu.admin.events import EventRegistrationAdmin

        registration = self.registration
        old_sumup = registration.payment_transactions.get()
        self._cancel(self.user, self.event)
        registration.refresh_from_db()
        registration.status = "pending"
        registration.payment_confirmed = True

        staff = self._user("cycle-cash-coach@crush.lu")
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])
        request = _admin_request(staff)
        form = type("F", (), {"changed_data": ["payment_confirmed"]})()

        admin_obj = EventRegistrationAdmin(EventRegistration, AdminSite())
        with self.captureOnCommitCallbacks(execute=True):
            admin_obj.save_model(request, registration, form, change=True)

        payments = registration.payment_transactions.filter(
            status=PaymentTransaction.Status.PAID
        ).order_by("created_at")
        self.assertEqual(payments.count(), 2)
        manual = payments.get(provider=PaymentTransaction.Provider.MANUAL)
        self.assertNotEqual(manual.pk, old_sumup.pk)
        self.assertEqual(manual.amount, self.event.registration_fee)

        registration.status = "cancelled"
        credits = issue_cancellation_credits(registration)
        self.assertEqual(credits[0].source_payment_id, manual.pk)

    def test_unchecking_sumup_cycle_does_not_cancel_older_manual_revenue(self):
        from django.contrib.admin.sites import AdminSite

        from crush_lu.admin.events import EventRegistrationAdmin

        event = self._event(hours_away=96, max_participants=5)
        registration = self._registration(event, self.user, status="confirmed")
        old_time = timezone.now() - timedelta(days=30)
        old_manual = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-MANUAL-old-cycle",
            provider=PaymentTransaction.Provider.MANUAL,
            amount=event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=registration,
            paid_at=old_time,
        )
        current_time = timezone.now()
        current_sumup = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-current-cycle",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-CURRENT-CYCLE",
            amount=event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=registration,
            paid_at=current_time,
        )
        registration.payment_confirmed = True
        registration.payment_date = current_time
        registration.save(update_fields=["payment_confirmed", "payment_date"])
        registration.payment_confirmed = False
        form = type("F", (), {"changed_data": ["payment_confirmed"]})()
        staff = self._user("cycle-correction-coach@crush.lu")
        staff.is_staff = True
        staff.save(update_fields=["is_staff"])

        admin_obj = EventRegistrationAdmin(EventRegistration, AdminSite())
        admin_obj.save_model(
            _admin_request(staff), registration, form, change=True
        )

        old_manual.refresh_from_db()
        current_sumup.refresh_from_db()
        self.assertEqual(old_manual.status, PaymentTransaction.Status.PAID)
        self.assertEqual(current_sumup.status, PaymentTransaction.Status.PAID)

    def test_late_replacement_capture_after_cancellation_compensates_both_payers(self):
        from crush_lu.views_payments import _apply_paid_checkout

        event = self._event(hours_away=30, max_participants=1)
        self._paid_registration(event, self.user)
        replacement_user = self._user("late-replacement@crush.lu")
        self._registration(event, replacement_user, status="waitlist")
        self._cancel(self.user, event)
        replacement = EventRegistration.objects.get(event=event, user=replacement_user)
        MeetupEvent.objects.filter(pk=event.pk).update(is_cancelled=True)
        payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-late-replacement",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-LATE-REPLACEMENT",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=replacement_user,
            event_registration=replacement,
        )
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(payment, {"status": "PAID"})

        original_credit = self._credits(self.user).get()
        replacement_credit = self._credits(replacement_user).get()
        self.assertEqual(original_credit.reason, CrushCredit.Reason.EVENT_CANCELLED)
        self.assertEqual(replacement_credit.reason, CrushCredit.Reason.EVENT_CANCELLED)
        self.assertEqual(original_credit.amount_cents, 2000)
        self.assertEqual(replacement_credit.amount_cents, 2000)
        self.assertEqual(len(mail.outbox), 2)

    def test_account_merge_preserves_claim_when_source_registration_is_deleted(self):
        from crush_lu.services.account_merge import merge_accounts

        event = self._event(hours_away=30, max_participants=1)
        original = self._paid_registration(event, self.user)
        replacement_user = self._user("merge-replacement@crush.lu")
        self._registration(event, replacement_user, status="waitlist")
        self._cancel(self.user, event)
        replacement = EventRegistration.objects.get(event=event, user=replacement_user)
        keeper = self._user("merge-keeper@crush.lu")
        EventRegistration.objects.create(event=event, user=keeper, status="cancelled")

        merge_accounts(keeper, self.user)

        replacement.refresh_from_db()
        self.assertFalse(EventRegistration.objects.filter(pk=original.pk).exists())
        self.assertIsNone(replacement.resale_source_registration_id)
        self.assertIsNotNone(replacement.resale_source_payment_id)
        self.assertEqual(replacement.resale_beneficiary_id, keeper.pk)

        self._pay_replacement(replacement)
        credit = self._credits(keeper).get()
        self.assertEqual(credit.reason, CrushCredit.Reason.SEAT_RESOLD)
        self.assertEqual(credit.amount_cents, FEE_CENTS // 2)

    @override_settings(CRUSH_CREDIT_CANCELLATION_EMAIL_LIMIT=1)
    def test_admin_cancellation_email_batch_resumes_without_double_credit(self):
        from crush_lu.admin.events import MeetupEventAdmin
        from crush_lu.admin.site import crush_admin_site

        event = self._event(hours_away=96, max_participants=5)
        self._paid_registration(event, self.user)
        other = self._user("second-cancelled@crush.lu")
        self._paid_registration(event, other)
        staff = self._user("batch-organiser@crush.lu")
        staff.is_staff = True
        staff.is_superuser = True
        staff.save(update_fields=["is_staff", "is_superuser"])
        request = _admin_request(staff)
        admin_obj = MeetupEventAdmin(MeetupEvent, crush_admin_site)

        with patch("crush_lu.admin.events._enqueue_echo_sync"):
            admin_obj.cancel_events(request, MeetupEvent.objects.filter(pk=event.pk))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            CrushCredit.objects.filter(
                source_registration__event=event,
                reason=CrushCredit.Reason.EVENT_CANCELLED,
            ).count(),
            2,
        )

        request = _admin_request(staff)
        with patch("crush_lu.admin.events._enqueue_echo_sync"):
            admin_obj.cancel_events(request, MeetupEvent.objects.filter(pk=event.pk))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            CrushCredit.objects.filter(
                source_registration__event=event,
                reason=CrushCredit.Reason.EVENT_CANCELLED,
            ).count(),
            2,
        )

    @override_settings(CRUSH_CREDIT_CANCELLATION_EMAIL_LIMIT=1)
    def test_consumed_credit_stays_in_the_delayed_cancellation_email_queue(self):
        from crush_lu.admin.events import MeetupEventAdmin
        from crush_lu.admin.site import crush_admin_site

        event = self._event(hours_away=96, max_participants=5)
        self._paid_registration(event, self.user)
        other = self._user("consume-before-email@crush.lu")
        other_registration = self._paid_registration(event, other)
        staff = self._user("delayed-email-organiser@crush.lu")
        staff.is_staff = True
        staff.is_superuser = True
        staff.save(update_fields=["is_staff", "is_superuser"])
        admin_obj = MeetupEventAdmin(MeetupEvent, crush_admin_site)

        with patch("crush_lu.admin.events._enqueue_echo_sync"):
            admin_obj.cancel_events(
                _admin_request(staff), MeetupEvent.objects.filter(pk=event.pk)
            )

        other_registration.refresh_from_db()
        self.assertIsNone(other_registration.organiser_cancellation_notified_at)
        credit = self._credits(other).get()
        target = self._registration(
            self._event(
                hours_away=200,
                fee=Decimal("20.00"),
                max_participants=5,
            ),
            other,
            status="pending",
        )
        redeem_for_registration(other, target, 2000)
        credit.refresh_from_db()
        self.assertEqual(credit.status, CrushCredit.Status.CONSUMED)

        with patch("crush_lu.admin.events._enqueue_echo_sync"):
            admin_obj.cancel_events(
                _admin_request(staff), MeetupEvent.objects.filter(pk=event.pk)
            )

        other_registration.refresh_from_db()
        self.assertIsNotNone(other_registration.organiser_cancellation_notified_at)
        self.assertEqual(mail.outbox[-1].to, [other.email])
        self.assertIn("cash-refund option has closed", mail.outbox[-1].body)

    def test_delayed_email_reports_that_partial_redemption_closed_cash_refund(self):
        from crush_lu.email_helpers import send_event_cancelled_by_organiser

        event = self._event(
            hours_away=96,
            max_participants=5,
            is_cancelled=True,
        )
        registration = self._paid_registration(event, self.user)
        credit = credit_paid_registrations_for_cancelled_event(event)[0]
        target = self._registration(
            self._event(hours_away=200, max_participants=5),
            self.user,
            status="pending",
        )
        redeem_for_registration(self.user, target, FEE_CENTS)
        credit.refresh_from_db()
        self.assertEqual(credit.status, CrushCredit.Status.ACTIVE)
        self.assertFalse(credit.cash_refund_still_available)

        mail.outbox.clear()
        send_event_cancelled_by_organiser(registration, [credit])

        self.assertNotIn("you may ask for a cash refund", mail.outbox[0].body)
        self.assertIn("cash-refund option has closed", mail.outbox[0].body)


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

        src = inspect.getsource(credits).replace(
            inspect.getsource(credits.void_credit), ""
        )
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
