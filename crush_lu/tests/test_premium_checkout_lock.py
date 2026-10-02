"""At most one payable Premium checkout per membership (#925 D6, #1012).

Every click on the pay button used to insert another PENDING checkout, all of
them payable at SumUp, so a member who opened the page twice could be charged
twice. create_sumup_premium_checkout now reuses the newest checkout when SumUp
still reports it payable, or retires the older ones, under a payments ->
membership lock that matches the refund sweep.

SumUp is never reached: the HTTP layer and the socket raise on any call, and
every SumUpClient method the view uses is patched per test.
"""

import inspect
import re
import socket
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings

from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import (
    CrushCoach,
    CrushProfile,
    PremiumMembership,
    UserDataConsent,
)
from crush_lu.services.sumup import SumUpClient, SumUpConfigurationError

User = get_user_model()


def _no_network(*args, **kwargs):
    raise AssertionError("A test tried to reach SumUp")


def _unexpected_sumup_call(*args, **kwargs):
    raise AssertionError("Unexpected SumUpClient call")


@override_settings(PREMIUM_REDIRECTS_TO_BETA=False, SUMUP_PREMIUM_MONTHLY_FEE="10.00")
class PremiumCheckoutLockTests(TestCase):
    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "crush.lu", "name": "Crush.lu"}
        )
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="lock@crush.lu", email="lock@crush.lu", password="pw-123456"
        )
        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        CrushProfile.objects.create(
            user=self.user, verification_status="verified", completion_status="step4"
        )
        coach_user = User.objects.create_user(
            username="lockcoach@crush.lu", email="lockcoach@crush.lu", password="pw"
        )
        self.coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=10,
        )
        self.membership = PremiumMembership.objects.create(
            user=self.user, coach=self.coach, status="pending"
        )
        self.url = f"/payments/sumup/create-premium-checkout/{self.membership.id}/"
        self.client.force_login(self.user)

        # Network guard: nothing below the patched client methods may run.
        for target in (
            "crush_lu.services.sumup.requests.get",
            "crush_lu.services.sumup.requests.post",
            "crush_lu.services.sumup.requests.delete",
            "crush_lu.services.sumup.requests.put",
        ):
            p = patch(target, side_effect=_no_network)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(socket.socket, "connect", _no_network)
        p.start()
        self.addCleanup(p.stop)

        # Every client method the view can use, raising unless a test opts in.
        # ensure_checkout_not_payable stays real so it composes the patched
        # deactivate_checkout and get_checkout exactly as in production.
        self.sumup = {}
        for name in (
            "get_checkout",
            "deactivate_checkout",
            "create_checkout",
            "create_customer",
            "refund_transaction",
            "charge_recurring",
            "get_transactions_history",
        ):
            p = patch.object(SumUpClient, name, side_effect=_unexpected_sumup_call)
            self.sumup[name] = p.start()
            self.addCleanup(p.stop)
        self.sumup["create_customer"].side_effect = None
        self.sumup["create_customer"].return_value = {}
        self.remote = {}
        self.sumup["get_checkout"].side_effect = lambda checkout_id: dict(
            self.remote[checkout_id]
        )
        self.created = []

        def _create_checkout(**kwargs):
            checkout_id = f"CHK_NEW_{len(self.created) + 1}"
            self.created.append(kwargs)
            self.remote[checkout_id] = {
                "id": checkout_id,
                "status": "PENDING",
                "amount": kwargs["amount"],
                "currency": kwargs["currency"],
            }
            return {"id": checkout_id, "status": "PENDING"}

        self.sumup["create_checkout"].side_effect = _create_checkout

    def _pending_row(self, checkout_id, *, status="PENDING", amount="10.00"):
        row = PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-PREM-{checkout_id}",
            sumup_checkout_id=checkout_id,
            sumup_customer_id=f"crush-user-{self.user.id}",
            amount=Decimal(amount),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.user,
            premium_membership=self.membership,
        )
        self.remote[checkout_id] = {
            "id": checkout_id,
            "status": status,
            "amount": float(amount),
            "currency": "EUR",
        }
        return row

    def _statuses(self):
        return dict(
            PaymentTransaction.objects.filter(
                premium_membership=self.membership
            ).values_list("sumup_checkout_id", "status")
        )

    def test_two_clicks_leave_one_payable_checkout(self):
        """The second click hands out the first checkout instead of a new one."""
        first = self.client.post(self.url)
        second = self.client.post(self.url)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["checkout_id"], first.json()["checkout_id"])
        self.assertEqual(
            second.json()["checkout_reference"], first.json()["checkout_reference"]
        )
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self._statuses(), {"CHK_NEW_1": "pending"})
        self.sumup["deactivate_checkout"].assert_not_called()

    def test_unpayable_newest_and_older_checkouts_are_retired(self):
        """A stale-price newest checkout and every older one are closed first."""
        older = self._pending_row("CHK_OLD")
        newest = self._pending_row("CHK_STALE_PRICE", amount="15.00")
        self.sumup["deactivate_checkout"].side_effect = None
        self.sumup["deactivate_checkout"].return_value = True

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checkout_id"], "CHK_NEW_1")
        self.assertEqual(
            self._statuses(),
            {
                "CHK_OLD": "cancelled",
                "CHK_STALE_PRICE": "cancelled",
                "CHK_NEW_1": "pending",
            },
        )
        deactivated = {
            c.args[0] for c in self.sumup["deactivate_checkout"].call_args_list
        }
        self.assertEqual(deactivated, {"CHK_OLD", "CHK_STALE_PRICE"})
        for row in (older, newest):
            row.refresh_from_db()
            self.assertIn("Retired by a newer premium checkout", row.failure_reason)

    def test_reuse_retires_the_older_checkouts(self):
        self._pending_row("CHK_OLD")
        self._pending_row("CHK_NEWEST")
        self.sumup["deactivate_checkout"].side_effect = None
        self.sumup["deactivate_checkout"].return_value = True

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checkout_id"], "CHK_NEWEST")
        self.assertEqual(self.created, [])
        self.assertEqual(
            self._statuses(), {"CHK_OLD": "cancelled", "CHK_NEWEST": "pending"}
        )
        self.sumup["deactivate_checkout"].assert_called_once_with("CHK_OLD")

    def test_older_checkout_sumup_reports_paid_is_not_cancelled(self):
        """A capture SumUp already took is left for completion/reconciliation."""
        self._pending_row("CHK_OLD_PAID", status="PAID")
        self._pending_row("CHK_NEWEST", status="EXPIRED")
        # DELETE on a paid checkout is refused, so the helper asks SumUp.
        self.sumup["deactivate_checkout"].side_effect = lambda checkout_id: (
            checkout_id != "CHK_OLD_PAID"
        )

        with self.assertLogs("crush_lu.views_payments", level="WARNING"):
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 409)
        # Not "please try again": SumUp already holds this member's money.
        self.assertIn("already received a payment", response.json()["error"])
        self.assertEqual(self.created, [])
        statuses = self._statuses()
        self.assertEqual(statuses["CHK_OLD_PAID"], "pending")
        self.assertEqual(statuses["CHK_NEWEST"], "cancelled")
        paid = PaymentTransaction.objects.get(sumup_checkout_id="CHK_OLD_PAID")
        self.assertEqual(paid.failure_reason, "")

    def test_newest_checkout_sumup_reports_paid_refuses_a_new_charge(self):
        self._pending_row("CHK_PAID", status="PAID")

        with self.assertLogs("crush_lu.views_payments", level="ERROR"):
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 409)
        self.assertIn("already received a payment", response.json()["error"])
        self.assertEqual(self.created, [])
        self.assertEqual(self._statuses(), {"CHK_PAID": "pending"})
        self.sumup["deactivate_checkout"].assert_not_called()

    def test_membership_cancelled_by_sweep_refuses_the_reused_checkout(self):
        """The reuse path re-checks the membership under the lock too."""
        self._pending_row("CHK_REUSABLE")

        def _sweep_cancels_meanwhile(checkout_id):
            PremiumMembership.objects.filter(pk=self.membership.pk).update(
                status="cancelled"
            )
            return dict(self.remote[checkout_id])

        self.sumup["get_checkout"].side_effect = _sweep_cancels_meanwhile
        self.sumup["deactivate_checkout"].side_effect = None
        self.sumup["deactivate_checkout"].return_value = True

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 409)
        self.assertNotIn("checkout_id", response.json())
        self.assertEqual(self.created, [])
        # A saved widget link must not stay payable for an ended membership.
        self.sumup["deactivate_checkout"].assert_called_once_with("CHK_REUSABLE")
        self.assertEqual(self._statuses(), {"CHK_REUSABLE": "cancelled"})
        row = PaymentTransaction.objects.get(sumup_checkout_id="CHK_REUSABLE")
        self.assertIn("stopped being pending", row.failure_reason)

    def test_missing_api_key_while_retiring_returns_the_json_refusal(self):
        """A SumUpConfigurationError is a 409 JSON refusal, not an HTML 500."""
        self._pending_row("CHK_OLD")
        self._pending_row("CHK_NEWEST", status="EXPIRED")
        self.sumup["deactivate_checkout"].side_effect = SumUpConfigurationError(
            "SUMUP_API_KEY is not configured"
        )

        with self.assertLogs("crush_lu.views_payments", level="WARNING"):
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 409)
        self.assertIn("could not be closed", response.json()["error"])
        self.assertEqual(self.created, [])
        self.assertEqual(
            self._statuses(), {"CHK_OLD": "pending", "CHK_NEWEST": "pending"}
        )

    def test_retiring_is_capped_per_request(self):
        """Legacy rows are closed ten per click, not all in one request."""
        for n in range(12):
            self._pending_row(f"CHK_LEGACY_{n:02d}", status="EXPIRED")
        self.sumup["deactivate_checkout"].side_effect = None
        self.sumup["deactivate_checkout"].return_value = True

        with self.assertLogs("crush_lu.views_payments", level="WARNING"):
            first = self.client.post(self.url)

        self.assertEqual(first.status_code, 409)
        self.assertEqual(self.sumup["deactivate_checkout"].call_count, 10)
        self.assertEqual(list(self._statuses().values()).count("cancelled"), 10)
        self.assertEqual(self.created, [])

        second = self.client.post(self.url)

        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.sumup["deactivate_checkout"].call_count, 12)
        self.assertEqual(len(self.created), 1)

    def test_concurrent_click_publishing_first_keeps_one_payable_checkout(self):
        """A checkout another click published meanwhile wins; ours is closed."""

        def _other_click_publishes(**kwargs):
            self._pending_row("CHK_OTHER_CLICK")
            return {"id": "CHK_MINE", "status": "PENDING"}

        self.sumup["create_checkout"].side_effect = _other_click_publishes
        self.sumup["deactivate_checkout"].side_effect = None
        self.sumup["deactivate_checkout"].return_value = True

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._statuses(), {"CHK_OTHER_CLICK": "pending"})
        self.sumup["deactivate_checkout"].assert_called_once_with("CHK_MINE")

    def test_creation_locks_payments_before_the_membership(self):
        """Runtime spy: SQLite drops FOR UPDATE, so assert the ORM calls."""
        from django.db.models.query import QuerySet

        original = QuerySet.select_for_update
        locked = []

        def _spy(qs, *args, **kwargs):
            locked.append(qs.model.__name__)
            return original(qs, *args, **kwargs)

        with patch.object(QuerySet, "select_for_update", _spy):
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(locked, ["PaymentTransaction", "PremiumMembership"])


class PremiumCheckoutLockOrderTests(TestCase):
    """Creation and the refund sweep must lock payments -> membership."""

    @staticmethod
    def _lock_sequence(func):
        src = inspect.getsource(func)
        return re.findall(r"(\w+)\.objects\s*\.?\s*select_for_update", src)

    def test_creation_helper_locks_payments_then_membership(self):
        from crush_lu.views_payments import _lock_premium_checkout_state

        self.assertEqual(
            self._lock_sequence(_lock_premium_checkout_state),
            ["PaymentTransaction", "PremiumMembership"],
        )

    def test_view_locks_only_through_the_helper(self):
        from crush_lu.views_payments import create_sumup_premium_checkout

        src = inspect.getsource(create_sumup_premium_checkout)
        self.assertNotIn("select_for_update", src)
        self.assertIn("_lock_premium_checkout_state(", src)

    def test_sweep_uses_the_same_order(self):
        from crush_lu.management.commands.reconcile_sumup_payments import Command

        src = inspect.getsource(Command._reconcile_refunded)
        payments = src.index("_related_payment_rows(tx_obj, lock=True)")
        membership = src.index("PremiumMembership.objects.select_for_update()")
        self.assertLess(payments, membership)
