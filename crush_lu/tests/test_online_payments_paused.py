"""ONLINE_PAYMENTS_ENABLED=False: no card payment anywhere, pay at the door.

SumUp refused the platform in 2026-10 and has answered every new checkout with
403 since. While there is no provider, the switch must:

- refuse every card checkout (event seat, Premium, donation) with a clear 503,
  before a ``SumUpClient`` is ever built;
- keep Crush Credit payable without touching SumUp;
- keep a saved card-form link from opening;
- replace the card buttons and the "Pay now" copy with "pay at the door",
  in the pages and in the payment-pending email.
"""

import json
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from crush_lu.email_helpers import send_event_payment_pending_notification
from crush_lu.models.credits import CrushCredit

from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushCoach, PremiumMembership
from crush_lu.services.credits import issue_credit
from crush_lu.services.event_payments import online_payments_paused
from crush_lu.tests.test_crush_credit import FEE_CENTS, CreditFixture, User


def _no_sumup(*args, **kwargs):
    raise AssertionError("SumUpClient must not be built while payments are paused")


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush", ONLINE_PAYMENTS_ENABLED=False
)
class OnlinePaymentsPausedTests(CreditFixture):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.buyer = self._user("buyer@crush.lu")
        self.target = self._event(hours_away=96, max_participants=10)
        self.seat = self._registration(self.target, self.buyer, status="pending")
        self.checkout_url = reverse(
            "sumup_create_event_checkout", kwargs={"registration_id": self.seat.pk}
        )
        self.client.force_login(self.buyer)

    def _post(self, url, payload):
        return self.client.post(
            url, data=json.dumps(payload), content_type="application/json"
        )

    def test_switch_reads_the_setting(self):
        self.assertTrue(online_payments_paused())
        with self.settings(ONLINE_PAYMENTS_ENABLED=True):
            self.assertFalse(online_payments_paused())

    @patch("crush_lu.views_payments.SumUpClient", side_effect=_no_sumup)
    def test_card_checkout_for_a_seat_is_refused(self, _client):
        response = self._post(self.checkout_url, {"payment_method": "card"})

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["payments_paused"])
        self.assertIn("pay at the door", response.json()["error"])
        self.assertFalse(
            PaymentTransaction.objects.filter(event_registration=self.seat).exists()
        )

    @override_settings(SUMUP_API_KEY="")
    @patch("crush_lu.views_payments.SumUpClient", side_effect=_no_sumup)
    def test_crush_credit_still_pays_without_any_provider(self, _client):
        issue_credit(self.buyer, FEE_CENTS, CrushCredit.Reason.GOODWILL)

        with self.captureOnCommitCallbacks(execute=True):
            response = self._post(self.checkout_url, {"payment_method": "credit"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["paid_with_credit"])
        self.seat.refresh_from_db()
        self.assertTrue(self.seat.payment_confirmed)
        self.assertEqual(self.seat.status, "confirmed")

    @patch("crush_lu.views_payments.SumUpClient", side_effect=_no_sumup)
    def test_donation_is_refused(self, _client):
        response = self._post(
            reverse("sumup_create_donation_checkout"), {"amount": "10.00"}
        )

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["payments_paused"])

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=False)
    @patch("crush_lu.views_payments.SumUpClient", side_effect=_no_sumup)
    def test_premium_is_refused(self, _client):
        coach_user = User.objects.create_user(
            username="coach@crush.lu", email="coach@crush.lu", password="pw"
        )
        coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=10,
        )
        membership = PremiumMembership.objects.create(
            user=self.buyer, coach=coach, status="pending"
        )

        response = self._post(
            f"/payments/sumup/create-premium-checkout/{membership.pk}/", {}
        )

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["payments_paused"])

    def test_saved_card_form_link_goes_back_to_the_event(self):
        PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-EVT-{self.seat.pk}-saved",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK-SAVED",
            amount=self.target.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.buyer,
            event_registration=self.seat,
        )

        response = self.client.get("/payments/sumup/widget/CHK-SAVED/")

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/events/{self.target.pk}/", response["Location"])

    def test_event_page_offers_the_door_instead_of_a_card(self):
        url = reverse("crush_lu:event_detail", kwargs={"event_id": self.target.pk})

        paused = self.client.get(url).content.decode()
        self.assertIn("in cash at the door", paused)
        self.assertNotIn('data-payment-method="card"', paused)

        with self.settings(ONLINE_PAYMENTS_ENABLED=True):
            live = self.client.get(url).content.decode()
        self.assertIn('data-payment-method="card"', live)
        self.assertNotIn("in cash at the door", live)

    def test_my_events_and_ticket_drop_the_card_button(self):
        my_events = self.client.get(reverse("crush_lu:my_events")).content.decode()
        self.assertIn("in cash at the door", my_events)
        self.assertNotIn('data-payment-method="card"', my_events)

        ticket = self.client.get(
            reverse("crush_lu:event_ticket", kwargs={"event_id": self.target.pk})
        ).content.decode()
        self.assertIn("in cash at the door", ticket)
        self.assertNotIn("#event-payment-actions", ticket)

    def test_payment_pending_email_says_pay_at_the_door(self):
        mail.outbox.clear()
        send_event_payment_pending_notification(self.seat)

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("pay at the door", message.subject)
        self.assertIn("in cash at the door", message.body)
        self.assertNotIn("Pay Now", message.body)

        mail.outbox.clear()
        with self.settings(ONLINE_PAYMENTS_ENABLED=True):
            send_event_payment_pending_notification(self.seat)
        self.assertIn("Payment required", mail.outbox[0].subject)
