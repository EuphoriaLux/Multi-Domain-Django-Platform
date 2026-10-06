"""Crush-side failures restore credit-funded seats on a spendable expiry.

When the organiser cancels an event, or a certified curated group collapses,
the member did nothing wrong. Restoring each credit tranche on its ORIGINAL
expiry (right for a member's own cancellation, otherwise book-and-cancel would
extend credit forever) can hand back credit that has already lapsed, so the
member silently loses what they paid. For those two reasons the restored
tranche gets ``max(original expiry, a fresh default window)``.

Run with: pytest crush_lu/tests/test_crush_credit_restore_expiry.py -v
"""

from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from crush_lu.models.credits import CrushCredit, add_months
from crush_lu.models.payments import PaymentTransaction
from crush_lu.services.credits import (
    available_credit_cents,
    credit_registration_for_cancelled_event,
    credit_registration_for_unavailable_curated_group,
    issue_cancellation_credits,
    issue_credit,
    redeem_for_registration,
)
from crush_lu.tests.test_crush_credit import FEE, FEE_CENTS, CreditFixture


class _CreditPaidSeatFixture(CreditFixture):
    def setUp(self):
        super().setUp()
        cache.clear()

    def _credit_paid_seat(self, expires_in):
        """A seat paid wholly with one credit, whose expiry is ``expires_in``
        from now at the moment things go wrong (negative = already lapsed)."""
        user = self._user("credit-payer@crush.lu")
        credit = issue_credit(user, FEE_CENTS, CrushCredit.Reason.GOODWILL)
        # Redeem while still valid, then let the clock move past it.
        CrushCredit.objects.filter(pk=credit.pk).update(
            expires_at=timezone.now() + timedelta(days=7)
        )
        event = self._event(hours_away=24 * 21, max_participants=5)
        seat = self._registration(event, user, status="pending")
        redeem_for_registration(user, seat, FEE_CENTS)
        seat.payment_confirmed = True
        seat.payment_date = timezone.now()
        seat.status = "confirmed"
        seat.save()
        payment = PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-CREDIT-{seat.pk}",
            provider=PaymentTransaction.Provider.CREDIT,
            sumup_checkout_id="",
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=user,
            event_registration=seat,
            raw_response={
                "paid_with": "crush_credit",
                "redemptions": [{"credit_id": credit.pk, "amount_cents": FEE_CENTS}],
            },
        )
        original_expiry = timezone.now() + expires_in
        CrushCredit.objects.filter(pk=credit.pk).update(expires_at=original_expiry)
        return user, seat, payment, original_expiry

    def _restored(self, issued):
        return next(c for c in issued if c.restored_from_credit_id)

    def _fresh_window_start(self):
        return add_months(timezone.now(), 6) - timedelta(minutes=5)


class RestoreExpiryOnCrushSideFailureTests(_CreditPaidSeatFixture):
    def test_organiser_cancel_restores_an_already_lapsed_tranche_spendable(self):
        user, seat, payment, _ = self._credit_paid_seat(timedelta(days=-3))

        issued = credit_registration_for_cancelled_event(seat, payment=payment)

        # 1550 restored + 450 premium bonus = 2000, all spendable.
        restored = self._restored(issued)
        self.assertEqual(restored.amount_cents, FEE_CENTS)
        self.assertGreater(restored.expires_at, timezone.now())
        self.assertGreaterEqual(restored.expires_at, self._fresh_window_start())
        self.assertEqual(available_credit_cents(user), 2000)

    def test_unavailable_curated_group_restores_a_lapsed_tranche_spendable(self):
        user, seat, payment, _ = self._credit_paid_seat(timedelta(days=-3))

        issued = credit_registration_for_unavailable_curated_group(
            seat, payment=payment
        )

        self.assertEqual(sum(c.amount_cents for c in issued), FEE_CENTS)
        for credit in issued:
            self.assertGreaterEqual(credit.expires_at, self._fresh_window_start())
        self.assertEqual(available_credit_cents(user), FEE_CENTS)

    def test_a_tranche_expiring_soon_gets_a_fresh_window_not_a_shorter_one(self):
        _, seat, payment, original = self._credit_paid_seat(timedelta(days=2))

        issued = credit_registration_for_cancelled_event(seat, payment=payment)

        restored = self._restored(issued)
        self.assertGreater(restored.expires_at, original)
        self.assertGreaterEqual(restored.expires_at, self._fresh_window_start())

    def test_a_tranche_beyond_the_default_window_keeps_its_original_expiry(self):
        _, seat, payment, original = self._credit_paid_seat(timedelta(days=400))

        issued = credit_registration_for_cancelled_event(seat, payment=payment)

        self.assertEqual(self._restored(issued).expires_at, original)

    def test_member_cancellation_still_restores_on_the_original_expiry(self):
        _, seat, _, original = self._credit_paid_seat(timedelta(days=2))

        issued = issue_cancellation_credits(seat)

        self.assertEqual(len(issued), 1)
        self.assertEqual(issued[0].reason, CrushCredit.Reason.MEMBER_CANCELLATION)
        self.assertEqual(issued[0].expires_at, original)


class CrushSideRemedyEmailCopyTests(_CreditPaidSeatFixture):
    """The restored credit now gets a fresh window when the original had
    lapsed, so the emails must not claim it kept its *original* expiry. The
    per-credit "valid until" lines are the authoritative dates."""

    def _body(self, message):
        parts = [message.body]
        parts.extend(content for content, _ in getattr(message, "alternatives", []))
        return "\n".join(parts)

    def test_organiser_cancel_email_does_not_claim_the_original_expiry(self):
        from django.core import mail

        from crush_lu.email_helpers import send_event_cancelled_by_organiser

        _, seat, payment, _ = self._credit_paid_seat(timedelta(days=-3))
        issued = credit_registration_for_cancelled_event(seat, payment=payment)
        mail.outbox.clear()

        send_event_cancelled_by_organiser(seat, issued)

        self.assertEqual(len(mail.outbox), 1)
        body = self._body(mail.outbox[0])
        self.assertNotIn("original expiry dates", body)
        self.assertIn("valid until", body)

    def test_curated_group_remedy_email_does_not_claim_the_original_expiry(self):
        from django.core import mail

        from crush_lu.email_helpers import send_curated_group_payment_remedy

        _, seat, payment, _ = self._credit_paid_seat(timedelta(days=-3))
        issued = credit_registration_for_unavailable_curated_group(
            seat, payment=payment
        )
        mail.outbox.clear()

        send_curated_group_payment_remedy(seat, issued)

        self.assertEqual(len(mail.outbox), 1)
        body = self._body(mail.outbox[0])
        self.assertNotIn("original expiry dates", body)
        self.assertIn("valid until", body)
