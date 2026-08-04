import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushCoach, CrushProfile, PremiumMembership
from crush_lu.services.sumup import SumUpClient, SumUpError, clean_credential
from crush_lu.views_payments import _payment_owner_ids

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
class PremiumCompletionRevalidationTests(SiteTestMixin, TestCase):
    """The same in-flight problem on the Premium side.

    ``PremiumMembership.confirm()`` re-checks the coach's capacity under a row
    lock and raises ValueError if it is gone. The completion handler cannot let
    that pass silently: SumUp has captured the money by then.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="prem-race@crush.lu",
            email="prem-race@crush.lu",
            password="password123",
        )
        self.profile = CrushProfile.objects.create(
            user=self.user, gender="F", location="Luxembourg"
        )
        coach_user = User.objects.create_user(
            username="coach-race@crush.lu",
            email="coach-race@crush.lu",
            password="password123",
            first_name="Robin",
        )
        # One seat only, so a single rival membership fills it.
        self.coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=1,
        )
        self.membership = PremiumMembership.objects.create(
            user=self.user, coach=self.coach, status="pending"
        )

    def _tx(self, ref):
        return PaymentTransaction.objects.create(
            transaction_reference=ref,
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=f"CHK_{ref}",
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.user,
            premium_membership=self.membership,
        )

    def _login_for_the_browser_return(self, client=None):
        """The return URL is a real browser request, so it passes through the
        consent middleware and the site host — neither of which the direct
        ``_apply_paid_checkout`` tests in this class have to satisfy.

        Takes an optional client so a test can replay the URL in a *fresh*
        session: messages that nothing rendered stay queued in the old one and
        would otherwise be indistinguishable from the replay's own.
        """
        from crush_lu.models.profiles import UserDataConsent

        client = client or self.client
        client.defaults["HTTP_HOST"] = "crush.lu"
        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        client.force_login(self.user)
        return client

    def _fill_the_coach(self):
        rival = User.objects.create_user(
            username="rival@crush.lu", email="rival@crush.lu", password="password123"
        )
        CrushProfile.objects.create(user=rival, gender="M")
        PremiumMembership.objects.create(
            user=rival, coach=self.coach, status="active", payment_confirmed=True
        )

    def test_payment_for_a_coach_that_filled_up_does_not_grant_premium(self):
        """Charged, but the seat is gone — the money must stay on record and the
        failure must be loud, because it needs a human to reassign or refund."""
        from crush_lu.views_payments import _apply_paid_checkout

        tx = self._tx("PREMFULL")
        self._fill_the_coach()

        with self.assertLogs("crush_lu.views_payments", level="ERROR") as logs:
            with self.captureOnCommitCallbacks(execute=True):
                _apply_paid_checkout(tx, {"status": "PAID"})

        self.membership.refresh_from_db()
        self.profile.refresh_from_db()
        tx.refresh_from_db()

        # Premium was NOT granted.
        self.assertEqual(self.membership.status, "pending")
        self.assertFalse(self.membership.payment_confirmed)
        self.assertIsNone(self.profile.assigned_coach_id)
        # ...but the charge is still on record so staff can act on it.
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        # ...and it is loud enough to find.
        self.assertTrue(
            any("Premium NOT granted" in line for line in logs.output),
            logs.output,
        )

    def test_payment_on_a_cancelled_request_does_not_grant_premium(self):
        """A request cancelled before the money lands must not be resurrected.

        This one is caught by the outer ``status == "pending"`` guard rather
        than by confirm(), so it logs nothing — confirm()'s own "only a pending
        membership" ValueError is reachable only in a genuine race, where the
        status flips between this read and confirm()'s locked re-read.
        """
        from crush_lu.views_payments import _apply_paid_checkout

        tx = self._tx("PREMCANCEL")
        self.membership.status = "cancelled"
        self.membership.save(update_fields=["status"])

        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})

        self.membership.refresh_from_db()
        self.profile.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(self.membership.status, "cancelled")
        self.assertIsNone(self.profile.assigned_coach_id)
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)

    def test_normal_premium_payment_still_confirms(self):
        """The ordinary path must still activate and assign the coach."""
        from crush_lu.views_payments import _apply_paid_checkout

        tx = self._tx("PREMHAPPY")
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})

        self.membership.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        self.assertTrue(self.membership.payment_confirmed)
        self.assertIsNotNone(self.membership.payment_date)
        self.assertEqual(self.profile.assigned_coach_id, self.coach.id)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_return_confirms_premium_even_when_the_hub_bounces_them(
        self, mock_get_checkout
    ):
        """The return page must confirm the purchase without relying on the hub.

        Premium can be bought before Crush Connect onboarding (the coach
        directory needs a profile, not a membership), and ``_hub_access_blocker``
        redirects exactly that member out of the hub — so the hub's Premium
        badge is unreachable on the load right after paying. This user has no
        CrushConnectMembership at all, which is that case.
        """
        tx = self._tx("PREMRETURN")
        mock_get_checkout.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}
        self._login_for_the_browser_return()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(
                reverse("sumup_payment_return"), {"ref": "PREMRETURN"}
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], reverse("crush_lu:crush_connect_hub")
        )
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "active")
        # The message rides along through whatever redirect chain the hub gate
        # sends them down, so it does not depend on the hub rendering.
        texts = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any("Premium" in t and "Robin" in t for t in texts), texts
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_the_confirmation_is_actually_rendered_where_the_gate_lands_them(
        self, mock_get_checkout
    ):
        """Follows the redirect chain and asserts the coach name is ON the page.

        The sibling test above proves the message reaches *storage*, which is a
        gate test: storage is not a surface, and a queued message on a template
        that never renders one is invisible while still looking fixed from the
        view's side. This user has no CrushConnectMembership, so the hub gate
        bounces them — exactly the case the badge alone could not reach.
        """
        tx = self._tx("PREMRENDER")
        mock_get_checkout.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}
        self._login_for_the_browser_return()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(
                reverse("sumup_payment_return"), {"ref": "PREMRENDER"}, follow=True
            )

        self.assertEqual(response.status_code, 200)
        # They were bounced off the hub, which is the whole point.
        self.assertNotIn(
            reverse("crush_lu:crush_connect_hub"),
            [url for url, _status in response.redirect_chain[-1:]],
        )
        body = response.content.decode()
        self.assertIn("Robin", body)
        self.assertIn("Premium", body)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_replayed_return_names_the_current_coach_not_the_bought_one(
        self, mock_get_checkout
    ):
        """This URL is replayable from browser history long after the purchase.

        ``CrushProfile.assigned_coach`` and ``PremiumMembership.coach`` are both
        editable in the admin, and moving a member to a coach with capacity is
        the documented remedy when confirm() fails — so the two diverge in
        exactly the case staff have had to intervene. Naming pm.coach here while
        the hub renders profile.assigned_coach told the member two different
        coaches on a single page-load.
        """
        tx = self._tx("PREMREASSIGN")
        mock_get_checkout.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}
        self._login_for_the_browser_return()

        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(reverse("sumup_payment_return"), {"ref": "PREMREASSIGN"})

        # Staff move the member to a different coach after the sale.
        new_coach_user = User.objects.create_user(
            username="coach-new@crush.lu",
            email="coach-new@crush.lu",
            password="password123",
            first_name="Sam",
        )
        new_coach = CrushCoach.objects.create(
            user=new_coach_user,
            is_active=True,
            accepting_premium=True,
            max_premium_members=5,
        )
        self.profile.refresh_from_db()
        self.profile.assigned_coach = new_coach
        self.profile.save(update_fields=["assigned_coach"])

        # The member reopens the return URL from history, in a fresh session so
        # the first visit's still-unrendered messages cannot be mistaken for
        # this one's.
        replay = self._login_for_the_browser_return(client=Client())
        response = replay.get(reverse("sumup_payment_return"), {"ref": "PREMREASSIGN"})

        texts = [str(m) for m in response.wsgi_request._messages]
        # The hub renders profile.assigned_coach; this must agree with it.
        self.assertTrue(any("Sam" in t for t in texts), texts)
        self.assertFalse(any("Robin" in t for t in texts), texts)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_demoted_staff_creator_loses_access_to_the_payment(
        self, mock_get_checkout
    ):
        """``tx.user`` on a staff-opened checkout is the STAFF account.

        Both checkout creators let staff act for a member and stamp
        user=request.user. Counting that as ownership gave a staff member who
        once opened a checkout for someone a permanent, personal route to that
        member's payment — surviving the revocation of is_staff, which is the
        one event that is supposed to take such access away.
        """
        from crush_lu.models.profiles import UserDataConsent

        ex_staff = User.objects.create_user(
            username="ex-staff@crush.lu",
            email="ex-staff@crush.lu",
            password="password123",
            is_staff=True,
        )
        UserDataConsent.objects.update_or_create(
            user=ex_staff,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        # Staff opened the checkout on the member's behalf.
        tx = self._tx("PREMSTAFF")
        tx.user = ex_staff
        tx.save(update_fields=["user"])
        mock_get_checkout.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}

        # Access revoked.
        ex_staff.is_staff = False
        ex_staff.save(update_fields=["is_staff"])

        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.client.force_login(ex_staff)
        response = self.client.get(
            reverse("sumup_payment_return"), {"ref": "PREMSTAFF"}
        )

        texts = [str(m) for m in response.wsgi_request._messages]
        self.assertFalse(any("Premium" in t for t in texts), texts)
        self.assertFalse(any("Robin" in t for t in texts), texts)
        # The member is still the owner and is unaffected.
        self.assertEqual(
            _payment_owner_ids(tx), {self.user.id}, "member must own their payment"
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_default_profile_still_follows_the_browsing_language(
        self, mock_get_checkout
    ):
        """The majority case: a member who never opened the language setting.

        ``preferred_language`` is ``default="en"`` and non-blank, so a
        profile-first helper answers "en" for them however they are browsing.
        Pinning that was worse than not overriding at all — LocaleMiddleware
        had been resolving Accept-Language correctly, and the override threw
        that away. The sibling test below cannot see this: it sets the profile
        to French explicitly, which is exactly the path that hides the bug.
        """
        self.assertEqual(
            self.profile.preferred_language, "en", "precondition: untouched default"
        )
        tx = self._tx("PREMACCEPT")
        mock_get_checkout.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}

        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        client = Client(HTTP_HOST="crush.lu", HTTP_ACCEPT_LANGUAGE="fr")
        client.force_login(self.user)

        with self.captureOnCommitCallbacks(execute=True):
            response = client.get(
                reverse("sumup_payment_return"), {"ref": "PREMACCEPT"}
            )

        texts = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any("votre coach est Robin" in t for t in texts), texts)
        self.assertFalse(any("your coach is" in t for t in texts), texts)
        self.assertTrue(
            response["Location"].startswith("/fr/"), response["Location"]
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_an_explicit_preference_beats_the_browsing_language(
        self, mock_get_checkout
    ):
        """The other half: a member who DID set French, on an English browser.

        This route is outside i18n_patterns, so LocaleMiddleware would answer
        "en" from Accept-Language and hand a French member an English
        confirmation however complete the catalogs are. A stored de/fr is
        unambiguous — nobody reaches it by default — so it wins outright.
        """
        self.profile.preferred_language = "fr"
        self.profile.save(update_fields=["preferred_language"])

        tx = self._tx("PREMFR")
        mock_get_checkout.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        # Browser explicitly asks for English — the preference must still win.
        client = Client(HTTP_HOST="crush.lu", HTTP_ACCEPT_LANGUAGE="en")
        client.force_login(self.user)

        with self.captureOnCommitCallbacks(execute=True):
            response = client.get(
                reverse("sumup_payment_return"), {"ref": "PREMFR"}
            )

        texts = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any("votre coach est Robin" in t for t in texts), texts
        )
        self.assertFalse(any("your coach is" in t for t in texts), texts)
        # The whole page, not just the line this PR added: a French
        # confirmation beside an English "Payment completed successfully" is
        # still a confirmation the member cannot read.
        self.assertFalse(
            any("Payment completed successfully" in t for t in texts), texts
        )
        # ...and it must not land them on an English page.
        self.assertTrue(
            response["Location"].startswith("/fr/"), response["Location"]
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_an_explicit_english_survives_a_french_browser(self, mock_get_checkout):
        """The third case, and the one the schema could not express.

        The two tests above are the easy halves: an untouched default follows
        the browser, an explicit de/fr overrides it. Between them sits a member
        who OPENED the switcher and picked English. The stored value is "en"
        either way, so before ``language_explicitly_set`` this member was
        indistinguishable from one who never answered, and on a new device —
        no ``django_language`` cookie, ``Accept-Language: fr`` — got a French
        confirmation and a /fr/ redirect for a language they had rejected.
        """
        self.profile.preferred_language = "en"
        self.profile.language_explicitly_set = True
        self.profile.save(
            update_fields=["preferred_language", "language_explicitly_set"]
        )

        tx = self._tx("PREMEN")
        mock_get_checkout.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}

        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        # New device: nothing but Accept-Language to go on, and it says French.
        client = Client(HTTP_HOST="crush.lu", HTTP_ACCEPT_LANGUAGE="fr")
        client.force_login(self.user)

        with self.captureOnCommitCallbacks(execute=True):
            response = client.get(reverse("sumup_payment_return"), {"ref": "PREMEN"})

        texts = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any("your coach is Robin" in t for t in texts), texts)
        self.assertFalse(any("votre coach est" in t for t in texts), texts)
        # Same whole-page check as the French sibling, in the other direction.
        self.assertFalse(any("Paiement" in t for t in texts), texts)
        self.assertTrue(response["Location"].startswith("/en/"), response["Location"])

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_stranger_cannot_read_someone_elses_premium_status(
        self, mock_get_checkout
    ):
        """The return page names the member's coach, so authentication alone is
        not enough — the reference travels in a URL (history, a shared link, a
        support ticket) and any logged-in user could replay it.

        An unowned reference must answer exactly like an unknown one, or the
        page becomes an oracle for which references exist.
        """
        from crush_lu.models.profiles import UserDataConsent

        tx = self._tx("PREMSNOOP")
        mock_get_checkout.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}

        stranger = User.objects.create_user(
            username="stranger@crush.lu",
            email="stranger@crush.lu",
            password="password123",
        )
        UserDataConsent.objects.update_or_create(
            user=stranger,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.client.force_login(stranger)

        response = self.client.get(
            reverse("sumup_payment_return"), {"ref": "PREMSNOOP"}
        )

        texts = [str(m) for m in response.wsgi_request._messages]
        self.assertFalse(any("Robin" in t for t in texts), texts)
        self.assertFalse(any("Premium" in t for t in texts), texts)
        # Indistinguishable from a reference that does not exist at all.
        self.assertTrue(
            any("reference not found" in t for t in texts), texts
        )
        # ...and a stranger must not be able to drive the payment either.
        mock_get_checkout.assert_not_called()
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_return_does_not_claim_premium_when_it_was_not_granted(
        self, mock_get_checkout
    ):
        """Charged but the coach filled up: the entitlement was NOT granted, so
        the return page must not tell the member they are Premium."""
        tx = self._tx("PREMRETURNFULL")
        self._fill_the_coach()
        mock_get_checkout.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}
        self._login_for_the_browser_return()

        with self.assertLogs("crush_lu.views_payments", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.get(
                    reverse("sumup_payment_return"), {"ref": "PREMRETURNFULL"}
                )

        self.membership.refresh_from_db()
        self.assertEqual(self.membership.status, "pending")
        texts = [str(m) for m in response.wsgi_request._messages]
        self.assertFalse(any("Premium" in t for t in texts), texts)


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
        superseded = PaymentTransaction.objects.get(sumup_checkout_id="CHK_A")
        # CANCELLED, not FAILED: SumUp's dashboard lists a deactivated checkout
        # as a failed sale, so our own record has to say which "Échec" rows are
        # our supersessions and which are a bank refusing a card.
        self.assertEqual(superseded.status, PaymentTransaction.Status.CANCELLED)
        self.assertIn("Superseded", superseded.failure_reason)
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


class DescribeSumUpFailureTests(TestCase):
    """The "why" extracted from a checkout payload.

    SumUp's dashboard shows a failed online payment as the word "Échec" and a
    struck-through amount. Everything useful is in the checkout resource, and
    this is what turns it into a sentence a coach can read.
    """

    def test_names_the_decline_reason_from_the_attempt(self):
        from crush_lu.views_payments import describe_sumup_failure

        reason = describe_sumup_failure(
            {
                "status": "PENDING",
                "transactions": [
                    {"status": "FAILED", "failure_reason": "AUTHORISATION_ERROR"}
                ],
            }
        )

        self.assertIn("PENDING", reason)
        self.assertIn("FAILED", reason)
        self.assertIn("AUTHORISATION_ERROR", reason)

    def test_says_outright_when_no_card_was_ever_submitted(self):
        """The case that points back at us rather than at the card.

        An EXPIRED or deactivated checkout carries no transactions at all, and
        that difference is the whole question when a merchant is staring at a
        row of failures wondering whether a customer's bank refused them.
        """
        from crush_lu.views_payments import describe_sumup_failure

        reason = describe_sumup_failure({"status": "EXPIRED", "transactions": []})

        self.assertIn("EXPIRED", reason)
        self.assertIn("no card attempt", reason)

    def test_survives_a_payload_shaped_unlike_the_documentation(self):
        """This runs on live provider output during a payment; it may not 500."""
        from crush_lu.views_payments import describe_sumup_failure

        self.assertEqual(describe_sumup_failure(None), "")
        self.assertEqual(describe_sumup_failure("nonsense"), "")
        self.assertIn(
            "UNKNOWN", describe_sumup_failure({"transactions": ["not-a-dict"]})
        )


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class FailedAttemptVisibilityTests(SiteTestMixin, TestCase):
    """A refused card must stop being invisible on our side.

    Before this, the widget printed an error in the customer's browser and that
    was the end of it: SumUp leaves a checkout PENDING after a decline so
    another card can be tried, so no webhook fired, the customer never reached
    the return page, and the PaymentTransaction sat PENDING with nothing
    recorded. The only evidence was a "Échec" row in SumUp's dashboard, in a
    different system, naming no member and no registration.
    """

    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="fail@crush.lu", email="fail@crush.lu", password="password123"
        )
        self.other = User.objects.create_user(
            username="nosy@crush.lu", email="nosy@crush.lu", password="password123"
        )
        from crush_lu.models.profiles import UserDataConsent

        for user in (self.user, self.other):
            UserDataConsent.objects.update_or_create(
                user=user,
                defaults={
                    "powerup_consent_given": True,
                    "crushlu_consent_given": True,
                },
            )
        self.event = MeetupEvent.objects.create(
            title="Declined",
            description="d",
            event_type="speed_dating",
            location="Luxembourg",
            address="10 Grand Rue",
            date_time=timezone.now() + timezone.timedelta(days=2),
            registration_deadline=timezone.now() + timezone.timedelta(days=1),
            registration_fee=Decimal("1.00"),
            is_published=True,
        )
        self.registration = EventRegistration.objects.create(
            user=self.user, event=self.event, status="pending"
        )
        self.tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-DECLINED",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_DECLINED",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        self.url = reverse(
            "crush_lu:sumup_widget_failure",
            kwargs={"checkout_id": "CHK_DECLINED"},
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_decline_leaves_the_checkout_payable_but_recorded(self, mock_get):
        """PENDING is correct here — the customer can still try another card."""
        mock_get.return_value = {
            "id": "CHK_DECLINED",
            "status": "PENDING",
            "transactions": [
                {"status": "FAILED", "failure_reason": "INSUFFICIENT_FUNDS"}
            ],
        }
        self.client.force_login(self.user)

        response = self.client.post(
            self.url,
            data=json.dumps({"type": "fail", "message": "Card was declined"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PENDING)
        self.assertIn("INSUFFICIENT_FUNDS", self.tx.failure_reason)
        self.assertIn("Card was declined", self.tx.failure_reason)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_the_browser_cannot_talk_a_payment_into_being_paid(self, mock_get):
        """Same contract as the webhook: the posted body is a hint, not truth."""
        mock_get.return_value = {"id": "CHK_DECLINED", "status": "PENDING"}
        self.client.force_login(self.user)

        self.client.post(
            self.url,
            data=json.dumps({"type": "success", "status": "PAID"}),
            content_type="application/json",
        )

        self.tx.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PENDING)
        self.assertFalse(self.registration.payment_confirmed)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_stranger_cannot_scribble_on_someone_elses_payment(self, mock_get):
        mock_get.return_value = {"id": "CHK_DECLINED", "status": "PENDING"}
        self.client.force_login(self.other)

        response = self.client.post(
            self.url,
            data=json.dumps({"type": "fail", "message": "not mine"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.failure_reason, "")

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_terminal_failure_closes_the_row_with_its_reason(self, mock_get):
        mock_get.return_value = {
            "id": "CHK_DECLINED",
            "status": "FAILED",
            "transactions": [{"status": "FAILED", "error_message": "3DS aborted"}],
        }
        self.client.force_login(self.user)

        self.client.post(
            self.url,
            data=json.dumps({"type": "error", "message": "widget error"}),
            content_type="application/json",
        )

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.FAILED)
        self.assertIn("3DS aborted", self.tx.failure_reason)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_payment_that_actually_succeeded_is_still_applied(self, mock_get):
        """The widget can report an error on a checkout SumUp did capture.

        Asking SumUp first means this path repairs the seat instead of filing a
        failure against a payment the member was really charged for.
        """
        mock_get.return_value = {"id": "CHK_DECLINED", "status": "PAID"}
        self.client.force_login(self.user)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.url,
                data=json.dumps({"type": "error", "message": "widget hiccup"}),
                content_type="application/json",
            )

        self.tx.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)
        self.assertTrue(self.registration.payment_confirmed)

    def test_the_widget_page_renders_and_carries_the_reporting_url(self):
        """Renders the real template, not just the view.

        The reporting endpoint is reached through ``{% url %}`` in the widget
        page, so a name that does not resolve is a 500 on the payment screen —
        and the widget view had no test rendering it at all.
        """
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("crush_lu:sumup_widget", kwargs={"checkout_id": "CHK_DECLINED"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/payments/sumup/widget/CHK_DECLINED/failed/")
        # base.html renders the token the reporting fetch reads; without it the
        # POST is a 403 nobody sees (see test_template_hygiene).
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_an_anonymous_post_cannot_reach_a_payment(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"type": "fail"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 302)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.failure_reason, "")
