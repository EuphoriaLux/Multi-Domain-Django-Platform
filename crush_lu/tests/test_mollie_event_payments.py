"""Event seats through Mollie (crush_lu/views_mollie.py, open_mollie_event_checkout).

Every HTTP call to Mollie is mocked at ``requests.request``. The checkout keeps
the SumUp event URL and JSON contract, so these tests post to that URL.
"""

import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from django.urls import reverse

from crush_lu.models.payments import PaymentTransaction
from crush_lu.services.event_payments import (
    mollie_event_payments_active,
    online_payments_paused,
    sumup_payments_paused,
)
from crush_lu.tests.test_crush_credit import FEE, CreditFixture

WEBHOOK = "/payments/mollie/webhook/"
RETURN = "/payments/mollie/return/"
CHECKOUT_HREF = "https://www.mollie.com/checkout/select-method/7UhSN1zuXS"
MOLLIE_ON = {
    "MOLLIE_API_KEY": "test_xxxxxxxxxxxxxxxxxxxxxxxx",
    "MOLLIE_EVENT_PAYMENTS_ENABLED": True,
}
REQUEST = "crush_lu.services.mollie.requests.request"
PAYMENT_ID = "tr_7UhSN1zuXS"


def _response(status_code, body):
    response = mock.Mock(status_code=status_code)
    response.json.return_value = body
    return response


def _payment(status, value=None, payment_id=PAYMENT_ID, checkout=True):
    body = {
        "id": payment_id,
        "status": status,
        "amount": {"value": value or f"{FEE:.2f}", "currency": "EUR"},
    }
    if checkout:
        body["_links"] = {"checkout": {"href": CHECKOUT_HREF}}
    return _response(201 if status == "open" else 200, body)


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush", ONLINE_PAYMENTS_ENABLED=False
)
class MollieEventCheckoutTests(CreditFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.buyer = self._user("buyer@crush.lu")
        self.target = self._event(hours_away=96, max_participants=10)
        self.seat = self._registration(self.target, self.buyer, status="pending")
        self.url = reverse(
            "sumup_create_event_checkout", kwargs={"registration_id": self.seat.pk}
        )
        self.client.force_login(self.buyer)

    def _post(self, payload=None):
        return self.client.post(
            self.url,
            data=json.dumps(payload or {"payment_method": "card"}),
            content_type="application/json",
        )

    def _mollie_rows(self):
        return PaymentTransaction.objects.filter(
            event_registration=self.seat, provider=PaymentTransaction.Provider.MOLLIE
        )

    def _pending_row(self, payment_id=PAYMENT_ID, ref="CRUSH-EVT-MOLLIE-1"):
        return PaymentTransaction.objects.create(
            transaction_reference=ref,
            provider=PaymentTransaction.Provider.MOLLIE,
            mollie_payment_id=payment_id,
            amount=FEE,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.buyer,
            event_registration=self.seat,
        )

    # -- the switch ---------------------------------------------------------

    def test_flag_alone_opens_nothing_without_a_key(self):
        with self.settings(MOLLIE_EVENT_PAYMENTS_ENABLED=True, MOLLIE_API_KEY=""):
            self.assertFalse(mollie_event_payments_active())
            self.assertTrue(online_payments_paused())

    def test_without_the_flag_card_checkout_stays_paused(self):
        with self.settings(MOLLIE_API_KEY=MOLLIE_ON["MOLLIE_API_KEY"]):
            with mock.patch(REQUEST) as call:
                response = self._post()
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["payments_paused"])
        call.assert_not_called()

    def test_mollie_reopens_events_but_not_sumup_or_premium(self):
        with self.settings(**MOLLIE_ON):
            self.assertFalse(online_payments_paused())
            self.assertTrue(sumup_payments_paused())

    # -- opening a payment --------------------------------------------------

    @override_settings(**MOLLIE_ON)
    def test_checkout_opens_a_mollie_payment_for_the_seat(self):
        with mock.patch(REQUEST, return_value=_payment("open")) as call:
            response = self._post()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["widget_url"], CHECKOUT_HREF)
        sent = call.call_args.kwargs["json"]
        self.assertEqual(sent["amount"], {"currency": "EUR", "value": f"{FEE:.2f}"})
        row = self._mollie_rows().get()
        self.assertEqual(row.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(row.mollie_payment_id, PAYMENT_ID)
        self.assertEqual(row.amount, FEE)
        self.assertEqual(
            sent["metadata"], {"transaction_reference": row.transaction_reference}
        )

    @override_settings(**MOLLIE_ON)
    def test_second_click_reuses_the_open_payment(self):
        with mock.patch(REQUEST, return_value=_payment("open")):
            self._post()
        cache.clear()

        with mock.patch(REQUEST, return_value=_payment("open")) as call:
            response = self._post()

        self.assertEqual(response.json()["widget_url"], CHECKOUT_HREF)
        self.assertEqual(self._mollie_rows().count(), 1)
        self.assertEqual({c.args[0] for c in call.call_args_list}, {"GET"})

    @override_settings(**MOLLIE_ON)
    def test_stale_priced_payment_is_cancelled_before_a_new_one_opens(self):
        self._pending_row()
        calls = [
            _payment("open", value="9.00"),  # GET: priced for an older fee
            _response(200, {"id": PAYMENT_ID, "status": "canceled"}),  # DELETE
            _payment("open", payment_id="tr_NewPayment1"),  # POST
        ]
        with mock.patch(REQUEST, side_effect=calls) as call:
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [c.args[0] for c in call.call_args_list], ["GET", "DELETE", "POST"]
        )
        old, new = self._mollie_rows().order_by("pk")
        self.assertEqual(old.status, PaymentTransaction.Status.CANCELLED)
        self.assertEqual(new.mollie_payment_id, "tr_NewPayment1")

    @override_settings(**MOLLIE_ON)
    def test_no_new_payment_when_the_old_one_cannot_be_cancelled(self):
        self._pending_row()
        calls = [
            _payment("open", value="9.00"),
            _response(422, {"detail": "not cancelable"}),
        ]
        with mock.patch(REQUEST, side_effect=calls) as call:
            response = self._post()

        self.assertEqual(response.status_code, 409)
        self.assertNotIn("POST", [c.args[0] for c in call.call_args_list])
        self.assertEqual(self._mollie_rows().count(), 1)

    @override_settings(**MOLLIE_ON)
    def test_mollie_refusal_marks_the_row_failed(self):
        with mock.patch(REQUEST, return_value=_response(422, {"detail": "no"})):
            response = self._post()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            self._mollie_rows().get().status, PaymentTransaction.Status.FAILED
        )

    @override_settings(**MOLLIE_ON)
    def test_paid_seat_and_cancelled_event_are_refused_before_mollie(self):
        self.target.is_cancelled = True
        self.target.save(update_fields=["is_cancelled"])
        with mock.patch(REQUEST) as call:
            response = self._post()
        self.assertEqual(response.status_code, 400)
        call.assert_not_called()
        self.assertFalse(self._mollie_rows().exists())

    @override_settings(**MOLLIE_ON)
    def test_another_members_registration_is_refused(self):
        other = self._user("other@crush.lu")
        self.client.force_login(other)
        with mock.patch(REQUEST) as call:
            response = self._post()
        self.assertEqual(response.status_code, 403)
        call.assert_not_called()

    @override_settings(**MOLLIE_ON)
    def test_crush_credit_path_does_not_touch_mollie(self):
        with mock.patch(REQUEST) as call:
            response = self._post({"payment_method": "credit"})
        self.assertEqual(response.status_code, 400)  # no balance
        call.assert_not_called()

    # -- settling -----------------------------------------------------------

    @override_settings(**MOLLIE_ON)
    def test_webhook_confirms_the_seat_once(self):
        self._pending_row()
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(REQUEST, return_value=_payment("paid", checkout=False)):
                response = self.client.post(WEBHOOK, {"id": PAYMENT_ID})
        self.assertEqual(response.status_code, 200)

        self.seat.refresh_from_db()
        self.assertTrue(self.seat.payment_confirmed)
        self.assertEqual(self.seat.status, "confirmed")
        row = self._mollie_rows().get()
        self.assertEqual(row.status, PaymentTransaction.Status.PAID)
        self.assertEqual(row.event_id, self.target.pk)

        # A repeat changes nothing.
        with mock.patch(REQUEST, return_value=_payment("paid", checkout=False)):
            self.client.post(WEBHOOK, {"id": PAYMENT_ID})
        self.assertEqual(self._mollie_rows().get().failure_reason, "")

    @override_settings(**MOLLIE_ON)
    def test_amount_mismatch_is_not_applied(self):
        self._pending_row()
        with mock.patch(
            REQUEST, return_value=_payment("paid", value="1.00", checkout=False)
        ):
            self.client.post(WEBHOOK, {"id": PAYMENT_ID})

        self.seat.refresh_from_db()
        self.assertFalse(self.seat.payment_confirmed)
        self.assertEqual(
            self._mollie_rows().get().status, PaymentTransaction.Status.PENDING
        )

    @override_settings(**MOLLIE_ON)
    def test_expired_payment_closes_the_row_and_leaves_the_seat(self):
        self._pending_row()
        with mock.patch(REQUEST, return_value=_payment("expired", checkout=False)):
            self.client.post(WEBHOOK, {"id": PAYMENT_ID})

        self.assertEqual(
            self._mollie_rows().get().status, PaymentTransaction.Status.CANCELLED
        )
        self.seat.refresh_from_db()
        self.assertEqual(self.seat.status, "pending")
        self.assertFalse(self.seat.payment_confirmed)

    @override_settings(**MOLLIE_ON)
    def test_second_charge_for_a_paid_seat_is_flagged_for_refund(self):
        self._pending_row()
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(REQUEST, return_value=_payment("paid", checkout=False)):
                self.client.post(WEBHOOK, {"id": PAYMENT_ID})
        second = self._pending_row(
            payment_id="tr_SecondPaid1", ref="CRUSH-EVT-MOLLIE-2"
        )

        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(
                REQUEST,
                return_value=_payment(
                    "paid", payment_id="tr_SecondPaid1", checkout=False
                ),
            ):
                self.client.post(WEBHOOK, {"id": "tr_SecondPaid1"})

        second.refresh_from_db()
        self.assertEqual(second.status, PaymentTransaction.Status.PAID)
        self.assertIn("Duplicate payment", second.failure_reason)
        self.assertIn("CRUSH-EVT-MOLLIE-1", second.failure_reason)

    @override_settings(**MOLLIE_ON)
    def test_full_refund_marks_the_row_refunded_and_leaves_the_registration(self):
        self._pending_row()
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(REQUEST, return_value=_payment("paid", checkout=False)):
                self.client.post(WEBHOOK, {"id": PAYMENT_ID})
        refunded = _response(
            200,
            {
                "id": PAYMENT_ID,
                "status": "paid",
                "amount": {"value": f"{FEE:.2f}", "currency": "EUR"},
                "_embedded": {
                    "refunds": [
                        {
                            "amount": {"value": f"{FEE:.2f}", "currency": "EUR"},
                            "status": "refunded",
                        }
                    ],
                    "chargebacks": [],
                },
            },
        )
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(REQUEST, return_value=refunded):
                self.client.post(WEBHOOK, {"id": PAYMENT_ID})

        row = self._mollie_rows().get()
        self.assertEqual(row.status, PaymentTransaction.Status.REFUNDED)
        self.assertIn("refunded", row.failure_reason)
        self.seat.refresh_from_db()
        self.assertTrue(self.seat.payment_confirmed)

    @override_settings(**MOLLIE_ON)
    def test_return_page_lands_on_the_event(self):
        row = self._pending_row()
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(REQUEST, return_value=_payment("paid", checkout=False)):
                response = self.client.get(RETURN, {"ref": row.transaction_reference})
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/events/{self.target.pk}", response["Location"])
        self.seat.refresh_from_db()
        self.assertTrue(self.seat.payment_confirmed)

    @override_settings(**MOLLIE_ON)
    def test_return_page_hides_another_members_payment(self):
        row = self._pending_row()
        self.client.force_login(self._user("nosy@crush.lu"))
        with mock.patch(REQUEST) as call:
            response = self.client.get(RETURN, {"ref": row.transaction_reference})
        self.assertEqual(response.status_code, 302)
        call.assert_not_called()

    # -- the three Codex P1 gaps -------------------------------------------

    @override_settings(**MOLLIE_ON)
    def test_credit_payment_closes_an_open_mollie_checkout_first(self):
        from crush_lu.models.credits import CrushCredit
        from crush_lu.services.credits import issue_credit
        from crush_lu.tests.test_crush_credit import FEE_CENTS

        issue_credit(self.buyer, FEE_CENTS, CrushCredit.Reason.GOODWILL)
        row = self._pending_row()
        calls = [
            _payment("open"),  # GET
            _response(200, {"id": PAYMENT_ID, "status": "canceled"}),  # DELETE
        ]
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(REQUEST, side_effect=calls) as call:
                response = self._post({"payment_method": "credit"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([c.args[0] for c in call.call_args_list], ["GET", "DELETE"])
        row.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.CANCELLED)
        self.seat.refresh_from_db()
        self.assertTrue(self.seat.payment_confirmed)

    @override_settings(**MOLLIE_ON)
    def test_credit_payment_refused_when_mollie_checkout_cannot_be_closed(self):
        from crush_lu.models.credits import CrushCredit
        from crush_lu.services.credits import available_credit_cents, issue_credit
        from crush_lu.tests.test_crush_credit import FEE_CENTS

        issue_credit(self.buyer, FEE_CENTS, CrushCredit.Reason.GOODWILL)
        row = self._pending_row()
        calls = [_payment("open"), _response(422, {"detail": "not cancelable"})]
        with mock.patch(REQUEST, side_effect=calls):
            response = self._post({"payment_method": "credit"})

        self.assertEqual(response.status_code, 409)
        row.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.PENDING)
        self.seat.refresh_from_db()
        self.assertFalse(self.seat.payment_confirmed)
        self.assertEqual(available_credit_cents(self.buyer), FEE_CENTS)

    @override_settings(**MOLLIE_ON)
    def test_account_erasure_cancels_open_mollie_checkouts(self):
        from crush_lu.views_account import (
            _retire_event_checkouts_before_profile_deletion,
        )

        row = self._pending_row()
        calls = [_payment("open"), _response(200, {"status": "canceled"})]
        with mock.patch(REQUEST, side_effect=calls) as call:
            _retire_event_checkouts_before_profile_deletion(self.buyer)

        self.assertIn("DELETE", [c.args[0] for c in call.call_args_list])
        row.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.CANCELLED)

    @override_settings(**MOLLIE_ON)
    def test_account_erasure_pauses_when_mollie_checkout_is_already_paid(self):
        from crush_lu.views_account import (
            _retire_event_checkouts_before_profile_deletion,
        )

        row = self._pending_row()
        with mock.patch(REQUEST, return_value=_payment("paid", checkout=False)):
            with self.assertRaises(RuntimeError):
                _retire_event_checkouts_before_profile_deletion(self.buyer)
        row.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.PENDING)

    def test_resale_capture_time_is_mollies_paid_at_not_processing_time(self):
        from django.utils.dateparse import parse_datetime

        from crush_lu.services.credits import _replacement_capture_moment

        paid_at = "2026-11-20T17:55:00+00:00"
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-MOLLIE-CAP",
            provider=PaymentTransaction.Provider.MOLLIE,
            mollie_payment_id=PAYMENT_ID,
            amount=FEE,
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.buyer,
            event_registration=self.seat,
            raw_response={"id": PAYMENT_ID, "status": "paid", "paidAt": paid_at},
        )
        moment, payment = _replacement_capture_moment(self.seat)
        self.assertEqual(moment, parse_datetime(paid_at))
        self.assertEqual(payment.provider, PaymentTransaction.Provider.MOLLIE)

    @override_settings(**MOLLIE_ON)
    def test_payment_refunded_before_first_sync_grants_no_seat_or_credit(self):
        from crush_lu.models.credits import CrushCredit

        self.target.is_cancelled = True
        self.target.save(update_fields=["is_cancelled"])
        row = self._pending_row()
        refunded = _response(
            200,
            {
                "id": PAYMENT_ID,
                "status": "paid",
                "amount": {"value": f"{FEE:.2f}", "currency": "EUR"},
                "_embedded": {
                    "refunds": [
                        {
                            "amount": {"value": f"{FEE:.2f}", "currency": "EUR"},
                            "status": "refunded",
                        }
                    ],
                    "chargebacks": [],
                },
            },
        )
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(REQUEST, return_value=refunded):
                self.client.post(WEBHOOK, {"id": PAYMENT_ID})

        row.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.REFUNDED)
        self.assertFalse(CrushCredit.objects.filter(user=self.buyer).exists())
        self.seat.refresh_from_db()
        self.assertFalse(self.seat.payment_confirmed)

    @override_settings(**MOLLIE_ON)
    def test_chargeback_after_settlement_voids_the_credit_it_funded(self):
        from crush_lu.models.credits import CrushCredit
        from crush_lu.services.credits import available_credit_cents

        row = self._pending_row()
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(REQUEST, return_value=_payment("paid", checkout=False)):
                self.client.post(WEBHOOK, {"id": PAYMENT_ID})
        # A credit the capture earned (e.g. a late cancellation remedy).
        credit = CrushCredit.objects.create(
            user=self.buyer,
            amount_cents=1550,
            reason=CrushCredit.Reason.GOODWILL,
            source_payment=row,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.assertEqual(available_credit_cents(self.buyer), 1550)
        charged_back = _response(
            200,
            {
                "id": PAYMENT_ID,
                "status": "paid",
                "amount": {"value": f"{FEE:.2f}", "currency": "EUR"},
                "_embedded": {
                    "refunds": [],
                    "chargebacks": [
                        {
                            "amount": {"value": f"{FEE:.2f}", "currency": "EUR"},
                            "reversedAt": None,
                        }
                    ],
                },
            },
        )
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch(REQUEST, return_value=charged_back):
                self.client.post(WEBHOOK, {"id": PAYMENT_ID})

        row.refresh_from_db()
        credit.refresh_from_db()
        self.assertEqual(row.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(credit.status, CrushCredit.Status.VOID)
        self.assertEqual(available_credit_cents(self.buyer), 0)

    # -- reports ------------------------------------------------------------

    def test_mollie_revenue_counts_as_event_cash(self):
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-MOLLIE-REV",
            provider=PaymentTransaction.Provider.MOLLIE,
            mollie_payment_id=PAYMENT_ID,
            amount=Decimal("15.50"),
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.buyer,
            event_registration=self.seat,
        )
        self.assertTrue(
            PaymentTransaction.objects.paid_event_registrations()
            .filter(transaction_reference="CRUSH-EVT-MOLLIE-REV")
            .exists()
        )
