"""
Tests for Tier-2 SumUp refund reconciliation sweep (reconcile_sumup_payments).
"""

import io
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from crush_lu.management.commands.reconcile_sumup_payments import is_checkout_refunded
from crush_lu.models.credits import CrushCredit, CreditRedemption
from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushProfile, CrushCoach, PremiumMembership
from crush_lu.services.sumup import SumUpError

User = get_user_model()


class IsCheckoutRefundedTests(TestCase):
    def test_top_level_status_refunded(self):
        self.assertTrue(is_checkout_refunded({"status": "REFUNDED"}))
        self.assertTrue(is_checkout_refunded({"status": "refunded"}))

    def test_top_level_amount_refunded(self):
        self.assertTrue(is_checkout_refunded({"status": "PAID", "amount_refunded": 15.50}))

    def test_transaction_item_status_refunded(self):
        payload = {
            "status": "PAID",
            "transactions": [
                {"id": "txn-1", "status": "REFUNDED", "amount": 15.50}
            ],
        }
        self.assertTrue(is_checkout_refunded(payload))

    def test_transaction_item_refunds_array(self):
        payload = {
            "status": "PAID",
            "transactions": [
                {"id": "txn-1", "status": "SUCCESSFUL", "refunds": [{"amount": 15.50}]}
            ],
        }
        self.assertTrue(is_checkout_refunded(payload))

    def test_transaction_item_amount_refunded(self):
        payload = {
            "status": "PAID",
            "transactions": [
                {"id": "txn-1", "status": "SUCCESSFUL", "amount_refunded": 10.00}
            ],
        }
        self.assertTrue(is_checkout_refunded(payload))

    def test_normal_paid_payload_returns_false(self):
        payload = {
            "status": "PAID",
            "transactions": [
                {"id": "txn-1", "status": "SUCCESSFUL", "amount": 15.50}
            ],
        }
        self.assertFalse(is_checkout_refunded(payload))

    def test_empty_or_invalid_payload(self):
        self.assertFalse(is_checkout_refunded({}))
        self.assertFalse(is_checkout_refunded(None))


class SumUpReconciliationCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="reconcile_member",
            email="reconcile_member@test.crush.lu",
            password="secretpassword123",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            verification_status="verified",
            completion_status="step4",
        )
        self.event = MeetupEvent.objects.create(
            title="Speed Dating #42",
            event_type="speed_dating",
            location="Luxembourg City",
            address="10 Grand Rue",
            date_time=timezone.now() + timedelta(days=7),
            registration_deadline=timezone.now() + timedelta(days=6),
            registration_fee=Decimal("15.50"),
            max_participants=20,
            is_published=True,
        )
        self.registration = EventRegistration.objects.create(
            event=self.event,
            user=self.user,
            status="confirmed",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )
        self.payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-42-reconcile",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="chk_refund_123",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
            event=self.event,
            raw_response={"status": "PAID"},
        )

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_reconcile_external_refund_event_registration(self, mock_get_checkout):
        mock_get_checkout.return_value = {
            "id": "chk_refund_123",
            "status": "REFUNDED",
            "amount": 15.50,
            "transactions": [{"id": "txn-1", "status": "REFUNDED"}],
        }

        out = io.StringIO()
        call_command("reconcile_sumup_payments", stdout=out)

        self.payment.refresh_from_db()
        self.registration.refresh_from_db()

        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertIn("External refund detected", self.payment.failure_reason)
        self.assertFalse(self.registration.payment_confirmed)
        self.assertIsNone(self.registration.payment_date)
        self.assertEqual(self.registration.status, "cancelled")
        self.assertIn("1 external refund(s) reconciled", out.getvalue())

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_reconcile_dry_run_makes_no_db_changes(self, mock_get_checkout):
        mock_get_checkout.return_value = {
            "id": "chk_refund_123",
            "status": "REFUNDED",
        }

        out = io.StringIO()
        call_command("reconcile_sumup_payments", "--dry-run", stdout=out)

        self.payment.refresh_from_db()
        self.registration.refresh_from_db()

        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)
        self.assertTrue(self.registration.payment_confirmed)
        self.assertEqual(self.registration.status, "confirmed")
        self.assertIn("[DRY RUN]", out.getvalue())

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_reconcile_voids_unspent_crush_credit(self, mock_get_checkout):
        credit = CrushCredit.objects.create(
            user=self.user,
            amount_cents=1550,
            currency="EUR",
            reason=CrushCredit.Reason.EVENT_CANCELLED,
            status=CrushCredit.Status.ACTIVE,
            source_payment=self.payment,
            source_registration=self.registration,
            cash_refund_eligible=True,
        )

        mock_get_checkout.return_value = {
            "id": "chk_refund_123",
            "status": "REFUNDED",
        }

        call_command("reconcile_sumup_payments", quiet=True)

        credit.refresh_from_db()
        self.assertEqual(credit.status, CrushCredit.Status.VOID)
        self.assertIn("External SumUp cash refund reconciled", credit.note)

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_reconcile_handles_partially_spent_crush_credit_with_warning(self, mock_get_checkout):
        credit = CrushCredit.objects.create(
            user=self.user,
            amount_cents=2000,
            currency="EUR",
            reason=CrushCredit.Reason.EVENT_CANCELLED,
            status=CrushCredit.Status.ACTIVE,
            source_payment=self.payment,
            source_registration=self.registration,
            cash_refund_eligible=True,
        )
        CreditRedemption.objects.create(
            credit=credit,
            amount_cents=1000,
        )

        mock_get_checkout.return_value = {
            "id": "chk_refund_123",
            "status": "REFUNDED",
        }

        call_command("reconcile_sumup_payments", quiet=True)

        credit.refresh_from_db()
        self.assertEqual(credit.status, CrushCredit.Status.VOID)
        self.assertIn("already redeemed", credit.note)

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_reconcile_premium_membership_refund(self, mock_get_checkout):
        coach_user = User.objects.create_user(
            username="coach_sam",
            email="coach_sam@crush.lu",
            password="password123",
        )
        coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=10,
        )
        pm = PremiumMembership.objects.create(
            user=self.user,
            coach=coach,
            status="active",
        )
        prem_tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-PREM-42-test",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="chk_prem_refund",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.user,
            premium_membership=pm,
            raw_response={"status": "PAID"},
        )

        mock_get_checkout.return_value = {
            "id": "chk_prem_refund",
            "status": "REFUNDED",
        }

        call_command("reconcile_sumup_payments", checkout_id="chk_prem_refund")

        prem_tx.refresh_from_db()
        pm.refresh_from_db()
        self.assertEqual(prem_tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(pm.status, "cancelled")
        self.assertFalse(pm.payment_confirmed)
        self.assertIsNone(pm.payment_date)

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_sumup_api_error_does_not_crash_sweep(self, mock_get_checkout):
        mock_get_checkout.side_effect = SumUpError("500 Internal Server Error")

        out = io.StringIO()
        call_command("reconcile_sumup_payments", stdout=out)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)
        self.assertIn("1 error(s)", out.getvalue())
