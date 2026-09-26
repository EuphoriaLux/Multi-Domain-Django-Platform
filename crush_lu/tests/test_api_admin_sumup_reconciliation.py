"""Tests for POST /api/admin/sumup-reconciliation/ (SumUp Tier-2 poll).

Implements the test matrix in
ai-memory-hub/policies/sumup-tier2-refund-automation-contract.md §10 — the case
number from that table is in each test's docstring — plus the structural pins
it asks for (§8 no refund client, §9.8 no --include-partial, §4 lock order).

Every flag-on test mocks BOTH SumUp reads the sweep makes (the checkout and the
transaction history): unmocked, they are real HTTP calls to SumUp.
"""

import inspect
import io
import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from crush_lu.models.credits import CreditRedemption, CrushCredit
from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import EventCheckoutCreationClaim, PaymentTransaction
from crush_lu.models.profiles import CrushCoach, CrushProfile, PremiumMembership
from crush_lu.services.sumup import SumUpError

User = get_user_model()

API_KEY = "test-admin-api-key"
URL = "/api/admin/sumup-reconciliation/"
CMD = "crush_lu.management.commands.reconcile_sumup_payments"
GET_CHECKOUT = f"{CMD}.SumUpClient.get_checkout"
GET_HISTORY = f"{CMD}.SumUpClient.get_transactions_history"
COUNTER_KEYS = {
    "in_window",
    "checked",
    "reconciled",
    "needs_review",
    "partial",
    "errors",
    "unchecked",
}

STILL_PAID = {"id": "chk_t2_1", "status": "PAID", "amount": 15.50}
FULL_REFUND = {"id": "chk_t2_1", "status": "REFUNDED", "amount": 15.50}
PARTIAL_REFUND = {"id": "chk_t2_1", "status": "PAID", "amount_refunded": "2.00"}


@override_settings(ROOT_URLCONF="azureproject.urls_crush", ADMIN_API_KEY=API_KEY)
class SumUpReconciliationEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.user = User.objects.create_user(
            username="t2_member",
            email="t2_member@test.crush.lu",
            password="secretpassword123",
        )
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            verification_status="verified",
            completion_status="step4",
        )
        self.event = MeetupEvent.objects.create(
            title="Speed Dating T2",
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
            transaction_reference="CRUSH-EVT-T2-secret-ref",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="chk_t2_1",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
            event=self.event,
            raw_response={"status": "PAID", "payer": "raw-payload-marker"},
        )

    # -- helpers -----------------------------------------------------------

    def _post(self, token=API_KEY):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post(URL, **headers)

    def _run(self, checkout_payload, history=None):
        """POST with the flag on and both SumUp reads mocked."""
        with (
            override_settings(SUMUP_RECONCILIATION_ENABLED=True),
            patch(GET_CHECKOUT) as get_checkout,
            patch(GET_HISTORY, return_value=history or {"items": []}),
        ):
            if isinstance(checkout_payload, BaseException):
                get_checkout.side_effect = checkout_payload
            else:
                get_checkout.return_value = checkout_payload
            resp = self._post()
        return resp

    # -- case 1: auth ------------------------------------------------------

    def test_missing_bearer_is_401_and_runs_nothing(self):
        """Case 1."""
        with (
            override_settings(SUMUP_RECONCILIATION_ENABLED=True),
            patch(GET_CHECKOUT) as get_checkout,
            patch(GET_HISTORY) as get_history,
        ):
            resp = self._post(token=None)
        self.assertEqual(resp.status_code, 401)
        get_checkout.assert_not_called()
        get_history.assert_not_called()

    def test_bad_bearer_is_401_and_runs_nothing(self):
        """Case 1 (bad-bearer)."""
        with (
            override_settings(SUMUP_RECONCILIATION_ENABLED=True),
            patch(GET_CHECKOUT) as get_checkout,
            patch(GET_HISTORY) as get_history,
        ):
            resp = self._post(token="not-the-key")
        self.assertEqual(resp.status_code, 401)
        get_checkout.assert_not_called()
        get_history.assert_not_called()

    # -- case 2: flag off --------------------------------------------------

    def test_flag_defaults_off(self):
        """§9.4: the deploy is dark unless the environment opts in."""
        from pathlib import Path

        from django.conf import settings

        self.assertIs(settings.SUMUP_RECONCILIATION_ENABLED, False)
        src = (
            Path(__file__).resolve().parents[2] / "azureproject" / "settings.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'SUMUP_RECONCILIATION_ENABLED = _env_bool("SUMUP_RECONCILIATION_ENABLED", False)',
            src,
        )

    def test_flag_off_skips_and_names_the_flag(self):
        """Case 2."""
        with (
            override_settings(SUMUP_RECONCILIATION_ENABLED=False),
            patch(GET_CHECKOUT) as get_checkout,
            patch(GET_HISTORY) as get_history,
        ):
            resp = self._post()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIs(body["skipped"], True)
        self.assertIn("SUMUP_RECONCILIATION_ENABLED", body["reason"])
        get_checkout.assert_not_called()
        get_history.assert_not_called()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)

    # -- case 3: nothing refunded -----------------------------------------

    def test_flag_on_nothing_refunded_reports_counters(self):
        """Case 3."""
        resp = self._run(STILL_PAID)
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["checked"], 1)
        self.assertEqual(body["reconciled"], 0)
        self.assertEqual(body["partial"], 0)
        self.assertEqual(body["errors"], 0)
        self.assertEqual(body["unchecked"], 0)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)

    def test_flag_on_with_no_paid_rows_still_reports_counters(self):
        """The empty-window early return must still hand back counters."""
        self.payment.status = PaymentTransaction.Status.FAILED
        self.payment.save(update_fields=["status"])
        resp = self._run(STILL_PAID)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["checked"], 0)

    # -- case 4: full external refund --------------------------------------

    def test_full_external_refund_is_reconciled(self):
        """Case 4."""
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
        resp = self._run(FULL_REFUND)

        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["reconciled"], 1)
        self.payment.refresh_from_db()
        self.registration.refresh_from_db()
        credit.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(self.registration.status, "cancelled")
        self.assertFalse(self.registration.payment_confirmed)
        self.assertEqual(credit.status, CrushCredit.Status.VOID)

    def test_refund_seen_only_in_transaction_history_is_reconciled(self):
        """Case 4, dashboard shape: checkout stays PAID, history says REFUNDED."""
        checkout = {
            "id": "chk_t2_1",
            "status": "PAID",
            "transaction_code": "TX_T2",
            "transactions": [
                {"status": "SUCCESSFUL", "transaction_code": "TX_T2", "amount": 15.5}
            ],
        }
        history = {
            "items": [
                {
                    "transaction_code": "TX_T2",
                    "type": "PAYMENT",
                    "status": "REFUNDED",
                    "amount": 15.5,
                    "refunded_amount": 15.5,
                }
            ]
        }
        resp = self._run(checkout, history=history)
        self.assertEqual(resp.json()["reconciled"], 1)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)

    # -- case 5: duplicate delivery ---------------------------------------

    def test_duplicate_delivery_applies_the_refund_once(self):
        """Case 5: a second run reconciles nothing and promotes nobody again."""
        from crush_lu import views_events

        waiter = User.objects.create_user(
            username="t2_waiter", email="t2_waiter@test.crush.lu", password="x" * 12
        )
        EventRegistration.objects.create(
            event=self.event, user=waiter, status="waitlist"
        )

        with patch.object(
            views_events,
            "_promote_from_waitlist",
            wraps=views_events._promote_from_waitlist,
        ) as promote:
            with self.captureOnCommitCallbacks(execute=True):
                first = self._run(FULL_REFUND)
            first_updated = PaymentTransaction.objects.get(
                pk=self.payment.pk
            ).updated_at
            with self.captureOnCommitCallbacks(execute=True):
                second = self._run(FULL_REFUND)

        self.assertEqual(first.json()["reconciled"], 1)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.json()["reconciled"], 0)
        # The REFUNDED row is no longer selected at all.
        self.assertEqual(second.json()["checked"], 0)
        self.assertEqual(promote.call_count, 1)
        self.assertEqual(
            PaymentTransaction.objects.get(pk=self.payment.pk).updated_at,
            first_updated,
            "the second run must not write the transaction again",
        )

    def test_overlapping_run_that_loses_the_lock_counts_nothing(self):
        """Codex 4079854861: the run that waited on the lock must not count.

        Simulates the overlap: between this run's read and its write, another
        run reconciles the row. _reconcile_refunded then finds it no longer
        PAID and does nothing, and the counter must say so.
        """
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        original = Command._reconcile_refunded

        def other_run_wins(cmd, tx_obj, remote_data, dry_run=False):
            PaymentTransaction.objects.filter(pk=tx_obj.pk).update(
                status=PaymentTransaction.Status.REFUNDED
            )
            return original(cmd, tx_obj, remote_data, dry_run=dry_run)

        with patch.object(Command, "_reconcile_refunded", other_run_wins):
            resp = self._run(FULL_REFUND)
        body = resp.json()
        self.assertEqual((body["checked"], body["reconciled"]), (1, 0))
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "confirmed")

    def test_reconcile_reports_whether_it_transitioned(self):
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        self.assertEqual(
            Command()._reconcile_refunded(self.payment, FULL_REFUND), "reconciled"
        )
        self.assertIs(Command()._reconcile_refunded(self.payment, FULL_REFUND), False)

    def test_a_row_refunded_between_selection_and_lock_is_skipped(self):
        """Case 5, overlap: the write re-checks status under the row lock."""
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        cmd = Command()
        PaymentTransaction.objects.filter(pk=self.payment.pk).update(
            status=PaymentTransaction.Status.REFUNDED
        )
        cmd._reconcile_refunded(self.payment, FULL_REFUND)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "confirmed")
        self.assertTrue(self.registration.payment_confirmed)

    # -- case 6: partial refund -------------------------------------------

    def test_partial_refund_is_counted_and_left_alone(self):
        """Case 6."""
        with self.assertLogs(CMD, level=logging.WARNING) as cmd_logs:
            with self.assertLogs("crush_lu.api_admin_sumup", level=logging.WARNING):
                resp = self._run(PARTIAL_REFUND)

        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["partial"], 1)
        self.assertEqual(body["reconciled"], 0)
        self.payment.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.registration.status, "confirmed")
        self.assertTrue(self.registration.payment_confirmed)
        self.assertTrue(any("PARTIAL refund" in m for m in cmd_logs.output))

    # -- case 7: method ----------------------------------------------------

    def test_get_is_405(self):
        """Case 7."""
        with override_settings(SUMUP_RECONCILIATION_ENABLED=True):
            resp = self.client.get(URL, HTTP_AUTHORIZATION=f"Bearer {API_KEY}")
        self.assertEqual(resp.status_code, 405)

    # -- case 8: downstream failure ---------------------------------------

    def test_sumup_error_is_counted_not_raised(self):
        """Case 8: SumUpError."""
        resp = self._run(SumUpError("500 from SumUp"))
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["errors"], 1)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)

    def test_non_json_body_is_counted_not_raised(self):
        """Case 8: a non-JSON 2xx surfaces as ValueError (JSONDecodeError)."""
        resp = self._run(ValueError("Expecting value: line 1 column 1"))
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["errors"], 1)

    def test_one_bad_row_does_not_stop_the_next(self):
        """Case 8: the sweep continues past a failing row."""
        other = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-T2-other",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="chk_t2_2",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
        )
        # The endpoint reads oldest first: make the failing row the older one.
        PaymentTransaction.objects.filter(pk=other.pk).update(
            created_at=timezone.now() - timedelta(days=5),
            paid_at=timezone.now() - timedelta(days=5),
        )

        def by_id(checkout_id):
            if checkout_id == "chk_t2_2":
                raise SumUpError("boom")
            return {"id": checkout_id, "status": "REFUNDED"}

        with (
            override_settings(SUMUP_RECONCILIATION_ENABLED=True),
            patch(GET_CHECKOUT, side_effect=by_id),
            patch(GET_HISTORY, return_value={"items": []}),
        ):
            resp = self._post()
        body = resp.json()
        self.assertEqual(
            (body["checked"], body["errors"], body["reconciled"]), (2, 1, 1)
        )
        self.payment.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(other.status, PaymentTransaction.Status.PAID)

    def test_history_prefetch_failure_does_not_500(self):
        """Case 8: the history prefetch raising is logged, not fatal."""
        with (
            override_settings(SUMUP_RECONCILIATION_ENABLED=True),
            patch(GET_CHECKOUT, return_value=FULL_REFUND),
            patch(GET_HISTORY, side_effect=SumUpError("history down")),
        ):
            resp = self._post()
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["reconciled"], 1)

    # -- case 9: no PII ----------------------------------------------------

    def test_response_carries_counts_only(self):
        """Case 9."""
        resp = self._run(FULL_REFUND)
        body = resp.json()
        self.assertEqual(
            set(body),
            {"status", "timestamp", "cursor_resumed", "wrapped"} | COUNTER_KEYS,
        )
        raw = resp.content.decode()
        for secret in (
            self.user.email,
            self.payment.transaction_reference,
            self.payment.sumup_checkout_id,
            "raw-payload-marker",
            "REFUNDED",
        ):
            self.assertNotIn(secret, raw)

    def test_summary_log_line_carries_counts_only(self):
        """§7.2 structured line; §7.3 nothing identifying in it."""
        with self.assertLogs("crush_lu.api_admin_sumup", level=logging.INFO) as logs:
            self._run(STILL_PAID)
        line = next(m for m in logs.output if "checked=" in m)
        self.assertIn(
            "in_window=1 checked=1 reconciled=0 needs_review=0 partial=0 "
            "errors=0 unchecked=0 cursor_resumed=False wrapped=True",
            line,
        )
        self.assertNotIn(self.payment.transaction_reference, line)
        self.assertNotIn(self.payment.sumup_checkout_id, line)

    # -- bounded work (§6.3) ----------------------------------------------

    def test_budget_stops_starting_new_rows_and_reports_them(self):
        """A spent budget leaves rows PAID for the next run and says so."""
        from crush_lu import api_admin_sumup

        with (
            patch.object(api_admin_sumup, "RECONCILIATION_BUDGET_SECONDS", 0),
            self.assertLogs("crush_lu.api_admin_sumup", level=logging.WARNING),
        ):
            resp = self._run(FULL_REFUND)
        body = resp.json()
        self.assertEqual((body["checked"], body["unchecked"]), (0, 1))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)

    def test_a_refund_is_not_written_without_time_for_its_post_commit_work(self):
        """Codex 4079854872: a write sets off synchronous post-commit emails.

        With too little budget left for the worst case of that work, the
        detected refund is left PAID and counted unchecked - never committed
        and then cut off by the caller's timeout before the member is told.
        """
        from crush_lu import api_admin_sumup

        with (
            patch.object(api_admin_sumup, "WRITE_RESERVE_SECONDS", 10_000),
            self.assertLogs(CMD, level=logging.WARNING) as logs,
        ):
            resp = self._run(FULL_REFUND)
        body = resp.json()
        self.assertEqual(
            (body["checked"], body["reconciled"], body["unchecked"]), (0, 0, 1)
        )
        self.assertTrue(any("deferred a detected refund" in m for m in logs.output))
        self.payment.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.registration.status, "confirmed")

    def test_write_reserve_does_not_hold_back_rows_that_need_no_write(self):
        """The write reserve only gates writes; plain PAID rows still get read."""
        from crush_lu import api_admin_sumup

        with patch.object(api_admin_sumup, "WRITE_RESERVE_SECONDS", 10_000):
            resp = self._run(STILL_PAID)
        body = resp.json()
        self.assertEqual((body["checked"], body["unchecked"]), (1, 0))

    def test_budget_is_built_from_the_real_timeouts(self):
        """Pin the numbers and the source timeouts they are derived from."""
        from pathlib import Path

        from crush_lu import api_admin_sumup as m

        root = Path(__file__).resolve().parents[2]
        sumup_src = (root / "crush_lu" / "services" / "sumup.py").read_text(
            encoding="utf-8"
        )
        for method in ("def get_checkout", "def get_transactions_history"):
            body = sumup_src[sumup_src.index(method) :]
            body = body[: body.index("\n    def ", 1)]
            self.assertIn(f"timeout={m.SUMUP_READ_TIMEOUT_SECONDS}", body, method)
        graph_src = (root / "azureproject" / "graph_email_backend.py").read_text(
            encoding="utf-8"
        )
        send = graph_src[graph_src.index("def _send_message") :]
        send = send[: send.index("\ndef ")]
        self.assertIn(f"timeout={m.GRAPH_SEND_TIMEOUT_SECONDS}", send)
        fa_src = (
            root / "azure-functions" / "hybrid-maintenance" / "function_app.py"
        ).read_text(encoding="utf-8")
        call = fa_src[
            fa_src.index('"DJANGO_SUMUP_RECONCILIATION_URL",\n        timeout=') :
        ]
        self.assertIn(f"timeout={m.FUNCTION_TIMEOUT_SECONDS},", call[:120])
        self.assertIn(
            "--timeout 120", (root / "startup.sh").read_text(encoding="utf-8")
        )

        google_src = (root / "crush_lu" / "wallet" / "google_api.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            f"GOOGLE_WALLET_HTTP_TIMEOUT = {m.GOOGLE_WALLET_TIMEOUT_SECONDS}.0",
            google_src,
        )
        ticket_src = (
            root / "crush_lu" / "wallet" / "google_event_ticket_api.py"
        ).read_text(encoding="utf-8")
        patch_fn = ticket_src[ticket_src.index("def _patch_ticket_state") :]
        self.assertIn(
            f"httpx.Client(timeout={m.GOOGLE_WALLET_TIMEOUT_SECONDS}.0)", patch_fn
        )
        apns_src = (root / "crush_lu" / "wallet" / "passkit_apns.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            f"httpx.Client(http2=True, timeout={m.APNS_TIMEOUT_SECONDS}.0)", apns_src
        )

        # The post-commit chain of one event refund, and the fact that it
        # does NOT fit the deadline on its own — pinned so the comment in
        # api_admin_sumup cannot drift into false comfort.
        self.assertEqual(m.POST_COMMIT_BOUNDED_WORST_SECONDS, 240)
        self.assertEqual(m.POST_COMMIT_APNS_PER_DEVICE_SECONDS, 40)
        self.assertGreater(
            m.POST_COMMIT_BOUNDED_WORST_SECONDS, m.RECONCILIATION_BUDGET_SECONDS
        )
        self.assertEqual(m.MAX_WRITES_PER_RUN, 1)

        self.assertEqual(m.RECONCILIATION_BUDGET_SECONDS, 100)
        self.assertEqual(m.READ_RESERVE_SECONDS, 41)
        self.assertEqual(m.WRITE_RESERVE_SECONDS, 65)
        self.assertLess(m.RECONCILIATION_BUDGET_SECONDS, m.FUNCTION_TIMEOUT_SECONDS)
        self.assertLess(m.FUNCTION_TIMEOUT_SECONDS, 120)
        self.assertGreater(m.RECONCILIATION_BUDGET_SECONDS - m.WRITE_RESERVE_SECONDS, 0)

    def _second_refundable_payment(self):
        other_user = User.objects.create_user(
            username="t2_other", email="t2_other@test.crush.lu", password="x" * 12
        )
        reg = EventRegistration.objects.create(
            event=self.event,
            user=other_user,
            status="confirmed",
            payment_confirmed=True,
            payment_date=timezone.now(),
        )
        return PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-T2-second",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="chk_t2_2",
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=other_user,
            event_registration=reg,
            event=self.event,
        )

    def test_one_refund_write_per_run(self):
        """Codex 4080044185 (a): a second refund waits for the next run."""
        second = self._second_refundable_payment()

        def refunded(checkout_id):
            return {"id": checkout_id, "status": "REFUNDED"}

        def run():
            with (
                override_settings(SUMUP_RECONCILIATION_ENABLED=True),
                patch(GET_CHECKOUT, side_effect=refunded),
                patch(GET_HISTORY, return_value={"items": []}),
            ):
                return self._post().json()

        first = run()
        self.assertEqual(
            (first["checked"], first["reconciled"], first["unchecked"]), (1, 1, 1)
        )
        second_run = run()
        self.assertEqual(
            (second_run["checked"], second_run["reconciled"], second_run["unchecked"]),
            (1, 1, 0),
        )
        self.payment.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(second.status, PaymentTransaction.Status.REFUNDED)

    def test_cli_keeps_writing_every_refund(self):
        """The one-write limit is the endpoint's; the CLI is unchanged."""
        from django.core.management import call_command

        self._second_refundable_payment()
        with (
            patch(GET_CHECKOUT, side_effect=lambda c: {"id": c, "status": "REFUNDED"}),
            patch(GET_HISTORY, return_value={"items": []}),
        ):
            call_command("reconcile_sumup_payments", quiet=True)
        self.assertEqual(
            PaymentTransaction.objects.filter(
                status=PaymentTransaction.Status.REFUNDED
            ).count(),
            2,
        )

    # -- the refunded member's email (Codex 4080044193) -------------------

    def _mails_to(self, address):
        return [m for m in mail.outbox if address in m.to]

    def test_refunded_member_gets_one_honest_email(self):
        waiter = User.objects.create_user(
            username="t2_wait", email="t2_wait@test.crush.lu", password="x" * 12
        )
        EventRegistration.objects.create(
            event=self.event, user=waiter, status="waitlist"
        )
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._run(FULL_REFUND)
        self.assertEqual(resp.json()["reconciled"], 1)

        mine = self._mails_to(self.user.email)
        self.assertEqual(len(mine), 1)
        body = mine[0].body
        self.assertIn("has been refunded to the payment method you used", body)
        self.assertNotIn("No payment was recorded", body)
        self.assertNotIn("Crush Credit to your account", body)
        # The promotion still happens and is announced to the promoted member.
        self.assertEqual(len(self._mails_to(waiter.email)), 1)

        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            again = self._run(FULL_REFUND)
        self.assertEqual(again.json()["reconciled"], 0)
        self.assertEqual(mail.outbox, [])

    # -- refund on an ALREADY-cancelled seat (Codex 4080234097) -----------

    REFUNDED_SENTENCE = "has been refunded to the payment method you used."
    WITHDRAWN_SENTENCE = "has been withdrawn"

    def _cancel_seat_first(self, credit_cents=None, redeemed_cents=0):
        """The member cancelled earlier (>48h, credit issued); the card refund
        comes later. Written with .update() so no signal fires here."""
        EventRegistration.objects.filter(pk=self.registration.pk).update(
            status="cancelled", payment_confirmed=False
        )
        if credit_cents is None:
            return None
        credit = CrushCredit.objects.create(
            user=self.user,
            amount_cents=credit_cents,
            currency="EUR",
            reason=CrushCredit.Reason.MEMBER_CANCELLATION,
            status=CrushCredit.Status.ACTIVE,
            source_payment=self.payment,
            source_registration=self.registration,
        )
        if redeemed_cents:
            CreditRedemption.objects.create(credit=credit, amount_cents=redeemed_cents)
        return credit

    def test_already_cancelled_with_credit_gets_one_withdrawal_email(self):
        credit = self._cancel_seat_first(credit_cents=1550)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._run(FULL_REFUND)
        self.assertEqual(resp.json()["reconciled"], 1)

        mine = self._mails_to(self.user.email)
        self.assertEqual(len(mine), 1)
        body = mine[0].body
        self.assertIn(self.REFUNDED_SENTENCE, body)
        self.assertIn("15.50 EUR in Crush Credit", body)
        self.assertIn(self.WITHDRAWN_SENTENCE, body)
        self.assertNotIn("No payment was recorded", body)
        credit.refresh_from_db()
        self.assertEqual(credit.status, CrushCredit.Status.VOID)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)

    def test_withdrawn_amount_is_what_was_still_spendable(self):
        self._cancel_seat_first(credit_cents=2000, redeemed_cents=1000)
        with self.captureOnCommitCallbacks(execute=True):
            self._run(FULL_REFUND)
        (only,) = self._mails_to(self.user.email)
        self.assertIn("10.00 EUR in Crush Credit", only.body)

    def test_already_cancelled_without_credit_gets_one_plain_email(self):
        self._cancel_seat_first()
        with self.captureOnCommitCallbacks(execute=True):
            self._run(FULL_REFUND)
        mine = self._mails_to(self.user.email)
        self.assertEqual(len(mine), 1)
        self.assertIn(self.REFUNDED_SENTENCE, mine[0].body)
        self.assertNotIn(self.WITHDRAWN_SENTENCE, mine[0].body)

    def test_already_cancelled_second_run_and_dry_run_send_nothing(self):
        from django.core.management import call_command

        self._cancel_seat_first(credit_cents=1550)
        with (
            patch(GET_CHECKOUT, return_value=FULL_REFUND),
            patch(GET_HISTORY, return_value={"items": []}),
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command("reconcile_sumup_payments", dry_run=True, quiet=True)
        self.assertEqual(mail.outbox, [])

        with self.captureOnCommitCallbacks(execute=True):
            self._run(FULL_REFUND)
        self.assertEqual(len(mail.outbox), 1)
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            again = self._run(FULL_REFUND)
        self.assertEqual(again.json()["checked"], 0)
        self.assertEqual(mail.outbox, [])

    def test_a_seat_cancelled_by_the_refund_gets_only_the_signal_email(self):
        """No double send: the new notice is only for already-cancelled seats."""
        with self.captureOnCommitCallbacks(execute=True):
            self._run(FULL_REFUND)
        mine = self._mails_to(self.user.email)
        self.assertEqual(len(mine), 1)
        self.assertIn("Registration Cancelled", mine[0].subject)

    def test_a_failing_refund_notice_does_not_break_the_sweep(self):
        self._cancel_seat_first(credit_cents=1550)
        with (
            patch(
                "crush_lu.email_helpers.send_refund_after_cancellation_notice",
                side_effect=RuntimeError("graph down"),
            ),
            self.captureOnCommitCallbacks(execute=True),
        ):
            resp = self._run(FULL_REFUND)
        body = resp.json()
        self.assertEqual((body["reconciled"], body["errors"]), (1, 0))

    def test_refund_notice_strings_are_translated(self):
        from django.utils import translation
        from django.utils.translation import gettext

        m1 = (
            "Your payment for <strong>%(title)s</strong> has been refunded to "
            "the payment method you used."
        )
        m2 = (
            "Because of this refund, the %(amount)s EUR in Crush Credit issued "
            "when you cancelled this registration has been withdrawn."
        )
        with translation.override("de"):
            self.assertIn("du bezahlt hast", gettext(m1))
            self.assertIn("zurückgezogen", gettext(m2))
        with translation.override("fr"):
            self.assertIn("vous avez utilisé", gettext(m1))
            self.assertIn("votre inscription a été retiré", gettext(m2))

    # -- round 4 (Codex 4080357451 / 463 / 469 / 478) ----------------------

    def _age(self, tx, days):
        """Backdate a payment's paid_at and created_at (no signals)."""
        when = timezone.now() - timedelta(days=days)
        PaymentTransaction.objects.filter(pk=tx.pk).update(
            created_at=when, paid_at=when
        )

    def _new_payment(self, ref, checkout_id, **extra):
        defaults = dict(
            provider=PaymentTransaction.Provider.SUMUP,
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
        )
        defaults.update(extra)
        return PaymentTransaction.objects.create(
            transaction_reference=ref, sumup_checkout_id=checkout_id, **defaults
        )

    def _run_by_id(self, payloads, history_by_code=None, history_calls=None):
        def get_history(**kwargs):
            if history_calls is not None:
                history_calls.append(kwargs)
            return {
                "items": (history_by_code or {}).get(
                    kwargs.get("transaction_code"), []
                )
            }

        with (
            override_settings(SUMUP_RECONCILIATION_ENABLED=True),
            patch(GET_CHECKOUT, side_effect=lambda c: payloads[c]) as get_checkout,
            patch(GET_HISTORY, side_effect=get_history),
        ):
            resp = self._post()
        return resp.json(), [c.args[0] for c in get_checkout.call_args_list]

    # -- two PAID payments on one registration/membership (Codex round 7) --

    def _snapshot(self, *objs):
        """Everything the sweep could mutate, as plain values."""
        out = []
        for obj in objs:
            obj.refresh_from_db()
            if isinstance(obj, PaymentTransaction):
                out.append((obj.pk, obj.status, obj.raw_response, obj.failure_reason))
            elif isinstance(obj, EventRegistration):
                out.append(
                    (obj.pk, obj.status, obj.payment_confirmed, obj.payment_date)
                )
            elif isinstance(obj, CrushCredit):
                out.append((obj.pk, obj.status, obj.note))
            elif isinstance(obj, PremiumMembership):
                out.append((obj.pk, obj.status, obj.payment_confirmed))
        return out

    def _assert_flagged_for_review(self, body, logs, refunded, others, funded):
        self.assertEqual(
            (body["needs_review"], body["errors"], body["reconciled"]), (1, 1, 0)
        )
        warning = next(m for m in logs.output if "needs manual review" in m)
        self.assertIn(f"payment {refunded.pk}", warning)
        self.assertIn(refunded.sumup_checkout_id, warning)
        self.assertIn(funded, warning)
        for other in others:
            self.assertIn(str(other.pk), warning)
        self.assertNotIn(refunded.transaction_reference, warning)
        self.assertNotIn(self.user.email, warning)

    def _review_run(self, payloads):
        with (
            self.assertLogs(CMD, level=logging.WARNING) as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            body, _ = self._run_by_id(payloads)
        return body, logs

    def test_old_payment_refunded_with_a_newer_paid_one_is_flagged(self):
        self._age(self.payment, 20)
        new = self._new_payment(
            "CRUSH-EVT-T2-repaid",
            "chk_t2_new",
            event_registration=self.registration,
            event=self.event,
        )
        credit = CrushCredit.objects.create(
            user=self.user,
            amount_cents=1550,
            currency="EUR",
            reason=CrushCredit.Reason.MEMBER_CANCELLATION,
            status=CrushCredit.Status.ACTIVE,
            source_payment=self.payment,
            source_registration=self.registration,
        )
        before = self._snapshot(self.payment, new, self.registration, credit)
        body, logs = self._review_run(
            {"chk_t2_1": FULL_REFUND, "chk_t2_new": STILL_PAID}
        )
        self._assert_flagged_for_review(
            body, logs, self.payment, [new], f"registration {self.registration.pk}"
        )
        self.assertEqual(
            self._snapshot(self.payment, new, self.registration, credit), before
        )
        self.assertEqual(mail.outbox, [])

    def test_new_payment_refunded_with_an_older_compensated_one_is_flagged(self):
        self._age(self.payment, 20)
        old_credit = CrushCredit.objects.create(
            user=self.user,
            amount_cents=1550,
            currency="EUR",
            reason=CrushCredit.Reason.MEMBER_CANCELLATION,
            status=CrushCredit.Status.ACTIVE,
            source_payment=self.payment,
            source_registration=self.registration,
        )
        new = self._new_payment(
            "CRUSH-EVT-T2-repaid",
            "chk_t2_new",
            event_registration=self.registration,
            event=self.event,
        )
        before = self._snapshot(self.payment, new, self.registration, old_credit)
        body, logs = self._review_run(
            {"chk_t2_1": STILL_PAID, "chk_t2_new": FULL_REFUND}
        )
        self._assert_flagged_for_review(
            body, logs, new, [self.payment], f"registration {self.registration.pk}"
        )
        self.assertEqual(
            self._snapshot(self.payment, new, self.registration, old_credit), before
        )
        self.assertEqual(mail.outbox, [])

    def test_a_credit_funded_repayment_is_flagged_not_kept(self):
        """The card payment is refunded; the newer CREDIT payment was funded by
        credit issued FROM that card payment (now consumed). Keeping the seat
        would leave the member with both the seat and the cash."""
        self._age(self.payment, 20)
        credit = CrushCredit.objects.create(
            user=self.user,
            amount_cents=1550,
            currency="EUR",
            reason=CrushCredit.Reason.MEMBER_CANCELLATION,
            status=CrushCredit.Status.CONSUMED,
            source_payment=self.payment,
            source_registration=self.registration,
        )
        credit_payment = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-T2-credit",
            provider=PaymentTransaction.Provider.CREDIT,
            amount=Decimal("15.50"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
            event=self.event,
        )
        CreditRedemption.objects.create(
            credit=credit, amount_cents=1550, event_registration=self.registration
        )
        before = self._snapshot(self.payment, credit_payment, self.registration, credit)
        body, logs = self._review_run({"chk_t2_1": FULL_REFUND})
        self._assert_flagged_for_review(
            body,
            logs,
            self.payment,
            [credit_payment],
            f"registration {self.registration.pk}",
        )
        self.assertEqual(
            self._snapshot(self.payment, credit_payment, self.registration, credit),
            before,
        )
        self.assertEqual(mail.outbox, [])

    def test_premium_with_two_paid_payments_is_flagged(self):
        coach_user = User.objects.create_user(
            username="t2_coach", email="t2_coach@crush.lu", password="x" * 12
        )
        coach = CrushCoach.objects.create(
            user=coach_user, is_active=True, accepting_premium=True
        )
        pm = PremiumMembership.objects.create(
            user=self.user, coach=coach, status="active"
        )
        PaymentTransaction.objects.filter(pk=self.payment.pk).update(
            status=PaymentTransaction.Status.FAILED
        )
        old = self._new_payment(
            "CRUSH-PREM-T2-old",
            "chk_prem_old",
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            premium_membership=pm,
        )
        self._age(old, 20)
        new = self._new_payment(
            "CRUSH-PREM-T2-new",
            "chk_prem_new",
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            premium_membership=pm,
        )
        before = self._snapshot(old, new, pm)
        body, logs = self._review_run(
            {
                "chk_prem_old": {"id": "chk_prem_old", "status": "REFUNDED"},
                "chk_prem_new": {"id": "chk_prem_new", "status": "PAID"},
            }
        )
        self._assert_flagged_for_review(body, logs, old, [new], f"membership {pm.pk}")
        self.assertEqual(self._snapshot(old, new, pm), before)

    def test_a_flagged_row_spends_no_write_and_the_sweep_goes_on(self):
        """Nothing was written, so a later refund is still reconciled in the
        same run; the flagged row counts as read for the cursor."""
        from crush_lu import api_admin_sumup

        self._age(self.payment, 20)
        self._new_payment(
            "CRUSH-EVT-T2-repaid",
            "chk_t2_new",
            event_registration=self.registration,
            event=self.event,
        )
        later = self._second_refundable_payment()
        body, order = self._run_by_id(
            {
                "chk_t2_1": FULL_REFUND,
                "chk_t2_new": STILL_PAID,
                "chk_t2_2": {"id": "chk_t2_2", "status": "REFUNDED"},
            }
        )
        self.assertEqual(order[0], "chk_t2_1")
        self.assertEqual((body["needs_review"], body["reconciled"]), (1, 1))
        later.refresh_from_db()
        self.assertEqual(later.status, PaymentTransaction.Status.REFUNDED)

        # And it counts as read: a one-row run moves the cursor past it.
        cache.clear()
        PaymentTransaction.objects.filter(pk=later.pk).update(
            status=PaymentTransaction.Status.PAID
        )
        import itertools

        # Clock: start, the row's read check and its write check are in
        # budget; the next row's read check is past it.
        clock = patch(
            f"{CMD}.time",
            **{
                "monotonic.side_effect": itertools.chain(
                    [0, 0, 0], itertools.repeat(10**6)
                )
            },
        )
        with clock:
            _, order = self._run_by_id(
                {
                    "chk_t2_1": FULL_REFUND,
                    "chk_t2_new": STILL_PAID,
                    "chk_t2_2": STILL_PAID,
                }
            )
        self.assertEqual(order, ["chk_t2_1"])
        cursor = cache.get(api_admin_sumup.CURSOR_CACHE_KEY)
        self.assertEqual(cursor["pk"], self.payment.pk)

    def test_cli_prints_the_review_as_an_error(self):
        from django.core.management import call_command

        self._new_payment(
            "CRUSH-EVT-T2-repaid",
            "chk_t2_new",
            event_registration=self.registration,
            event=self.event,
        )
        out = io.StringIO()
        with (
            patch(
                GET_CHECKOUT,
                side_effect=lambda c: FULL_REFUND if c == "chk_t2_1" else STILL_PAID,
            ),
            patch(GET_HISTORY, return_value={"items": []}),
        ):
            call_command("reconcile_sumup_payments", stdout=out, no_color=True)
        text = out.getvalue()
        self.assertIn("needs manual review", text)
        self.assertIn("1 error(s)", text)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)

    # -- a failed write must not block every run (Codex 4080525269) -------

    def _two_refunds_oldest_first(self):
        self._age(self.payment, 20)
        later = self._second_refundable_payment()
        return later, {
            "chk_t2_1": FULL_REFUND,
            "chk_t2_2": {"id": "chk_t2_2", "status": "REFUNDED"},
        }

    def test_a_rolled_back_write_does_not_spend_the_allowance(self):
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        later, payloads = self._two_refunds_oldest_first()
        original = Command._reconcile_refunded

        def fail_before_commit(cmd, tx_obj, remote_data, **kwargs):
            if tx_obj.pk == self.payment.pk:
                raise RuntimeError("credit could not be voided")
            return original(cmd, tx_obj, remote_data, **kwargs)

        with patch.object(Command, "_reconcile_refunded", fail_before_commit):
            body, order = self._run_by_id(payloads)
        self.assertEqual(order, ["chk_t2_1", "chk_t2_2"])
        self.assertEqual(
            (body["errors"], body["reconciled"], body["unchecked"]), (1, 1, 0)
        )
        self.payment.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(later.status, PaymentTransaction.Status.REFUNDED)

    def test_a_committed_write_that_raises_afterwards_still_stops(self):
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        later, payloads = self._two_refunds_oldest_first()
        original = Command._reconcile_refunded

        def fail_after_commit(cmd, tx_obj, remote_data, **kwargs):
            original(cmd, tx_obj, remote_data, **kwargs)
            raise RuntimeError("on_commit callback blew up")

        with patch.object(Command, "_reconcile_refunded", fail_after_commit):
            body, order = self._run_by_id(payloads)
        self.assertEqual(order, ["chk_t2_1"])
        self.assertEqual((body["errors"], body["unchecked"]), (1, 1))
        self.payment.refresh_from_db()
        later.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(later.status, PaymentTransaction.Status.PAID)

    # -- resume cursor (Codex 4080651811) ----------------------------------

    def _three_paid_rows(self):
        """Rows 1/2/3, oldest first, that all stay PAID."""
        self._age(self.payment, 25)
        second = self._new_payment("CRUSH-T2-row2", "chk_row2")
        self._age(second, 15)
        third = self._new_payment("CRUSH-T2-row3", "chk_row3")
        self._age(third, 5)
        return {c: STILL_PAID for c in ("chk_t2_1", "chk_row2", "chk_row3")}

    def _one_row_run(self, payloads):
        """A run whose budget allows exactly one row: the clock reads 0 at
        the start and for the first row, then is past any deadline."""
        import itertools

        clock = patch(
            f"{CMD}.time",
            **{
                "monotonic.side_effect": itertools.chain(
                    [0, 0], itertools.repeat(10**6)
                )
            },
        )
        with clock:
            return self._run_by_id(payloads)

    def test_bounded_runs_walk_the_window_and_wrap(self):
        payloads = self._three_paid_rows()
        seen = []
        flags = []
        for _ in range(4):
            body, order = self._one_row_run(payloads)
            seen.append(order)
            flags.append((body["cursor_resumed"], body["wrapped"]))
        self.assertEqual(seen, [["chk_t2_1"], ["chk_row2"], ["chk_row3"], ["chk_t2_1"]])
        self.assertEqual(
            flags, [(False, False), (True, False), (True, True), (False, False)]
        )

    def test_a_cursor_older_than_the_window_is_ignored(self):
        from crush_lu import api_admin_sumup

        payloads = self._three_paid_rows()
        cache.set(
            api_admin_sumup.CURSOR_CACHE_KEY,
            {"t": (timezone.now() - timedelta(days=40)).isoformat(), "pk": 1},
        )
        body, order = self._one_row_run(payloads)
        self.assertEqual(order, ["chk_t2_1"])
        self.assertIs(body["cursor_resumed"], False)

    def test_an_unreadable_cursor_is_ignored(self):
        from crush_lu import api_admin_sumup

        payloads = self._three_paid_rows()
        cache.set(api_admin_sumup.CURSOR_CACHE_KEY, {"t": "not a date", "pk": "x"})
        with self.assertLogs("crush_lu.api_admin_sumup", level=logging.WARNING):
            _, order = self._one_row_run(payloads)
        self.assertEqual(order, ["chk_t2_1"])

    def test_a_cache_failure_falls_back_to_oldest_first(self):
        payloads = self._three_paid_rows()
        broken = patch("crush_lu.api_admin_sumup.cache")
        with broken as fake_cache:
            fake_cache.get.side_effect = ConnectionError("redis down")
            fake_cache.set.side_effect = ConnectionError("redis down")
            fake_cache.delete.side_effect = ConnectionError("redis down")
            with self.assertLogs("crush_lu.api_admin_sumup", level=logging.WARNING):
                body, order = self._one_row_run(payloads)
        self.assertEqual(order, ["chk_t2_1"])
        self.assertEqual((body["errors"], body["cursor_resumed"]), (0, False))

    def test_a_deferred_refund_does_not_move_the_cursor_past_it(self):
        """A refund left for lack of time is still owed: next run reads it."""
        from crush_lu import api_admin_sumup

        payloads = self._three_paid_rows()
        payloads["chk_t2_1"] = FULL_REFUND
        with patch.object(api_admin_sumup, "WRITE_RESERVE_SECONDS", 10_000):
            body, order = self._run_by_id(payloads)
        self.assertEqual(order, ["chk_t2_1"])
        self.assertEqual(body["unchecked"], 3)
        self.assertIsNone(cache.get(api_admin_sumup.CURSOR_CACHE_KEY))
        _, order2 = self._run_by_id(payloads)
        self.assertEqual(order2[0], "chk_t2_1")

    def test_the_cli_ignores_the_cursor(self):
        from django.core.management import call_command

        from crush_lu import api_admin_sumup

        payloads = self._three_paid_rows()
        cache.set(
            api_admin_sumup.CURSOR_CACHE_KEY,
            {"t": timezone.now().isoformat(), "pk": 10**9},
        )
        with (
            patch(GET_CHECKOUT, side_effect=lambda c: payloads[c]) as get_checkout,
            patch(GET_HISTORY, return_value={"items": []}),
        ):
            call_command("reconcile_sumup_payments", quiet=True)
        self.assertEqual(get_checkout.call_count, 3)

    def test_single_payment_refund_is_unchanged(self):
        body, _ = self._run_by_id({"chk_t2_1": FULL_REFUND})
        self.assertEqual((body["reconciled"], body["needs_review"]), (1, 0))
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "cancelled")

    def test_endpoint_reads_oldest_first(self):
        self._age(self.payment, 2)
        older = self._new_payment("CRUSH-T2-older", "chk_older")
        self._age(older, 25)
        middle = self._new_payment("CRUSH-T2-middle", "chk_middle")
        self._age(middle, 10)
        body, order = self._run_by_id(
            {c: STILL_PAID for c in ("chk_t2_1", "chk_older", "chk_middle")}
        )
        self.assertEqual(order, ["chk_older", "chk_middle", "chk_t2_1"])
        self.assertEqual((body["in_window"], body["checked"]), (3, 3))

    def test_two_bounded_runs_cover_different_rows(self):
        """With the one-write limit, run 2 moves past the row run 1 wrote."""
        self._age(self.payment, 2)
        older = self._new_payment("CRUSH-T2-older", "chk_older")
        self._age(older, 25)
        payloads = {
            "chk_older": {"id": "chk_older", "status": "REFUNDED"},
            "chk_t2_1": FULL_REFUND,
        }
        first, order1 = self._run_by_id(payloads)
        second, order2 = self._run_by_id(payloads)
        self.assertEqual(order1, ["chk_older"])
        self.assertEqual(order2, ["chk_t2_1"])
        self.assertEqual((first["unchecked"], second["unchecked"]), (1, 0))
        older.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(older.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)

    def test_window_is_on_paid_at_not_checkout_creation(self):
        """Checkout opened 40 days ago, paid 5 days ago: inside a 30-day window."""
        PaymentTransaction.objects.filter(pk=self.payment.pk).update(
            created_at=timezone.now() - timedelta(days=40),
            paid_at=timezone.now() - timedelta(days=5),
        )
        body, _ = self._run_by_id({"chk_t2_1": STILL_PAID})
        self.assertEqual(body["checked"], 1)

    def test_legacy_row_without_paid_at_falls_back_to_created_at(self):
        recent = self._new_payment("CRUSH-T2-legacy-recent", "chk_legacy_recent")
        old = self._new_payment("CRUSH-T2-legacy-old", "chk_legacy_old")
        PaymentTransaction.objects.filter(pk=recent.pk).update(
            paid_at=None, created_at=timezone.now() - timedelta(days=10)
        )
        PaymentTransaction.objects.filter(pk=old.pk).update(
            paid_at=None, created_at=timezone.now() - timedelta(days=40)
        )
        body, order = self._run_by_id(
            {c: STILL_PAID for c in ("chk_t2_1", "chk_legacy_recent", "chk_legacy_old")}
        )
        self.assertIn("chk_legacy_recent", order)
        self.assertNotIn("chk_legacy_old", order)

    def test_history_evidence_is_kept_with_the_checkout(self):
        checkout = {
            "id": "chk_t2_1",
            "status": "PAID",
            "transaction_code": "TX_EVID",
            "transactions": [
                {"status": "SUCCESSFUL", "transaction_code": "TX_EVID", "amount": 15.5}
            ],
        }
        history_rows = [
            {"transaction_code": "TX_EVID", "type": "REFUND", "amount": 7.5},
            {"transaction_code": "TX_EVID", "type": "REFUND", "amount": 8.0},
        ]
        body, _ = self._run_by_id(
            {"chk_t2_1": checkout}, history_by_code={"TX_EVID": history_rows}
        )
        self.assertEqual(body["reconciled"], 1)
        self.payment.refresh_from_db()
        stored = self.payment.raw_response
        self.assertEqual(stored["status"], "PAID")
        self.assertEqual(stored["transactions"], checkout["transactions"])
        evidence = stored["reconciliation_history_evidence"]
        self.assertEqual(evidence, history_rows)

    def test_retried_checkout_queries_every_nested_transaction_code(self):
        """A declined first attempt must not hide the later successful code."""
        checkout = {
            "id": "chk_t2_1",
            "status": "PAID",
            "amount": 15.5,
            "transaction_code": "TX_DECLINED",
            "transactions": [
                {"status": "FAILED", "transaction_code": "TX_DECLINED"},
                {
                    "status": "SUCCESSFUL",
                    "transaction_code": "TX_CAPTURED",
                    "amount": 15.5,
                },
            ],
        }
        captured_refund = {
            "transaction_code": "TX_CAPTURED",
            "type": "PAYMENT",
            "status": "SUCCESSFUL",
            "amount": 15.5,
            "refunded_amount": 15.5,
        }
        history_calls = []
        body, _ = self._run_by_id(
            {"chk_t2_1": checkout},
            history_by_code={"TX_CAPTURED": [captured_refund]},
            history_calls=history_calls,
        )
        queried = {
            call.get("transaction_code")
            for call in history_calls
            if call.get("transaction_code")
        }
        self.assertEqual(queried, {"TX_DECLINED", "TX_CAPTURED"})
        self.assertEqual(body["reconciled"], 1)

    # -- refund on a still-PENDING registration: flag, don't cancel ---------
    # Owner decision (Codex thread "Release pending seats after reconciling
    # their refunds"): the money side is reconciled, the seat is left held for
    # a human, nobody is emailed or promoted, and the run reports needs_review.

    def _waitlisted_member(self):
        waiter = User.objects.create_user(
            username="t2_wait", email="t2_wait@test.crush.lu", password="x" * 12
        )
        CrushProfile.objects.create(
            user=waiter,
            date_of_birth=date(1994, 1, 1),
            gender="F",
            location="Luxembourg",
            verification_status="verified",
            completion_status="step4",
        )
        return EventRegistration.objects.create(
            event=self.event, user=waiter, status="waitlist"
        )

    def _stale_price_pending_seat(self):
        """What _apply_paid_checkout leaves after a stale-price capture.

        The fee moved to 20.00 while the member's widget was open on 15.50:
        the payment is PAID, the registration stays pending and unpaid
        ("refund or top-up required"). Written with .update(): no signals.
        """
        MeetupEvent.objects.filter(pk=self.event.pk).update(
            registration_fee=Decimal("20.00")
        )
        EventRegistration.objects.filter(pk=self.registration.pk).update(
            status="pending", payment_confirmed=False, payment_date=None
        )

    def test_refunded_stale_price_pending_registration_is_flagged_and_keeps_seat(
        self,
    ):
        self._stale_price_pending_seat()
        waiter = self._waitlisted_member()

        with (
            self.captureOnCommitCallbacks(execute=True),
            self.assertLogs(CMD, level=logging.WARNING) as logs,
        ):
            body = self._run(FULL_REFUND).json()

        self.assertEqual(
            (body["reconciled"], body["needs_review"], body["errors"]), (1, 1, 1)
        )
        self.payment.refresh_from_db()
        self.registration.refresh_from_db()
        waiter.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(self.registration.status, "pending")
        self.assertFalse(self.registration.payment_confirmed)
        self.assertIsNone(self.registration.payment_date)
        self.assertEqual(waiter.status, "waitlist")
        # Nobody is emailed: not the member, not a promoted waitlister.
        self.assertEqual(mail.outbox, [])
        warning = next(m for m in logs.output if "still pending" in m)
        self.assertIn(f"payment {self.payment.pk}", warning)
        self.assertIn(f"registration {self.registration.pk}", warning)
        self.assertNotIn(self.payment.transaction_reference, warning)
        self.assertNotIn(self.user.email, warning)

        # Flagged once: the row is REFUNDED now, so no later run selects it.
        with self.captureOnCommitCallbacks(execute=True):
            again = self._run(FULL_REFUND).json()
        self.assertEqual((again["checked"], again["needs_review"]), (0, 0))
        self.assertEqual(mail.outbox, [])

    def test_reused_pending_row_keeps_its_new_cycle_seat_and_is_flagged(self):
        """The member paid, cancelled for credit, and re-registered (pending,
        no checkout yet). Tom then cash-refunds the OLD capture: the credit it
        funded is voided, the new-cycle seat is left alone and flagged."""
        EventRegistration.objects.filter(pk=self.registration.pk).update(
            status="pending", payment_confirmed=False, payment_date=None
        )
        self._age(self.payment, 7)
        credit = CrushCredit.objects.create(
            user=self.user,
            amount_cents=1550,
            currency="EUR",
            reason=CrushCredit.Reason.MEMBER_CANCELLATION,
            status=CrushCredit.Status.ACTIVE,
            source_payment=self.payment,
            source_registration=self.registration,
        )
        waiter = self._waitlisted_member()

        with (
            self.captureOnCommitCallbacks(execute=True),
            self.assertLogs(CMD, level=logging.WARNING) as logs,
        ):
            body = self._run(FULL_REFUND).json()

        self.assertEqual((body["reconciled"], body["needs_review"]), (1, 1))
        self.payment.refresh_from_db()
        self.registration.refresh_from_db()
        credit.refresh_from_db()
        waiter.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(credit.status, CrushCredit.Status.VOID)
        self.assertEqual(self.registration.status, "pending")
        self.assertEqual(waiter.status, "waitlist")
        self.assertEqual(mail.outbox, [])
        warning = next(m for m in logs.output if "still pending" in m)
        self.assertIn("1550 cents of unspent Crush Credit", warning)

    def test_held_pending_seat_spends_the_write_allowance(self):
        """It is a write: the endpoint's one-write limit stops the run there."""
        self._stale_price_pending_seat()
        self._age(self.payment, 20)
        later = self._second_refundable_payment()
        body, order = self._run_by_id(
            {
                "chk_t2_1": FULL_REFUND,
                "chk_t2_2": {"id": "chk_t2_2", "status": "REFUNDED"},
            }
        )
        self.assertEqual(order, ["chk_t2_1"])
        self.assertEqual(
            (body["reconciled"], body["needs_review"], body["unchecked"]), (1, 1, 1)
        )
        later.refresh_from_db()
        self.assertEqual(later.status, PaymentTransaction.Status.PAID)

    def test_held_pending_seat_in_a_dry_run_is_reported_not_written(self):
        from django.core.management import call_command

        self._stale_price_pending_seat()
        out = io.StringIO()
        with (
            patch(GET_CHECKOUT, return_value=FULL_REFUND),
            patch(GET_HISTORY, return_value={"items": []}),
            self.captureOnCommitCallbacks(execute=True),
            self.assertLogs(CMD, level=logging.WARNING) as logs,
        ):
            call_command(
                "reconcile_sumup_payments", dry_run=True, stdout=out, no_color=True
            )
        self.assertIn("still pending", out.getvalue())
        self.assertIn("1 error(s)", out.getvalue())
        self.assertTrue(any("would be reconciled" in m for m in logs.output))
        self.payment.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.registration.status, "pending")
        self.assertEqual(mail.outbox, [])

    def test_confirmed_seat_is_still_released_by_its_refund(self):
        """Unchanged: only a pending seat is held back for review."""
        waiter = self._waitlisted_member()
        with self.captureOnCommitCallbacks(execute=True):
            body = self._run(FULL_REFUND).json()
        self.assertEqual(
            (body["reconciled"], body["needs_review"], body["errors"]), (1, 0, 0)
        )
        self.registration.refresh_from_db()
        waiter.refresh_from_db()
        self.assertEqual(self.registration.status, "cancelled")
        self.assertEqual(waiter.status, "pending")  # promoted into a paid seat
        self.assertEqual(len(self._mails_to(self.user.email)), 1)

    def test_pending_sibling_capture_is_flagged_without_releasing_the_seat(self):
        pending = self._new_payment(
            "CRUSH-EVT-T2-pending-sibling",
            "chk_t2_pending_sibling",
            event_registration=self.registration,
            event=self.event,
            status=PaymentTransaction.Status.PENDING,
        )
        with self.assertLogs(CMD, level=logging.WARNING) as logs:
            body, _ = self._run_by_id({"chk_t2_1": FULL_REFUND})

        self.registration.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual((body["needs_review"], body["reconciled"]), (1, 0))
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.registration.status, "confirmed")
        self.assertTrue(
            any(f"PENDING payment {pending.pk}" in line for line in logs.output)
        )

    def test_payment_published_while_waiting_for_event_lock_is_flagged(self):
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        original_related_rows = Command._related_payment_rows
        created = []

        def add_payment_after_initial_snapshot(tx, *, lock=False):
            rows = original_related_rows(tx, lock=lock)
            if lock and not created:
                created.append(
                    self._new_payment(
                        "CRUSH-EVT-T2-racing-sibling",
                        "chk_t2_racing_sibling",
                        event_registration=self.registration,
                        event=self.event,
                        status=PaymentTransaction.Status.PENDING,
                    )
                )
            return rows

        with (
            patch.object(
                Command,
                "_related_payment_rows",
                side_effect=add_payment_after_initial_snapshot,
            ),
            self.assertLogs(CMD, level=logging.WARNING) as logs,
        ):
            body, _ = self._run_by_id({"chk_t2_1": FULL_REFUND})

        self.registration.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual((body["needs_review"], body["reconciled"]), (1, 0))
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.registration.status, "confirmed")
        self.assertTrue(
            any(f"PENDING payment {created[0].pk}" in line for line in logs.output)
        )

    def test_active_checkout_claim_is_flagged_before_refund_reconciliation(self):
        EventCheckoutCreationClaim.objects.create(
            registration=self.registration,
            registration_id_snapshot=self.registration.pk,
            event_id_snapshot=self.event.pk,
            transaction_reference="CRUSH-EVT-T2-active-claim",
            payment_method="card",
        )
        with self.assertLogs(CMD, level=logging.WARNING) as logs:
            body, _ = self._run_by_id({"chk_t2_1": FULL_REFUND})

        self.registration.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual((body["needs_review"], body["reconciled"]), (1, 0))
        self.assertEqual(self.payment.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.registration.status, "confirmed")
        self.assertTrue(
            any("active checkout-creation claim" in line for line in logs.output)
        )

    # -- lock scheme, asserted structurally (SQLite ignores FOR UPDATE) ------

    def _record_locks(self):
        """Patch QuerySet.select_for_update to record each locked model."""
        from django.db.models.query import QuerySet

        locked = []
        original = QuerySet.select_for_update

        def recording(qs, *args, **kwargs):
            locked.append(qs.model.__name__)
            return original(qs, *args, **kwargs)

        return locked, patch.object(QuerySet, "select_for_update", recording)

    def _payment_lock_sql(self, tx):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from crush_lu.management.commands.reconcile_sumup_payments import Command

        locked, recording = self._record_locks()
        with recording, CaptureQueriesContext(connection) as ctx:
            rows = Command._related_payment_rows(tx, lock=True)
        self.assertEqual(locked, ["PaymentTransaction"])
        (query,) = ctx.captured_queries
        return rows, query["sql"]

    def _assert_lock_query_shape(self, sql, fk_column):
        # The SELECT list names every column, status included, so only the
        # WHERE clause is inspected. A status predicate there would let
        # PostgreSQL skip a sibling whose COMMITTED status does not match —
        # exactly the row a capture is flipping to PAID right now.
        where = sql[sql.index(" WHERE ") : sql.index(" ORDER BY ")]
        self.assertIn(fk_column, where)
        self.assertNotIn("status", where)
        self.assertRegex(sql[sql.index(" ORDER BY ") :], r'\."id" ASC$')

    def test_related_payment_lock_has_no_status_predicate_and_is_pk_ordered(self):
        siblings = [
            self._new_payment(
                f"CRUSH-EVT-T2-{status}",
                f"chk_t2_{status}",
                event_registration=self.registration,
                event=self.event,
                status=status,
            )
            for status in (
                PaymentTransaction.Status.PENDING,
                PaymentTransaction.Status.FAILED,
            )
        ]
        self._second_refundable_payment()  # another registration: not locked
        rows, sql = self._payment_lock_sql(self.payment)
        self.assertEqual(
            [row.pk for row in rows],
            sorted([self.payment.pk, *(s.pk for s in siblings)]),
        )
        self._assert_lock_query_shape(sql, "event_registration_id")

    def _active_membership(self):
        coach_user = User.objects.create_user(
            username="t2_lock_coach", email="t2_lock_coach@crush.lu", password="x" * 12
        )
        coach = CrushCoach.objects.create(
            user=coach_user, is_active=True, accepting_premium=True
        )
        return PremiumMembership.objects.create(
            user=self.user, coach=coach, status="active"
        )

    def test_related_premium_payment_lock_has_no_status_predicate(self):
        pm = self._active_membership()
        refunded = self._new_payment(
            "CRUSH-PREM-T2-a",
            "chk_prem_a",
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            premium_membership=pm,
        )
        pending = self._new_payment(
            "CRUSH-PREM-T2-b",
            "chk_prem_b",
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            premium_membership=pm,
            status=PaymentTransaction.Status.PENDING,
        )
        rows, sql = self._payment_lock_sql(refunded)
        self.assertEqual([row.pk for row in rows], [refunded.pk, pending.pk])
        self._assert_lock_query_shape(sql, "premium_membership_id")

    def test_reconcile_locks_payment_rows_then_event_then_registration(self):
        """Runtime order of every select_for_update in one event refund."""
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        CrushCredit.objects.create(
            user=self.user,
            amount_cents=1550,
            currency="EUR",
            reason=CrushCredit.Reason.MEMBER_CANCELLATION,
            status=CrushCredit.Status.ACTIVE,
            source_payment=self.payment,
            source_registration=self.registration,
        )
        locked, recording = self._record_locks()
        with recording:
            outcome = Command(stdout=io.StringIO())._reconcile_refunded(
                self.payment, FULL_REFUND
            )
        self.assertEqual(outcome, "reconciled")
        self.assertEqual(
            locked[:3], ["PaymentTransaction", "MeetupEvent", "EventRegistration"]
        )
        # No payment row is locked after the event (that would invert the
        # capture's payment -> event -> registration order), and the credit
        # comes last.
        self.assertNotIn("PaymentTransaction", locked[1:])
        self.assertGreater(
            locked.index("CrushCredit"), locked.index("EventRegistration")
        )

    def test_reconcile_locks_premium_payment_rows_before_the_membership(self):
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        pm = self._active_membership()
        refunded = self._new_payment(
            "CRUSH-PREM-T2-a",
            "chk_prem_a",
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            premium_membership=pm,
        )
        locked, recording = self._record_locks()
        with recording:
            outcome = Command(stdout=io.StringIO())._reconcile_refunded(
                refunded, {"id": "chk_prem_a", "status": "REFUNDED"}
            )
        self.assertEqual(outcome, "reconciled")
        self.assertEqual(locked[:2], ["PaymentTransaction", "PremiumMembership"])
        self.assertNotIn("PaymentTransaction", locked[1:])

    def test_no_evidence_key_when_the_checkout_itself_proves_it(self):
        self._run(FULL_REFUND)
        self.payment.refresh_from_db()
        self.assertNotIn("reconciliation_history_evidence", self.payment.raw_response)
        self.assertEqual(self.payment.raw_response, FULL_REFUND)

    def test_dry_run_sends_nothing(self):
        from django.core.management import call_command

        with (
            patch(GET_CHECKOUT, return_value=FULL_REFUND),
            patch(GET_HISTORY, return_value={"items": []}),
            self.captureOnCommitCallbacks(execute=True),
        ):
            call_command("reconcile_sumup_payments", dry_run=True, quiet=True)
        self.assertEqual(mail.outbox, [])

    def test_refund_sentence_is_translated(self):
        from django.utils import translation
        from django.utils.translation import gettext

        msgid = (
            "Your payment for this event has been refunded to the payment method "
            "you used, so your registration has been cancelled and your seat "
            "released."
        )
        with translation.override("de"):
            de = gettext(msgid)
        with translation.override("fr"):
            fr = gettext(msgid)
        self.assertIn("du bezahlt hast", de)
        self.assertIn("vous avez utilisé", fr)

    def test_scheduled_run_uses_the_contract_arguments(self):
        """§6.2 30-day window, §9.8 never --include-partial, never a dry run."""
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        counters = dict.fromkeys(COUNTER_KEYS, 0)
        with (
            override_settings(SUMUP_RECONCILIATION_ENABLED=True),
            patch.object(Command, "run_sweep", return_value=counters) as sweep,
        ):
            resp = self._post()
        self.assertEqual(resp.status_code, 202)
        kwargs = sweep.call_args.kwargs
        self.assertEqual(kwargs["days"], 30)
        self.assertIs(kwargs["include_partial"], False)
        self.assertIs(kwargs["dry_run"], False)
        self.assertNotIn("batch_delay", kwargs)
        self.assertEqual(kwargs["max_writes"], 1)
        self.assertIs(kwargs["oldest_first"], True)
        self.assertIsNotNone(kwargs["budget_seconds"])

    # -- §6.4 history cap --------------------------------------------------

    def test_full_history_page_logs_the_coverage_warning(self):
        items = [
            {"transaction_code": f"TX{i}", "type": "PAYMENT", "status": "SUCCESSFUL"}
            for i in range(100)
        ]
        with self.assertLogs(CMD, level=logging.WARNING) as logs:
            self._run(STILL_PAID, history={"items": items})
        self.assertTrue(any("full page" in m for m in logs.output))


class SumUpReconciliationStructureTests(TestCase):
    """Pins that cannot be expressed as runtime tests."""

    def test_no_new_module_can_issue_a_refund(self):
        """Case 10 / §9.9: automation observes refunds, it never issues one."""
        from crush_lu import api_admin_sumup
        from crush_lu.management.commands import reconcile_sumup_payments

        for module in (api_admin_sumup, reconcile_sumup_payments):
            self.assertNotIn("refund_transaction", inspect.getsource(module))

    def test_endpoint_never_passes_include_partial_true(self):
        """§9.8, by reading the source as well as by the mocked call."""
        from crush_lu import api_admin_sumup

        src = inspect.getsource(api_admin_sumup)
        self.assertIn("include_partial=False", src)
        self.assertNotIn("include_partial=True", src)
        self.assertNotIn("--include-partial", src.replace("# ", ""))

    def test_reconcile_locks_payment_before_everything_else(self):
        """§4 lock order: payment rows -> event -> registration/membership.

        SQLite ignores select_for_update, so an inversion passes every runtime
        test and deadlocks only on production Postgres. Same idiom as
        test_sumup_payments.CheckoutLockOrderTests. CrushCredit is locked
        inside services.credits.void_credit, which is called last.
        """
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        src = inspect.getsource(Command._reconcile_refunded)
        payment_lock_src = inspect.getsource(Command._related_payment_rows)
        self.assertIn("select_for_update()", payment_lock_src)
        self.assertIn('order_by("pk")', payment_lock_src)
        payment_lock = src.index("self._related_payment_rows(tx_obj, lock=True)")
        event_lock = src.index("MeetupEvent.objects.select_for_update")
        registration_lock = src.index("EventRegistration.objects.select_for_update")
        self.assertLess(payment_lock, event_lock)
        self.assertLess(event_lock, registration_lock)
        self.assertEqual(
            list(
                dict.fromkeys(
                    re.findall(r"(\w+)\.objects\s*\.?\s*select_for_update", src)
                )
            ),
            ["MeetupEvent", "EventRegistration", "PremiumMembership"],
        )
        self.assertGreater(
            src.index("void_credit("),
            src.index("PremiumMembership.objects.select_for_update"),
            "credits must be voided after the payment and its rows are locked",
        )

    def test_route_is_language_neutral(self):
        from django.urls import resolve

        match = resolve(URL, urlconf="azureproject.urls_crush")
        self.assertEqual(match.url_name, "api_admin_sumup_reconciliation")
