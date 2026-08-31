"""
Tests for Tier-2 SumUp refund reconciliation sweep (reconcile_sumup_payments).
"""

import io
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from crush_lu.management.commands.reconcile_sumup_payments import (
    _history_refund_rank,
    history_row_shows_refund,
    index_history,
    is_checkout_refunded,
    refunded_amount,
)
from crush_lu.models.credits import CrushCredit, CreditRedemption
from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushProfile, CrushCoach, PremiumMembership
from crush_lu.services.sumup import SumUpClient, SumUpError, _normalise_history_order

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

    def test_history_map_matching_transaction_code(self):
        payload = {
            "status": "PAID",
            "transaction_code": "TX123",
            "transactions": [
                {"id": "txn-1", "status": "SUCCESSFUL", "amount": 15.50}
            ],
        }
        history_map = {
            "TX123": {
                "transaction_code": "TX123",
                "status": "REFUNDED",
                "amount": 15.50,
            }
        }
        self.assertTrue(is_checkout_refunded(payload, history_map=history_map))

    def test_history_map_matching_nested_transaction_code(self):
        payload = {
            "status": "PAID",
            "transactions": [
                {"id": "txn-1", "transaction_code": "TX456", "status": "SUCCESSFUL", "amount": 15.50}
            ],
        }
        history_map = {
            "TX456": {
                "transaction_code": "TX456",
                "status": "REFUNDED",
                "amount_refunded": 15.50,
            }
        }
        self.assertTrue(is_checkout_refunded(payload, history_map=history_map))


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


    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_partial_refund_is_not_reconciled(self, mock_get_checkout):
        """A EUR2 goodwill refund on a EUR15.50 seat must not unbook the seat."""
        mock_get_checkout.return_value = {
            "id": "chk_refund_123",
            "status": "PAID",
            "amount_refunded": "2.00",
        }

        out = io.StringIO()
        call_command("reconcile_sumup_payments", stdout=out)

        self.payment.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.registration.status, "confirmed")
        self.assertTrue(self.registration.payment_confirmed)
        self.assertIn("PARTIAL", out.getvalue())

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_partial_refund_reconciles_when_forced(self, mock_get_checkout):
        mock_get_checkout.return_value = {
            "id": "chk_refund_123",
            "status": "PAID",
            "amount_refunded": "2.00",
        }

        call_command("reconcile_sumup_payments", include_partial=True, quiet=True)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_full_refund_still_reconciles(self, mock_get_checkout):
        mock_get_checkout.return_value = {
            "id": "chk_refund_123",
            "status": "PAID",
            "amount_refunded": "15.50",
        }

        call_command("reconcile_sumup_payments", quiet=True)

        self.payment.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(self.registration.status, "cancelled")


    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_credit_from_an_earlier_cycle_is_not_voided(self, mock_get_checkout):
        """EventRegistration rows are reused across re-registration cycles.

        A credit issued against payment A must survive a refund on payment B,
        even though both name the same registration row.
        """
        payment_a = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-42-cycle-one",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="chk_cycle_one",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
            event=self.event,
            raw_response={"status": "PAID"},
        )
        earlier_credit = CrushCredit.objects.create(
            user=self.user,
            amount_cents=1550,
            currency="EUR",
            reason=CrushCredit.Reason.EVENT_CANCELLED,
            status=CrushCredit.Status.ACTIVE,
            source_payment=payment_a,
            source_registration=self.registration,
            cash_refund_eligible=True,
        )

        # Only the *second* payment is refunded externally.
        mock_get_checkout.side_effect = lambda cid: (
            {"id": cid, "status": "REFUNDED"}
            if cid == "chk_refund_123"
            else {"id": cid, "status": "PAID"}
        )

        call_command("reconcile_sumup_payments", quiet=True)

        earlier_credit.refresh_from_db()
        self.assertEqual(
            earlier_credit.status,
            CrushCredit.Status.ACTIVE,
            "credit from payment A's cycle must not be voided by a refund on payment B",
        )


    @patch("crush_lu.management.commands.reconcile_sumup_payments.Command._reconcile_refunded")
    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_write_failure_does_not_abort_the_sweep(
        self, mock_get_checkout, mock_reconcile
    ):
        """One poisoned row must cost one row, not the whole run.

        The queryset is ordered with no per-run offset, so an unguarded write
        failure would abort every future sweep at the same transaction.
        """
        other = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-42-second",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="chk_second",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event=self.event,
            raw_response={"status": "PAID"},
        )
        mock_get_checkout.return_value = {"id": "x", "status": "REFUNDED"}
        mock_reconcile.side_effect = [RuntimeError("deadlock"), None]

        out = io.StringIO()
        call_command("reconcile_sumup_payments", stdout=out)

        # Both rows were attempted despite the first one blowing up.
        self.assertEqual(mock_reconcile.call_count, 2)
        output = out.getvalue()
        self.assertIn("Error reconciling", output)
        self.assertIn("Sweep complete", output)
        self.assertIn("1 error(s)", output)
        self.assertTrue(other.pk)


    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_malformed_json_body_does_not_kill_the_sweep(self, mock_get_checkout):
        """A 2xx with a non-JSON body raises JSONDecodeError, not SumUpError.

        SumUpClient.get_checkout() wraps only requests.RequestException, so a
        proxy returning an HTML error page with a 200 escapes as a bare
        ValueError and would abort every remaining row.
        """
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-42-after-bad-body",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="chk_after",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event=self.event,
            raw_response={"status": "PAID"},
        )
        mock_get_checkout.side_effect = [
            json.JSONDecodeError("Expecting value", "<html>502</html>", 0),
            {"id": "chk_after", "status": "PAID"},
        ]

        out = io.StringIO()
        call_command("reconcile_sumup_payments", stdout=out)

        self.assertEqual(mock_get_checkout.call_count, 2)
        output = out.getvalue()
        self.assertIn("Sweep complete", output)
        self.assertIn("1 error(s)", output)


    def _make_coach(self, username):
        coach_user = User.objects.create_user(
            username=username, email=f"{username}@crush.lu", password="password123"
        )
        return CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=10,
        )

    def _premium_payment(self, pm, checkout_id):
        return PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-PREM-{checkout_id}",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=checkout_id,
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.user,
            premium_membership=pm,
            raw_response={"status": "PAID"},
        )

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_refund_clears_the_coach_that_membership_assigned(self, mock_get_checkout):
        """The premium reserved-seat gates read assigned_coach_id directly
        (views_events.py:701, :924, :1448), so a stale coach keeps a refunded
        member claiming premium seats."""
        coach = self._make_coach("coach_clears")
        pm = PremiumMembership.objects.create(
            user=self.user, coach=coach, status="active"
        )
        self._premium_payment(pm, "chk_clear_coach")
        self.profile.assigned_coach = coach
        self.profile.assigned_coach_at = timezone.now()
        self.profile.save(update_fields=["assigned_coach", "assigned_coach_at"])

        mock_get_checkout.return_value = {"id": "chk_clear_coach", "status": "REFUNDED"}
        call_command("reconcile_sumup_payments", checkout_id="chk_clear_coach", quiet=True)

        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.assigned_coach_id)
        self.assertIsNone(self.profile.assigned_coach_at)

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_refund_leaves_a_coach_this_membership_did_not_assign(self, mock_get_checkout):
        """A coach held for another reason (attendance auto-assign, the 0150
        backfill) names a different coach and must survive the refund."""
        membership_coach = self._make_coach("coach_membership")
        other_coach = self._make_coach("coach_attendance")
        pm = PremiumMembership.objects.create(
            user=self.user, coach=membership_coach, status="active"
        )
        self._premium_payment(pm, "chk_keep_coach")
        self.profile.assigned_coach = other_coach
        self.profile.assigned_coach_at = timezone.now()
        self.profile.save(update_fields=["assigned_coach", "assigned_coach_at"])

        mock_get_checkout.return_value = {"id": "chk_keep_coach", "status": "REFUNDED"}
        call_command("reconcile_sumup_payments", checkout_id="chk_keep_coach", quiet=True)

        self.profile.refresh_from_db()
        pm.refresh_from_db()
        self.assertEqual(pm.status, "cancelled")
        self.assertEqual(self.profile.assigned_coach_id, other_coach.pk)


class ExternalRefundRegressionTests(TestCase):
    """Regressions for the four review findings on PR #903."""

    def test_numeric_string_amount_does_not_raise(self):
        """SumUp may return amounts as strings; `"0.00" > 0` is a TypeError.

        is_checkout_refunded() is called outside the per-row try/except in
        handle(), so an uncaught TypeError here killed the whole sweep.
        """
        self.assertFalse(is_checkout_refunded({"status": "PAID", "amount_refunded": "0.00"}))
        self.assertTrue(is_checkout_refunded({"status": "PAID", "amount_refunded": "2.50"}))
        self.assertFalse(
            is_checkout_refunded(
                {"status": "PAID", "transactions": [{"status": "SUCCESSFUL", "amount_refunded": "0.00"}]}
            )
        )
        # Garbage must degrade to "not refunded", never explode.
        self.assertFalse(is_checkout_refunded({"status": "PAID", "amount_refunded": "not-a-number"}))

    def test_nan_and_infinity_do_not_kill_the_sweep(self):
        """json.loads parses NaN/Infinity by default, and Decimal accepts both.

        Neither raises on construction; NaN raises InvalidOperation from the
        max() comparison inside refunded_amount(), which is outside the
        caller's try/except. Same guard as views_payments' donation parse.
        """
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                self.assertEqual(
                    refunded_amount({"amount_refunded": token}), Decimal("0")
                )
                self.assertFalse(is_checkout_refunded({"status": "PAID", "amount_refunded": token}))

        # And the real json path, not just the literal strings.
        payload = json.loads('{"status": "PAID", "amount_refunded": NaN}')
        self.assertEqual(refunded_amount(payload), Decimal("0"))
        self.assertFalse(is_checkout_refunded(payload))

    def test_refunded_amount_reads_every_payload_shape(self):
        self.assertEqual(refunded_amount({"amount_refunded": "2.50"}), Decimal("2.50"))
        self.assertEqual(
            refunded_amount({"transactions": [{"amount_refunded": 4}]}), Decimal("4")
        )
        self.assertEqual(
            refunded_amount({"transactions": [{"refunds": [{"amount": "1.25"}, {"amount": "0.75"}]}]}),
            Decimal("2.00"),
        )
        # Status-only refund reports no amount: 0 means "unknown", not "nothing".
        self.assertEqual(refunded_amount({"status": "REFUNDED"}), Decimal("0"))

    def test_refunded_amount_from_history_map(self):
        payload = {
            "status": "PAID",
            "amount": 15.50,
            "transaction_code": "TX999",
            "transactions": [{"id": "t1", "status": "SUCCESSFUL", "amount": 15.50}],
        }
        history_map = {
            "TX999": {
                "transaction_code": "TX999",
                "status": "REFUNDED",
                "amount_refunded": "15.50",
            }
        }
        self.assertEqual(refunded_amount(payload, history_map=history_map), Decimal("15.50"))

    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_transactions_history")
    @patch("crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout")
    def test_reconciles_refund_detected_only_in_transaction_history(
        self, mock_get_checkout, mock_get_history
    ):
        """Dashboard refunds do not mutate the checkout resource, but appear in transaction history.

        This test proves that a checkout returning status='PAID' is still reconciled to REFUNDED
        when its transaction_code is marked REFUNDED in get_transactions_history.
        """
        user = User.objects.create_user(username="dash_refund", email="dash_refund@crush.lu")
        event = MeetupEvent.objects.create(
            title="Quiz Night #99",
            event_type="quiz_night",
            location="Luxembourg",
            address="10 Grand Rue",
            date_time=timezone.now() + timedelta(days=5),
            registration_deadline=timezone.now() + timedelta(days=4),
            registration_fee=Decimal("15.50"),
            is_published=True,
        )
        reg = EventRegistration.objects.create(
            event=event, user=user, status="confirmed", payment_confirmed=True
        )
        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-99-dash-ref",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="chk_dash_refund",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=user,
            event_registration=reg,
            event=event,
        )
        credit = CrushCredit.objects.create(
            user=user,
            amount_cents=2000,
            reason=CrushCredit.Reason.EVENT_CANCELLED,
            source_registration=reg,
            source_payment=tx,
            status=CrushCredit.Status.ACTIVE,
        )

        # Checkout payload still shows PAID and SUCCESSFUL transaction (how SumUp API behaves)
        mock_get_checkout.return_value = {
            "id": "chk_dash_refund",
            "status": "PAID",
            "transaction_code": "TX_DASH_123",
            "transactions": [
                {
                    "id": "txn-uuid-1",
                    "status": "SUCCESSFUL",
                    "transaction_code": "TX_DASH_123",
                    "amount": 15.50,
                }
            ],
        }
        # Transaction history reports the actual external dashboard refund
        mock_get_history.return_value = {
            "items": [
                {
                    "transaction_code": "TX_DASH_123",
                    "status": "REFUNDED",
                    "amount": 15.50,
                    "amount_refunded": 15.50,
                    "type": "PAYMENT",
                }
            ]
        }

        call_command("reconcile_sumup_payments", checkout_id="chk_dash_refund", quiet=True)

        tx.refresh_from_db()
        reg.refresh_from_db()
        credit.refresh_from_db()

        self.assertEqual(tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertFalse(reg.payment_confirmed)
        self.assertEqual(reg.status, "cancelled")
        self.assertEqual(credit.status, CrushCredit.Status.VOID)


class ExternalDashboardRefundRegressionTests(TestCase):
    """Regression cover for a live refund the sweep reported as "still PAID".

    A €15.50 Quiz Night seat was refunded from the SumUp merchant dashboard on
    2026-08-31. ``reconcile_sumup_payments`` checked that exact payment and
    printed ``still PAID``, leaving the member holding both the cash and a €20
    event-cancellation credit. Three independent defects had to line up for
    that, and each one below is enough on its own to hide a refund — so each
    gets its own test rather than a single end-to-end one that would go green
    again the moment any one of them was fixed.

    The payloads here are the shapes SumUp actually returned, not invented
    ones: the pre-existing history test asserted against a single row carrying
    ``amount_refunded``, which is a key the history endpoint does not emit.
    """

    # The two rows SumUp returns for one refunded transaction. They SHARE a
    # transaction_code, and the refunded total on the payment row is spelled
    # ``refunded_amount`` — not ``amount_refunded`` as the checkout resource
    # spells it.
    PAYMENT_ROW = {
        "transaction_code": "TAAA4QQ7M99",
        "transaction_id": "ffaa6390-6976-4291-92b7-7eeb7b0ab449",
        "type": "PAYMENT",
        "status": "SUCCESSFUL",
        "amount": 15.5,
        "refunded_amount": 15.5,
        "timestamp": "2026-08-10T21:30:52.891Z",
    }
    REFUND_ROW = {
        "transaction_code": "TAAA4QQ7M99",
        "transaction_id": "ffaa6390-6976-4291-92b7-7eeb7b0ab449",
        "type": "REFUND",
        "status": "REFUNDED",
        "amount": 15.5,
        "timestamp": "2026-08-31T12:11:39.170Z",
    }
    # What /v0.1/checkouts still says afterwards: nothing at all. A dashboard
    # refund never mutates the checkout resource, which is the whole reason
    # this sweep consults the transaction history in the first place.
    CHECKOUT_PAYLOAD = {
        "id": "9ff8fb20-77a8-4ee7-b103-b9de4f177152",
        "status": "PAID",
        "amount": 15.5,
        "transaction_code": "TAAA4QQ7M99",
        "transactions": [
            {
                "id": "ffaa6390-6976-4291-92b7-7eeb7b0ab449",
                "status": "SUCCESSFUL",
                "transaction_code": "TAAA4QQ7M99",
                "amount": 15.5,
            }
        ],
    }

    def _make_refunded_seat(self):
        """A cancelled seat holding an unspent event-cancellation credit."""
        user = User.objects.create_user(
            username="dashboard_refund", email="dashboard_refund@crush.lu"
        )
        event = MeetupEvent.objects.create(
            title="Quiz Night | New format",
            event_type="quiz_night",
            location="Luxembourg",
            address="10 Grand Rue",
            date_time=timezone.now() + timedelta(days=5),
            registration_deadline=timezone.now() + timedelta(days=4),
            registration_fee=Decimal("15.50"),
            is_published=True,
        )
        # Mirrors production: issue_credit already cleared payment_confirmed
        # when the organiser-cancellation credit was minted, so the row the
        # sweep meets is cancelled and unpaid — only the credit and the
        # payment status are still out of sync with SumUp.
        reg = EventRegistration.objects.create(
            event=event, user=user, status="cancelled", payment_confirmed=False
        )
        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-588-ce863b",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="9ff8fb20-77a8-4ee7-b103-b9de4f177152",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=user,
            event_registration=reg,
            event=event,
        )
        credit = CrushCredit.objects.create(
            user=user,
            # €20.00 against a €15.50 seat: the organiser-cancellation premium.
            # The gap is what makes the partial-refund guard load-bearing here.
            amount_cents=2000,
            reason=CrushCredit.Reason.EVENT_CANCELLED,
            source_registration=reg,
            source_payment=tx,
            cash_refund_eligible=True,
            status=CrushCredit.Status.ACTIVE,
        )
        return tx, credit

    # -- Defect 1: the refunded total is spelled ``refunded_amount`` here -----

    def test_history_payment_row_reports_refund_via_refunded_amount(self):
        self.assertTrue(history_row_shows_refund(self.PAYMENT_ROW))

    def test_detects_refund_from_payment_row_alone(self):
        """The PAYMENT row carries the refunded total; no REFUND row needed."""
        history_map = {"TAAA4QQ7M99": self.PAYMENT_ROW}
        self.assertTrue(
            is_checkout_refunded(self.CHECKOUT_PAYLOAD, history_map=history_map)
        )

    def test_refund_of_full_capture_is_not_read_as_partial(self):
        """15.50 of 15.50 is full — a partial reading would skip the row.

        ``refunded_amount`` returning 0 off the unread key made every
        externally refunded payment look like a zero-amount refund, and the
        credit's €20 face value is not the yardstick — the €15.50 capture is.
        """
        history_map = {"TAAA4QQ7M99": self.PAYMENT_ROW}
        self.assertEqual(
            refunded_amount(self.CHECKOUT_PAYLOAD, history_map=history_map),
            Decimal("15.5"),
        )

    # -- Defect 2: last-write-wins evicted the REFUND row --------------------

    def test_index_history_keeps_refund_signal_whatever_the_arrival_order(self):
        """The invariant is that the indexed row SHOWS a refund.

        Not that it is the ``REFUND`` row specifically: once the
        ``refunded_amount`` spelling is read, the PAYMENT row carries the
        refunded total too and is an equally good answer — a better one for
        ``refunded_amount()``, which reads the total straight off it.
        """
        for order in ([self.REFUND_ROW, self.PAYMENT_ROW],
                      [self.PAYMENT_ROW, self.REFUND_ROW]):
            with self.subTest(first=order[0]["type"]):
                history_map = index_history({}, order)
                self.assertTrue(history_row_shows_refund(history_map["TAAA4QQ7M99"]))

    def test_index_history_never_lets_a_plain_payment_row_evict_the_refund(self):
        """The eviction that actually hid this refund.

        A payment row with no refund signal must lose to the REFUND row beside
        it in BOTH directions. Under the old last-write-wins index, the pair
        arriving as ``[REFUND, payment]`` left the payment row in the map and
        the refund was gone.
        """
        stale = {k: v for k, v in self.PAYMENT_ROW.items() if k != "refunded_amount"}
        for order in ([self.REFUND_ROW, stale], [stale, self.REFUND_ROW]):
            with self.subTest(first=order[0]["type"]):
                history_map = index_history({}, order)
                self.assertEqual(history_map["TAAA4QQ7M99"]["type"], "REFUND")

    def test_index_history_survives_junk_rows(self):
        history_map = index_history({}, [None, "nope", {}, {"status": "REFUNDED"}])
        self.assertEqual(history_map, {})

    # -- Defect 3: a stale prefetch row suppressed the per-code lookup -------

    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments."
        "SumUpClient.get_transactions_history"
    )
    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout"
    )
    def test_stale_prefetch_row_does_not_suppress_per_code_lookup(
        self, mock_get_checkout, mock_get_history
    ):
        """The prefetch knows the code but not the refund; look again anyway.

        The prefetch covers the account's most recent 100 transactions. A
        refund taken today against a three-week-old payment falls outside that
        window while the payment's own row sits inside it — so the code IS
        present, carries no refund signal, and the old ``code not in
        history_map`` guard skipped the targeted lookup that would have found
        the refund.
        """
        tx, credit = self._make_refunded_seat()
        mock_get_checkout.return_value = self.CHECKOUT_PAYLOAD

        stale_payment_row = dict(self.PAYMENT_ROW)
        stale_payment_row.pop("refunded_amount")

        def history(**kwargs):
            if kwargs.get("transaction_code") == "TAAA4QQ7M99":
                return {"items": [self.REFUND_ROW, stale_payment_row]}
            return {"items": [stale_payment_row]}

        mock_get_history.side_effect = history

        call_command(
            "reconcile_sumup_payments",
            reference="CRUSH-EVT-588-ce863b",
            quiet=True,
        )

        tx.refresh_from_db()
        credit.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(credit.status, CrushCredit.Status.VOID)

    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments."
        "SumUpClient.get_transactions_history"
    )
    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout"
    )
    def test_dry_run_reports_the_refund_without_writing(
        self, mock_get_checkout, mock_get_history
    ):
        tx, credit = self._make_refunded_seat()
        mock_get_checkout.return_value = self.CHECKOUT_PAYLOAD
        mock_get_history.return_value = {
            "items": [self.REFUND_ROW, self.PAYMENT_ROW]
        }

        out = io.StringIO()
        call_command(
            "reconcile_sumup_payments",
            reference="CRUSH-EVT-588-ce863b",
            dry_run=True,
            stdout=out,
        )

        self.assertIn("External refund detected", out.getvalue())
        tx.refresh_from_db()
        credit.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(credit.status, CrushCredit.Status.ACTIVE)

    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments."
        "SumUpClient.get_transactions_history"
    )
    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout"
    )
    def test_history_lookup_failure_is_an_error_not_a_still_paid_tick(
        self, mock_get_checkout, mock_get_history
    ):
        """An unchecked row must never read as a verified one.

        Logging the provider error is not enough by itself: the row still fell
        through to the ``✓ … still PAID`` line and was counted clean, and in
        production the console handler only emits ERROR, so the operator saw a
        tick for a check that never ran. Now that the targeted lookup runs for
        every unrefunded row, a transient SumUp failure is routine rather than
        exceptional — so it has to be reported as unknown, not as paid.
        """
        self._make_refunded_seat()
        mock_get_checkout.return_value = self.CHECKOUT_PAYLOAD
        mock_get_history.side_effect = SumUpError("history unavailable")

        out = io.StringIO()
        with self.assertLogs(
            "crush_lu.management.commands.reconcile_sumup_payments", level="WARNING"
        ) as logs:
            call_command(
                "reconcile_sumup_payments",
                reference="CRUSH-EVT-588-ce863b",
                stdout=out,
            )

        printed = out.getvalue()
        self.assertIn("UNKNOWN", printed)
        self.assertNotIn("still PAID", printed)
        self.assertIn("1 error(s)", printed)
        self.assertTrue(any("TAAA4QQ7M99" in line for line in logs.output), logs.output)

    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments."
        "SumUpClient.get_transactions_history"
    )
    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout"
    )
    def test_quiet_still_reports_an_unverifiable_row(
        self, mock_get_checkout, mock_get_history
    ):
        """--quiet hides ticks, not errors — an unknown row is actionable."""
        self._make_refunded_seat()
        mock_get_checkout.return_value = self.CHECKOUT_PAYLOAD
        mock_get_history.side_effect = SumUpError("history unavailable")

        out = io.StringIO()
        call_command(
            "reconcile_sumup_payments",
            reference="CRUSH-EVT-588-ce863b",
            quiet=True,
            stdout=out,
        )
        self.assertIn("UNKNOWN", out.getvalue())

    @patch("crush_lu.management.commands.reconcile_sumup_payments.time.sleep")
    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments."
        "SumUpClient.get_transactions_history"
    )
    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout"
    )
    def test_batch_delay_throttles_the_targeted_history_request(
        self, mock_get_checkout, mock_get_history, mock_sleep
    ):
        """--batch-delay promises a pause "between SumUp API requests".

        The targeted lookup is the second request for a row, and on the now-common
        unrefunded path it followed the checkout call immediately — two
        back-to-back calls no value of the setting could throttle.
        """
        self._make_refunded_seat()
        mock_get_checkout.return_value = self.CHECKOUT_PAYLOAD
        # No refund anywhere: forces the targeted lookup and nothing else.
        clean = {k: v for k, v in self.PAYMENT_ROW.items() if k != "refunded_amount"}
        mock_get_history.return_value = {"items": [clean]}

        call_command(
            "reconcile_sumup_payments",
            reference="CRUSH-EVT-588-ce863b",
            batch_delay=0.05,
            quiet=True,
        )

        mock_sleep.assert_called_with(0.05)

    # -- Defect 2, second direction: cumulative totals must win --------------

    def test_cumulative_refunded_total_beats_a_bare_refund_row(self):
        """Several partial refunds adding up to the capture are a FULL refund.

        Under descending order the newest REFUND row arrives first, and it
        states only its own amount. Preferring it over the PAYMENT row that
        carries SumUp's cumulative ``refunded_amount`` makes a fully refunded
        payment read as partial — which the guard then parks for manual review
        and never reconciles. The opposite failure of the one this PR fixes,
        reachable through the same index.
        """
        payment_cumulative = dict(self.PAYMENT_ROW, refunded_amount=15.5)
        latest_partial = dict(self.REFUND_ROW, amount=5.0)

        for order in ([latest_partial, payment_cumulative],
                      [payment_cumulative, latest_partial]):
            with self.subTest(first=order[0]["type"]):
                history_map = index_history({}, order)
                self.assertEqual(
                    refunded_amount(self.CHECKOUT_PAYLOAD, history_map=history_map),
                    Decimal("15.5"),
                )

    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments."
        "SumUpClient.get_transactions_history"
    )
    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout"
    )
    def test_bare_refund_row_in_prefetch_does_not_suppress_the_lookup(
        self, mock_get_checkout, mock_get_history
    ):
        """A refund row without a total still leaves the AMOUNT unestablished.

        The prefetch covers the account's newest 100 transactions, so for an
        older capture refunded in several goes it can hold the recent REFUND
        rows while the PAYMENT row carrying the cumulative ``refunded_amount``
        sits outside the window. Gating the targeted lookup on "does anything
        show a refund" skipped it exactly there, and the guard then sized a
        FULL refund off one partial and parked the payment unreconciled.
        """
        tx, credit = self._make_refunded_seat()
        mock_get_checkout.return_value = self.CHECKOUT_PAYLOAD

        one_partial = dict(self.REFUND_ROW, amount=5.0)
        cumulative = dict(self.PAYMENT_ROW, refunded_amount=15.5)

        def history(**kwargs):
            if kwargs.get("transaction_code") == "TAAA4QQ7M99":
                return {"items": [cumulative, one_partial]}
            # Prefetch: only the newest refund row is inside the window.
            return {"items": [one_partial]}

        mock_get_history.side_effect = history

        out = io.StringIO()
        call_command(
            "reconcile_sumup_payments", reference="CRUSH-EVT-588-ce863b", stdout=out
        )

        self.assertNotIn("PARTIAL", out.getvalue())
        tx.refresh_from_db()
        credit.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(credit.status, CrushCredit.Status.VOID)

    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments."
        "SumUpClient.get_transactions_history"
    )
    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout"
    )
    def test_individual_refunds_are_summed_when_no_cumulative_row_exists(
        self, mock_get_checkout, mock_get_history
    ):
        """Belt and braces: three partials adding to the capture are FULL.

        If SumUp answers the per-code lookup with refund rows but none stating
        a cumulative total, the individual amounts are added up rather than the
        largest single one being taken for the whole.
        """
        tx, credit = self._make_refunded_seat()
        mock_get_checkout.return_value = self.CHECKOUT_PAYLOAD
        partials = [
            dict(self.REFUND_ROW, amount=5.0, id="r1"),
            dict(self.REFUND_ROW, amount=5.0, id="r2"),
            dict(self.REFUND_ROW, amount=5.5, id="r3"),
        ]
        mock_get_history.return_value = {"items": partials}

        out = io.StringIO()
        call_command(
            "reconcile_sumup_payments", reference="CRUSH-EVT-588-ce863b", stdout=out
        )

        self.assertNotIn("PARTIAL", out.getvalue())
        tx.refresh_from_db()
        credit.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(credit.status, CrushCredit.Status.VOID)

    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments."
        "SumUpClient.get_transactions_history"
    )
    @patch(
        "crush_lu.management.commands.reconcile_sumup_payments.SumUpClient.get_checkout"
    )
    def test_a_genuine_partial_refund_is_still_refused(
        self, mock_get_checkout, mock_get_history
    ):
        """The guard must not be blunted by any of the above.

        Summing and the rank preference exist to stop a FULL refund reading as
        partial. A refund that really is partial must still be left alone —
        reconciling it would unbook a still-mostly-paid seat and void the
        member's credit over a goodwill adjustment.
        """
        tx, credit = self._make_refunded_seat()
        mock_get_checkout.return_value = self.CHECKOUT_PAYLOAD
        mock_get_history.return_value = {
            "items": [dict(self.REFUND_ROW, amount=5.0)]
        }

        out = io.StringIO()
        call_command(
            "reconcile_sumup_payments", reference="CRUSH-EVT-588-ce863b", stdout=out
        )

        self.assertIn("PARTIAL", out.getvalue())
        tx.refresh_from_db()
        credit.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(credit.status, CrushCredit.Status.ACTIVE)

    def test_refund_rank_orders_the_three_kinds_of_row(self):
        bare_payment = {k: v for k, v in self.PAYMENT_ROW.items()
                        if k != "refunded_amount"}
        self.assertEqual(_history_refund_rank(self.PAYMENT_ROW), 2)
        self.assertEqual(_history_refund_rank(self.REFUND_ROW), 1)
        self.assertEqual(_history_refund_rank(bare_payment), 0)
        self.assertEqual(_history_refund_rank(None), 0)


class HistoryOrderNormalisationTests(TestCase):
    """``order=desc`` is not a value SumUp's history endpoint understands.

    It does not reject the request either — it silently sorts ASCENDING, so a
    caller asking for the newest 100 transactions received the oldest 100 the
    merchant ever took. Any refund made recently was outside the prefetch by
    construction.
    """

    def test_short_forms_map_to_the_spelled_out_values(self):
        self.assertEqual(_normalise_history_order("desc"), "descending")
        self.assertEqual(_normalise_history_order("DESC"), "descending")
        self.assertEqual(_normalise_history_order("asc"), "ascending")
        self.assertEqual(_normalise_history_order("ascending"), "ascending")

    def test_unknown_and_empty_fall_back_to_descending(self):
        self.assertEqual(_normalise_history_order(""), "descending")
        self.assertEqual(_normalise_history_order(None), "descending")
        self.assertEqual(_normalise_history_order("sideways"), "descending")

    @patch("crush_lu.services.sumup.requests.get")
    def test_client_sends_the_spelling_sumup_honours(self, mock_get):
        mock_get.return_value = MagicMock(
            raise_for_status=MagicMock(), json=MagicMock(return_value={"items": []})
        )
        SumUpClient(api_key="sup_sk_test", merchant_code="TESTLU")
        SumUpClient(api_key="sup_sk_test").get_transactions_history(
            limit=100, order="desc"
        )
        self.assertEqual(mock_get.call_args.kwargs["params"]["order"], "descending")
