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


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class PaymentRaceAndStaleStateTests(SiteTestMixin, TestCase):
    """Round-4 Codex findings: stale reads, downgrades and stale amounts."""

    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="r4@crush.lu", email="r4@crush.lu", password="password123"
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
            title="Race4",
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
    def test_checks_run_against_the_locked_row_not_a_stale_copy(
        self, mock_create_checkout
    ):
        """A paid registration is refused, checked against the database row.

        HONEST LIMIT: this does not reproduce the actual race, and it passes
        against the pre-fix code too. Genuinely exercising it needs two
        connections interleaved around the SELECT ... FOR UPDATE, which the
        test client cannot express. It is kept as a guard on the outcome --
        that a paid row cannot open a checkout however it became paid -- while
        the fix itself (binding the locked row instead of discarding it) is
        verified by reading the code.
        """
        EventRegistration.objects.filter(pk=self.registration.pk).update(
            payment_confirmed=True
        )
        self.client.force_login(self.user)

        response = self._post()

        self.assertEqual(response.status_code, 400)
        mock_create_checkout.assert_not_called()

    def test_late_payment_does_not_downgrade_an_attendee(self):
        """A pending seat can be scanned while its payment is still settling."""
        from crush_lu.views_payments import _apply_paid_checkout

        self.registration.status = "attended"
        self.registration.save()
        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-LATE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_LATE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "attended")
        self.assertTrue(self.registration.payment_confirmed)

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_member_can_open_a_staff_created_checkout(self, mock_create_checkout):
        """The widget authorises by who the payment is for, not who opened it."""
        mock_create_checkout.return_value = {"id": "CHK_STAFF", "status": "PENDING"}
        from crush_lu.models.profiles import UserDataConsent

        staff = User.objects.create_user(
            username="staff@crush.lu",
            email="staff@crush.lu",
            password="password123",
            is_staff=True,
        )
        # consent_middleware 302s any crush_lu request without this.
        UserDataConsent.objects.update_or_create(
            user=staff,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.client.force_login(staff)
        self._post()

        self.client.force_login(self.user)
        response = self.client.get("/payments/sumup/widget/CHK_STAFF/")
        self.assertEqual(response.status_code, 200)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class SeatStatusAtCompletionTests(SiteTestMixin, TestCase):
    """Round-6: only a seat-holding status may be confirmed by a payment."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="r6@crush.lu", email="r6@crush.lu", password="password123"
        )
        self.event = MeetupEvent.objects.create(
            title="R6",
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

    def _apply(self, ref):
        from crush_lu.views_payments import _apply_paid_checkout

        tx = PaymentTransaction.objects.create(
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
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})
        self.registration.refresh_from_db()
        tx.refresh_from_db()
        return tx

    def test_payment_does_not_resurrect_a_waitlisted_seat(self):
        """Staff can move a row to waitlist while the widget is open."""
        self.registration.status = "waitlist"
        self.registration.save()

        tx = self._apply("WL")

        self.assertEqual(self.registration.status, "waitlist")
        self.assertFalse(self.registration.payment_confirmed)
        # Money captured -- the record must survive for the refund.
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)

    def test_payment_does_not_erase_a_recorded_no_show(self):
        self.registration.status = "no_show"
        self.registration.save()

        self._apply("NS")

        self.assertEqual(self.registration.status, "no_show")
        self.assertFalse(self.registration.payment_confirmed)

    def test_pending_still_confirms(self):
        """The allow-list must not break the ordinary path."""
        self._apply("OK")

        self.assertEqual(self.registration.status, "confirmed")
        self.assertTrue(self.registration.payment_confirmed)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class SupersedeCheckoutTests(SiteTestMixin, TestCase):
    """Every Pay click opens a fresh checkout and kills the previous one.

    This replaced reuse-and-reconcile. Reuse needed a provider call to decide
    whether an old checkout was still payable, and sequencing that call against
    an editable price, a cancellable event and a concurrent webhook produced a
    new defect in three successive rounds. Superseding is what keeps the
    double-charge guarantee: only the newest checkout is payable at SumUp.
    """

    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="sup@crush.lu", email="sup@crush.lu", password="password123"
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.event = MeetupEvent.objects.create(
            title="Supersede",
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
        self.url = reverse(
            "crush_lu:sumup_create_event_checkout",
            kwargs={"registration_id": self.registration.id},
        )
        self.client.force_login(self.user)

    @patch("crush_lu.views_payments.SumUpClient.deactivate_checkout")
    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_second_click_supersedes_the_first(
        self, mock_create_checkout, mock_deactivate
    ):
        mock_create_checkout.side_effect = [
            {"id": "CHK_A", "status": "PENDING"},
            {"id": "CHK_B", "status": "PENDING"},
        ]
        mock_deactivate.return_value = True

        first = self.client.post(self.url)
        second = self.client.post(self.url)

        self.assertEqual(first.json()["checkout_id"], "CHK_A")
        self.assertEqual(second.json()["checkout_id"], "CHK_B")
        # The old one is dead at SumUp, so it cannot also be paid.
        mock_deactivate.assert_called_once_with("CHK_A")
        self.assertEqual(
            PaymentTransaction.objects.get(sumup_checkout_id="CHK_A").status,
            PaymentTransaction.Status.FAILED,
        )
        self.assertEqual(
            PaymentTransaction.objects.get(sumup_checkout_id="CHK_B").status,
            PaymentTransaction.Status.PENDING,
        )

    @patch("crush_lu.views_payments.SumUpClient.deactivate_checkout")
    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_new_checkout_always_uses_the_current_fee(
        self, mock_create_checkout, mock_deactivate
    ):
        """No stale-price path exists any more: the fee is read per request."""
        mock_create_checkout.side_effect = [
            {"id": "CHK_OLD", "status": "PENDING"},
            {"id": "CHK_NEW", "status": "PENDING"},
        ]
        mock_deactivate.return_value = True

        self.client.post(self.url)
        self.event.registration_fee = Decimal("25.00")
        self.event.save()
        second = self.client.post(self.url)

        self.assertEqual(second.json()["amount"], 25.00)
        self.assertEqual(
            PaymentTransaction.objects.get(sumup_checkout_id="CHK_NEW").amount,
            Decimal("25.00"),
        )

    @patch("crush_lu.views_payments.SumUpClient.deactivate_checkout")
    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_unreachable_deactivation_does_not_block_the_new_checkout(
        self, mock_create_checkout, mock_deactivate
    ):
        """Best-effort by design — a member must not be stuck unable to pay.

        _apply_paid_checkout stays idempotent and payment_confirmed still guards
        a second attempt, so a payment slipping through is handled.
        """
        mock_create_checkout.side_effect = [
            {"id": "CHK_1", "status": "PENDING"},
            {"id": "CHK_2", "status": "PENDING"},
        ]
        mock_deactivate.return_value = False

        self.client.post(self.url)
        second = self.client.post(self.url)

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["checkout_id"], "CHK_2")

    @patch("crush_lu.views_payments.SumUpClient.deactivate_checkout")
    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_no_provider_call_is_made_to_reconcile(
        self, mock_create_checkout, mock_deactivate
    ):
        """The creation path must never call get_checkout.

        That call is what forced the lock ordering against the webhook and
        caused the deadlock; its absence here is the actual simplification.
        """
        mock_create_checkout.return_value = {"id": "CHK_X", "status": "PENDING"}
        mock_deactivate.return_value = True

        with patch("crush_lu.views_payments.SumUpClient.get_checkout") as mock_get:
            self.client.post(self.url)
            self.client.post(self.url)
            mock_get.assert_not_called()


class CheckoutLockOrderTests(TestCase):
    """Pin the lock order structurally — it has been inverted twice.

    A deadlock only shows up under real concurrency, which the test client
    cannot express, so the runtime tests below can never catch it. This reads
    the source instead: in the checkout path the PaymentTransaction lock must be
    acquired before the EventRegistration lock, matching _apply_paid_checkout.
    Taking them the other way round is an ABBA deadlock against a webhook for
    those very rows.
    """

    def _lock_sequence(self, func):
        import inspect
        import re

        src = inspect.getsource(func)
        return re.findall(r"(\w+)\.objects\s*\.?\s*select_for_update", src) or re.findall(
            r"(\w+)\.objects[\s\S]{0,40}?select_for_update", src
        )

    def test_creation_locks_transaction_before_registration(self):
        from crush_lu.views_payments import create_sumup_event_checkout

        seq = self._lock_sequence(create_sumup_event_checkout)
        self.assertEqual(
            seq[:2],
            ["PaymentTransaction", "EventRegistration"],
            f"checkout path must lock PaymentTransaction first; got {seq}",
        )

    def test_completion_locks_transaction_before_registration(self):
        from crush_lu.views_payments import _apply_paid_checkout

        seq = self._lock_sequence(_apply_paid_checkout)
        self.assertEqual(
            seq[:2],
            ["PaymentTransaction", "EventRegistration"],
            f"completion path must lock PaymentTransaction first; got {seq}",
        )

    def test_both_paths_agree(self):
        from crush_lu.views_payments import (
            _apply_paid_checkout,
            create_sumup_event_checkout,
        )

        self.assertEqual(
            self._lock_sequence(create_sumup_event_checkout)[:2],
            self._lock_sequence(_apply_paid_checkout)[:2],
        )


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class SupersedeFailureModeTests(SiteTestMixin, TestCase):
    """Round-9: what happens when deactivation or the fee misbehaves."""

    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="r9@crush.lu", email="r9@crush.lu", password="password123"
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.event = MeetupEvent.objects.create(
            title="R9",
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
        self.url = reverse(
            "crush_lu:sumup_create_event_checkout",
            kwargs={"registration_id": self.registration.id},
        )
        self.client.force_login(self.user)

    @patch("crush_lu.views_payments.SumUpClient.deactivate_checkout")
    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_failed_deactivation_leaves_the_row_reconcilable(
        self, mock_create_checkout, mock_deactivate
    ):
        """Marking it FAILED would hide a captured payment forever.

        _sync_checkout_with_sumup returns immediately for any non-PENDING row,
        so a checkout that could not be deactivated *because it had just been
        paid* must stay PENDING or the money is never applied.
        """
        mock_create_checkout.side_effect = [
            {"id": "CHK_P1", "status": "PENDING"},
            {"id": "CHK_P2", "status": "PENDING"},
        ]
        mock_deactivate.return_value = False

        self.client.post(self.url)
        self.client.post(self.url)

        self.assertEqual(
            PaymentTransaction.objects.get(sumup_checkout_id="CHK_P1").status,
            PaymentTransaction.Status.PENDING,
        )

    @patch("crush_lu.views_payments.SumUpClient.deactivate_checkout")
    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_event_cancelled_during_deactivation_is_caught(
        self, mock_create_checkout, mock_deactivate
    ):
        """Deactivation is network I/O; the organiser can cancel meanwhile."""
        mock_create_checkout.side_effect = [
            {"id": "CHK_C1", "status": "PENDING"},
            {"id": "CHK_C2", "status": "PENDING"},
        ]
        self.client.post(self.url)

        event_id = self.event.id

        def cancel_midway(_cid):
            MeetupEvent.objects.filter(pk=event_id).update(is_cancelled=True)
            return True

        mock_deactivate.side_effect = cancel_midway

        self.assertEqual(self.client.post(self.url).status_code, 400)

    def test_stale_priced_payment_does_not_buy_the_seat(self):
        """A widget left open across a fee change captures the old amount."""
        from crush_lu.views_payments import _apply_paid_checkout

        tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-STALE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_STALE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        self.event.registration_fee = Decimal("25.00")
        self.event.save()

        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})

        self.registration.refresh_from_db()
        tx.refresh_from_db()
        self.assertFalse(self.registration.payment_confirmed)
        self.assertNotEqual(self.registration.status, "confirmed")
        # Money captured -- the record must survive for the refund/top-up.
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
