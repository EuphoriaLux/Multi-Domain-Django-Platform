"""Issue #925 evidence at 8148fbc5, not the desired recovery contract.

These explicit, opt-in characterization probes reproduce existing gaps without
changing payment logic. They are outside pytest's default testpaths. Replace
the defect assertions with the specification's acceptance cases when recovery
is implemented; do not preserve these outcomes as product requirements.

The specification lives in the shared memory hub:
ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from crush_lu.management.commands.reconcile_sumup_payments import Command
from crush_lu.models import CrushCoach, CrushProfile, PremiumMembership
from crush_lu.models.payments import PaymentTransaction
from crush_lu.views_payments import _apply_paid_checkout, _sync_checkout_with_sumup


@override_settings(PREMIUM_REDIRECTS_TO_BETA=False)
class PremiumRecoverySnapshotTests(TestCase):
    def setUp(self):
        network = self.enterContext(
            patch(
                "socket.socket.connect",
                side_effect=AssertionError(
                    "Recovery evidence must not access the network"
                ),
            )
        )
        self.addCleanup(network.assert_not_called)
        cache.clear()
        users = get_user_model()
        self.member = users.objects.create_user(
            username="recovery-member@example.invalid", password="fixture"
        )
        self.profile = CrushProfile.objects.create(user=self.member, gender="F")
        coach_user = users.objects.create_user(
            username="recovery-coach@example.invalid", password="fixture"
        )
        self.coach = CrushCoach.objects.create(
            user=coach_user, accepting_premium=True, max_premium_members=1
        )
        self.membership = PremiumMembership.objects.create(
            user=self.member, coach=self.coach
        )
        self.receipt = self.enterContext(
            patch("crush_lu.email_helpers.send_premium_membership_payment_receipt")
        )
        self.provider = self.enterContext(
            patch(
                "crush_lu.views_payments.SumUpClient.get_checkout",
                side_effect=lambda checkout: {"id": checkout, "status": "PAID"},
            )
        )

    def payment(self, reference):
        return PaymentTransaction.objects.create(
            transaction_reference=reference,
            sumup_checkout_id=reference,
            provider=PaymentTransaction.Provider.SUMUP,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            amount=Decimal("10.00"),
            user=self.member,
            premium_membership=self.membership,
        )

    def capture(self, payment):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(_sync_checkout_with_sumup(payment), "PAID")
        payment.refresh_from_db()
        self.membership.refresh_from_db()
        self.profile.refresh_from_db()

    def test_second_capture_is_paid_without_a_second_receipt_or_log(self):
        first, second = self.payment("first"), self.payment("second")
        self.capture(first)
        with patch("crush_lu.views_payments.logger") as logger:
            self.capture(second)
        self.assertEqual(first.status, "paid")
        self.assertEqual(second.status, "paid")
        self.assertEqual(self.membership.status, "active")
        self.assertEqual(self.receipt.call_count, 1)
        self.assertEqual(self.receipt.call_args.args[0].pk, first.pk)
        self.assertEqual(logger.mock_calls, [])
        self.assertEqual(second.failure_reason, "")

    def test_pre_cancelled_membership_capture_is_silent(self):
        payment = self.payment("cancelled-before-capture")
        self.membership.cancel()
        with patch("crush_lu.views_payments.logger") as logger:
            self.capture(payment)
        self.assertEqual(payment.status, "paid")
        self.assertEqual(self.membership.status, "cancelled")
        self.assertEqual(logger.mock_calls, [])
        self.receipt.assert_not_called()

    def test_capacity_recovery_cannot_be_retried_by_replaying_paid_checkout(self):
        payment = self.payment("capacity")
        self.coach.max_premium_members = 0
        self.coach.save(update_fields=["max_premium_members"])
        with self.assertLogs("crush_lu.views_payments", level="ERROR"):
            self.capture(payment)
        self.assertEqual(payment.status, "paid")
        self.assertEqual(self.membership.status, "pending")
        self.assertEqual(payment.failure_reason, "")
        self.coach.max_premium_members = 1
        self.coach.save(update_fields=["max_premium_members"])
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(payment, {"status": "PAID"})
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")
        self.receipt.assert_not_called()

    def test_duplicate_refund_currently_cancels_the_valid_first_entitlement(self):
        first, second = self.payment("original"), self.payment("duplicate")
        self.capture(first)
        self.capture(second)
        # No provider/refund call: exercise only the existing local reconciliation
        # transition with a synthetic provider-confirmed refund snapshot.
        Command()._reconcile_refunded(second, {"status": "REFUNDED"})
        first.refresh_from_db()
        second.refresh_from_db()
        self.membership.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(first.status, "paid")
        self.assertEqual(second.status, "refunded")
        self.assertEqual(self.membership.status, "cancelled")
        self.assertIsNone(self.profile.assigned_coach_id)

    def test_unexpected_activation_error_rolls_back_local_paid_record(self):
        payment = self.payment("unexpected-error")
        with patch.object(
            PremiumMembership, "confirm", side_effect=RuntimeError("probe")
        ):
            with self.assertRaises(RuntimeError):
                self.capture(payment)
        payment.refresh_from_db()
        self.membership.refresh_from_db()
        self.assertEqual(payment.status, "pending")
        self.assertIsNone(payment.paid_at)
        self.assertEqual(self.membership.status, "pending")
        self.receipt.assert_not_called()
