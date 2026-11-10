"""Premium's first payment through Mollie (views_mollie.open_mollie_premium_checkout).

Every HTTP call to Mollie is mocked at ``requests.request``. The checkout keeps
the SumUp Premium URL and JSON contract, so these tests post to that URL.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from crush_lu.models import CrushCoach, PremiumMembership
from crush_lu.models.crush_connect import CrushConnectWaitlist
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.premium_recovery import PremiumPaymentRecoveryCase
from crush_lu.models.profiles import CrushProfile
from crush_lu.services.event_payments import (
    mollie_premium_active,
    premium_payments_paused,
)
from crush_lu.tests.test_crush_credit import CreditFixture

User = get_user_model()

WEBHOOK = "/payments/mollie/webhook/"
RETURN = "/payments/mollie/return/"
CHECKOUT_HREF = "https://www.mollie.com/checkout/credit-card/embedded/7UhSN1zuXS"
KEY = "test_xxxxxxxxxxxxxxxxxxxxxxxx"
MOLLIE_ON = {"MOLLIE_API_KEY": KEY, "MOLLIE_PREMIUM_ENABLED": True}
REQUEST = "crush_lu.services.mollie.requests.request"
ONBOARDING_FEE = "crush_lu.views_premium.premium_onboarding_fee"
PAYMENT_ID = "tr_7UhSN1zuXS"
CUSTOMER_ID = "cst_8wmqcHMN4U"
MANDATE_ID = "mdt_h3gAaD5zP"
ONBOARDING = Decimal("10.00")
MONTHLY = Decimal("9.99")


def _response(status_code, body):
    response = mock.Mock(status_code=status_code)
    response.json.return_value = body
    return response


class FakeMollie:
    """Routes ``requests.request`` by method and path; records every call."""

    def __init__(self):
        self.calls = []
        self.created = None  # body of POST /payments; None = Mollie refuses
        self.remote = None  # body of GET /payments/<id>
        self.cancel_code = 204
        self.after_cancel = None  # GET body once a DELETE was attempted
        self.mandates = []  # GET /customers/<id>/mandates
        self.by_id = {}  # payment id -> GET body, before ``remote``

    def __call__(self, method, url, **kwargs):
        path = url.split("/v2", 1)[1]
        self.calls.append((method, path, kwargs.get("json")))
        if method == "POST" and path == "/customers":
            return _response(201, {"id": CUSTOMER_ID})
        if method == "POST" and path == "/payments":
            if self.created is None:
                return _response(422, {"detail": "The payment method is not enabled"})
            return _response(201, self.created)
        if method == "DELETE":
            if self.after_cancel is not None:
                self.remote = self.after_cancel
            return _response(self.cancel_code, {})
        if path.startswith("/customers/"):
            return _response(200, {"_embedded": {"mandates": self.mandates}})
        payment_id = path.split("/payments/", 1)[-1].split("?", 1)[0]
        return _response(200, self.by_id.get(payment_id, self.remote))

    def sent(self, method, path):
        return [body for m, p, body in self.calls if (m, p) == (method, path)]

    def count(self, method, prefix):
        return sum(1 for m, p, _b in self.calls if m == method and p.startswith(prefix))


def _payment(status, value=ONBOARDING, *, description=None, mandate=True, **extra):
    body = {
        "id": PAYMENT_ID,
        "status": status,
        "amount": {"value": f"{value:.2f}", "currency": "EUR"},
        "sequenceType": "first",
        "customerId": CUSTOMER_ID,
    }
    if description is not None:
        body["description"] = description
    if status == "open":
        body["_links"] = {"checkout": {"href": CHECKOUT_HREF}}
    if status == "paid" and mandate:
        body["mandateId"] = MANDATE_ID
    body.update(extra)
    return body


def _refunded(value=ONBOARDING):
    return {"refunds": [{"status": "refunded", "amount": {"value": f"{value:.2f}"}}]}


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    ONLINE_PAYMENTS_ENABLED=False,
    PREMIUM_REDIRECTS_TO_BETA=False,
    PREMIUM_ONBOARDING_FEE="10.00",
    SUMUP_PREMIUM_MONTHLY_FEE="9.99",
    MOLLIE_PREMIUM_METHOD="creditcard",
    **MOLLIE_ON,
)
class MolliePremiumTests(CreditFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.buyer = self._user("buyer@crush.lu")
        coach_user = User.objects.create_user(
            username="coach@crush.lu", email="coach@crush.lu", password="pw"
        )
        self.coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=10,
        )
        self.membership = PremiumMembership.objects.create(
            user=self.buyer, coach=self.coach, status="pending"
        )
        self.url = f"/payments/sumup/create-premium-checkout/{self.membership.pk}/"
        self.description = f"Crush Connect Premium - Coach {self.membership.coach}"
        self.client.force_login(self.buyer)
        self.mollie = FakeMollie()
        self.mollie.created = _payment("open", description=self.description)
        patcher = mock.patch(REQUEST, side_effect=self.mollie)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The member has attended no event, so the onboarding fee applies; the
        # event-verified tests patch the classification instead of building one.
        fee = mock.patch(ONBOARDING_FEE, return_value=ONBOARDING)
        self.fee = fee.start()
        self.addCleanup(fee.stop)

    def _post(self):
        return self.client.post(self.url, data="{}", content_type="application/json")

    def _rows(self):
        return PaymentTransaction.objects.filter(premium_membership=self.membership)

    def _pending_row(
        self, amount=ONBOARDING, payment_id=PAYMENT_ID, ref="CRUSH-PREM-M1"
    ):
        return PaymentTransaction.objects.create(
            transaction_reference=ref,
            provider=PaymentTransaction.Provider.MOLLIE,
            mollie_payment_id=payment_id,
            amount=amount,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.buyer,
            premium_membership=self.membership,
        )

    def _webhook(self, remote):
        self.mollie.remote = remote
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(WEBHOOK, {"id": PAYMENT_ID})

    # -- the switch ---------------------------------------------------------

    def test_key_alone_leaves_premium_paused(self):
        with self.settings(MOLLIE_PREMIUM_ENABLED=False):
            self.assertFalse(mollie_premium_active())
            self.assertTrue(premium_payments_paused())
            response = self._post()
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["payments_paused"])
        self.assertEqual(self.mollie.calls, [])
        self.assertFalse(self._rows().exists())

    def test_flag_alone_opens_nothing_without_a_key(self):
        with self.settings(MOLLIE_API_KEY=""):
            self.assertFalse(mollie_premium_active())
            self.assertTrue(premium_payments_paused())

    def test_flag_and_key_open_premium_only(self):
        from crush_lu.services.event_payments import online_payments_paused

        self.assertTrue(mollie_premium_active())
        self.assertFalse(premium_payments_paused())
        self.assertTrue(online_payments_paused())  # event seats have their own flag

    # -- opening the first payment -----------------------------------------

    def test_checkout_opens_a_first_payment_under_a_new_customer(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["widget_url"], CHECKOUT_HREF)
        # The customer only holds the mandate: no name, no email.
        self.assertEqual(
            self.mollie.sent("POST", "/customers"),
            [{"metadata": {"user_id": self.buyer.pk}}],
        )
        (sent,) = self.mollie.sent("POST", "/payments")
        self.assertEqual(sent["amount"], {"currency": "EUR", "value": "10.00"})
        self.assertEqual(sent["sequenceType"], "first")
        self.assertEqual(sent["customerId"], CUSTOMER_ID)
        self.assertEqual(sent["method"], "creditcard")
        self.assertEqual(sent["description"], self.description)
        row = self._rows().get()
        self.assertEqual(row.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(row.provider, PaymentTransaction.Provider.MOLLIE)
        self.assertEqual(row.mollie_payment_id, PAYMENT_ID)
        self.assertEqual(row.amount, ONBOARDING)
        self.assertEqual(row.purpose, PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.onboarding_fee, ONBOARDING)
        self.assertEqual(self.membership.mollie_customer_id, CUSTOMER_ID)
        self.assertEqual(self.membership.status, "pending")

    def test_event_verified_member_pays_the_first_month(self):
        self.fee.return_value = Decimal("0")
        self.mollie.created = _payment(
            "open", MONTHLY, description=f"{self.description} - first month"
        )

        self.assertEqual(self._post().status_code, 200)

        (sent,) = self.mollie.sent("POST", "/payments")
        self.assertEqual(sent["amount"]["value"], "9.99")
        self.assertTrue(sent["description"].endswith(" - first month"))
        self.assertEqual(self._rows().get().amount, MONTHLY)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.onboarding_fee, Decimal("0"))

    def test_empty_method_setting_lets_the_member_choose(self):
        with self.settings(MOLLIE_PREMIUM_METHOD=""):
            self._post()
        (sent,) = self.mollie.sent("POST", "/payments")
        self.assertNotIn("method", sent)

    def test_customer_of_an_earlier_membership_is_reused(self):
        PremiumMembership.objects.create(
            user=self.buyer,
            coach=self.coach,
            status="cancelled",
            mollie_customer_id="cst_earlier",
        )

        self.assertEqual(self._post().status_code, 200)

        self.assertEqual(self.mollie.sent("POST", "/customers"), [])
        (sent,) = self.mollie.sent("POST", "/payments")
        self.assertEqual(sent["customerId"], "cst_earlier")

    def test_second_click_reuses_the_open_payment(self):
        row = self._pending_row()
        self.mollie.remote = _payment("open", description=self.description)

        response = self._post()

        self.assertEqual(response.json()["widget_url"], CHECKOUT_HREF)
        self.assertEqual(
            response.json()["checkout_reference"], row.transaction_reference
        )
        self.assertEqual(self.mollie.sent("POST", "/payments"), [])
        self.assertEqual(self._rows().count(), 1)

    def test_payment_on_other_terms_is_cancelled_before_a_new_one_opens(self):
        # Opened as a first month; the member is now due the onboarding fee.
        old = self._pending_row(amount=MONTHLY)
        self.mollie.remote = _payment(
            "open", MONTHLY, description=f"{self.description} - first month"
        )

        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.mollie.count("DELETE", f"/payments/{PAYMENT_ID}"), 1)
        old.refresh_from_db()
        self.assertEqual(old.status, PaymentTransaction.Status.CANCELLED)
        self.assertEqual(len(self.mollie.sent("POST", "/payments")), 1)
        self.assertEqual(
            self._rows().filter(status=PaymentTransaction.Status.PENDING).count(), 1
        )

    def test_no_new_payment_when_the_old_one_cannot_be_cancelled(self):
        self._pending_row(amount=MONTHLY)
        self.mollie.remote = _payment("open", MONTHLY, description="other terms")
        self.mollie.cancel_code = 422

        response = self._post()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.mollie.sent("POST", "/payments"), [])

    def test_started_payment_is_left_to_finish(self):
        self._pending_row()
        self.mollie.remote = _payment("pending", description=self.description)

        response = self._post()

        data = response.json()
        self.assertTrue(data["payment_in_progress"])
        self.assertEqual(self.mollie.count("DELETE", "/payments"), 0)
        self.assertEqual(self.mollie.sent("POST", "/payments"), [])

    def test_already_paid_payment_is_applied_not_charged_again(self):
        row = self._pending_row()
        self.mollie.remote = _payment("paid")

        with self.captureOnCommitCallbacks(execute=True):
            response = self._post()

        data = response.json()
        self.assertTrue(data["already_paid"])
        self.assertIn(f"{RETURN}?ref={row.transaction_reference}", data["widget_url"])
        self.assertEqual(self.mollie.sent("POST", "/payments"), [])
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")

    def test_mollie_refusal_marks_the_row_failed(self):
        self.mollie.created = None

        response = self._post()

        self.assertEqual(response.status_code, 502)
        row = self._rows().get()
        self.assertEqual(row.status, PaymentTransaction.Status.FAILED)
        self.assertIn("not enabled", row.failure_reason)

    # -- an earlier SumUp checkout ------------------------------------------

    def _sumup_row(self):
        return PaymentTransaction.objects.create(
            transaction_reference="CRUSH-PREM-SUMUP",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-OLD",
            amount=ONBOARDING,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.buyer,
            premium_membership=self.membership,
        )

    def _sumup(self, *, deactivated=False, status=None):
        from crush_lu.services.sumup import SumUpError

        client = mock.Mock()
        client.deactivate_checkout.return_value = deactivated
        if status is None:
            client.get_checkout.side_effect = SumUpError("403")
        else:
            client.get_checkout.return_value = {"status": status, "id": "CHK-OLD"}
        return mock.patch("crush_lu.services.sumup.SumUpClient", return_value=client)

    def test_captured_sumup_checkout_is_recorded_not_charged_again(self):
        row = self._sumup_row()

        with self._sumup(status="PAID"), self.captureOnCommitCallbacks(execute=True):
            response = self._post()

        # Paused or not, a checkout SumUp reports PAID is never "closed".
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.mollie.sent("POST", "/payments"), [])
        row.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.PAID)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")

    def test_closed_sumup_checkout_gives_way_to_mollie(self):
        row = self._sumup_row()

        with self._sumup(deactivated=True):
            response = self._post()

        self.assertEqual(response.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.CANCELLED)
        self.assertEqual(len(self.mollie.sent("POST", "/payments")), 1)

    def test_unreadable_sumup_checkout_counts_as_closed_only_while_paused(self):
        row = self._sumup_row()

        with self._sumup(), self.settings(ONLINE_PAYMENTS_ENABLED=True):
            strict = self._post()
        cache.clear()
        with self._sumup():
            paused = self._post()

        self.assertEqual(strict.status_code, 409)
        self.assertEqual(paused.status_code, 200)
        row.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.CANCELLED)

    # -- refusals, the SumUp checkout's own ---------------------------------

    def test_another_members_membership_is_refused(self):
        self.client.force_login(self._user("other@crush.lu"))
        response = self._post()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.mollie.calls, [])

    def test_beta_allowlist_still_decides_who_may_buy(self):
        with self.settings(PREMIUM_REDIRECTS_TO_BETA=True):
            refused = self._post()
            CrushConnectWaitlist.objects.create(
                user=self.buyer, selected_as_tester=True
            )
            cache.clear()
            allowed = self._post()
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(allowed.status_code, 200)

    def test_membership_that_is_not_pending_is_refused(self):
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])
        self.assertEqual(self._post().status_code, 400)
        self.assertEqual(self.mollie.calls, [])

    def test_recorded_capture_blocks_a_second_charge(self):
        paid = self._pending_row(ref="CRUSH-PREM-PAID", payment_id="tr_paid")
        paid.status = PaymentTransaction.Status.PAID
        paid.save(update_fields=["status"])

        response = self._post()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.mollie.sent("POST", "/payments"), [])

    # -- the webhook ---------------------------------------------------------

    def test_paid_first_payment_starts_premium_and_keeps_the_mandate(self):
        self._post()
        before = timezone.now()

        self.assertEqual(self._webhook(_payment("paid")).status_code, 200)

        row = self._rows().get()
        self.assertEqual(row.status, PaymentTransaction.Status.PAID)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        self.assertEqual(self.membership.mollie_mandate_id, MANDATE_ID)
        self.assertEqual(self.membership.mollie_customer_id, CUSTOMER_ID)
        # Onboarding buys one month.
        self.assertGreater(
            self.membership.current_period_end, before + timedelta(days=27)
        )
        self.assertLess(self.membership.current_period_end, before + timedelta(days=32))
        profile = CrushProfile.objects.get(user=self.buyer)
        self.assertEqual(profile.assigned_coach, self.coach)
        self.assertTrue(profile.has_active_premium)
        self.assertFalse(PremiumPaymentRecoveryCase.objects.exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_webhook_replay_changes_nothing(self):
        self._post()
        self._webhook(_payment("paid"))
        self.membership.refresh_from_db()
        period_end = self.membership.current_period_end

        self._webhook(_payment("paid"))

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.current_period_end, period_end)
        self.assertEqual(len(mail.outbox), 1)

    def test_event_verified_first_month_buys_two_months(self):
        self.fee.return_value = Decimal("0")
        self.mollie.created = _payment(
            "open", MONTHLY, description=f"{self.description} - first month"
        )
        self._post()
        before = timezone.now()

        self._webhook(_payment("paid", MONTHLY))

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        self.assertGreater(
            self.membership.current_period_end, before + timedelta(days=58)
        )

    def test_payment_without_a_mandate_still_starts_premium(self):
        self._post()

        self._webhook(_payment("paid", mandate=False))

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        self.assertEqual(self.membership.mollie_mandate_id, "")

    def test_mandate_is_read_from_the_customer_when_the_payment_names_none(self):
        self._post()
        self.mollie.mandates = [
            {"id": "mdt_revoked", "status": "invalid"},
            {"id": MANDATE_ID, "status": "valid"},
        ]

        self._webhook(_payment("paid", mandate=False))

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.mollie_mandate_id, MANDATE_ID)
        self.assertEqual(
            self.mollie.count("GET", f"/customers/{CUSTOMER_ID}/mandates"), 1
        )
        # Stored: a repeat of the webhook asks Mollie for the payment only.
        self._webhook(_payment("paid", mandate=False))
        self.assertEqual(
            self.mollie.count("GET", f"/customers/{CUSTOMER_ID}/mandates"), 1
        )

    def test_amount_mismatch_is_not_applied(self):
        self._post()

        self._webhook(_payment("paid", Decimal("1.00")))

        self.assertEqual(self._rows().get().status, PaymentTransaction.Status.PENDING)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")

    def test_expired_payment_closes_the_row_and_keeps_the_request(self):
        self._post()

        self._webhook(_payment("expired"))

        self.assertEqual(self._rows().get().status, PaymentTransaction.Status.CANCELLED)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")

    def test_capture_that_cannot_be_applied_opens_a_recovery_case(self):
        self._post()
        # The request was cancelled by staff while the member was paying.
        PremiumMembership.objects.filter(pk=self.membership.pk).update(
            status="cancelled"
        )

        self.assertEqual(self._webhook(_payment("paid")).status_code, 200)

        row = self._rows().get()
        self.assertEqual(row.status, PaymentTransaction.Status.PAID)
        case = PremiumPaymentRecoveryCase.objects.get(payment=row)
        self.assertEqual(case.status, PremiumPaymentRecoveryCase.Status.OPEN)
        self.assertEqual(
            case.reason, PremiumPaymentRecoveryCase.Reason.REQUEST_CANCELLED
        )
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "cancelled")
        # Money to hand back: its mandate must never be kept for renewals.
        self.assertEqual(self.membership.mollie_mandate_id, "")
        self.assertFalse(CrushProfile.objects.get(user=self.buyer).has_active_premium)

    def test_full_coach_opens_a_case_instead_of_granting(self):
        self._post()
        CrushCoach.objects.filter(pk=self.coach.pk).update(max_premium_members=0)

        self._webhook(_payment("paid"))

        case = PremiumPaymentRecoveryCase.objects.get()
        self.assertEqual(
            case.reason, PremiumPaymentRecoveryCase.Reason.COACH_UNAVAILABLE
        )
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")
        self.assertEqual(self.membership.mollie_mandate_id, "")

    def test_unexpected_activation_error_keeps_the_charge_and_opens_a_case(self):
        self._post()

        with mock.patch.object(
            PremiumMembership, "confirm", side_effect=RuntimeError("boom")
        ):
            response = self._webhook(_payment("paid"))

        # Not a 500 and not a lost charge (#1164): PAID with a case for staff.
        self.assertEqual(response.status_code, 200)
        row = self._rows().get()
        self.assertEqual(row.status, PaymentTransaction.Status.PAID)
        case = PremiumPaymentRecoveryCase.objects.get(payment=row)
        self.assertEqual(case.reason, PremiumPaymentRecoveryCase.Reason.OTHER)
        self.assertIn("boom", case.detail)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")
        self.assertEqual(self.membership.mollie_mandate_id, "")

    def test_a_capture_with_a_case_closes_the_members_other_open_payment(self):
        self._pending_row(ref="CRUSH-PREM-A")
        other = self._pending_row(ref="CRUSH-PREM-B", payment_id="tr_other")
        PremiumMembership.objects.filter(pk=self.membership.pk).update(
            status="cancelled"
        )
        self.mollie.by_id["tr_other"] = _payment("open", id="tr_other")

        self._webhook(_payment("paid"))

        self.assertTrue(PremiumPaymentRecoveryCase.objects.exists())
        self.assertEqual(self.mollie.count("DELETE", "/payments/tr_other"), 1)
        other.refresh_from_db()
        self.assertEqual(other.status, PaymentTransaction.Status.CANCELLED)

    # -- money going back ----------------------------------------------------

    def test_full_refund_ends_premium_and_releases_the_coach(self):
        self._post()
        self._webhook(_payment("paid"))

        self._webhook(_payment("paid", _embedded=_refunded()))

        self.assertEqual(self._rows().get().status, PaymentTransaction.Status.REFUNDED)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "cancelled")
        self.assertIsNotNone(self.membership.ended_at)
        profile = CrushProfile.objects.get(user=self.buyer)
        self.assertIsNone(profile.assigned_coach)
        self.assertFalse(profile.has_active_premium)

    def test_chargeback_ends_premium_too(self):
        self._post()
        self._webhook(_payment("paid"))

        self._webhook(
            _payment(
                "paid",
                _embedded={"chargebacks": [{"amount": {"value": "10.00"}}]},
            )
        )

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "cancelled")

    def test_partial_refund_keeps_premium(self):
        self._post()
        self._webhook(_payment("paid"))

        self._webhook(_payment("paid", _embedded=_refunded(Decimal("4.00"))))

        self.assertEqual(self._rows().get().status, PaymentTransaction.Status.PAID)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")

    def test_refund_of_an_unapplied_capture_only_settles_its_case(self):
        self._post()
        PremiumMembership.objects.filter(pk=self.membership.pk).update(
            status="cancelled"
        )
        self._webhook(_payment("paid"))

        self._webhook(_payment("paid", _embedded=_refunded()))

        row = self._rows().get()
        self.assertEqual(row.status, PaymentTransaction.Status.REFUNDED)
        case = PremiumPaymentRecoveryCase.objects.get(payment=row)
        self.assertEqual(case.status, PremiumPaymentRecoveryCase.Status.RESOLVED)
        self.assertEqual(
            case.resolution, PremiumPaymentRecoveryCase.Resolution.REFUNDED
        )

    def test_payment_returned_before_it_was_settled_grants_nothing(self):
        self._post()

        self._webhook(_payment("paid", _embedded=_refunded()))

        self.assertEqual(self._rows().get().status, PaymentTransaction.Status.REFUNDED)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")
        self.assertEqual(len(mail.outbox), 0)

    # -- the member's own cancel and the return page -------------------------

    def test_cancelling_the_request_closes_the_open_payment(self):
        row = self._pending_row()
        self.mollie.remote = _payment("open", description=self.description)

        response = self.client.post("/en/premium/cancel/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.mollie.count("DELETE", f"/payments/{PAYMENT_ID}"), 1)
        row.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.CANCELLED)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "cancelled")

    def test_request_is_kept_when_the_payment_cannot_be_closed(self):
        self._pending_row()
        self.mollie.remote = _payment("pending", description=self.description)
        self.mollie.cancel_code = 422

        self.client.post("/en/premium/cancel/")

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")

    def test_cancelling_a_request_that_was_just_paid_keeps_the_purchase(self):
        self._pending_row()
        self.mollie.remote = _payment("paid")

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post("/en/premium/cancel/")

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")

    def test_return_page_after_payment_says_premium(self):
        self._post()
        row = self._rows().get()
        self.mollie.remote = _payment("paid")

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(f"{RETURN}?ref={row.transaction_reference}")

        self.assertEqual(response.status_code, 302)
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("Premium" in text for text in texts), texts)
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")

    def test_return_page_without_payment_goes_back_to_the_request(self):
        self._post()
        row = self._rows().get()
        self.mollie.remote = _payment("canceled")

        response = self.client.get(f"{RETURN}?ref={row.transaction_reference}")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/premium/coaches/", response["Location"])
        row.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.CANCELLED)


class MolliePremiumLockOrderTests(CreditFixture):
    """SQLite ignores row locks, so the order is asserted on the source:
    payment, then membership, then the recovery case, then (cancel_active) the
    profile, the order checkout publication and the SumUp refund sweep take."""

    def test_settle_locks_the_payment_before_anything_premium(self):
        import inspect

        from crush_lu import views_mollie

        src = inspect.getsource(views_mollie._settle_paid)
        payment = src.index("PaymentTransaction.objects.select_for_update()")
        self.assertLess(payment, src.index("_apply_paid_checkout(locked, payment)"))
        self.assertLess(payment, src.index("_end_refunded_premium(locked)"))

    def test_refund_locks_membership_then_case_then_profile(self):
        import inspect

        from crush_lu import views_mollie

        src = inspect.getsource(views_mollie._end_refunded_premium)
        membership = src.index("PremiumMembership.objects.select_for_update()")
        case = src.index("refund_is_of_unapplied_capture(")
        profile = src.index("cancel_active(")
        self.assertLess(membership, case)
        self.assertLess(case, profile)
        self.assertNotIn("PaymentTransaction.objects.select_for_update", src)

    def test_checkout_publishes_only_through_the_shared_lock_helper(self):
        import inspect

        from crush_lu import views_mollie

        src = inspect.getsource(views_mollie.open_mollie_premium_checkout)
        self.assertNotIn("select_for_update", src)
        helper = src.index("_lock_premium_checkout_state(membership.pk, set())")
        self.assertLess(helper, src.index("_lock_member_and_check_blocked("))
        self.assertLess(helper, src.index("PaymentTransaction.objects.create("))
        # No provider call under the lock: Mollie is asked after the block.
        self.assertLess(
            src.index("PaymentTransaction.objects.create("),
            src.index("client.create_payment("),
        )
