"""#925 WP1 part 2: resolving recovery cases.

Refunds are made by hand in the SumUp dashboard (D2/D4); the sweep records
them without touching the membership. A coach-unavailable case is applied
once the member agrees to a coach (D3). The SumUp refund API is never called:
the sweep is driven with a canned refunded payload.

Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

from io import StringIO
from types import SimpleNamespace

from django.core import mail

from crush_lu.management.commands.reconcile_sumup_payments import (
    NEEDS_REVIEW,
    RECONCILED,
    Command,
)
from crush_lu.models import PremiumPaymentRecoveryCase
from crush_lu.models.payments import PaymentTransaction
from crush_lu.tests.test_premium_recovery import Reason, User, _Base

Case = PremiumPaymentRecoveryCase
REFUNDED = {"status": "REFUNDED", "amount": 10.0}


def _sweep():
    return Command(stdout=StringIO())


class UnappliedCaptureRefundTests(_Base):
    def _case(self, ref, reason, membership=None, **kwargs):
        tx = self._tx(ref, status=PaymentTransaction.Status.PAID)
        if membership is not None:
            PaymentTransaction.objects.filter(pk=tx.pk).update(
                premium_membership=membership
            )
            tx.refresh_from_db()
        return tx, Case.objects.create(
            payment=tx,
            user=self.member,
            premium_membership=membership or self.membership,
            reason=reason,
            **kwargs,
        )

    def _activate(self):
        applied = self._tx("RES-APPLIED", status=PaymentTransaction.Status.PAID)
        self.membership.confirm()
        return applied

    def test_duplicate_capture_refund_leaves_the_membership_active(self):
        applied = self._activate()
        tx, case = self._case("RES-DUP", Reason.DUPLICATE_CAPTURE)
        self.assertEqual(_sweep()._reconcile_refunded(tx, REFUNDED), RECONCILED)
        tx.refresh_from_db()
        case.refresh_from_db()
        self.membership.refresh_from_db()
        applied.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(
            (case.status, case.resolution, case.resolved_by),
            (Case.Status.RESOLVED, Case.Resolution.REFUNDED, None),
        )
        self.assertIsNotNone(case.resolved_at)
        # D2: the paid membership and its applied capture stay intact.
        self.assertEqual(self.membership.status, "active")
        self.assertEqual(applied.status, PaymentTransaction.Status.PAID)

    def test_cancelled_request_refund_settles_only_payment_and_case(self):
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        tx, case = self._case("RES-CANCEL", Reason.REQUEST_CANCELLED)
        self.assertEqual(_sweep()._reconcile_refunded(tx, REFUNDED), RECONCILED)
        case.refresh_from_db()
        self.assertEqual(case.resolution, Case.Resolution.REFUNDED)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "cancelled")

    def test_dry_run_reports_without_writing(self):
        self._activate()
        tx, case = self._case("RES-DRY", Reason.DUPLICATE_CAPTURE)
        self.assertEqual(
            _sweep()._reconcile_refunded(tx, REFUNDED, dry_run=True), RECONCILED
        )
        tx.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(case.status, Case.Status.OPEN)

    def test_ambiguous_staff_only_case_still_goes_to_review(self):
        self._activate()
        tx, case = self._case("RES-AMB", Reason.DUPLICATE_CAPTURE, staff_only=True)
        # Which capture was applied is unknown: a human decides.
        self.assertEqual(_sweep()._reconcile_refunded(tx, REFUNDED), NEEDS_REVIEW)
        tx.refresh_from_db()
        case.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(case.status, Case.Status.OPEN)

    def test_refund_of_an_applied_case_takes_the_usual_path(self):
        tx, case = self._case("RES-APPLIED-CASE", Reason.COACH_UNAVAILABLE)
        Case.objects.filter(pk=case.pk).update(
            status=Case.Status.RESOLVED, resolution=Case.Resolution.APPLIED
        )
        self.membership.confirm()
        self.assertEqual(_sweep()._reconcile_refunded(tx, REFUNDED), RECONCILED)
        self.membership.refresh_from_db()
        # That capture WAS the membership's payment: refunding it ends it.
        self.assertEqual(self.membership.status, "cancelled")


class ApplyCasePaymentTests(_Base):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username="res-staff@example.invalid",
            email="res-staff@example.invalid",
            password="pass12345",
            is_staff=True,
        )
        self.tx = self._tx("RES-COACH", status=PaymentTransaction.Status.PAID)
        self.case = Case.objects.create(
            payment=self.tx,
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.COACH_UNAVAILABLE,
        )

    def test_member_agreed_coach_activates_and_resolves(self):
        from crush_lu.services.premium_recovery import apply_case_payment

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertIsNone(apply_case_payment(self.case.pk, self.staff))
        self.membership.refresh_from_db()
        self.case.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        self.assertEqual(self.membership.confirmed_by, self.staff)
        self.assertEqual(
            (self.case.status, self.case.resolution, self.case.resolved_by),
            (Case.Status.RESOLVED, Case.Resolution.APPLIED, self.staff),
        )
        # The Premium receipt follows.
        self.assertEqual(len(self._member_mails()), 1)

    def test_a_full_coach_leaves_the_case_open(self):
        from crush_lu.services.premium_recovery import apply_case_payment

        self._fill_the_coach()
        error = apply_case_payment(self.case.pk, self.staff)
        self.assertTrue(error)
        self.case.refresh_from_db()
        self.membership.refresh_from_db()
        self.assertEqual(self.case.status, Case.Status.OPEN)
        self.assertEqual(self.membership.status, "pending")

    def test_only_a_coach_unavailable_case_can_be_applied(self):
        from crush_lu.services.premium_recovery import apply_case_payment

        Case.objects.filter(pk=self.case.pk).update(reason=Reason.REQUEST_CANCELLED)
        self.assertTrue(apply_case_payment(self.case.pk, self.staff))
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")

    def test_payment_lock_is_taken_before_the_membership(self):
        import inspect

        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery.apply_case_payment)
        payment = src.index("PaymentTransaction.objects.select_for_update()")
        self.assertLess(payment, src.index("Case.objects.select_for_update()"))
        self.assertLess(payment, src.index("membership.confirm("))

    def test_hand_resolution_is_recorded_as_other(self):
        from crush_lu.admin import crush_admin_site

        model_admin = crush_admin_site._registry[Case]
        self.case.status = Case.Status.RESOLVED
        request = SimpleNamespace(user=self.staff)
        model_admin.save_model(
            request, self.case, SimpleNamespace(changed_data=["status"]), True
        )
        self.case.refresh_from_db()
        self.assertEqual(
            (self.case.resolution, self.case.resolved_by),
            (Case.Resolution.OTHER, self.staff),
        )
        self.assertIn("resolution", model_admin.readonly_fields)

    def test_queue_shows_the_next_step(self):
        from crush_lu.admin import crush_admin_site

        model_admin = crush_admin_site._registry[Case]
        self.assertIn("Apply payment", model_admin.next_step(self.case))
        self.case.reason = Reason.DUPLICATE_CAPTURE
        self.assertIn("SumUp dashboard", model_admin.next_step(self.case))
        self.case.staff_only = True
        self.assertIn("Verify", model_admin.next_step(self.case))
