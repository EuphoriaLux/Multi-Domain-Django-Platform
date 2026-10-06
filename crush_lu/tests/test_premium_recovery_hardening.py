"""#925 WP1 part 3: the findings deferred from #1163 and #1204.

* the Graph token request is bounded, cached, and counted in a send's budget;
* a member blocked from buying sees no "Go Premium" call to action;
* hand resolutions and the legacy membership confirmation are serialized
  with the refund sweep and with case creation (lock order, asserted on
  source: SQLite ignores select_for_update).

The SumUp checkout deadline is covered in test_premium_checkout_lock.
Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

import inspect
import re
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory, TestCase, override_settings

from azureproject import graph_email_backend as g
from crush_lu.models import PremiumPaymentRecoveryCase
from crush_lu.models.payments import PaymentTransaction
from crush_lu.tests.test_premium_recovery import Reason, User, _Base

Case = PremiumPaymentRecoveryCase


class GraphTokenTests(TestCase):
    def setUp(self):
        g._msal_apps.clear()
        self.addCleanup(g._msal_apps.clear)

    def _backend(self):
        return g.GraphEmailBackend(
            tenant_id="tenant", client_id="client", client_secret="secret"
        )

    def test_token_requests_are_bounded_and_the_app_is_reused(self):
        app = MagicMock()
        app.acquire_token_silent.return_value = {"access_token": "tok"}
        with patch("msal.ConfidentialClientApplication", return_value=app) as cca:
            self.assertEqual(self._backend().get_access_token(), "tok")
            self.assertEqual(self._backend().get_access_token(), "tok")
        # One app per credentials and process: its token cache is reused.
        cca.assert_called_once()
        http_client = cca.call_args.kwargs["http_client"]
        self.assertEqual(
            http_client.request.keywords["timeout"],
            (g.GRAPH_TOKEN_CONNECT_TIMEOUT_SECONDS, g.GRAPH_TOKEN_READ_TIMEOUT_SECONDS),
        )
        # No retry adapter of MSAL's, which would double a request's worst case.
        self.assertEqual(http_client.get_adapter("https://x").max_retries.total, 0)

    def test_a_send_reserves_the_token_request_too(self):
        from crush_lu.services import premium_recovery

        self.assertEqual(
            premium_recovery.SEND_SECONDS,
            g.GRAPH_CONNECT_TIMEOUT_SECONDS
            + g.GRAPH_READ_TIMEOUT_SECONDS
            + g.GRAPH_TOKEN_REQUESTS
            * (
                g.GRAPH_TOKEN_CONNECT_TIMEOUT_SECONDS
                + g.GRAPH_TOKEN_READ_TIMEOUT_SECONDS
            ),
        )


@override_settings(PREMIUM_REDIRECTS_TO_BETA=False)
class BlockedPurchaseCtaTests(_Base):
    """A staff-only case (ambiguous backfill) shows no notice but still blocks
    a fresh purchase: no CTA may lead to a chooser that refuses."""

    def setUp(self):
        super().setUp()
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        Case.objects.create(
            payment=self._tx("HARD-STAFF", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.REQUEST_CANCELLED,
            staff_only=True,
        )
        self.profile.verification_status = "verified"
        self.profile.save(update_fields=["verification_status"])
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.member)

    def test_dashboard_offers_no_go_premium(self):
        html = self.client.get("/en/dashboard/").content.decode()
        self.assertNotIn("Go Premium — first month free", html)
        self.assertNotIn('data-testid="premium-recovery-notice"', html)

    def test_membership_page_offers_support_not_go_premium(self):
        html = self.client.get("/en/membership/").content.decode()
        card = re.search(r'id="premium-plan".*?</section>', html, re.S).group(0)
        self.assertNotIn("Go Premium", card)
        self.assertIn("Contact support about your Premium payment", card)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_membership_page_offers_support_not_the_waitlist_in_the_beta(self):
        html = self.client.get("/en/membership/").content.decode()
        card = re.search(r'id="premium-plan".*?</section>', html, re.S).group(0)
        self.assertNotIn("Join the Waitlist", card)
        self.assertIn("Contact support about your Premium payment", card)

    def test_the_cta_returns_once_nothing_blocks(self):
        Case.objects.update(status=Case.Status.RESOLVED)
        html = self.client.get("/en/dashboard/").content.decode()
        self.assertIn("Go Premium — first month free", html)


class HandResolutionSerializationTests(_Base):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username="hard-staff@example.invalid",
            email="hard-staff@example.invalid",
            password="pass12345",
            is_staff=True,
        )
        self.case = Case.objects.create(
            payment=self._tx("HARD-HAND", status=PaymentTransaction.Status.PAID),
            user=self.member,
            premium_membership=self.membership,
            reason=Reason.DUPLICATE_CAPTURE,
        )

    def _save_status(self, stale_case, status):
        from crush_lu.admin import crush_admin_site

        model_admin = crush_admin_site._registry[Case]
        request = RequestFactory().post("/")
        request.user = self.staff
        request.session = {}
        request._messages = FallbackStorage(request)
        stale_case.status = status
        model_admin.save_model(
            request, stale_case, MagicMock(changed_data=["status"]), True
        )
        return [str(m) for m in get_messages(request)]

    def test_a_refund_the_sweep_recorded_meanwhile_is_kept(self):
        stale = Case.objects.get(pk=self.case.pk)  # the admin form's row
        Case.objects.filter(pk=self.case.pk).update(
            status=Case.Status.RESOLVED, resolution=Case.Resolution.REFUNDED
        )
        messages = self._save_status(stale, Case.Status.RESOLVED)
        self.case.refresh_from_db()
        self.assertEqual(self.case.resolution, Case.Resolution.REFUNDED)
        self.assertIsNone(self.case.resolved_by)
        self.assertTrue(any("changed meanwhile" in m for m in messages))

    def test_reopening_keeps_the_resolution(self):
        Case.objects.filter(pk=self.case.pk).update(
            status=Case.Status.RESOLVED, resolution=Case.Resolution.APPLIED
        )
        self._save_status(Case.objects.get(pk=self.case.pk), Case.Status.OPEN)
        self.case.refresh_from_db()
        self.assertEqual(
            (self.case.status, self.case.resolution),
            (Case.Status.OPEN, Case.Resolution.APPLIED),
        )

    def test_resolving_a_reopened_case_keeps_its_resolution(self):
        Case.objects.filter(pk=self.case.pk).update(
            status=Case.Status.OPEN, resolution=Case.Resolution.APPLIED
        )
        self._save_status(Case.objects.get(pk=self.case.pk), Case.Status.RESOLVED)
        self.case.refresh_from_db()
        self.assertEqual(
            (self.case.status, self.case.resolution, self.case.resolved_by),
            (Case.Status.RESOLVED, Case.Resolution.APPLIED, None),
        )

    def test_a_refused_change_is_not_logged(self):
        from django.contrib.admin.models import LogEntry

        from crush_lu.admin import crush_admin_site

        stale = Case.objects.get(pk=self.case.pk)
        Case.objects.filter(pk=self.case.pk).update(
            status=Case.Status.RESOLVED, resolution=Case.Resolution.REFUNDED
        )
        request = RequestFactory().post("/")
        request.user = self.staff
        request.session = {}
        request._messages = FallbackStorage(request)
        model_admin = crush_admin_site._registry[Case]
        stale.status = Case.Status.RESOLVED
        model_admin.save_model(request, stale, MagicMock(changed_data=["status"]), True)
        model_admin.log_change(request, stale, [{"changed": {"fields": ["Status"]}}])
        self.assertFalse(LogEntry.objects.filter(object_id=str(stale.pk)).exists())

    def test_payment_is_locked_before_the_case(self):
        from crush_lu.services import premium_recovery

        src = inspect.getsource(premium_recovery.set_case_status_by_hand)
        self.assertLess(
            src.index("PaymentTransaction.objects.select_for_update()"),
            src.index("Case.objects.select_for_update()"),
        )


class LegacyConfirmSerializationTests(TestCase):
    def test_payments_are_locked_before_the_case_check_and_confirm(self):
        from crush_lu.admin.profiles import PremiumMembershipAdmin

        src = inspect.getsource(PremiumMembershipAdmin.confirm_payment)
        lock = src.index("_lock_premium_checkout_state(membership.pk")
        self.assertLess(lock, src.index("PremiumPaymentRecoveryCase.objects.filter"))
        self.assertLess(lock, src.index("membership.confirm("))
        self.assertIn("transaction.atomic()", src[:lock])

    def test_admin_site_is_importable(self):
        self.assertIsNotNone(AdminSite)
