"""Donations through Mollie (crush_lu/views_mollie.py, services/mollie.py).

Every HTTP call to Mollie is mocked at ``requests.request``. Requests use
literal paths on the crush.lu host (AGENTS.md: ``reverse()`` resolves against
the default urlconf, not the host's).
"""

import json
from decimal import Decimal
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.sites.models import Site
from django.core import mail
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings

from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushProfile, UserDataConsent
from crush_lu.services import mollie

User = get_user_model()

HOST = "crush.lu"
CREATE = "/payments/mollie/create-donation-checkout/"
WEBHOOK = "/payments/mollie/webhook/"
RETURN = "/payments/mollie/return/"
MY_EVENTS = "/en/my-events/"
CHECKOUT_HREF = "https://www.mollie.com/checkout/select-method/7UhSN1zuXS"
PAUSED = "Online contributions are paused for now. Thank you for your support!"
# Shaped like a key (test_ prefix) but low-entropy, so the secret scanner
# (gitleaks generic-api-key) does not take it for a real one.
MOLLIE_ON = {"MOLLIE_API_KEY": "test_xxxxxxxxxxxxxxxxxxxxxxxx"}
REQUEST = "crush_lu.services.mollie.requests.request"
RECEIPT = "crush_lu.email_helpers.send_donation_payment_receipt"


def _response(status_code, body):
    response = mock.Mock(status_code=status_code)
    response.json.return_value = body
    return response


def _opened(payment_id="tr_7UhSN1zuXS"):
    return _response(
        201,
        {
            "id": payment_id,
            "status": "open",
            "amount": {"value": "10.00", "currency": "EUR"},
            "_links": {"checkout": {"href": CHECKOUT_HREF}},
        },
    )


def _payment(status, value="10.00", payment_id="tr_7UhSN1zuXS", embedded=None):
    body = {
        "id": payment_id,
        "status": status,
        "amount": {"value": value, "currency": "EUR"},
    }
    if embedded:
        body["_embedded"] = embedded
    return _response(200, body)


def _refund(value="10.00", status="refunded"):
    return {"amount": {"value": value, "currency": "EUR"}, "status": status}


def _chargeback(value="10.00", reversed_at=None):
    return {"amount": {"value": value, "currency": "EUR"}, "reversedAt": reversed_at}


def _returned(refunds=(), chargebacks=()):
    """A ``paid`` read with refunds and chargebacks embedded."""
    return _payment(
        "paid",
        embedded={"refunds": list(refunds), "chargebacks": list(chargebacks)},
    )


def _member(email):
    user = User.objects.create_user(username=email, email=email, password="pw12345678")
    UserDataConsent.objects.update_or_create(
        user=user,
        defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
    )
    profile = CrushProfile.objects.create(
        user=user, verification_status="verified", completion_status="step4"
    )
    return user, profile


def _mollie_donation(user, payment_id="tr_7UhSN1zuXS", **fields):
    return PaymentTransaction.objects.create(
        transaction_reference=fields.pop("ref", "CRUSH-DON-MOLLIE"),
        provider=PaymentTransaction.Provider.MOLLIE,
        mollie_payment_id=payment_id,
        amount=Decimal("10.00"),
        currency="EUR",
        status=fields.pop("status", PaymentTransaction.Status.PENDING),
        purpose=PaymentTransaction.Purpose.DONATION,
        user=user,
        **fields,
    )


class MollieClientTests(SimpleTestCase):
    def test_create_payment_sends_what_mollie_expects(self):
        with mock.patch(REQUEST, return_value=_opened()) as call:
            payment = mollie.MollieClient(api_key=' "test_abc" ').create_payment(
                amount=Decimal("10.00"),
                description="Crush.lu Project Support (€10.00)",
                redirect_url="https://crush.lu/payments/mollie/return/?ref=R",
                webhook_url="https://crush.lu/payments/mollie/webhook/",
                metadata={"transaction_reference": "R"},
                locale="fr_FR",
            )

        method, url = call.call_args.args
        kwargs = call.call_args.kwargs
        self.assertEqual((method, url), ("POST", "https://api.mollie.com/v2/payments"))
        # The pasted key is normalised, as SUMUP_API_KEY is.
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test_abc")
        self.assertEqual(
            kwargs["json"],
            {
                "amount": {"currency": "EUR", "value": "10.00"},
                "description": "Crush.lu Project Support (€10.00)",
                "redirectUrl": "https://crush.lu/payments/mollie/return/?ref=R",
                "webhookUrl": "https://crush.lu/payments/mollie/webhook/",
                "metadata": {"transaction_reference": "R"},
                "locale": "fr_FR",
            },
        )
        self.assertEqual(kwargs["timeout"], mollie.TIMEOUT)
        self.assertEqual(mollie.checkout_url(payment), CHECKOUT_HREF)

    def test_an_error_body_raises_with_its_detail(self):
        refused = _response(
            422,
            {
                "status": 422,
                "title": "Unprocessable Entity",
                "detail": "The amount is lower than the minimum",
                "field": "amount",
            },
        )
        with (
            mock.patch(REQUEST, return_value=refused),
            self.assertRaises(mollie.MollieError) as raised,
        ):
            mollie.MollieClient(api_key="test_abc").get_payment("tr_x1")

        self.assertEqual(raised.exception.status, 422)
        self.assertIn("lower than the minimum", str(raised.exception))
        self.assertIn("amount", str(raised.exception))

    def test_a_network_failure_raises(self):
        with (
            mock.patch(REQUEST, side_effect=requests.ConnectionError("down")),
            self.assertRaises(mollie.MollieError),
        ):
            mollie.MollieClient(api_key="test_abc").get_payment("tr_x1")

    def test_get_payment_refuses_anything_but_a_payment_id(self):
        with mock.patch(REQUEST) as call:
            for bad in ("", "../methods", "tr_", "ord_123"):
                with self.subTest(bad=bad), self.assertRaises(mollie.MollieError):
                    mollie.MollieClient(api_key="test_abc").get_payment(bad)
        call.assert_not_called()

    def test_only_a_mollie_key_counts_as_configured(self):
        for key, expected in (
            ("", False),
            ("sup_sk_123", False),
            ("test_abc", True),
            ('  "live_abc"  ', True),
        ):
            with self.subTest(key=key), override_settings(MOLLIE_API_KEY=key):
                self.assertIs(mollie.mollie_configured(), expected)

    def test_helpers(self):
        self.assertEqual(mollie.mollie_locale("de"), "de_DE")
        self.assertEqual(mollie.mollie_locale("fr-be"), "fr_FR")
        self.assertIsNone(mollie.mollie_locale("lb"))
        self.assertIsNone(
            mollie.checkout_url({"_links": {"checkout": {"href": "http://x"}}})
        )
        payment = {"amount": {"value": "10.00", "currency": "EUR"}}
        self.assertTrue(mollie.amount_matches(payment, Decimal("10.00"), "EUR"))
        self.assertFalse(mollie.amount_matches(payment, Decimal("10.01"), "EUR"))
        self.assertFalse(mollie.amount_matches({}, Decimal("10.00"), "EUR"))

    def test_get_payment_asks_for_refunds_and_chargebacks(self):
        with mock.patch(REQUEST, return_value=_payment("paid")) as call:
            mollie.MollieClient(api_key="test_abc").get_payment("tr_x1")

        self.assertEqual(
            call.call_args.args,
            (
                "GET",
                "https://api.mollie.com/v2/payments/tr_x1?embed=refunds,chargebacks",
            ),
        )

    def test_money_returned_counts_only_money_that_really_went_back(self):
        for label, embedded, expected in (
            ("nothing embedded", None, ("0", "0")),
            ("empty lists", {"refunds": [], "chargebacks": []}, ("0", "0")),
            ("refunded", {"refunds": [_refund()]}, ("10.00", "0")),
            ("processing", {"refunds": [_refund(status="processing")]}, ("10.00", "0")),
            ("queued", {"refunds": [_refund(status="queued")]}, ("0", "0")),
            ("pending", {"refunds": [_refund(status="pending")]}, ("0", "0")),
            ("canceled", {"refunds": [_refund(status="canceled")]}, ("0", "0")),
            ("failed", {"refunds": [_refund(status="failed")]}, ("0", "0")),
            ("chargeback", {"chargebacks": [_chargeback()]}, ("0", "10.00")),
            (
                "reversed chargeback",
                {"chargebacks": [_chargeback(reversed_at="2026-10-30T09:00:00+00:00")]},
                ("0", "0"),
            ),
            (
                "several",
                {
                    "refunds": [_refund("2.00"), _refund("3.00", status="failed")],
                    "chargebacks": [_chargeback("1.50")],
                },
                ("2.00", "1.50"),
            ),
        ):
            with self.subTest(label):
                payment = _payment("paid", embedded=embedded).json()
                self.assertEqual(
                    mollie.money_returned(payment),
                    (Decimal(expected[0]), Decimal(expected[1])),
                )


class MollieDonationCheckoutTests(TestCase):
    def setUp(self):
        # Recycled SQLite primary keys share one cooldown/rate-limit counter.
        cache.clear()
        self.user, self.profile = _member("donor@crush.lu")
        self.client = Client(HTTP_HOST=HOST)
        self.client.force_login(self.user)

    def _post(self, amount=10, **extra):
        return self.client.post(
            CREATE,
            data=json.dumps({"amount": amount}),
            content_type="application/json",
            **extra,
        )

    @override_settings(ONLINE_PAYMENTS_ENABLED=False, **MOLLIE_ON)
    def test_opens_a_mollie_payment_while_sumup_stays_paused(self):
        with mock.patch(REQUEST, return_value=_opened()) as call:
            response = self._post(secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checkout_url"], CHECKOUT_HREF)
        tx = PaymentTransaction.objects.get()
        self.assertEqual(tx.provider, PaymentTransaction.Provider.MOLLIE)
        self.assertEqual(tx.mollie_payment_id, "tr_7UhSN1zuXS")
        self.assertEqual(tx.sumup_checkout_id, "")
        self.assertEqual(tx.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(tx.purpose, PaymentTransaction.Purpose.DONATION)
        self.assertEqual(tx.amount, Decimal("10.00"))
        self.assertEqual(tx.owner_id(), self.user.id)

        sent = call.call_args.kwargs["json"]
        self.assertEqual(sent["amount"], {"currency": "EUR", "value": "10.00"})
        self.assertEqual(
            sent["redirectUrl"],
            f"https://crush.lu/payments/mollie/return/?ref={tx.transaction_reference}",
        )
        self.assertEqual(
            sent["webhookUrl"], "https://crush.lu/payments/mollie/webhook/"
        )
        self.assertEqual(
            sent["metadata"], {"transaction_reference": tx.transaction_reference}
        )

    @override_settings(**MOLLIE_ON)
    def test_a_local_http_run_opens_the_payment_without_a_webhook(self):
        """Mollie refuses a webhook it cannot reach; the return page settles it."""
        with mock.patch(REQUEST, return_value=_opened()) as call:
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("webhookUrl", call.call_args.kwargs["json"])

    def test_refused_without_a_mollie_key(self):
        with mock.patch(REQUEST) as call:
            response = self._post()

        self.assertEqual(response.status_code, 503)
        call.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())

    @override_settings(ANDROID_NATIVE_COMMERCE_ENABLED=False, **MOLLIE_ON)
    def test_refused_inside_the_native_shell(self):
        """Store compliance: the endpoint agrees with the hidden card."""
        with mock.patch(REQUEST) as call:
            response = self._post(HTTP_X_CRUSH_CLIENT="android-app")

        self.assertEqual(response.status_code, 403)
        call.assert_not_called()

    @override_settings(**MOLLIE_ON)
    def test_refused_without_a_profile(self):
        self.profile.delete()
        with mock.patch(REQUEST) as call:
            response = self._post()

        self.assertEqual(response.status_code, 400)
        call.assert_not_called()

    @override_settings(**MOLLIE_ON)
    def test_the_amount_rules_are_the_sumup_ones(self):
        with mock.patch(REQUEST) as call:
            for amount in (1, 501, "abc", "Infinity"):
                with self.subTest(amount=amount):
                    self.assertEqual(self._post(amount).status_code, 400)
        call.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())

    @override_settings(**MOLLIE_ON)
    def test_a_refused_payment_leaves_a_failed_row_and_no_checkout(self):
        refused = _response(
            422, {"status": 422, "detail": "Invalid API key", "field": None}
        )
        with (
            mock.patch(REQUEST, return_value=refused),
            self.assertLogs("crush_lu.views_mollie", level="ERROR"),
        ):
            response = self._post()

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("checkout_url", response.json())
        tx = PaymentTransaction.objects.get()
        self.assertEqual(tx.status, PaymentTransaction.Status.FAILED)
        self.assertIn("Invalid API key", tx.failure_reason)

    @override_settings(**MOLLIE_ON)
    def test_a_second_click_inside_the_cooldown_is_refused(self):
        with mock.patch(REQUEST, return_value=_opened()):
            self.assertEqual(self._post().status_code, 200)
            self.assertEqual(self._post().status_code, 429)
        self.assertEqual(PaymentTransaction.objects.count(), 1)


@override_settings(**MOLLIE_ON)
class MollieWebhookTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.profile = _member("donor@crush.lu")
        self.tx = _mollie_donation(self.user)
        # Mollie posts form data with no session and no CSRF token.
        self.client = Client(HTTP_HOST=HOST, enforce_csrf_checks=True)

    def _notify(self, payment_id="tr_7UhSN1zuXS"):
        return self.client.post(WEBHOOK, {"id": payment_id})

    def test_a_paid_donation_grants_the_badge_and_sends_the_receipt(self):
        with (
            mock.patch(REQUEST, return_value=_payment("paid")) as call,
            mock.patch(RECEIPT) as receipt,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self._notify()

        self.assertEqual(response.status_code, 200)
        # The status was re-read from Mollie, not taken from the request.
        self.assertEqual(
            call.call_args.args,
            (
                "GET",
                "https://api.mollie.com/v2/payments/tr_7UhSN1zuXS"
                "?embed=refunds,chargebacks",
            ),
        )
        self.tx.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)
        self.assertIsNotNone(self.tx.paid_at)
        self.assertTrue(self.profile.is_community_supporter)
        receipt.assert_called_once()

    def test_a_repeated_webhook_changes_nothing_twice(self):
        with (
            mock.patch(REQUEST, return_value=_payment("paid")),
            mock.patch(RECEIPT) as receipt,
        ):
            for _attempt in range(2):
                with self.captureOnCommitCallbacks(execute=True):
                    self.assertEqual(self._notify().status_code, 200)

        receipt.assert_called_once()

    def test_a_paid_payment_for_another_amount_is_not_applied(self):
        with (
            mock.patch(REQUEST, return_value=_payment("paid", value="1.00")),
            self.assertLogs("crush_lu.views_mollie", level="ERROR"),
        ):
            self.assertEqual(self._notify().status_code, 200)

        self.tx.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PENDING)
        self.assertFalse(self.profile.is_community_supporter)

    def test_final_unpaid_statuses_close_the_row(self):
        for mollie_status, expected in (
            ("failed", PaymentTransaction.Status.FAILED),
            ("canceled", PaymentTransaction.Status.CANCELLED),
            ("expired", PaymentTransaction.Status.CANCELLED),
        ):
            with self.subTest(mollie_status=mollie_status):
                PaymentTransaction.objects.filter(pk=self.tx.pk).update(
                    status=PaymentTransaction.Status.PENDING, failure_reason=""
                )
                with mock.patch(REQUEST, return_value=_payment(mollie_status)):
                    self.assertEqual(self._notify().status_code, 200)
                self.tx.refresh_from_db()
                self.assertEqual(self.tx.status, expected)
                self.assertEqual(
                    self.tx.failure_reason, f"Mollie: payment {mollie_status}"
                )

    def test_an_open_payment_stays_pending(self):
        with mock.patch(REQUEST, return_value=_payment("open")):
            self.assertEqual(self._notify().status_code, 200)

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PENDING)

    def test_a_paid_row_is_never_moved_back(self):
        PaymentTransaction.objects.filter(pk=self.tx.pk).update(
            status=PaymentTransaction.Status.PAID
        )
        with mock.patch(REQUEST, return_value=_payment("canceled")):
            self._notify()

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)

    def test_unknown_and_malformed_ids_are_answered_without_asking_mollie(self):
        with mock.patch(REQUEST) as call:
            for payment_id in ("tr_unknown1", "x", ""):
                with self.subTest(payment_id=payment_id):
                    self.assertEqual(self._notify(payment_id).status_code, 200)
        call.assert_not_called()

    def test_a_failed_reread_asks_mollie_to_retry(self):
        with mock.patch(REQUEST, side_effect=requests.ConnectionError("down")):
            response = self._notify()

        self.assertEqual(response.status_code, 503)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PENDING)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(WEBHOOK).status_code, 405)


@override_settings(**MOLLIE_ON)
class MollieMoneyReturnedTests(TestCase):
    """Refunds and chargebacks: the payment stays ``paid`` at Mollie throughout."""

    def setUp(self):
        cache.clear()
        self.user, self.profile = _member("donor@crush.lu")
        self.tx = _mollie_donation(self.user)
        self.client = Client(HTTP_HOST=HOST, enforce_csrf_checks=True)
        with (
            mock.patch(REQUEST, return_value=_payment("paid")),
            mock.patch(RECEIPT),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self._notify()
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)
        self.paid_at = self.tx.paid_at

    def _notify(self):
        return self.client.post(WEBHOOK, {"id": "tr_7UhSN1zuXS"})

    def _deliver(self, payment):
        with (
            mock.patch(REQUEST, return_value=payment),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.assertEqual(self._notify().status_code, 200)
        self.tx.refresh_from_db()

    def test_a_full_refund_marks_the_row_refunded(self):
        with self.assertLogs("crush_lu.views_mollie", level="WARNING") as logs:
            self._deliver(_returned(refunds=[_refund()]))

        self.assertEqual(self.tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(self.tx.failure_reason, "Mollie: EUR 10.00 refunded")
        self.assertEqual(
            self.tx.raw_response["_embedded"]["refunds"][0]["status"], "refunded"
        )
        self.assertEqual(self.tx.paid_at, self.paid_at)
        self.assertIn(self.tx.transaction_reference, logs.output[0])

    def test_a_full_chargeback_marks_the_row_refunded_and_says_which(self):
        with self.assertLogs("crush_lu.views_mollie", level="WARNING"):
            self._deliver(_returned(chargebacks=[_chargeback()]))

        self.assertEqual(self.tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(self.tx.failure_reason, "Mollie: EUR 10.00 charged back")

    def test_a_refund_and_a_chargeback_can_add_up_to_all_of_it(self):
        with self.assertLogs("crush_lu.views_mollie", level="WARNING"):
            self._deliver(
                _returned(refunds=[_refund("3.00")], chargebacks=[_chargeback("7.00")])
            )

        self.assertEqual(self.tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(
            self.tx.failure_reason,
            "Mollie: EUR 3.00 refunded, EUR 7.00 charged back",
        )

    def test_more_than_was_paid_still_counts_as_all_of_it(self):
        """Mollie's docs: for some methods a refund can exceed the payment."""
        with self.assertLogs("crush_lu.views_mollie", level="WARNING"):
            self._deliver(_returned(refunds=[_refund("12.00")]))

        self.assertEqual(self.tx.status, PaymentTransaction.Status.REFUNDED)

    def test_the_funnel_records_one_refund_event(self):
        with (
            mock.patch("crush_lu.services.funnel_events.record_refund") as record,
            self.assertLogs("crush_lu.views_mollie", level="WARNING"),
        ):
            self._deliver(_returned(refunds=[_refund()]))
            self._deliver(_returned(refunds=[_refund()]))

        record.assert_called_once()

    def test_a_repeated_webhook_on_a_refunded_row_changes_nothing(self):
        """The trap: Mollie still says paid, and that must not re-apply the row."""
        with self.assertLogs("crush_lu.views_mollie", level="WARNING"):
            self._deliver(_returned(refunds=[_refund()]))
        updated_at = self.tx.updated_at

        with (
            mock.patch(RECEIPT) as receipt,
            mock.patch("crush_lu.views_mollie._apply_paid_checkout") as apply_paid,
            self.assertNoLogs("crush_lu.views_mollie", level="WARNING"),
        ):
            self._deliver(_returned(refunds=[_refund()]))

        apply_paid.assert_not_called()
        receipt.assert_not_called()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(self.tx.failure_reason, "Mollie: EUR 10.00 refunded")
        self.assertEqual(self.tx.updated_at, updated_at)

    def test_a_partial_refund_stays_paid_and_keeps_the_amounts(self):
        with self.assertLogs("crush_lu.views_mollie", level="WARNING") as logs:
            self._deliver(_returned(refunds=[_refund("3.00")]))

        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.tx.failure_reason, "")
        self.assertEqual(
            self.tx.raw_response["_embedded"]["refunds"][0]["amount"]["value"], "3.00"
        )
        self.assertIn("EUR 3.00 refunded", logs.output[0])

    def test_a_repeated_partial_refund_is_logged_and_written_once(self):
        with self.assertLogs("crush_lu.views_mollie", level="WARNING"):
            self._deliver(_returned(refunds=[_refund("3.00")]))
        updated_at = self.tx.updated_at

        with self.assertNoLogs("crush_lu.views_mollie", level="WARNING"):
            self._deliver(_returned(refunds=[_refund("3.00")]))

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.updated_at, updated_at)

    def test_partial_refunds_that_reach_the_total_mark_the_row_refunded(self):
        with self.assertLogs("crush_lu.views_mollie", level="WARNING"):
            self._deliver(_returned(refunds=[_refund("3.00")]))
            self._deliver(_returned(refunds=[_refund("3.00"), _refund("7.00")]))

        self.assertEqual(self.tx.status, PaymentTransaction.Status.REFUNDED)
        self.assertEqual(self.tx.failure_reason, "Mollie: EUR 10.00 refunded")

    def test_a_refund_that_can_still_be_canceled_changes_nothing(self):
        updated_at = self.tx.updated_at
        with self.assertNoLogs("crush_lu.views_mollie", level="WARNING"):
            self._deliver(_returned(refunds=[_refund(status="pending")]))

        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.tx.updated_at, updated_at)

    def test_a_failed_refund_on_a_refunded_row_is_flagged_but_not_undone(self):
        with self.assertLogs("crush_lu.views_mollie", level="WARNING"):
            self._deliver(_returned(refunds=[_refund()]))

        with self.assertLogs("crush_lu.views_mollie", level="WARNING") as logs:
            self._deliver(_returned(refunds=[_refund(status="failed")]))

        self.assertIn("manual follow-up", logs.output[0])
        self.assertEqual(self.tx.status, PaymentTransaction.Status.REFUNDED)

    def test_a_refund_seen_on_the_first_read_still_records_the_payment(self):
        """The paid webhook was lost: both facts land, and the member paid once."""
        pending = _mollie_donation(
            self.user, payment_id="tr_secondpayment", ref="CRUSH-DON-MOLLIE-2"
        )
        with (
            mock.patch(REQUEST, return_value=_returned(refunds=[_refund()])),
            mock.patch(RECEIPT) as receipt,
            self.assertLogs("crush_lu.views_mollie", level="WARNING"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.client.post(WEBHOOK, {"id": "tr_secondpayment"})

        pending.refresh_from_db()
        self.assertEqual(pending.status, PaymentTransaction.Status.REFUNDED)
        self.assertIsNotNone(pending.paid_at)
        receipt.assert_called_once()

    def test_a_refund_on_a_payment_for_another_amount_is_not_applied(self):
        mismatched = _payment(
            "paid", value="1.00", embedded={"refunds": [_refund("1.00")]}
        )
        with self.assertLogs("crush_lu.views_mollie", level="ERROR"):
            self._deliver(mismatched)

        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)

    def test_the_payment_lock_comes_first(self):
        """SQLite ignores select_for_update, so only the source can show this."""
        import inspect

        from crush_lu import views_mollie

        source = inspect.getsource(views_mollie._settle_paid)
        lock = source.index("PaymentTransaction.objects.select_for_update")
        self.assertLess(lock, source.index("_apply_paid_checkout(locked"))
        self.assertLess(lock, source.index("locked.save("))
        self.assertLess(lock, source.index("_revoke_supporter_badge(locked"))

    def test_the_badge_rule_reads_the_donations_under_the_profile_lock(self):
        """Read earlier, a donation confirming at that moment would be missed."""
        import inspect

        from crush_lu import views_mollie

        source = inspect.getsource(views_mollie._revoke_supporter_badge)
        lock = source.index("CrushProfile.objects.select_for_update")
        self.assertLess(lock, source.index("PaymentTransaction.objects.filter"))
        self.assertLess(lock, source.index("profile.save("))


def _other_payment(user, reference, **fields):
    """A row that is not the one under test: a paid SumUp donation by default."""
    return PaymentTransaction.objects.create(
        transaction_reference=reference,
        provider=fields.pop("provider", PaymentTransaction.Provider.SUMUP),
        amount=Decimal("5.00"),
        currency="EUR",
        status=fields.pop("status", PaymentTransaction.Status.PAID),
        purpose=fields.pop("purpose", PaymentTransaction.Purpose.DONATION),
        user=user,
        **fields,
    )


@override_settings(**MOLLIE_ON)
class MollieSupporterBadgeTests(TestCase):
    """Tom's rule, 2026-10-30: all the money back takes the badge off, unless
    another donation is still paid. Staff restore it by hand; nothing else does.
    """

    def setUp(self):
        cache.clear()
        self.user, self.profile = _member("donor@crush.lu")
        self.tx = _mollie_donation(self.user)
        self.client = Client(HTTP_HOST=HOST, enforce_csrf_checks=True)
        self._deliver(_payment("paid"))
        self.assertTrue(self._has_badge())
        mail.outbox.clear()

    def _deliver(self, payment, payment_id="tr_7UhSN1zuXS"):
        with (
            mock.patch(REQUEST, return_value=payment),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(WEBHOOK, {"id": payment_id})
        self.assertEqual(response.status_code, 200)

    def _has_badge(self, profile=None):
        profile = profile or self.profile
        profile.refresh_from_db()
        return profile.is_community_supporter

    def test_a_full_refund_takes_the_badge_off(self):
        self._deliver(_returned(refunds=[_refund()]))

        self.assertFalse(self._has_badge())

    def test_a_full_chargeback_takes_the_badge_off(self):
        self._deliver(_returned(chargebacks=[_chargeback()]))

        self.assertFalse(self._has_badge())

    def test_a_refund_and_a_chargeback_adding_up_take_the_badge_off(self):
        self._deliver(
            _returned(refunds=[_refund("3.00")], chargebacks=[_chargeback("7.00")])
        )

        self.assertFalse(self._has_badge())

    def test_a_partial_refund_leaves_the_badge_on(self):
        self._deliver(_returned(refunds=[_refund("3.00")]))

        self.assertTrue(self._has_badge())

    def test_a_partial_chargeback_leaves_the_badge_on(self):
        self._deliver(_returned(chargebacks=[_chargeback("3.00")]))

        self.assertTrue(self._has_badge())

    def test_a_refund_that_can_still_be_canceled_leaves_the_badge_on(self):
        self._deliver(_returned(refunds=[_refund(status="pending")]))

        self.assertTrue(self._has_badge())

    def test_partial_refunds_take_the_badge_off_when_they_reach_the_total(self):
        self._deliver(_returned(refunds=[_refund("3.00")]))
        self._deliver(_returned(refunds=[_refund("3.00"), _refund("7.00")]))

        self.assertFalse(self._has_badge())

    def test_another_paid_donation_keeps_the_badge(self):
        for provider in (
            PaymentTransaction.Provider.SUMUP,
            PaymentTransaction.Provider.MOLLIE,
        ):
            with self.subTest(provider=provider):
                other = _other_payment(
                    self.user, f"OTHER-{provider}", provider=provider
                )
                PaymentTransaction.objects.filter(pk=self.tx.pk).update(
                    status=PaymentTransaction.Status.PAID, raw_response={}
                )

                self._deliver(_returned(refunds=[_refund()]))

                self.assertTrue(self._has_badge())
                other.delete()

    def test_a_partly_refunded_donation_still_counts_as_paid(self):
        _other_payment(
            self.user,
            "OTHER-PARTIAL",
            provider=PaymentTransaction.Provider.MOLLIE,
            mollie_payment_id="tr_otherpayment",
        )
        self._deliver(
            _returned(refunds=[_refund("1.00")]), payment_id="tr_otherpayment"
        )

        self._deliver(_returned(refunds=[_refund()]))

        self.assertTrue(self._has_badge())

    def test_rows_that_are_not_a_paid_donation_do_not_keep_the_badge(self):
        cases = {
            "refunded": {"status": PaymentTransaction.Status.REFUNDED},
            "pending": {"status": PaymentTransaction.Status.PENDING},
            "failed": {"status": PaymentTransaction.Status.FAILED},
            "event seat": {"purpose": PaymentTransaction.Purpose.EVENT_REGISTRATION},
            "premium": {"purpose": PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP},
        }
        for name, fields in cases.items():
            with self.subTest(name):
                other = _other_payment(self.user, f"OTHER-{name}", **fields)
                PaymentTransaction.objects.filter(pk=self.tx.pk).update(
                    status=PaymentTransaction.Status.PAID, raw_response={}
                )
                CrushProfile.objects.filter(pk=self.profile.pk).update(
                    is_community_supporter=True
                )

                self._deliver(_returned(refunds=[_refund()]))

                self.assertFalse(self._has_badge())
                other.delete()

    def test_another_members_donation_does_not_keep_the_badge(self):
        stranger, stranger_profile = _member("stranger@crush.lu")
        _other_payment(stranger, "OTHER-STRANGER")
        CrushProfile.objects.filter(pk=stranger_profile.pk).update(
            is_community_supporter=True
        )

        self._deliver(_returned(refunds=[_refund()]))

        self.assertFalse(self._has_badge())
        self.assertTrue(self._has_badge(stranger_profile))

    def test_a_badge_restored_by_staff_survives_repeats_of_the_webhook(self):
        self._deliver(_returned(chargebacks=[_chargeback()]))
        CrushProfile.objects.filter(pk=self.profile.pk).update(
            is_community_supporter=True
        )

        self._deliver(_returned(chargebacks=[_chargeback()]))

        self.assertTrue(self._has_badge())

    def test_a_reversal_only_warns_and_does_not_restore_the_badge(self):
        self._deliver(_returned(chargebacks=[_chargeback()]))

        with self.assertLogs("crush_lu.views_mollie", level="WARNING") as logs:
            self._deliver(
                _returned(chargebacks=[_chargeback(reversed_at="2026-11-01T10:00:00Z")])
            )

        self.assertIn("badge not restored", logs.output[0])
        self.assertFalse(self._has_badge())

    def test_a_failed_refund_does_not_restore_the_badge(self):
        self._deliver(_returned(refunds=[_refund()]))

        with self.assertLogs("crush_lu.views_mollie", level="WARNING"):
            self._deliver(_returned(refunds=[_refund(status="failed")]))

        self.assertFalse(self._has_badge())

    def test_the_only_donation_refunded_on_its_first_read_leaves_no_badge(self):
        """The paid webhook was lost: granted and removed again in one read."""
        late, late_profile = _member("late@crush.lu")
        _mollie_donation(late, payment_id="tr_latepayment", ref="CRUSH-DON-LATE")

        self._deliver(_returned(refunds=[_refund()]), payment_id="tr_latepayment")

        self.assertFalse(self._has_badge(late_profile))

    def test_the_member_is_not_told(self):
        self._deliver(_returned(refunds=[_refund()]))

        self.assertEqual(mail.outbox, [])

    def test_a_member_without_a_profile_only_loses_the_row(self):
        CrushProfile.objects.filter(pk=self.profile.pk).delete()

        self._deliver(_returned(refunds=[_refund()]))

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.REFUNDED)


class SupporterBadgeAdminTests(TestCase):
    """Staff need a way to restore a badge the money rule took off."""

    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.user, self.profile = _member("donor@crush.lu")
        self.url = f"/crush-admin/crush_lu/crushprofile/{self.profile.pk}/change/"
        staff = User.objects.create_superuser(
            username="badge_admin", email="badge-admin@crush.lu", password="pw12345678"
        )
        self.client = Client(HTTP_HOST=HOST)
        self.client.force_login(staff)

    def test_the_change_form_offers_the_badge(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'name="is_community_supporter"')

    def test_staff_can_restore_the_badge(self):
        inline = self.client.get(self.url).context["inline_admin_formsets"][0].formset

        response = self.client.post(
            self.url,
            {
                f"{inline.prefix}-TOTAL_FORMS": "0",
                f"{inline.prefix}-INITIAL_FORMS": "0",
                "preferred_language": "en",
                "preferred_age_min": "18",
                "preferred_age_max": "99",
                "completion_status": self.profile.completion_status,
                "verification_status": self.profile.verification_status,
                "is_community_supporter": "on",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_community_supporter)


@override_settings(**MOLLIE_ON)
class MollieReturnTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.profile = _member("donor@crush.lu")
        self.tx = _mollie_donation(self.user)
        self.client = Client(HTTP_HOST=HOST)
        self.client.force_login(self.user)

    def _return(self):
        return self.client.get(RETURN, {"ref": self.tx.transaction_reference})

    def _messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_a_paid_return_settles_the_donation_before_the_webhook(self):
        with mock.patch(REQUEST, return_value=_payment("paid")), mock.patch(RECEIPT):
            response = self._return()

        self.assertRedirects(response, MY_EVENTS, fetch_redirect_response=False)
        self.assertIn(
            "Payment completed successfully! Thank you.", self._messages(response)
        )
        self.tx.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)
        self.assertTrue(self.profile.is_community_supporter)

    def test_an_unfinished_payment_says_so(self):
        with mock.patch(REQUEST, return_value=_payment("open")):
            response = self._return()

        self.assertRedirects(response, MY_EVENTS, fetch_redirect_response=False)
        self.assertIn(
            "Payment is pending or was not completed.", self._messages(response)
        )

    def test_an_already_settled_row_does_not_ask_mollie_again(self):
        PaymentTransaction.objects.filter(pk=self.tx.pk).update(
            status=PaymentTransaction.Status.PAID
        )
        with mock.patch(REQUEST) as call:
            response = self._return()

        call.assert_not_called()
        self.assertIn(
            "Payment completed successfully! Thank you.", self._messages(response)
        )

    def test_another_members_reference_is_answered_as_unknown(self):
        stranger, _profile = _member("stranger@crush.lu")
        self.client.force_login(stranger)
        with mock.patch(REQUEST) as call:
            response = self._return()

        call.assert_not_called()
        self.assertIn(
            "Payment transaction reference not found.", self._messages(response)
        )
        self.assertNotIn(MY_EVENTS, response.url)

    def test_requires_a_login(self):
        self.client.logout()
        response = self._return()

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)


class SupportCardMollieTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.profile = _member("donor@crush.lu")
        self.client = Client(HTTP_HOST=HOST)
        self.client.force_login(self.user)

    def _card(self, path=MY_EVENTS):
        response = self.client.get(path, follow=True)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    @override_settings(ONLINE_PAYMENTS_ENABLED=False, **MOLLIE_ON)
    def test_the_card_takes_donations_through_mollie_while_sumup_is_paused(self):
        body = self._card()

        self.assertIn(f'data-endpoint="{CREATE}"', body)
        self.assertIn("Secure checkout by Mollie", body)
        self.assertNotIn(PAUSED, body)
        self.assertNotIn("/payments/sumup/create-donation-checkout/", body)

    @override_settings(ONLINE_PAYMENTS_ENABLED=False)
    def test_without_a_key_the_paused_card_is_unchanged(self):
        body = self._card()

        self.assertIn(PAUSED, body)
        self.assertNotIn(CREATE, body)

    @override_settings(ANDROID_NATIVE_COMMERCE_ENABLED=False, **MOLLIE_ON)
    def test_the_native_shell_still_gets_no_payment_surface(self):
        body = self._card(f"{MY_EVENTS}?source=android_app")

        self.assertIn("Available outside the mobile app", body)
        self.assertNotIn(CREATE, body)

    @override_settings(ONLINE_PAYMENTS_ENABLED=False, **MOLLIE_ON)
    def test_german_card(self):
        self.assertIn("Sichere Zahlung über Mollie", self._card("/de/my-events/"))
