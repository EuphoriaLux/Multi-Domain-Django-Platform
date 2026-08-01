import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushCoach, CrushProfile, PremiumMembership
from crush_lu.services.sumup import SumUpClient, SumUpError, clean_credential

User = get_user_model()


class SiteTestMixin:
    """Mixin to create Site object for tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1,
            defaults={"domain": "crush.lu", "name": "Crush.lu"},
        )


class SumUpServiceTests(TestCase):
    def setUp(self):
        self.client_service = SumUpClient(api_key="test_api_key", merchant_code="test_merchant")

    @patch("requests.post")
    def test_create_checkout_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "id": "CHK123456",
            "status": "PENDING",
            "amount": 15.00,
            "currency": "EUR",
        }
        mock_post.return_value = mock_response

        res = self.client_service.create_checkout(
            amount=15.00,
            currency="EUR",
            checkout_reference="CRUSH-EVT-1",
            description="Speed Dating Ticket",
        )

        self.assertEqual(res["id"], "CHK123456")
        self.assertEqual(res["status"], "PENDING")
        mock_post.assert_called_once()

    @override_settings(SUMUP_API_KEY="cfg_key", SUMUP_MERCHANT_CODE="cfg_merchant")
    def test_client_reads_credentials_from_settings(self):
        """Every view builds SumUpClient() with no arguments, so the credentials
        have to come from settings. They were read but never defined, which sent
        an empty bearer token and made SumUp answer 401 on every checkout."""
        client = SumUpClient()
        self.assertEqual(client.api_key, "cfg_key")
        self.assertEqual(client.merchant_code, "cfg_merchant")

    @override_settings(SUMUP_API_KEY="  'quoted_key'  ")
    def test_client_strips_whitespace_outside_quotes(self):
        self.assertEqual(SumUpClient().api_key, "quoted_key")

    @override_settings(SUMUP_API_KEY='"  padded_key  "', SUMUP_MERCHANT_CODE="'  MAV9HKVS  '")
    def test_client_strips_whitespace_inside_quotes(self):
        """Quotes outermost, whitespace inside. A single strip-then-unquote pass
        leaves the inner padding behind and SumUp answers a bare 401."""
        client = SumUpClient()
        self.assertEqual(client.api_key, "padded_key")
        self.assertEqual(client.merchant_code, "MAV9HKVS")

    def test_clean_credential_handles_nested_wrapping(self):
        self.assertEqual(clean_credential('  "  \'  key  \'  "  '), "key")
        self.assertEqual(clean_credential(None), "")
        self.assertEqual(clean_credential(""), "")

    @override_settings(SUMUP_API_KEY="")
    @patch("requests.post")
    def test_missing_api_key_raises_before_calling_sumup(self, mock_post):
        with self.assertRaises(SumUpError):
            SumUpClient().create_checkout(
                amount=15.00, currency="EUR", checkout_reference="CRUSH-EVT-1"
            )
        mock_post.assert_not_called()

    @patch("requests.get")
    def test_get_checkout_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "id": "CHK123456",
            "status": "PAID",
        }
        mock_get.return_value = mock_response

        res = self.client_service.get_checkout("CHK123456")
        self.assertEqual(res["status"], "PAID")


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class SumUpPaymentViewsTests(SiteTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@crush.lu",
            password="password123",
        )
        from crush_lu.models.profiles import UserDataConsent
        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )


        self.profile = CrushProfile.objects.create(
            user=self.user,
            verification_status="verified",
            completion_status="step4",
        )
        self.event = MeetupEvent.objects.create(
            title="Speed Dating Luxembourg",
            description="Fun dating meetup",
            event_type="speed_dating",
            location="Luxembourg City",
            address="10 Grand Rue",
            date_time=timezone.now() + timezone.timedelta(days=2),
            registration_deadline=timezone.now() + timezone.timedelta(days=1),
            registration_fee=Decimal("15.00"),
            is_published=True,
        )
        self.registration = EventRegistration.objects.create(
            user=self.user,
            event=self.event,
            status="pending",
        )
        self.coach_user = User.objects.create_user(
            username="coach1", email="coach1@crush.lu", password="password123"
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=10,
        )
        self.membership = PremiumMembership.objects.create(
            user=self.user,
            coach=self.coach,
            status="pending",
        )

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_create_sumup_event_checkout_view(self, mock_create_checkout):
        mock_create_checkout.return_value = {"id": "CHK_EVT_001", "status": "PENDING"}
        self.client.force_login(self.user)

        url = reverse("sumup_create_event_checkout", kwargs={"registration_id": self.registration.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)


        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["checkout_id"], "CHK_EVT_001")

        tx = PaymentTransaction.objects.get(sumup_checkout_id="CHK_EVT_001")
        self.assertEqual(tx.purpose, PaymentTransaction.Purpose.EVENT_REGISTRATION)
        self.assertEqual(tx.event_registration, self.registration)
        self.assertEqual(tx.amount, Decimal("15.00"))

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_confirmed_but_unpaid_registration_can_still_pay(
        self, mock_create_checkout
    ):
        """The status a real signup actually produces must be payable.

        ``event_register`` sets ``status="confirmed"`` on signup, so this — not
        ``"pending"`` — is what every live registration looks like before it is
        paid. The original guard (``status != "pending" and not
        payment_confirmed``) rejected exactly this row with a 400, which is why
        the Pay button worked only after a staff member hand-set the status back
        to "Pending Payment".
        """
        mock_create_checkout.return_value = {"id": "CHK_EVT_002", "status": "PENDING"}
        self.registration.status = "confirmed"
        self.registration.payment_confirmed = False
        self.registration.save()
        self.client.force_login(self.user)

        url = reverse(
            "sumup_create_event_checkout",
            kwargs={"registration_id": self.registration.id},
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_already_paid_registration_cannot_start_a_second_checkout(
        self, mock_create_checkout
    ):
        """Guard the double-charge hole the old condition left open.

        With ``status="confirmed"`` and ``payment_confirmed=True`` — precisely
        what the return handler writes after a successful payment — the old
        check evaluated ``True and False`` and let the request straight through
        to SumUp a second time.
        """
        self.registration.status = "confirmed"
        self.registration.payment_confirmed = True
        self.registration.save()
        self.client.force_login(self.user)

        url = reverse(
            "sumup_create_event_checkout",
            kwargs={"registration_id": self.registration.id},
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, 400)
        mock_create_checkout.assert_not_called()

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_cancelled_registration_cannot_pay(self, mock_create_checkout):
        self.registration.status = "cancelled"
        self.registration.payment_confirmed = False
        self.registration.save()
        self.client.force_login(self.user)

        url = reverse(
            "sumup_create_event_checkout",
            kwargs={"registration_id": self.registration.id},
        )
        response = self.client.post(url)

        self.assertEqual(response.status_code, 400)
        mock_create_checkout.assert_not_called()

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    @patch("crush_lu.views_payments.SumUpClient.create_customer")
    def test_create_sumup_premium_checkout_view(self, mock_create_customer, mock_create_checkout):
        mock_create_customer.return_value = {"customer_id": f"crush-user-{self.user.id}"}
        mock_create_checkout.return_value = {"id": "CHK_PREM_001", "status": "PENDING"}
        self.client.force_login(self.user)


        url = reverse("sumup_create_premium_checkout", kwargs={"membership_id": self.membership.id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["checkout_id"], "CHK_PREM_001")

        tx = PaymentTransaction.objects.get(sumup_checkout_id="CHK_PREM_001")
        self.assertEqual(tx.purpose, PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP)
        self.assertEqual(tx.premium_membership, self.membership)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_sumup_webhook_event_registration(self, mock_get_checkout):
        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-REF-100",
            sumup_checkout_id="CHK_EVT_100",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

        mock_get_checkout.return_value = {"id": "CHK_EVT_100", "status": "PAID"}

        url = reverse("sumup_webhook")
        payload = {"id": "CHK_EVT_100", "status": "PAID", "event_type": "CHECKOUT_COMPLETED"}
        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "confirmed")
        self.assertTrue(self.registration.payment_confirmed)
        self.assertTrue(bool(self.registration.checkin_token))

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_return_redirects_to_event_detail_after_payment(self, mock_get_checkout):
        """The route is events/<int:event_id>/. Passing pk raised NoReverseMatch
        *after* the payment was recorded, so a successful purchase 500'd."""
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-REF-300",
            sumup_checkout_id="CHK_EVT_300",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        mock_get_checkout.return_value = {"id": "CHK_EVT_300", "status": "PAID"}
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("sumup_payment_return"), {"ref": "CRUSH-EVT-REF-300"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("crush_lu:event_detail", kwargs={"event_id": self.event.id}),
        )
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "confirmed")

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_return_accepts_sumup_server_post(self, mock_get_checkout):
        """SumUp POSTs the result server-to-server with no session and no CSRF
        token. That used to 403, losing the confirmation for any customer who
        closed the tab before the browser redirect."""
        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-REF-400",
            sumup_checkout_id="CHK_EVT_400",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        mock_get_checkout.return_value = {"id": "CHK_EVT_400", "status": "PAID"}

        response = self.client.post(
            reverse("sumup_payment_return") + "?ref=CRUSH-EVT-REF-400"
        )

        self.assertEqual(response.status_code, 200)
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "confirmed")

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_webhook_ignores_forged_paid_status(self, mock_get_checkout):
        """The webhook is public and unauthenticated. It must re-read the
        checkout from SumUp rather than believe payload["status"], or anyone
        could confirm a registration and mint a check-in token for free."""
        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-REF-500",
            sumup_checkout_id="CHK_EVT_500",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        # SumUp says it is still unpaid; the forged POST claims otherwise.
        mock_get_checkout.return_value = {"id": "CHK_EVT_500", "status": "PENDING"}

        response = self.client.post(
            reverse("sumup_webhook"),
            data=json.dumps({"id": "CHK_EVT_500", "status": "PAID"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_get_checkout.assert_called_once_with("CHK_EVT_500")
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PENDING)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "pending")
        self.assertFalse(self.registration.payment_confirmed)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_sumup_webhook_premium_membership(self, mock_get_checkout):
        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-PREM-REF-200",
            sumup_checkout_id="CHK_PREM_200",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.user,
            premium_membership=self.membership,
        )

        mock_get_checkout.return_value = {"id": "CHK_PREM_200", "status": "PAID"}

        url = reverse("sumup_webhook")
        payload = {"id": "CHK_PREM_200", "status": "PAID", "event_type": "CHECKOUT_COMPLETED"}
        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        self.assertTrue(self.membership.payment_confirmed)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.assigned_coach, self.coach)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class PremiumPriceConsistencyTests(SiteTestMixin, TestCase):
    """The price on the button must be the price the card is charged.

    These were two independent sources: the label hard-coded "€10.00 / month"
    while the checkout read SUMUP_PREMIUM_MONTHLY_FEE. They agreed only because
    both happened to say 10 -- setting the env var to anything else would have
    advertised one price and billed another.
    """

    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="premium@crush.lu",
            email="premium@crush.lu",
            password="password123",
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        CrushProfile.objects.create(
            user=self.user, verification_status="verified", completion_status="step4"
        )
        self.coach_user = User.objects.create_user(
            username="pricecoach@crush.lu",
            email="pricecoach@crush.lu",
            password="password123",
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=10,
        )
        self.membership = PremiumMembership.objects.create(
            user=self.user, coach=self.coach, status="pending"
        )

    @override_settings(
        SUMUP_PREMIUM_MONTHLY_FEE="15.00", PREMIUM_REDIRECTS_TO_BETA=False
    )
    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    @patch("crush_lu.views_payments.SumUpClient.create_customer")
    def test_displayed_price_matches_charged_price(
        self, mock_create_customer, mock_create_checkout
    ):
        mock_create_customer.return_value = {"customer_id": f"crush-user-{self.user.id}"}
        mock_create_checkout.return_value = {"id": "CHK_PRICE_001", "status": "PENDING"}
        self.client.force_login(self.user)

        # What the member is shown.
        page = self.client.get(reverse("crush_lu:premium_choose_coach"))
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertIn("15", body)
        self.assertNotIn("€10.00 / month", body)

        # What the card is actually charged.
        response = self.client.post(
            reverse(
                "crush_lu:sumup_create_premium_checkout",
                kwargs={"membership_id": self.membership.id},
            )
        )
        self.assertEqual(response.status_code, 200)
        tx = PaymentTransaction.objects.get(sumup_checkout_id="CHK_PRICE_001")
        self.assertEqual(tx.amount, Decimal("15.00"))


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CheckoutGuardTests(SiteTestMixin, TestCase):
    """Guards on who may open a checkout (Codex P1 findings on #757)."""

    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="guard@crush.lu", email="guard@crush.lu", password="password123"
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        CrushProfile.objects.create(
            user=self.user, verification_status="verified", completion_status="step4"
        )
        self.event = MeetupEvent.objects.create(
            title="Guarded Event",
            description="d",
            event_type="speed_dating",
            location="Luxembourg",
            address="10 Grand Rue",
            date_time=timezone.now() + timezone.timedelta(days=2),
            registration_deadline=timezone.now() + timezone.timedelta(days=1),
            registration_fee=Decimal("15.00"),
            is_published=True,
        )
        self.registration = EventRegistration.objects.create(
            user=self.user, event=self.event, status="pending"
        )

    def _post(self):
        return self.client.post(
            reverse(
                "crush_lu:sumup_create_event_checkout",
                kwargs={"registration_id": self.registration.id},
            )
        )

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_waitlisted_registration_cannot_pay(self, mock_create_checkout):
        """A waitlisted member must not buy past the capacity decision.

        The Pay button renders for any unpaid registration and
        _apply_paid_checkout promotes the payer to "confirmed" -- so allowing
        this would sell an over-capacity seat.
        """
        self.registration.status = "waitlist"
        self.registration.save()
        self.client.force_login(self.user)

        self.assertEqual(self._post().status_code, 400)
        mock_create_checkout.assert_not_called()

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_cannot_pay_for_a_cancelled_event(self, mock_create_checkout):
        self.event.is_cancelled = True
        self.event.save()
        self.client.force_login(self.user)

        self.assertEqual(self._post().status_code, 400)
        mock_create_checkout.assert_not_called()

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_second_click_reuses_the_existing_checkout(self, mock_create_checkout):
        """Two clicks must not open two checkouts -- that is a double charge.

        There is no uniqueness on the event_registration FK, so without reuse
        both transactions can be completed and each is marked paid independently.
        """
        mock_create_checkout.return_value = {"id": "CHK_ONCE", "status": "PENDING"}
        self.client.force_login(self.user)

        first = self._post()
        second = self._post()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["checkout_id"], second.json()["checkout_id"])
        self.assertEqual(mock_create_checkout.call_count, 1)
        self.assertEqual(
            PaymentTransaction.objects.filter(
                event_registration=self.registration
            ).count(),
            1,
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_successful_payment_sends_the_promised_confirmation(self, mock_get_checkout):
        """The payment-pending email promises a confirmation once paid."""
        from django.core import mail

        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-CONF",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_CONF",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        mail.outbox = []
        from crush_lu.views_payments import _apply_paid_checkout

        # The send is deferred with transaction.on_commit so a rolled-back
        # payment cannot email a confirmation; TestCase never commits, so the
        # callbacks have to be run explicitly.
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "confirmed")
        self.assertTrue(self.registration.payment_confirmed)
        self.assertEqual(len(mail.outbox), 1)

        # Idempotent: the browser return and SumUp's callback race routinely.
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})
        self.assertEqual(len(mail.outbox), 0)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class PaymentCompletionRevalidationTests(SiteTestMixin, TestCase):
    """State can change while a payment is in flight (Codex P1 on #757).

    The creation-time guards do not help once the widget is open: the member can
    cancel, or the organiser can cancel the event, before the payment lands.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="race@crush.lu", email="race@crush.lu", password="password123"
        )
        self.event = MeetupEvent.objects.create(
            title="Race Event",
            description="d",
            event_type="speed_dating",
            location="Luxembourg",
            address="10 Grand Rue",
            date_time=timezone.now() + timezone.timedelta(days=2),
            registration_deadline=timezone.now() + timezone.timedelta(days=1),
            registration_fee=Decimal("15.00"),
            is_published=True,
        )
        self.registration = EventRegistration.objects.create(
            user=self.user, event=self.event, status="pending"
        )

    def _tx(self, ref):
        return PaymentTransaction.objects.create(
            transaction_reference=ref,
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=f"CHK_{ref}",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

    def test_payment_on_a_cancelled_registration_does_not_restore_the_seat(self):
        """A seat the member released must not come back because money landed."""
        from crush_lu.views_payments import _apply_paid_checkout

        tx = self._tx("CANCELREG")
        self.registration.status = "cancelled"
        self.registration.save()

        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})

        self.registration.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(self.registration.status, "cancelled")
        self.assertFalse(self.registration.payment_confirmed)
        # The money is real and already captured -- the record must survive so
        # staff can refund it.
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)

    def test_payment_on_a_cancelled_event_does_not_confirm(self):
        from crush_lu.views_payments import _apply_paid_checkout

        tx = self._tx("CANCELEVT")
        self.event.is_cancelled = True
        self.event.save()

        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})

        self.registration.refresh_from_db()
        tx.refresh_from_db()
        self.assertNotEqual(self.registration.status, "confirmed")
        self.assertFalse(self.registration.payment_confirmed)
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)

    def test_normal_payment_still_confirms(self):
        """The guard must not break the ordinary path."""
        from crush_lu.views_payments import _apply_paid_checkout

        tx = self._tx("HAPPY")
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "confirmed")
        self.assertTrue(self.registration.payment_confirmed)
