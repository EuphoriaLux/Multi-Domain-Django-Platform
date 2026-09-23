"""Tests for POST /api/admin/sumup-reconciliation/ (SumUp Tier-2 poll).

Implements the test matrix in
ai-memory-hub/policies/sumup-tier2-refund-automation-contract.md §10 — the case
number from that table is in each test's docstring — plus the structural pins
it asks for (§8 no refund client, §9.8 no --include-partial, §4 lock order).

Every flag-on test mocks BOTH SumUp reads the sweep makes (the checkout and the
transaction history): unmocked, they are real HTTP calls to SumUp.
"""

import inspect
import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from crush_lu.models.credits import CrushCredit
from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushProfile
from crush_lu.services.sumup import SumUpError

User = get_user_model()

API_KEY = "test-admin-api-key"
URL = "/api/admin/sumup-reconciliation/"
CMD = "crush_lu.management.commands.reconcile_sumup_payments"
GET_CHECKOUT = f"{CMD}.SumUpClient.get_checkout"
GET_HISTORY = f"{CMD}.SumUpClient.get_transactions_history"
COUNTER_KEYS = {"checked", "reconciled", "partial", "errors", "unchecked"}

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

        self.assertIs(Command()._reconcile_refunded(self.payment, FULL_REFUND), True)
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

        def by_id(checkout_id):
            if checkout_id == "chk_t2_1":
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
        other.refresh_from_db()
        self.assertEqual(other.status, PaymentTransaction.Status.REFUNDED)

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
        self.assertEqual(set(body), {"status", "timestamp"} | COUNTER_KEYS)
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
        self.assertIn("checked=1 reconciled=0 partial=0 errors=0 unchecked=0", line)
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

        self.assertEqual(m.RECONCILIATION_BUDGET_SECONDS, 100)
        self.assertEqual(m.READ_RESERVE_SECONDS, 21)
        self.assertEqual(m.WRITE_RESERVE_SECONDS, 65)
        self.assertLess(m.RECONCILIATION_BUDGET_SECONDS, m.FUNCTION_TIMEOUT_SECONDS)
        self.assertLess(m.FUNCTION_TIMEOUT_SECONDS, 120)
        self.assertGreater(m.RECONCILIATION_BUDGET_SECONDS - m.WRITE_RESERVE_SECONDS, 0)

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
        """§4 lock order: PaymentTransaction -> EventRegistration -> PremiumMembership.

        SQLite ignores select_for_update, so an inversion passes every runtime
        test and deadlocks only on production Postgres. Same idiom as
        test_sumup_payments.CheckoutLockOrderTests. CrushCredit is locked
        inside services.credits.void_credit, which is called last.
        """
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        src = inspect.getsource(Command._reconcile_refunded)
        seq = re.findall(r"(\w+)\.objects\s*\.?\s*select_for_update", src)
        self.assertEqual(
            seq,
            ["PaymentTransaction", "EventRegistration", "PremiumMembership"],
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
