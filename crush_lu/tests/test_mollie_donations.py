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


def _payment(status, value="10.00", payment_id="tr_7UhSN1zuXS"):
    return _response(
        200,
        {
            "id": payment_id,
            "status": status,
            "amount": {"value": value, "currency": "EUR"},
        },
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
            ("GET", "https://api.mollie.com/v2/payments/tr_7UhSN1zuXS"),
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
