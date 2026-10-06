"""#925: a captured Premium payment that is NOT applied opens a recovery case.

PR 1/3: the durable case, created on commit and idempotently; the member
notice (pages + email) and the staff alert. No SumUp call is ever allowed:
the client and the socket layer are patched to fail loudly.

Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

import inspect
import re
from collections import defaultdict
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    PremiumMembership,
    PremiumPaymentRecoveryCase,
)
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import UserDataConsent
from crush_lu.views_payments import _apply_paid_checkout

User = get_user_model()
Reason = PremiumPaymentRecoveryCase.Reason

ALERT = "alerts@example.invalid"
D1_START = "We received your payment of"


@override_settings(PREMIUM_REDIRECTS_TO_BETA=False, PREMIUM_RECOVERY_ALERT_EMAIL=ALERT)
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        no_network = self.enterContext(
            patch(
                "socket.socket.connect",
                side_effect=AssertionError("premium recovery must not hit the net"),
            )
        )
        self.addCleanup(no_network.assert_not_called)
        sumup = self.enterContext(
            patch(
                "crush_lu.views_payments.SumUpClient",
                side_effect=AssertionError("SumUp must not be called"),
            )
        )
        self.addCleanup(sumup.assert_not_called)
        self.member = User.objects.create_user(
            username="rec-member@example.invalid",
            email="rec-member@example.invalid",
            password="pass12345",
            first_name="Mia",
        )
        UserDataConsent.objects.update_or_create(
            user=self.member,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.profile = CrushProfile.objects.create(
            user=self.member, gender="F", location="Luxembourg"
        )
        coach_user = User.objects.create_user(
            username="rec-coach@example.invalid",
            email="rec-coach@example.invalid",
            password="pass12345",
        )
        self.coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=1,
        )
        self.membership = PremiumMembership.objects.create(
            user=self.member, coach=self.coach, status="pending"
        )

    def _tx(self, ref, status=PaymentTransaction.Status.PENDING):
        return PaymentTransaction.objects.create(
            transaction_reference=ref,
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=f"CHK_{ref}",
            amount=Decimal("10.00"),
            currency="EUR",
            status=status,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.member,
            premium_membership=self.membership,
        )

    def _unlink_and_delete_membership(self):
        """Legacy state: rows unlinked before the FK became PROTECT."""
        PaymentTransaction.objects.filter(premium_membership=self.membership).update(
            premium_membership=None
        )
        self.membership.delete()

    def _apply(self, tx):
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})
        tx.refresh_from_db()

    def _fill_the_coach(self):
        rival = User.objects.create_user(
            username="rec-rival@example.invalid", password="pass12345"
        )
        CrushProfile.objects.create(user=rival, gender="M")
        PremiumMembership.objects.create(
            user=rival, coach=self.coach, status="active", payment_confirmed=True
        )

    def _alerts(self):
        return [m for m in mail.outbox if m.to == [ALERT]]

    def _member_mails(self):
        return [m for m in mail.outbox if m.to == [self.member.email]]


class FailurePathCaseTests(_Base):
    def _assert_one_case(self, tx, reason):
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        cases = list(PremiumPaymentRecoveryCase.objects.all())
        self.assertEqual(len(cases), 1)
        case = cases[0]
        self.assertEqual(case.payment_id, tx.pk)
        self.assertEqual(case.reason, reason)
        self.assertEqual(case.status, PremiumPaymentRecoveryCase.Status.OPEN)
        self.assertEqual(case.user_id, self.member.pk)
        self.assertEqual(case.premium_membership_id, self.membership.pk)
        return case

    def test_coach_full_opens_coach_unavailable_case(self):
        tx = self._tx("REC-FULL")
        self._fill_the_coach()
        with self.assertLogs("crush_lu.views_payments", level="ERROR"):
            self._apply(tx)
        self._assert_one_case(tx, Reason.COACH_UNAVAILABLE)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")

    def test_cancelled_request_opens_request_cancelled_case(self):
        tx = self._tx("REC-CANC")
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        self._apply(tx)
        self._assert_one_case(tx, Reason.REQUEST_CANCELLED)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "cancelled")

    def test_second_capture_opens_duplicate_capture_case(self):
        from unittest.mock import MagicMock

        first = self._tx("REC-DUP-1")
        second = self._tx("REC-DUP-2")
        # Activation closes the other checkout; SumUp says it was captured too.
        client = MagicMock()
        client.deactivate_checkout.return_value = False
        client.get_checkout.return_value = {
            "id": second.sumup_checkout_id,
            "status": "PAID",
            "amount": 10.0,
            "currency": "EUR",
        }
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            self._apply(first)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        client.get_checkout.assert_called_once_with("CHK_REC-DUP-2")
        client.refund.assert_not_called()

        # Its own webhook arriving later is a replay: still one case.
        self._apply(second)
        self._assert_one_case(second, Reason.DUPLICATE_CAPTURE)
        # D2: membership untouched.
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")

    def test_capture_racing_a_manual_confirmation_is_staff_only(self):
        staff = User.objects.create_user(
            username="rec-confirmer@example.invalid",
            email="rec-confirmer@example.invalid",
            password="pass12345",
            is_staff=True,
        )
        tx = self._tx("REC-RACE")
        membership_pk = self.membership.pk

        def staff_confirms_first(*args, **kwargs):
            # Staff confirm by hand between the callback's read and confirm().
            PremiumMembership.objects.filter(pk=membership_pk).update(
                status="active", payment_confirmed=True, confirmed_by=staff
            )
            raise ValueError("Only a pending membership can be confirmed.")

        mail.outbox.clear()
        with (
            patch.object(
                PremiumMembership, "confirm", side_effect=staff_confirms_first
            ),
            self.assertLogs("crush_lu.views_payments", level="ERROR"),
        ):
            self._apply(tx)
        case = PremiumPaymentRecoveryCase.objects.get(payment=tx)
        self.assertEqual(case.reason, Reason.DUPLICATE_CAPTURE)
        # Staff may have confirmed this very capture: no definitive notice.
        self.assertTrue(case.staff_only)
        self.assertFalse(case.member_unknown)
        self.assertEqual(self._member_mails(), [])

    def test_duplicate_and_cancelled_paths_write_an_error_log(self):
        # The log is the only trace if both mails fail.
        self._apply(self._tx("REC-LOG-1"))
        second = self._tx("REC-LOG-2")
        with self.assertLogs("crush_lu.views_payments", level="ERROR") as logs:
            self._apply(second)
        output = "\n".join(logs.output)
        self.assertIn("REC-LOG-2", output)
        self.assertIn("status=active", output)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_revoked_beta_tester_opens_beta_revoked_case(self):
        tx = self._tx("REC-BETA")
        with self.assertLogs("crush_lu.views_payments", level="ERROR"):
            self._apply(tx)
        self._assert_one_case(tx, Reason.BETA_REVOKED)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")

    def test_missing_profile_keeps_paid_and_opens_other_case(self):
        """confirm() raised CrushProfile.DoesNotExist uncaught, rolling the
        PAID record back -- the only local trace of a real charge."""
        self.profile.delete()
        tx = self._tx("REC-NOPROF")
        with self.assertLogs("crush_lu.views_payments", level="ERROR"):
            self._apply(tx)
        self._assert_one_case(tx, Reason.OTHER)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")

    def test_successful_confirmation_opens_no_case(self):
        tx = self._tx("REC-OK")
        self._apply(tx)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        self.assertFalse(PremiumPaymentRecoveryCase.objects.exists())
        self.assertEqual(self._alerts(), [])


class OnCommitAndIdempotencyTests(_Base):
    def test_case_commits_with_paid_and_mail_waits_for_commit(self):
        tx = self._tx("REC-COMMIT")
        self._fill_the_coach()
        with self.assertLogs("crush_lu.views_payments", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                _apply_paid_checkout(tx, {"status": "PAID"})
        # The case is part of the PAID write, not a later callback.
        self.assertEqual(PremiumPaymentRecoveryCase.objects.count(), 1)
        self.assertEqual(mail.outbox, [])
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)

        for callback in callbacks:
            callback()
        self.assertEqual(len(self._alerts()), 1)

    def test_case_failure_leaves_the_capture_retryable(self):
        tx = self._tx("REC-BOOM")
        self._fill_the_coach()
        with patch(
            "crush_lu.models.PremiumPaymentRecoveryCase.objects.get_or_create",
            side_effect=RuntimeError("db down"),
        ):
            with self.assertLogs("crush_lu.views_payments", level="ERROR"):
                with self.assertRaises(RuntimeError):
                    self._apply(tx)
        tx.refresh_from_db()
        # Not PAID without a case: the next return/webhook retries it.
        self.assertEqual(tx.status, PaymentTransaction.Status.PENDING)
        self._apply(tx)
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(PremiumPaymentRecoveryCase.objects.count(), 1)

    def test_replay_keeps_one_case_one_member_mail_one_alert(self):
        from crush_lu.services.premium_recovery import open_case

        tx = self._tx("REC-REPLAY")
        self._fill_the_coach()
        with self.assertLogs("crush_lu.views_payments", level="ERROR"):
            self._apply(tx)
        # Webhook + browser return replaying the same capture.
        self._apply(tx)
        self._apply(tx)
        # A racing second callback that got past the PAID guard.
        tx.refresh_from_db()
        self.assertFalse(open_case(tx, Reason.COACH_UNAVAILABLE)[1])

        self.assertEqual(PremiumPaymentRecoveryCase.objects.count(), 1)
        self.assertEqual(len(self._member_mails()), 1)
        self.assertEqual(len(self._alerts()), 1)


class NotificationTests(_Base):
    def _case_via_coach_full(self, ref="REC-MAIL"):
        tx = self._tx(ref)
        self._fill_the_coach()
        with self.assertLogs("crush_lu.views_payments", level="ERROR"):
            self._apply(tx)
        return tx, PremiumPaymentRecoveryCase.objects.get(payment=tx)

    def test_member_email_carries_d1_and_d5_not_the_receipt(self):
        tx, case = self._case_via_coach_full()
        mails = self._member_mails()
        self.assertEqual(len(mails), 1)
        body = mails[0].body
        self.assertIn(D1_START, body)
        self.assertIn("10.00 EUR", body)
        self.assertIn(tx.transaction_reference, body)
        self.assertIn("Please do not pay again.", body)
        self.assertIn("Our team will contact you about the next step.", body)
        self.assertNotIn("now active", body)
        self.assertIsNotNone(case.member_notified_at)

    def test_member_email_uses_preferred_language(self):
        self.profile.preferred_language = "fr"
        self.profile.save(update_fields=["preferred_language"])
        self._case_via_coach_full("REC-FR")
        body = self._member_mails()[0].body
        self.assertIn("Nous avons bien reçu votre paiement", body)
        self.assertIn("Notre équipe vous contactera", body)

    def test_staff_alert_names_reason_member_amount_reference_and_link(self):
        tx, case = self._case_via_coach_full("REC-ALERT")
        alerts = self._alerts()
        self.assertEqual(len(alerts), 1)
        body = alerts[0].body
        self.assertIn("Coach unavailable", body)
        self.assertIn(self.member.email, body)
        self.assertIn("10.00 EUR", body)
        self.assertIn(tx.transaction_reference, body)
        self.assertIn(
            f"https://crush.lu/crush-admin/crush_lu/premiumpaymentrecoverycase/"
            f"{case.pk}/change/",
            body,
        )
        self.assertIsNotNone(case.staff_alerted_at)

    @override_settings(PREMIUM_RECOVERY_ADMIN_BASE_URL="https://test.crush.lu")
    def test_alert_link_host_is_a_setting(self):
        _, case = self._case_via_coach_full("REC-STAGING")
        body = self._alerts()[0].body
        self.assertIn(
            f"https://test.crush.lu/crush-admin/crush_lu/premiumpaymentrecoverycase/"
            f"{case.pk}/change/",
            body,
        )
        self.assertNotIn("https://crush.lu/", body)

    @override_settings(PREMIUM_RECOVERY_ALERT_EMAIL="ops@example.invalid")
    def test_alert_address_is_a_setting(self):
        self._case_via_coach_full("REC-SETTING")
        self.assertEqual(
            [m.to for m in mail.outbox if m.to != [self.member.email]],
            [["ops@example.invalid"]],
        )

    def test_mail_failures_are_logged_never_raised(self):
        tx = self._tx("REC-MAILFAIL")
        self._fill_the_coach()
        with patch(
            "azureproject.email_utils.send_domain_email",
            side_effect=RuntimeError("graph down"),
        ), patch(
            "crush_lu.email_helpers.send_domain_email",
            side_effect=RuntimeError("graph down"),
        ):
            with self.assertLogs(level="ERROR") as logs:
                self._apply(tx)
        case = PremiumPaymentRecoveryCase.objects.get(payment=tx)
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.assertIsNone(case.member_notified_at)
        self.assertIsNone(case.staff_alerted_at)
        output = "\n".join(logs.output)
        self.assertIn("recovery notice", output)
        self.assertIn("staff alert", output)


class MemberNoticeTests(_Base):
    def setUp(self):
        super().setUp()
        self.tx = self._tx("REC-PAGE", status=PaymentTransaction.Status.PAID)
        self.case = PremiumPaymentRecoveryCase.objects.create(
            payment=self.tx,
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.COACH_UNAVAILABLE,
        )
        self.profile.verification_status = "verified"
        self.profile.save(update_fields=["verification_status"])
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.member)

    def _resolve(self):
        self.case.status = PremiumPaymentRecoveryCase.Status.RESOLVED
        self.case.save(update_fields=["status"])

    def _notice_shown(self, html):
        return 'data-testid="premium-recovery-notice"' in html

    def test_dashboard_shows_notice_instead_of_pay_cta(self):
        html = self.client.get("/en/dashboard/").content.decode()
        self.assertTrue(self._notice_shown(html))
        self.assertIn(D1_START, html)
        self.assertIn("REC-PAGE", html)
        self.assertIn("Please do not pay again.", html)
        self.assertNotIn("Go Premium — first month free", html)

    def test_dashboard_hides_notice_once_resolved(self):
        self._resolve()
        html = self.client.get("/en/dashboard/").content.decode()
        self.assertFalse(self._notice_shown(html))
        self.assertIn("Go Premium — first month free", html)

    def _premium_card(self, html):
        match = re.search(r'id="premium-plan".*?</section>', html, re.S)
        self.assertIsNotNone(match, "Premium plan card missing")
        return match.group(0)

    def test_membership_page_shows_notice_instead_of_pay_cta(self):
        card = self._premium_card(self.client.get("/en/membership/").content.decode())
        self.assertTrue(self._notice_shown(card))
        self.assertIn("REC-PAGE", card)
        for cta in ("/premium/coaches/", "/crush-connect/", "/support/"):
            self.assertNotIn(cta, card)

    def test_membership_page_hides_notice_once_resolved(self):
        self._resolve()
        html = self.client.get("/en/membership/").content.decode()
        self.assertFalse(self._notice_shown(html))

    def test_notice_is_translated(self):
        html = self.client.get("/de/membership/").content.decode()
        self.assertIn("Wir haben deine Zahlung von", html)
        self.assertIn("Bitte zahle nicht erneut.", html)

    def test_return_page_shows_d1_notice(self):
        response = self.client.get(
            "/payments/sumup/return/", {"ref": "REC-PAGE"}, follow=True
        )
        texts = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any(t.startswith(D1_START) and "REC-PAGE" in t for t in texts), texts
        )
        self.assertFalse(any("could not activate" in t for t in texts), texts)

    def test_return_page_says_nothing_definitive_for_a_staff_only_case(self):
        self.case.staff_only = True
        self.case.save(update_fields=["staff_only"])
        response = self.client.get(
            "/payments/sumup/return/", {"ref": "REC-PAGE"}, follow=True
        )
        texts = [str(m) for m in response.context["messages"]]
        self.assertFalse(any(t.startswith(D1_START) for t in texts), texts)
        self.assertFalse(any("completed successfully" in t for t in texts), texts)
        # Nor the fallback "could not activate" claim.
        self.assertFalse(any("could not activate" in t for t in texts), texts)

    def test_return_page_on_duplicate_capture_does_not_claim_premium(self):
        self.membership.status = "active"
        self.membership.save(update_fields=["status"])
        self.case.reason = Reason.DUPLICATE_CAPTURE
        self.case.save(update_fields=["reason"])
        response = self.client.get(
            "/payments/sumup/return/", {"ref": "REC-PAGE"}, follow=True
        )
        texts = [str(m) for m in response.context["messages"]]
        self.assertTrue(any(t.startswith(D1_START) for t in texts), texts)
        self.assertFalse(any("You're Premium" in t for t in texts), texts)

    def test_return_page_skips_generic_success_when_case_is_open(self):
        response = self.client.get(
            "/payments/sumup/return/", {"ref": "REC-PAGE"}, follow=True
        )
        texts = [str(m) for m in response.context["messages"]]
        self.assertFalse(any("completed successfully" in t for t in texts), texts)

    def test_return_page_amount_is_localized_like_the_other_surfaces(self):
        self.profile.preferred_language = "de"
        self.profile.save(update_fields=["preferred_language"])
        response = self.client.get(
            "/payments/sumup/return/", {"ref": "REC-PAGE"}, follow=True
        )
        texts = [str(m) for m in response.context["messages"]]
        notice = [t for t in texts if "REC-PAGE" in t]
        self.assertEqual(len(notice), 1, texts)
        self.assertIn("10,00 EUR", notice[0])


class PendingVerificationNoticeTests(_Base):
    """A pending profile on the Premium path keeps its membership 'pending'
    while a coach_unavailable case is open: the path copy must not then say
    "payment pending" or link back to the pay page (#925 D1)."""

    def setUp(self):
        super().setUp()
        self.tx = self._tx("REC-PEND", status=PaymentTransaction.Status.PAID)
        PremiumPaymentRecoveryCase.objects.create(
            payment=self.tx,
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.COACH_UNAVAILABLE,
        )
        self.profile.verification_status = "pending"
        self.profile.completion_status = "submitted"
        self.profile.save(update_fields=["verification_status", "completion_status"])
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.member)

    def test_dashboard_drops_payment_pending_and_pay_link(self):
        response = self.client.get("/en/dashboard/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("payment pending", html)
        self.assertNotIn("/en/premium/coaches/", html)
        self.assertIn('data-testid="premium-recovery-notice"', html)
        self.assertIn('data-testid="premium-recovery-path"', html)

    def test_profile_submitted_shows_notice_not_payment_pending(self):
        response = self.client.get("/en/profile-submitted/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("Payment pending", html)
        self.assertNotIn("/en/premium/coaches/", html)
        self.assertNotIn("complete your Premium membership", html)
        self.assertIn('data-testid="premium-recovery-notice"', html)
        self.assertIn("REC-PEND", html)


class AdminVisibilityTests(_Base):
    def test_case_is_registered_on_the_coach_panel(self):
        from crush_lu.admin import crush_admin_site

        model_admin = crush_admin_site._registry[PremiumPaymentRecoveryCase]
        self.assertIn("reason", model_admin.list_filter)
        self.assertIn("status", model_admin.list_filter)
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))

    def test_superuser_can_open_the_changelist(self):
        admin = User.objects.create_superuser(
            username="rec-admin@example.invalid",
            email="rec-admin@example.invalid",
            password="pass12345",
        )
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(admin)
        PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-ADMIN", status=PaymentTransaction.Status.PAID),
            user=self.member,
            reason=Reason.OTHER,
        )
        response = client.get("/crush-admin/crush_lu/premiumpaymentrecoverycase/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "REC-ADMIN")


class CaseLifecycleTests(_Base):
    """Review of #925 PR 1/3: a case must be resolvable, and an old case must
    not take over a member's later, unrelated Premium request."""

    def _case(self, ref, membership, reason=Reason.COACH_UNAVAILABLE):
        return PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx(ref, status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=membership,
            reason=reason,
        )

    def _member_client(self):
        self.profile.verification_status = "verified"
        self.profile.save(update_fields=["verification_status"])
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(self.member)
        return client

    def test_staff_can_resolve_a_case_but_not_rewrite_it(self):
        case = self._case("REC-RESOLVE", self.membership)
        admin = User.objects.create_superuser(
            username="rec-staff@example.invalid",
            email="rec-staff@example.invalid",
            password="pass12345",
        )
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(admin)
        url = f"/crush-admin/crush_lu/premiumpaymentrecoverycase/{case.pk}/change/"
        response = client.post(
            url, {"status": "resolved", "reason": "other", "detail": "x"}
        )
        self.assertEqual(response.status_code, 302, response.content[:500])
        case.refresh_from_db()
        self.assertEqual(case.status, PremiumPaymentRecoveryCase.Status.RESOLVED)
        self.assertEqual(case.reason, Reason.COACH_UNAVAILABLE)
        self.assertEqual(case.detail, "")

    def test_payment_and_claim_admins_stay_read_only(self):
        from crush_lu.admin import crush_admin_site
        from crush_lu.models.payments import EventCheckoutCreationClaim

        for model in (PaymentTransaction, EventCheckoutCreationClaim):
            self.assertFalse(
                crush_admin_site._registry[model].has_change_permission(None), model
            )

    def test_case_on_old_cancelled_request_does_not_hide_a_new_one(self):
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        self._case("REC-OLD", self.membership, Reason.REQUEST_CANCELLED)
        PremiumMembership.objects.create(
            user=self.member, coach=self.coach, status="pending"
        )
        client = self._member_client()
        for path in ("/en/dashboard/", "/en/membership/"):
            html = client.get(path).content.decode()
            self.assertNotIn('data-testid="premium-recovery-notice"', html, path)

    def test_case_with_deleted_membership_still_shows(self):
        self._case("REC-NULL", None, Reason.OTHER)
        self._unlink_and_delete_membership()
        html = self._member_client().get("/en/dashboard/").content.decode()
        self.assertIn('data-testid="premium-recovery-notice"', html)

    def test_case_with_deleted_membership_does_not_hide_a_new_request(self):
        self._case("REC-NULL-OLD", None, Reason.OTHER)
        self._unlink_and_delete_membership()
        PremiumMembership.objects.create(
            user=self.member, coach=self.coach, status="pending"
        )
        client = self._member_client()
        shown = {
            path: 'data-testid="premium-recovery-notice"'
            in client.get(path).content.decode()
            for path in ("/en/dashboard/", "/en/membership/")
        }
        self.assertEqual(shown, {"/en/dashboard/": False, "/en/membership/": False})

    def test_active_member_with_duplicate_case_keeps_your_plan(self):
        self.membership.status = "active"
        self.membership.save(update_fields=["status"])
        self._case("REC-PLAN", self.membership, Reason.DUPLICATE_CAPTURE)
        html = self._member_client().get("/en/membership/").content.decode()
        card = re.search(r'id="premium-plan".*?</section>', html, re.S).group(0)
        self.assertIn("Your plan", card)
        self.assertIn('data-testid="premium-recovery-notice"', card)
        self.assertIn("REC-PLAN", card)

    def test_unlinked_capture_opens_a_case_for_the_payer(self):
        tx = self._tx("REC-UNLINKED")
        self._unlink_and_delete_membership()
        tx.refresh_from_db()
        self.assertIsNone(tx.premium_membership_id)
        with self.assertLogs("crush_lu.views_payments", level="CRITICAL"):
            self._apply(tx)
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        case = PremiumPaymentRecoveryCase.objects.get(payment=tx)
        self.assertEqual(
            (case.reason, case.user_id, case.premium_membership_id),
            (Reason.OTHER, self.member.pk, None),
        )
        self.assertEqual(len(self._alerts()), 1)
        self.assertEqual(len(self._member_mails()), 1)

    def test_unlinked_staff_assisted_capture_mails_only_staff(self):
        from crush_lu.services.premium_recovery import MEMBER_UNKNOWN_DETAIL

        staff = User.objects.create_user(
            username="rec-staff@example.invalid",
            email="rec-staff@example.invalid",
            password="pass12345",
            is_staff=True,
        )
        tx = self._tx("REC-ASSISTED")
        PaymentTransaction.objects.filter(pk=tx.pk).update(user=staff)
        self._unlink_and_delete_membership()
        tx.refresh_from_db()
        with self.assertLogs("crush_lu.views_payments", level="CRITICAL"):
            self._apply(tx)
        case = PremiumPaymentRecoveryCase.objects.get(payment=tx)
        self.assertIn(MEMBER_UNKNOWN_DETAIL, case.detail)
        # No member notice to the staff opener; the alert says why.
        self.assertEqual([m for m in mail.outbox if m.to == [staff.email]], [])
        self.assertIsNone(case.member_notified_at)
        alerts = self._alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("Member unknown", alerts[0].body)

    def test_member_unknown_case_leaves_the_staff_opener_s_checkouts(self):
        from unittest.mock import MagicMock

        from crush_lu.services.premium_recovery import notify_safely

        staff = User.objects.create_user(
            username="rec-staff3@example.invalid",
            email="rec-staff3@example.invalid",
            password="pass12345",
            is_staff=True,
        )
        tx = self._tx("REC-UNKNOWN-SCOPE", status=PaymentTransaction.Status.PAID)
        PaymentTransaction.objects.filter(pk=tx.pk).update(user=staff)
        self._unlink_and_delete_membership()
        staff_membership = PremiumMembership.objects.create(
            user=staff, coach=self.coach, status="cancelled"
        )
        staff_checkout = PaymentTransaction.objects.create(
            transaction_reference="REC-STAFF-OWN",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_REC-STAFF-OWN",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=staff,
            premium_membership=staff_membership,
        )
        case = PremiumPaymentRecoveryCase.objects.create(
            payment=tx,
            user=staff,
            reason=Reason.OTHER,
            staff_only=True,
            member_unknown=True,
        )
        client = MagicMock()
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            notify_safely(case.pk)
        client.deactivate_checkout.assert_not_called()
        staff_checkout.refresh_from_db()
        self.assertEqual(staff_checkout.status, PaymentTransaction.Status.PENDING)

    def test_member_unknown_case_gates_nothing_of_the_staff_opener(self):
        from crush_lu.services.premium_recovery import blocks_new_charge
        from crush_lu.views_premium import open_recovery_case

        staff = User.objects.create_user(
            username="rec-staff4@example.invalid",
            email="rec-staff4@example.invalid",
            password="pass12345",
            is_staff=True,
        )
        tx = self._tx("REC-UNKNOWN-GATE", status=PaymentTransaction.Status.PAID)
        PaymentTransaction.objects.filter(pk=tx.pk).update(user=staff)
        self._unlink_and_delete_membership()
        PremiumPaymentRecoveryCase.objects.create(
            payment=tx,
            user=staff,
            reason=Reason.OTHER,
            staff_only=True,
            member_unknown=True,
        )
        PremiumMembership.objects.create(user=staff, coach=self.coach, status="pending")
        self.assertFalse(blocks_new_charge(staff))
        self.assertIsNone(open_recovery_case(staff))

    def test_notices_go_out_even_if_the_close_fails(self):
        from crush_lu.services import premium_recovery

        case = PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-CLOSE-BOOM", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.COACH_UNAVAILABLE,
        )
        with patch.object(
            premium_recovery,
            "_close_sibling_checkouts_safely",
            side_effect=RuntimeError("db"),
        ), self.assertLogs("crush_lu.services.premium_recovery", level="ERROR"):
            premium_recovery.notify_safely(case.pk)
        case.refresh_from_db()
        self.assertIsNotNone(case.member_notified_at)
        self.assertIsNotNone(case.staff_alerted_at)

    def test_no_notice_once_staff_resolve_the_case_during_the_close(self):
        from crush_lu.services import premium_recovery

        case = PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-RESOLVED-MID", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.COACH_UNAVAILABLE,
        )

        def _staff_resolve_meanwhile(_case):
            PremiumPaymentRecoveryCase.objects.filter(pk=case.pk).update(
                status=PremiumPaymentRecoveryCase.Status.RESOLVED
            )

        with patch.object(
            premium_recovery,
            "_close_sibling_checkouts_safely",
            side_effect=_staff_resolve_meanwhile,
        ):
            premium_recovery.notify_safely(case.pk)
        self.assertEqual(mail.outbox, [])

    def test_request_callbacks_run_under_a_deadline(self):
        from crush_lu.services import premium_recovery

        for fn in (
            premium_recovery.notify_safely,
            premium_recovery.close_after_activation_safely,
        ):
            self.assertIn("_request_deadline(deadline)", inspect.getsource(fn))
        # One close pass fits; the whole budget stays under Gunicorn's 120 s
        # with room for the request's own SumUp read before it.
        self.assertGreaterEqual(
            premium_recovery.REQUEST_BUDGET_SECONDS, premium_recovery._close_seconds()
        )
        self.assertLess(
            premium_recovery.REQUEST_BUDGET_SECONDS + premium_recovery._sync_seconds(),
            120,
        )

    def test_recovery_callback_is_robust(self):
        from crush_lu import views_payments

        src = inspect.getsource(views_payments._queue_premium_recovery_case)
        self.assertIn("robust=True", src)

    def test_case_scope_is_read_under_the_case_lock(self):
        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery._close_sibling_checkouts_safely)
        lock = src.index("select_for_update(skip_locked=True")
        self.assertLess(lock, src.index("others = PremiumMembership"))
        self.assertLess(
            src.index("others = PremiumMembership"),
            src.index("close_open_checkouts_safely("),
        )

    def test_member_unknown_survives_the_opener_losing_staff(self):
        from crush_lu.services.premium_recovery import blocks_new_charge, notify_safely

        staff = User.objects.create_user(
            username="rec-staff5@example.invalid",
            email="rec-staff5@example.invalid",
            password="pass12345",
            is_staff=True,
        )
        tx = self._tx("REC-UNKNOWN-DEMOTED")
        PaymentTransaction.objects.filter(pk=tx.pk).update(user=staff)
        self._unlink_and_delete_membership()
        tx.refresh_from_db()
        with self.assertLogs("crush_lu.views_payments", level="CRITICAL"):
            self._apply(tx)
        case = PremiumPaymentRecoveryCase.objects.get(payment=tx)
        self.assertTrue(case.staff_only and case.member_unknown)
        User.objects.filter(pk=staff.pk).update(is_staff=False)
        mail.outbox.clear()
        PremiumPaymentRecoveryCase.objects.filter(pk=case.pk).update(
            member_notified_at=None, staff_alerted_at=None
        )
        notify_safely(case.pk)
        self.assertEqual([m for m in mail.outbox if m.to == [staff.email]], [])
        self.assertFalse(blocks_new_charge(staff))

    def test_cleanup_closes_only_the_rows_snapshotted_under_the_lock(self):
        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery._close_sibling_checkouts_safely)
        self.assertLess(
            src.index("snapshot = set("), src.index("close_open_checkouts_safely(")
        )
        self.assertIn("only_ids=snapshot", src)

    def test_close_leaves_a_row_outside_the_snapshot(self):
        from unittest.mock import MagicMock

        from crush_lu.services.premium_recovery import close_open_checkouts_safely

        old = self._tx("REC-SNAP-OLD")
        replacement = self._tx("REC-SNAP-NEW")
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            close_open_checkouts_safely([self.membership], "test", only_ids={old.pk})
        client.deactivate_checkout.assert_called_once_with("CHK_REC-SNAP-OLD")
        replacement.refresh_from_db()
        self.assertEqual(replacement.status, PaymentTransaction.Status.PENDING)

    def test_member_unknown_case_takes_no_retry_slot(self):
        from datetime import timedelta

        from django.utils import timezone

        from crush_lu.services.premium_recovery import retry_unsent_notifications

        staff = User.objects.create_user(
            username="rec-staff2@example.invalid",
            email="rec-staff2@example.invalid",
            password="pass12345",
            is_staff=True,
        )
        tx = self._tx("REC-UNKNOWN-RETRY", status=PaymentTransaction.Status.PAID)
        PaymentTransaction.objects.filter(pk=tx.pk).update(user=staff)
        self._unlink_and_delete_membership()
        case = PremiumPaymentRecoveryCase.objects.create(
            payment=tx,
            user=staff,
            reason=Reason.OTHER,
            staff_alerted_at=timezone.now(),
            staff_only=True,
            member_unknown=True,
        )
        PremiumPaymentRecoveryCase.objects.filter(pk=case.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        self.assertEqual(retry_unsent_notifications(100), 0)

    def test_captured_request_cannot_be_cancelled(self):
        self._case("REC-NOCANCEL", self.membership)
        client = self._member_client()
        html = client.get("/en/premium/coaches/").content.decode()
        self.assertNotIn("/premium/cancel/", html)
        self.assertNotIn("data-membership-id=", html)
        self.assertIn("We have already received a payment", html)
        self.assertNotIn("payment pending", html)
        response = client.post("/en/premium/cancel/")
        self.assertEqual(response.status_code, 302)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")

    def test_merge_refuses_a_duplicate_with_an_open_case(self):
        from crush_lu.services.account_merge import merge_accounts

        self._case("REC-MERGE", self.membership)
        keeper = User.objects.create_user(
            username="rec-keeper@example.invalid",
            email="rec-keeper@example.invalid",
            password="pass12345",
        )
        with self.assertRaisesMessage(ValueError, "open Premium payment recovery"):
            merge_accounts(keeper, self.member)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)

    def test_backfill_flags_a_capture_after_staff_confirmation(self):
        from io import StringIO

        from django.core.management import call_command

        self.membership.confirm(by_user=self.coach.user)
        late = self._tx("REC-AFTER-STAFF", status=PaymentTransaction.Status.PAID)
        call_command("backfill_premium_recovery_cases", "--apply", stdout=StringIO())
        case = PremiumPaymentRecoveryCase.objects.get(payment=late)
        self.assertEqual(case.reason, Reason.DUPLICATE_CAPTURE)

    def _paid_on_confirmed_membership(self, refs):
        other = PremiumMembership.objects.create(
            user=self.member,
            coach=self.coach,
            status="active",
            payment_confirmed=True,
        )
        return [
            PaymentTransaction.objects.create(
                transaction_reference=ref,
                provider=PaymentTransaction.Provider.SUMUP,
                sumup_checkout_id=f"CHK_{ref}",
                amount=Decimal("10.00"),
                currency="EUR",
                status=PaymentTransaction.Status.PAID,
                purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
                user=self.member,
                premium_membership=other,
            )
            for ref in refs
        ]

    def test_backfill_flags_every_capture_of_a_multi_capture_membership(self):
        from io import StringIO

        from django.core.management import call_command

        rows = self._paid_on_confirmed_membership(["REC-MULTI-A", "REC-MULTI-B"])
        call_command("backfill_premium_recovery_cases", "--apply", stdout=StringIO())
        self.assertEqual(
            set(
                PremiumPaymentRecoveryCase.objects.values_list("payment_id", flat=True)
            ),
            {row.pk for row in rows},
        )

    def test_backfill_flags_all_captures_when_the_applied_one_is_unknown(self):
        from io import StringIO

        from django.core.management import call_command

        rows = self._paid_on_confirmed_membership(["REC-NODATE-A", "REC-NODATE-B"])
        PaymentTransaction.objects.filter(pk=rows[0].pk).update(paid_at=None)
        call_command("backfill_premium_recovery_cases", "--apply", stdout=StringIO())
        self.assertEqual(
            PremiumPaymentRecoveryCase.objects.filter(
                reason=Reason.DUPLICATE_CAPTURE
            ).count(),
            2,
        )

    def test_stale_checkout_blocks_a_new_charge_without_any_case(self):
        from crush_lu.services.premium_recovery import blocks_new_charge

        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        self._tx("REC-STALE-NO-CASE")
        self.assertFalse(PremiumPaymentRecoveryCase.objects.exists())
        self.assertTrue(blocks_new_charge(self.member))

    def test_deploy_migration_backfills_old_unapplied_captures(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module(
            "crush_lu.migrations.0262_backfill_premium_recovery_cases"
        )
        unapplied = self._tx("REC-MIG-UNAPPLIED", status=PaymentTransaction.Status.PAID)
        staff = User.objects.create_user(
            username="rec-staff6@example.invalid",
            email="rec-staff6@example.invalid",
            password="pass12345",
            is_staff=True,
        )
        for _ in range(2):  # idempotent
            migration.backfill(apps, None)
        case = PremiumPaymentRecoveryCase.objects.get(payment=unapplied)
        self.assertEqual(case.reason, Reason.COACH_UNAVAILABLE)
        self.assertFalse(case.staff_only)
        self.assertEqual(PremiumPaymentRecoveryCase.objects.count(), 1)
        self.assertEqual(mail.outbox, [])

        assisted = self._tx("REC-MIG-ASSISTED", status=PaymentTransaction.Status.PAID)
        PaymentTransaction.objects.filter(pk=assisted.pk).update(
            user=staff, premium_membership=None
        )
        migration.backfill(apps, None)
        assisted_case = PremiumPaymentRecoveryCase.objects.get(payment=assisted)
        self.assertTrue(assisted_case.staff_only)
        self.assertTrue(assisted_case.member_unknown)

    def test_admin_keeps_member_unknown_read_only(self):
        from crush_lu.admin import crush_admin_site

        model_admin = crush_admin_site._registry[PremiumPaymentRecoveryCase]
        self.assertIn("staff_only", model_admin.readonly_fields)
        self.assertIn("member_unknown", model_admin.readonly_fields)

    def test_backfill_spares_a_capture_staff_confirmed_afterwards(self):
        import importlib
        from io import StringIO

        from django.apps import apps
        from django.core.management import call_command

        before = self._tx("REC-BEFORE-STAFF", status=PaymentTransaction.Status.PAID)
        self.membership.confirm(by_user=self.coach.user)
        call_command("backfill_premium_recovery_cases", "--apply", stdout=StringIO())
        importlib.import_module(
            "crush_lu.migrations.0262_backfill_premium_recovery_cases"
        ).backfill(apps, None)
        self.assertFalse(
            PremiumPaymentRecoveryCase.objects.filter(payment=before).exists()
        )

    def test_member_unknown_case_is_not_selected_for_its_opener_s_checkouts(self):
        from datetime import timedelta

        from django.utils import timezone

        from crush_lu.services.premium_recovery import retry_unsent_notifications

        staff = User.objects.create_user(
            username="rec-staff7@example.invalid",
            email="rec-staff7@example.invalid",
            password="pass12345",
            is_staff=True,
        )
        tx = self._tx("REC-UNKNOWN-QUOTA", status=PaymentTransaction.Status.PAID)
        PaymentTransaction.objects.filter(pk=tx.pk).update(user=staff)
        self._unlink_and_delete_membership()
        case = PremiumPaymentRecoveryCase.objects.create(
            payment=tx,
            user=staff,
            reason=Reason.OTHER,
            staff_only=True,
            member_unknown=True,
            staff_alerted_at=timezone.now(),
        )
        PremiumPaymentRecoveryCase.objects.filter(pk=case.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        own = PremiumMembership.objects.create(
            user=staff, coach=self.coach, status="pending"
        )
        PaymentTransaction.objects.create(
            transaction_reference="REC-STAFF-OWN-2",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_REC-STAFF-OWN-2",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=staff,
            premium_membership=own,
        )
        self.assertEqual(retry_unsent_notifications(100), 0)

    def test_open_case_locks_the_member_before_inserting(self):
        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery.open_case)
        self.assertLess(src.index("select_for_update()"), src.index("get_or_create("))

    def test_ambiguous_backfilled_captures_still_block_a_new_charge(self):
        from io import StringIO

        from django.core.management import call_command

        from crush_lu.services.premium_recovery import blocks_new_charge

        rows = self._paid_on_confirmed_membership(["REC-AMB-G1", "REC-AMB-G2"])
        call_command("backfill_premium_recovery_cases", "--apply", stdout=StringIO())
        cases = PremiumPaymentRecoveryCase.objects.filter(payment__in=rows)
        self.assertTrue(cases.exists() and all(case.staff_only for case in cases))
        # The membership ends and no checkout is left: only the open cases
        # still stand between the member and another payment.
        PremiumMembership.objects.filter(pk=rows[0].premium_membership_id).update(
            status="cancelled"
        )
        self.assertTrue(blocks_new_charge(rows[0].premium_membership.user))

    def test_backfill_keeps_ambiguous_captures_staff_only(self):
        import importlib
        from datetime import timedelta
        from io import StringIO

        from django.apps import apps
        from django.core.management import call_command
        from django.utils import timezone

        from crush_lu.services.premium_recovery import retry_unsent_notifications

        rows = self._paid_on_confirmed_membership(["REC-AMB-A", "REC-AMB-B"])
        call_command("backfill_premium_recovery_cases", "--apply", stdout=StringIO())
        cases = PremiumPaymentRecoveryCase.objects.filter(payment__in=rows)
        self.assertEqual(cases.count(), 2)
        self.assertTrue(all(case.staff_only for case in cases))
        # The migration classifies the same way.
        PremiumPaymentRecoveryCase.objects.all().delete()
        importlib.import_module(
            "crush_lu.migrations.0262_backfill_premium_recovery_cases"
        ).backfill(apps, None)
        cases = PremiumPaymentRecoveryCase.objects.filter(payment__in=rows)
        self.assertTrue(cases.exists() and all(case.staff_only for case in cases))
        self.assertFalse(any(case.member_unknown for case in cases))
        # No member notice goes out for them; the staff alert does.
        cases.update(created_at=timezone.now() - timedelta(hours=2))
        mail.outbox.clear()
        retry_unsent_notifications(100)
        self.assertEqual(self._member_mails(), [])
        self.assertTrue(self._alerts())

    def test_coach_chooser_shows_the_notice_instead_of_choices(self):
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-CHOOSER", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.REQUEST_CANCELLED,
        )
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(self.member)
        html = client.get("/en/premium/coaches/").content.decode()
        self.assertIn('data-testid="premium-recovery-notice"', html)
        self.assertNotIn("/select/", html)

    def test_activation_close_callback_is_robust(self):
        from crush_lu import views_payments

        src = inspect.getsource(views_payments._apply_paid_checkout)
        close = src.index("close_after_activation_safely(")
        self.assertIn("robust=True", src[close : close + 300])

    def test_merge_refuses_a_duplicate_with_a_payable_checkout(self):
        from crush_lu.services.account_merge import merge_accounts

        self._tx("REC-MERGE-OPEN")
        keeper = User.objects.create_user(
            username="rec-keeper2@example.invalid",
            email="rec-keeper2@example.invalid",
            password="pass12345",
        )
        with self.assertRaisesMessage(ValueError, "could still be paid"):
            merge_accounts(keeper, self.member)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)

    def test_backfill_opens_cases_for_old_unapplied_captures(self):
        from io import StringIO

        from django.core.management import call_command

        # Never applied: the membership confirmed no payment.
        unapplied = self._tx("REC-OLD-UNAPPLIED", status=PaymentTransaction.Status.PAID)
        # Applied first capture plus a duplicate on another membership.
        other = PremiumMembership.objects.create(
            user=self.member,
            coach=self.coach,
            status="active",
            payment_confirmed=True,
        )
        applied, duplicate = (
            PaymentTransaction.objects.create(
                transaction_reference=ref,
                provider=PaymentTransaction.Provider.SUMUP,
                sumup_checkout_id=f"CHK_{ref}",
                amount=Decimal("10.00"),
                currency="EUR",
                status=PaymentTransaction.Status.PAID,
                purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
                user=self.member,
                premium_membership=other,
            )
            for ref in ("REC-OLD-APPLIED", "REC-OLD-DUP")
        )

        out = StringIO()
        call_command("backfill_premium_recovery_cases", stdout=out)
        self.assertIn("Would open 3", out.getvalue())
        self.assertFalse(PremiumPaymentRecoveryCase.objects.exists())

        for _ in range(2):  # idempotent
            call_command("backfill_premium_recovery_cases", "--apply", stdout=out)
        cases = dict(
            PremiumPaymentRecoveryCase.objects.values_list("payment_id", "reason")
        )
        # Two captures on one membership: which was applied is unprovable.
        self.assertEqual(
            cases,
            {
                unapplied.pk: Reason.COACH_UNAVAILABLE,
                applied.pk: Reason.DUPLICATE_CAPTURE,
                duplicate.pk: Reason.DUPLICATE_CAPTURE,
            },
        )
        # No mail from the command: the hourly retry sends the notices.
        self.assertEqual(mail.outbox, [])

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_duplicate_capture_after_beta_revocation_is_duplicate(self):
        self.membership.status = "active"
        self.membership.save(update_fields=["status"])
        tx = self._tx("REC-DUP-BETA")
        with self.assertLogs("crush_lu.views_payments", level="ERROR"):
            self._apply(tx)
        case = PremiumPaymentRecoveryCase.objects.get(payment=tx)
        self.assertEqual(case.reason, Reason.DUPLICATE_CAPTURE)

    def test_unsent_staff_alert_is_logged(self):
        self._fill_the_coach()
        tx = self._tx("REC-NOSEND")
        with patch("azureproject.email_utils.send_domain_email", return_value=0):
            with self.assertLogs(
                "crush_lu.services.premium_recovery", "WARNING"
            ) as logs:
                with self.assertLogs("crush_lu.views_payments", level="ERROR"):
                    self._apply(tx)
        self.assertIn("staff alert not sent", "\n".join(logs.output))
        case = PremiumPaymentRecoveryCase.objects.get(payment=tx)
        self.assertIsNone(case.staff_alerted_at)

    def test_membership_with_a_payment_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        self._tx("REC-PROTECT")
        with self.assertRaises(ProtectedError):
            self.membership.delete()

    def test_return_page_warns_for_an_unlinked_capture(self):
        tx = self._tx("REC-RET-NULL")
        self._unlink_and_delete_membership()
        with self.assertLogs("crush_lu.views_payments", level="CRITICAL"):
            self._apply(tx)
        response = self._member_client().get(
            "/payments/sumup/return/", {"ref": "REC-RET-NULL"}, follow=True
        )
        texts = [str(m) for m in response.context["messages"]]
        self.assertTrue(any(t.startswith(D1_START) for t in texts), texts)
        self.assertFalse(any("completed successfully" in t for t in texts), texts)

    def test_cancelled_request_case_shows_until_a_new_request(self):
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        self._case("REC-CANCELLED", self.membership, Reason.REQUEST_CANCELLED)
        client = self._member_client()
        for path in ("/en/dashboard/", "/en/membership/"):
            html = client.get(path).content.decode()
            self.assertIn('data-testid="premium-recovery-notice"', html, path)

    def test_open_case_blocks_a_fresh_request(self):
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        self._case("REC-NOFRESH", self.membership, Reason.REQUEST_CANCELLED)
        response = self._member_client().post(
            f"/en/premium/coaches/{self.coach.pk}/select/"
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            PremiumMembership.objects.filter(
                user=self.member, status="pending"
            ).exists()
        )

    def test_merge_locks_assisted_premium_payments_before_reading_cases(self):
        from crush_lu.services import account_merge

        src = inspect.getsource(account_merge.merge_accounts)
        lock = src.index("PaymentTransaction.objects.select_for_update")
        self.assertIn("premium_membership__user_id__in", src[lock : lock + 400])
        self.assertLess(lock, src.index("PremiumPaymentRecoveryCase.objects.filter"))


class LateCaptureTests(_Base):
    """Codex round 5: a cancelled request must not stay payable."""

    def _client(self):
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(self.member)
        return client

    def test_cancel_closes_the_open_checkout_first(self):
        from unittest.mock import MagicMock

        tx = self._tx("REC-OPEN-CHK")
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            self._client().post("/en/premium/cancel/")
        client.deactivate_checkout.assert_called_once_with("CHK_REC-OPEN-CHK")
        client.refund.assert_not_called()
        tx.refresh_from_db()
        self.membership.refresh_from_db()
        self.assertEqual(
            (tx.status, self.membership.status),
            (PaymentTransaction.Status.CANCELLED, "cancelled"),
        )

    def test_cancel_refused_while_a_checkout_may_still_capture(self):
        from unittest.mock import MagicMock

        from crush_lu.services.sumup import SumUpError

        self._tx("REC-STUCK-CHK")
        client = MagicMock()
        client.deactivate_checkout.side_effect = SumUpError("timeout")
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            response = self._client().post("/en/premium/cancel/", follow=True)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")
        texts = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("could not be closed" in t for t in texts), texts)

    def test_cancel_refused_when_sumup_already_captured(self):
        from unittest.mock import MagicMock

        tx = self._tx("REC-LATE-CAP")
        client = MagicMock()
        client.deactivate_checkout.return_value = False
        client.get_checkout.return_value = {"status": "PAID"}
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            with self.captureOnCommitCallbacks(execute=True):
                self._client().post("/en/premium/cancel/")
        tx.refresh_from_db()
        self.membership.refresh_from_db()
        # Webhook not in yet: the capture is recorded from the read that found
        # it (the coach had room, so it is applied), never cancelled.
        self.assertEqual(
            (tx.status, self.membership.status),
            (PaymentTransaction.Status.PAID, "active"),
        )
        client.refund.assert_not_called()

    def test_captured_request_still_closes_a_sibling_checkout(self):
        from unittest.mock import MagicMock

        self._tx("REC-SIB-PAID", status=PaymentTransaction.Status.PAID)
        sibling = self._tx("REC-SIB-OPEN")
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            self._client().post("/en/premium/cancel/")
        client.deactivate_checkout.assert_called_once_with("CHK_REC-SIB-OPEN")
        client.refund.assert_not_called()
        sibling.refresh_from_db()
        self.membership.refresh_from_db()
        self.assertEqual(
            (sibling.status, self.membership.status),
            (PaymentTransaction.Status.CANCELLED, "pending"),
        )

    def test_opening_a_case_closes_sibling_checkouts(self):
        from unittest.mock import MagicMock

        sibling = self._tx("REC-SIB-AUTO")
        tx = self._tx("REC-SIB-CAPTURE")
        self._fill_the_coach()
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            with self.assertLogs("crush_lu.views_payments", level="ERROR"):
                self._apply(tx)
        client.deactivate_checkout.assert_called_once_with("CHK_REC-SIB-AUTO")
        client.refund.assert_not_called()
        sibling.refresh_from_db()
        self.assertEqual(sibling.status, PaymentTransaction.Status.CANCELLED)

    def test_member_without_profile_still_gets_the_notice(self):
        PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-NOPROF", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.OTHER,
        )
        self.profile.delete()
        response = self._client().get("/en/dashboard/", follow=True)
        texts = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any(t.startswith(D1_START) and "REC-NOPROF" in t for t in texts), texts
        )

    def test_no_pay_button_while_an_older_case_blocks_checkout(self):
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-OLD-CTA", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.REQUEST_CANCELLED,
        )
        PremiumMembership.objects.create(
            user=self.member, coach=self.coach, status="pending"
        )
        self.profile.verification_status = "verified"
        self.profile.save(update_fields=["verification_status"])
        client = self._client()
        coaches = client.get("/en/premium/coaches/").content.decode()
        pricing = client.get("/en/membership/").content.decode()
        self.assertNotIn("data-membership-id=", coaches)
        self.assertNotIn("Complete your Premium signup", pricing)
        self.assertIn("We have already received a payment", pricing)

    def test_checkout_applies_a_capture_sumup_already_has(self):
        from unittest.mock import MagicMock

        tx = self._tx("REC-MISSED-HOOK")
        client = MagicMock()
        client.get_checkout.return_value = {
            "id": tx.sumup_checkout_id,
            "status": "PAID",
            "amount": 10.0,
            "currency": "EUR",
        }
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            with self.captureOnCommitCallbacks(execute=True):
                response = self._client().post(
                    f"/payments/sumup/create-premium-checkout/{self.membership.pk}/"
                )
        self.assertEqual(response.status_code, 409)
        tx.refresh_from_db()
        self.membership.refresh_from_db()
        self.assertEqual(
            (tx.status, self.membership.status),
            (PaymentTransaction.Status.PAID, "active"),
        )
        client.refund.assert_not_called()

    def test_checkout_refused_while_a_resolved_case_has_an_open_checkout(self):
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-RES-OLD", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.REQUEST_CANCELLED,
            status=PremiumPaymentRecoveryCase.Status.RESOLVED,
        )
        self._tx("REC-RES-STILL-OPEN")
        replacement = PremiumMembership.objects.create(
            user=self.member, coach=self.coach, status="pending"
        )
        response = self._client().post(
            f"/payments/sumup/create-premium-checkout/{replacement.pk}/"
        )
        self.assertEqual(response.status_code, 409)

    def test_checkout_applies_the_paid_payload_even_if_a_reread_fails(self):
        from unittest.mock import MagicMock

        from crush_lu.services.sumup import SumUpError

        tx = self._tx("REC-PAYLOAD")
        payload = {
            "id": tx.sumup_checkout_id,
            "status": "PAID",
            "amount": 10.0,
            "currency": "EUR",
        }
        client = MagicMock()
        client.get_checkout.side_effect = [payload, SumUpError("timeout")]
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            with self.captureOnCommitCallbacks(execute=True):
                self._client().post(
                    f"/payments/sumup/create-premium-checkout/{self.membership.pk}/"
                )
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(client.get_checkout.call_count, 1)

    def test_publication_rechecks_the_block_under_the_member_lock(self):
        from crush_lu import views_payments

        src = inspect.getsource(views_payments.create_sumup_premium_checkout)
        self.assertLess(
            src.index("_lock_member_and_check_blocked("),
            src.index("PaymentTransaction.objects.create("),
        )
        helper = inspect.getsource(views_payments._lock_member_and_check_blocked)
        self.assertLess(
            helper.index("select_for_update()"), helper.index("blocks_new_charge(")
        )

    def test_sibling_syncs_are_capped(self):
        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery.close_open_checkouts_safely)
        self.assertIn("handled >= SIBLING_SYNC_LIMIT", src)

    def _second_membership(self):
        return PremiumMembership.objects.create(
            user=self.member, coach=self.coach, status="pending"
        )

    def test_settle_keeps_the_payload_of_an_older_sibling_it_found_paid(self):
        from unittest.mock import MagicMock

        from crush_lu.views_payments import _settle_pending_premium_checkouts

        older = self._tx("REC-OLD-SIB-PAID")
        paid = {"id": older.sumup_checkout_id, "status": "PAID", "amount": 10.0}
        client = MagicMock()
        client.deactivate_checkout.return_value = False
        client.get_checkout.return_value = paid
        payloads = {}
        state, *_ = _settle_pending_premium_checkouts(
            client, self.membership, captured=True, paid_payloads=payloads
        )
        self.assertEqual(state, "paid")
        self.assertEqual(payloads, {older.pk: paid})

    def test_open_case_closes_a_checkout_of_another_membership(self):
        from unittest.mock import MagicMock

        from crush_lu.services.premium_recovery import notify_safely

        case = PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-X-PAID", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.COACH_UNAVAILABLE,
        )
        # Published just before the case was inserted, on a newer membership.
        other = PaymentTransaction.objects.create(
            transaction_reference="REC-X-OTHER",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_REC-X-OTHER",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.member,
            premium_membership=self._second_membership(),
        )
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            notify_safely(case.pk)
        client.deactivate_checkout.assert_called_once_with("CHK_REC-X-OTHER")
        other.refresh_from_db()
        self.assertEqual(other.status, PaymentTransaction.Status.CANCELLED)
        client.refund.assert_not_called()

    def _pending_on(self, membership, ref):
        return PaymentTransaction.objects.create(
            transaction_reference=ref,
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=f"CHK_{ref}",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.member,
            premium_membership=membership,
        )

    def test_close_applies_fetched_captures_before_older_open_rows(self):
        from unittest.mock import MagicMock

        from crush_lu.services.premium_recovery import (
            SIBLING_SYNC_LIMIT,
            close_open_checkouts_safely,
        )

        older = [self._tx(f"REC-OLDER-{i}") for i in range(SIBLING_SYNC_LIMIT)]
        captured = self._tx("REC-NEWER-CAPTURED")
        statuses = {row.sumup_checkout_id: "PENDING" for row in older}
        statuses[captured.sumup_checkout_id] = "PAID"
        client = MagicMock()
        client.deactivate_checkout.return_value = False
        client.get_checkout.side_effect = lambda checkout_id: {
            "id": checkout_id,
            "status": statuses[checkout_id],
            "amount": 10.0,
            "currency": "EUR",
        }
        with patch(
            "crush_lu.views_payments._settle_pending_premium_checkouts",
            side_effect=lambda *a, paid_payloads, **k: (
                paid_payloads.update(
                    {captured.pk: client.get_checkout(captured.sumup_checkout_id)}
                )
                or ("open", None, set(), set())
            ),
        ), patch("crush_lu.views_payments.SumUpClient", return_value=client):
            with self.assertLogs(level="WARNING"):
                close_open_checkouts_safely([self.membership], "test")
        captured.refresh_from_db()
        self.assertEqual(captured.status, PaymentTransaction.Status.PAID)

    def test_close_rereads_the_case_status(self):
        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery._close_sibling_checkouts_safely)
        self.assertLess(
            src.index('values_list("status", flat=True)'),
            src.index("case.status != case.Status.OPEN"),
        )

    def test_a_sync_reserves_time_for_the_mails_a_capture_sends(self):
        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery.close_open_checkouts_safely)
        self.assertIn("_fits(_sync_seconds() + 2 * SEND_SECONDS)", src)

    def test_member_request_closes_one_membership_and_the_tick_the_rest(self):
        from datetime import timedelta
        from unittest.mock import MagicMock

        from django.utils import timezone

        from crush_lu.services.premium_recovery import (
            notify_safely,
            retry_unsent_notifications,
        )

        stamp = timezone.now()
        case = PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-LIM-PAID", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.COACH_UNAVAILABLE,
            member_notified_at=stamp,
            staff_alerted_at=stamp,
        )
        first = self._pending_on(self._second_membership(), "REC-LIM-A")
        second = self._pending_on(self._second_membership(), "REC-LIM-B")
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            notify_safely(case.pk)
            self.assertEqual(client.deactivate_checkout.call_count, 1)
            PremiumPaymentRecoveryCase.objects.filter(pk=case.pk).update(
                created_at=stamp - timedelta(hours=2)
            )
            retry_unsent_notifications(100)
        for row in (first, second):
            row.refresh_from_db()
            self.assertEqual(row.status, PaymentTransaction.Status.CANCELLED)

    def _resolved_case_with_a_stale_checkout(self):
        from datetime import timedelta

        from django.utils import timezone

        case = PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-STALE-PAID", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.COACH_UNAVAILABLE,
            status=PremiumPaymentRecoveryCase.Status.RESOLVED,
        )
        PremiumPaymentRecoveryCase.objects.filter(pk=case.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        old = self._second_membership()
        old.status = "cancelled"
        old.save(update_fields=["status"])
        return self._pending_on(old, "REC-STALE-OTHER")

    def test_resolved_case_still_blocks_on_another_membership_s_checkout(self):
        from crush_lu.services.premium_recovery import blocks_new_charge

        self._resolved_case_with_a_stale_checkout()
        self.assertTrue(blocks_new_charge(self.member))

    def test_tick_closes_another_membership_s_checkout_after_resolution(self):
        from unittest.mock import MagicMock

        from crush_lu.services.premium_recovery import (
            blocks_new_charge,
            retry_unsent_notifications,
        )

        stale = self._resolved_case_with_a_stale_checkout()
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            retry_unsent_notifications(100)
        client.deactivate_checkout.assert_called_once_with("CHK_REC-STALE-OTHER")
        stale.refresh_from_db()
        self.assertEqual(stale.status, PaymentTransaction.Status.CANCELLED)
        self.assertFalse(blocks_new_charge(self.member))
        client.refund.assert_not_called()

    def test_applying_found_captures_is_capped_on_a_request(self):
        from unittest.mock import MagicMock

        from crush_lu.services.premium_recovery import (
            SIBLING_SYNC_LIMIT,
            close_open_checkouts_safely,
        )

        rows = [self._tx(f"REC-CAP-{i}") for i in range(SIBLING_SYNC_LIMIT + 1)]
        client = MagicMock()
        client.deactivate_checkout.return_value = False
        client.get_checkout.side_effect = lambda checkout_id: {
            "id": checkout_id,
            "status": "PAID",
            "amount": 10.0,
            "currency": "EUR",
        }
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            with self.assertLogs(level="ERROR"):
                close_open_checkouts_safely([self.membership], "test")
        paid = PaymentTransaction.objects.filter(
            pk__in=[r.pk for r in rows], status=PaymentTransaction.Status.PAID
        ).count()
        self.assertEqual(paid, SIBLING_SYNC_LIMIT)
        client.refund.assert_not_called()

    def test_tick_closes_a_checkout_left_open_for_a_deactivated_member(self):
        from datetime import timedelta
        from unittest.mock import MagicMock

        from django.utils import timezone

        from crush_lu.services.premium_recovery import retry_unsent_notifications

        left = self._tx("REC-MERGED-LEFT")
        PaymentTransaction.objects.filter(pk=left.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        User.objects.filter(pk=self.member.pk).update(is_active=False)
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            retry_unsent_notifications(100)
        client.deactivate_checkout.assert_called_once_with("CHK_REC-MERGED-LEFT")
        left.refresh_from_db()
        self.assertEqual(left.status, PaymentTransaction.Status.CANCELLED)
        client.refund.assert_not_called()

    def test_tick_closes_a_stale_checkout_on_a_cancelled_membership(self):
        from datetime import timedelta
        from unittest.mock import MagicMock

        from django.utils import timezone

        from crush_lu.services.premium_recovery import retry_unsent_notifications

        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        stale = self._tx("REC-STALE-CANCELLED")
        PaymentTransaction.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            retry_unsent_notifications(100)
        stale.refresh_from_db()
        self.assertEqual(stale.status, PaymentTransaction.Status.CANCELLED)
        client.refund.assert_not_called()

    def test_tick_closes_checkouts_before_retrying_notices(self):
        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery.retry_unsent_notifications)
        self.assertLess(
            src.index("_close_checkouts_beside_a_capture("),
            src.index("for pk in case_ids"),
        )

    def test_tick_closes_a_checkout_left_open_beside_an_activation(self):
        from datetime import timedelta
        from unittest.mock import MagicMock

        from django.utils import timezone

        from crush_lu.services.premium_recovery import retry_unsent_notifications

        self.membership.status = "active"
        self.membership.save(update_fields=["status"])
        self._tx("REC-ACT-OK", status=PaymentTransaction.Status.PAID)
        sibling = self._tx("REC-ACT-LEFT")
        PaymentTransaction.objects.filter(pk=sibling.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            retry_unsent_notifications(100)
        client.deactivate_checkout.assert_called_once_with("CHK_REC-ACT-LEFT")
        sibling.refresh_from_db()
        self.assertEqual(sibling.status, PaymentTransaction.Status.CANCELLED)
        client.refund.assert_not_called()

    def test_cancel_records_a_capture_it_discovers(self):
        from unittest.mock import MagicMock

        from crush_lu.views_premium import _cancel_premium_request

        tx = self._tx("REC-CANCEL-CAP")
        client = MagicMock()
        client.deactivate_checkout.return_value = False
        client.get_checkout.return_value = {
            "id": tx.sumup_checkout_id,
            "status": "PAID",
            "amount": 10.0,
            "currency": "EUR",
        }
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            with self.captureOnCommitCallbacks(execute=True):
                outcome = _cancel_premium_request(self.membership, self.member)
        self.assertNotEqual(outcome, "cancelled")
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(client.get_checkout.call_count, 1)
        client.refund.assert_not_called()

    def test_activation_closes_the_membership_s_other_checkouts(self):
        from unittest.mock import MagicMock

        paid = self._tx("REC-ACT-PAID")
        sibling = self._tx("REC-ACT-SIB")
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            self._apply(paid)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        client.deactivate_checkout.assert_called_once_with("CHK_REC-ACT-SIB")
        sibling.refresh_from_db()
        self.assertEqual(sibling.status, PaymentTransaction.Status.CANCELLED)
        client.refund.assert_not_called()

    def test_a_slow_receipt_leaves_the_activation_close_to_the_tick(self):
        from unittest.mock import MagicMock

        from crush_lu.services import premium_recovery

        paid = self._tx("REC-ACT-SLOW")
        sibling = self._tx("REC-ACT-SLOW-SIB")
        clock = {"now": 1000.0}

        def slow_receipt(payment):
            # The receipt spends Graph's whole worst case.
            clock["now"] += premium_recovery.SEND_SECONDS

        client = MagicMock()
        with (
            patch.object(
                premium_recovery.time, "monotonic", side_effect=lambda: clock["now"]
            ),
            patch(
                "crush_lu.views_payments._send_premium_membership_receipt_safely",
                side_effect=slow_receipt,
            ),
            patch("crush_lu.views_payments.SumUpClient", return_value=client),
        ):
            self._apply(paid)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        # 75 s from queueing minus the 30 s receipt leaves no 70 s close pass.
        client.deactivate_checkout.assert_not_called()
        sibling.refresh_from_db()
        self.assertEqual(sibling.status, PaymentTransaction.Status.PENDING)

    def test_publication_refuses_a_deactivated_account(self):
        from crush_lu import views_payments

        src = inspect.getsource(views_payments.create_sumup_premium_checkout)
        self.assertLess(
            src.index("not locked_membership.user.is_active"),
            src.index("PaymentTransaction.objects.create("),
        )

    def test_checkout_refused_while_an_older_case_is_open(self):
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-OLD-PAID", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.REQUEST_CANCELLED,
        )
        replacement = PremiumMembership.objects.create(
            user=self.member, coach=self.coach, status="pending"
        )
        response = self._client().post(
            f"/payments/sumup/create-premium-checkout/{replacement.pk}/"
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(
            PaymentTransaction.objects.filter(premium_membership=replacement).exists()
        )


class NotificationRetryTests(_Base):
    """Codex round 4: a notice/alert that failed at creation is retried."""

    def _failed_case(self, ref, minutes_old):
        from datetime import timedelta

        from django.utils import timezone

        tx = self._tx(ref)
        self._fill_the_coach()
        with patch(
            "azureproject.email_utils.send_domain_email",
            side_effect=RuntimeError("graph down"),
        ), patch(
            "crush_lu.email_helpers.send_domain_email",
            side_effect=RuntimeError("graph down"),
        ):
            with self.assertLogs(level="ERROR"):
                self._apply(tx)
        case = PremiumPaymentRecoveryCase.objects.get(payment=tx)
        PremiumPaymentRecoveryCase.objects.filter(pk=case.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_old)
        )
        return case

    def test_retry_resends_both_and_stamps_them(self):
        from crush_lu.services.premium_recovery import retry_unsent_notifications

        case = self._failed_case("REC-RETRY", minutes_old=30)
        mail.outbox.clear()
        self.assertEqual(retry_unsent_notifications(100), 1)
        case.refresh_from_db()
        self.assertIsNotNone(case.member_notified_at)
        self.assertIsNotNone(case.staff_alerted_at)
        self.assertEqual((len(self._member_mails()), len(self._alerts())), (1, 1))
        # Delivered once: the next tick sends nothing.
        self.assertEqual(retry_unsent_notifications(100), 0)

    def test_retry_keeps_going_after_days(self):
        from crush_lu.services.premium_recovery import retry_unsent_notifications

        case = self._failed_case("REC-OLD-RETRY", minutes_old=60 * 24 * 10)
        self.assertEqual(retry_unsent_notifications(100), 1)
        case.refresh_from_db()
        self.assertIsNotNone(case.staff_alerted_at)

    def test_retry_closes_a_sibling_checkout_left_open(self):
        from datetime import timedelta
        from unittest.mock import MagicMock

        from django.utils import timezone

        from crush_lu.services.premium_recovery import retry_unsent_notifications

        stamp = timezone.now()
        case = PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-RT-PAID", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.COACH_UNAVAILABLE,
            member_notified_at=stamp,
            staff_alerted_at=stamp,
        )
        PremiumPaymentRecoveryCase.objects.filter(pk=case.pk).update(
            created_at=stamp - timedelta(hours=2)
        )
        sibling = self._tx("REC-RT-SIB")
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            self.assertEqual(retry_unsent_notifications(100), 1)
        client.deactivate_checkout.assert_called_once_with("CHK_REC-RT-SIB")
        sibling.refresh_from_db()
        self.assertEqual(sibling.status, PaymentTransaction.Status.CANCELLED)
        self.assertEqual(mail.outbox, [])

    def test_retry_closes_a_sibling_checkout_after_the_case_is_resolved(self):
        from datetime import timedelta
        from unittest.mock import MagicMock

        from django.utils import timezone

        from crush_lu.services.premium_recovery import retry_unsent_notifications

        case = PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-RES-PAID", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.COACH_UNAVAILABLE,
            status=PremiumPaymentRecoveryCase.Status.RESOLVED,
        )
        PremiumPaymentRecoveryCase.objects.filter(pk=case.pk).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        sibling = self._tx("REC-RES-SIB")
        client = MagicMock()
        client.deactivate_checkout.return_value = True
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            retry_unsent_notifications(100)
        sibling.refresh_from_db()
        self.assertEqual(sibling.status, PaymentTransaction.Status.CANCELLED)
        # Resolved: no notice or alert is (re)sent.
        self.assertEqual(mail.outbox, [])

    def test_retry_records_a_sibling_sumup_already_captured(self):
        from datetime import timedelta
        from unittest.mock import MagicMock

        from django.utils import timezone

        from crush_lu.services.premium_recovery import retry_unsent_notifications

        self.membership.status = "active"
        self.membership.save(update_fields=["status"])
        stamp = timezone.now()
        case = PremiumPaymentRecoveryCase.objects.create(
            payment=self._tx("REC-CAP-FIRST", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.DUPLICATE_CAPTURE,
            member_notified_at=stamp,
            staff_alerted_at=stamp,
        )
        PremiumPaymentRecoveryCase.objects.filter(pk=case.pk).update(
            created_at=stamp - timedelta(hours=2)
        )
        sibling = self._tx("REC-CAP-SIB")
        client = MagicMock()
        client.deactivate_checkout.return_value = False
        client.get_checkout.return_value = {
            "id": sibling.sumup_checkout_id,
            "status": "PAID",
            "amount": 10.0,
            "currency": "EUR",
        }
        with patch("crush_lu.views_payments.SumUpClient", return_value=client):
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertLogs("crush_lu.views_payments", level="ERROR"):
                    retry_unsent_notifications(100)
        sibling.refresh_from_db()
        self.assertEqual(sibling.status, PaymentTransaction.Status.PAID)
        self.assertTrue(
            PremiumPaymentRecoveryCase.objects.filter(
                payment=sibling, reason=Reason.DUPLICATE_CAPTURE
            ).exists()
        )
        client.refund.assert_not_called()

    def test_retry_claims_each_case_under_a_row_lock(self):
        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery.retry_unsent_notifications)
        # Only the case row: PostgreSQL rejects FOR UPDATE on the nullable
        # outer-joined membership that select_related pulls in.
        claim = src.index('select_for_update(skip_locked=True, of=("self",))')
        self.assertLess(claim, src.index("case.member_notified_at is None"))
        self.assertLess(claim, src.index("case.staff_alerted_at is None"))

    def test_retry_closes_checkouts_outside_the_claim_in_pk_order(self):
        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery.retry_unsent_notifications)
        # No payment lock may be held under the case claim (deadlock with an
        # overlapping tick or a merge, which locks payments by PK).
        self.assertLess(
            src.index("_close_sibling_checkouts_safely(case)"),
            src.index("with transaction.atomic():"),
        )
        scope = inspect.getsource(premium_recovery._close_sibling_checkouts_safely)
        self.assertLess(
            scope.index("memberships.sort(key=lambda m: m.pk)"),
            scope.index("close_open_checkouts_safely("),
        )

    def test_retry_skips_a_case_still_settling(self):
        from crush_lu.services.premium_recovery import retry_unsent_notifications

        self._failed_case("REC-FRESH", minutes_old=1)
        self.assertEqual(retry_unsent_notifications(100), 0)

    def test_retry_never_starts_a_case_past_the_budget(self):
        from crush_lu.services.premium_recovery import retry_unsent_notifications

        case = self._failed_case("REC-LATE", minutes_old=30)
        mail.outbox.clear()
        self.assertEqual(retry_unsent_notifications(25), 0)
        self.assertEqual(mail.outbox, [])
        case.refresh_from_db()
        self.assertIsNone(case.staff_alerted_at)

    @override_settings(
        ROOT_URLCONF="azureproject.urls_crush",
        ADMIN_API_KEY="k",
        SUMUP_RECONCILIATION_ENABLED=True,
    )
    def test_reconciliation_tick_runs_the_retry(self):
        with patch(
            "crush_lu.management.commands.reconcile_sumup_payments.Command.run_sweep",
            return_value=defaultdict(int),
        ), patch(
            "crush_lu.services.premium_recovery.retry_unsent_notifications",
            return_value=2,
        ) as retry:
            response = Client(HTTP_HOST="crush.lu").post(
                "/api/admin/sumup-reconciliation/", HTTP_AUTHORIZATION="Bearer k"
            )
        retry.assert_called_once()
        self.assertEqual(set(retry.call_args.kwargs), {"budget_seconds"})
        self.assertLessEqual(retry.call_args.kwargs["budget_seconds"], 100)
        self.assertEqual(response.json()["recovery_notices_retried"], 2)

    @override_settings(
        ROOT_URLCONF="azureproject.urls_crush",
        ADMIN_API_KEY="k",
        SUMUP_RECONCILIATION_ENABLED=True,
    )
    def test_reconciliation_tick_actually_resends_a_failed_notice(self):
        case = self._failed_case("REC-TICK", minutes_old=30)
        mail.outbox.clear()
        with patch(
            "crush_lu.management.commands.reconcile_sumup_payments.Command.run_sweep",
            return_value=defaultdict(int),
        ):
            response = Client(HTTP_HOST="crush.lu").post(
                "/api/admin/sumup-reconciliation/", HTTP_AUTHORIZATION="Bearer k"
            )
        self.assertEqual(response.json()["recovery_notices_retried"], 1)
        case.refresh_from_db()
        self.assertIsNotNone(case.member_notified_at)
        self.assertIsNotNone(case.staff_alerted_at)

    @override_settings(
        ROOT_URLCONF="azureproject.urls_crush",
        ADMIN_API_KEY="k",
        SUMUP_RECONCILIATION_ENABLED=False,
    )
    def test_retry_runs_even_with_reconciliation_off(self):
        case = self._failed_case("REC-FLAG-OFF", minutes_old=30)
        mail.outbox.clear()
        response = Client(HTTP_HOST="crush.lu").post(
            "/api/admin/sumup-reconciliation/", HTTP_AUTHORIZATION="Bearer k"
        )
        # The hybrid-maintenance timer accepts exactly this skip shape.
        self.assertEqual(set(response.json()), {"skipped", "reason"})
        case.refresh_from_db()
        self.assertIsNotNone(case.staff_alerted_at)

    @override_settings(
        ROOT_URLCONF="azureproject.urls_crush",
        ADMIN_API_KEY="k",
        SUMUP_RECONCILIATION_ENABLED=True,
    )
    def test_tick_closes_premium_checkouts_before_the_refund_sweep(self):
        calls = []
        with patch(
            "crush_lu.management.commands.reconcile_sumup_payments.Command.run_sweep",
            side_effect=lambda **kw: calls.append("sweep") or defaultdict(int),
        ), patch(
            "crush_lu.services.premium_recovery.close_payable_checkouts",
            side_effect=lambda budget: calls.append("close"),
        ):
            Client(HTTP_HOST="crush.lu").post(
                "/api/admin/sumup-reconciliation/", HTTP_AUTHORIZATION="Bearer k"
            )
        self.assertEqual(calls[:2], ["close", "sweep"])

    def test_refund_sweep_starts_no_read_when_its_first_cannot_fit(self):
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        self._tx("REC-SWEEP-PAID", status=PaymentTransaction.Status.PAID)
        with patch(
            "crush_lu.management.commands.reconcile_sumup_payments.SumUpClient"
        ) as client, self.assertLogs(
            "crush_lu.management.commands.reconcile_sumup_payments", level="WARNING"
        ):
            counters = Command().run_sweep(
                quiet=True, budget_seconds=5, read_reserve_seconds=41
            )
        client.assert_not_called()
        self.assertEqual((counters["checked"], counters["unchecked"]), (0, 1))

    def test_every_unit_fits_the_reconciliation_budget(self):
        from crush_lu import api_admin_sumup as api
        from crush_lu.services import premium_recovery as rec

        self.assertEqual(rec.SEND_SECONDS, api.GRAPH_SEND_TIMEOUT_SECONDS)
        for cost in (rec.SEND_SECONDS, rec._close_seconds(), rec._sync_seconds()):
            self.assertLess(cost, api.RECONCILIATION_BUDGET_SECONDS)


class RecoveryLockOrderTests(TestCase):
    """SQLite ignores select_for_update, so lock order is asserted on source.

    Opening a case locks only the owner's User row, after the payment lock:
    PaymentTransaction is locked before CrushProfile (via confirm()); the case
    is written in that transaction and only the mail waits for commit.
    """

    def test_payment_lock_precedes_profile_lock_and_case_queueing(self):
        from crush_lu import views_payments

        src = inspect.getsource(views_payments._apply_paid_checkout)
        payment = src.index("PaymentTransaction.objects.select_for_update")
        self.assertLess(payment, src.index("CrushProfile.objects.select_for_update"))
        self.assertLess(payment, src.index("pm.confirm()"))
        self.assertLess(payment, src.index("_queue_premium_recovery_case("))

    def test_cancel_happens_under_the_checkout_publication_locks(self):
        from crush_lu import views_premium

        src = inspect.getsource(views_premium._cancel_premium_request)
        atomic = src.index("with transaction.atomic():")
        lock = src.index("_lock_premium_checkout_state(")
        cancel = src.index(".cancel(by_user=")
        self.assertLess(atomic, lock)
        self.assertLess(lock, cancel)
        # Still inside that atomic block: indented deeper than the "with".
        self.assertTrue(
            src[src.rindex("\n", 0, cancel) + 1 :].startswith(" " * 8),
        )

    def test_merge_locks_memberships_after_payments_before_reading_cases(self):
        from crush_lu.services import account_merge

        src = inspect.getsource(account_merge.merge_accounts)
        payments = src.index("PaymentTransaction.objects.select_for_update")
        memberships = src.index("PremiumMembership.objects.select_for_update")
        cases = src.index("PremiumPaymentRecoveryCase.objects.filter")
        self.assertLess(payments, memberships)
        self.assertLess(memberships, cases)

    def test_case_is_written_in_the_transaction_and_locks_only_the_member(self):
        from crush_lu import views_payments
        from crush_lu.services import premium_recovery

        queue_src = inspect.getsource(views_payments._queue_premium_recovery_case)
        self.assertLess(
            queue_src.index("premium_recovery.open_case("),
            queue_src.index("transaction.on_commit("),
        )
        # Opening takes one lock: the owner's User row (after the caller's
        # payment -> membership locks), never a payment or membership lock.
        open_src = inspect.getsource(premium_recovery.open_case)
        self.assertEqual(len(re.findall(r"select_for_update", open_src)), 1)
        self.assertIn("get_user_model().objects.select_for_update()", open_src)
