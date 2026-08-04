import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

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
        # The provider-call throttle lives in the cache, which outlives a test.
        # Every test here uses the same checkout id, so a leftover key would
        # silently skip the SumUp call the next test is asserting on.
        from django.core.cache import cache

        cache.clear()
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
    def test_repeated_reports_do_not_hammer_sumup(self, mock_get):
        """Nothing stops a client posting here in a loop, so the cap lives here.

        The report itself is still recorded every time — only the provider call
        is rationed, because that is the finite resource.
        """
        mock_get.return_value = {"id": "CHK_DECLINED", "status": "PENDING"}
        self.client.force_login(self.user)

        for attempt in range(5):
            self.client.post(
                self.url,
                data=json.dumps({"type": "fail", "message": f"try {attempt}"}),
                content_type="application/json",
            )

        self.assertEqual(mock_get.call_count, 1)
        self.tx.refresh_from_db()
        self.assertIn("try 4", self.tx.failure_reason)

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


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class WidgetStatusPollingTests(SiteTestMixin, TestCase):
    """The page must be able to find out for itself that a payment landed.

    A member completed 3-D Secure and sat on the widget page indefinitely. The
    widget emits "auth-screen" when the challenge *starts* and then goes quiet —
    the bank finishes the payment, not the SDK — so nothing on the page ever
    learned the money had gone through. This endpoint is the other half: the
    page asks the server, and the server asks SumUp.
    """

    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        from django.core.cache import cache

        cache.clear()
        self.user = User.objects.create_user(
            username="poll@crush.lu", email="poll@crush.lu", password="password123"
        )
        self.other = User.objects.create_user(
            username="other@crush.lu", email="other@crush.lu", password="password123"
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
            title="Polled",
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
            transaction_reference="CRUSH-EVT-POLL",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_POLL",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        self.url = reverse(
            "crush_lu:sumup_widget_status", kwargs={"checkout_id": "CHK_POLL"}
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_payment_completed_behind_3ds_settles_the_seat(self, mock_get):
        """The whole point: nobody had to come back for this to be applied."""
        mock_get.return_value = {"id": "CHK_POLL", "status": "PAID"}
        self.client.force_login(self.user)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settled"], True)
        self.assertEqual(response.json()["paid"], True)
        self.registration.refresh_from_db()
        self.assertTrue(self.registration.payment_confirmed)
        self.assertEqual(self.registration.status, "confirmed")

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_an_unfinished_payment_keeps_the_page_waiting(self, mock_get):
        mock_get.return_value = {"id": "CHK_POLL", "status": "PENDING"}
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.json()["settled"], False)
        self.assertEqual(response.json()["paid"], False)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_refusal_settles_too_so_the_page_stops_spinning(self, mock_get):
        mock_get.return_value = {
            "id": "CHK_POLL",
            "status": "FAILED",
            "transactions": [{"status": "FAILED", "failure_reason": "3DS_FAILED"}],
        }
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.json()["settled"], True)
        self.assertEqual(response.json()["paid"], False)
        self.tx.refresh_from_db()
        self.assertIn("3DS_FAILED", self.tx.failure_reason)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_polling_hard_does_not_hammer_sumup(self, mock_get):
        """The browser sets the interval, so the throttle has to live here."""
        mock_get.return_value = {"id": "CHK_POLL", "status": "PENDING"}
        self.client.force_login(self.user)

        for _unused in range(5):
            self.client.get(self.url)

        self.assertEqual(mock_get.call_count, 1)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_the_poll_loop_does_not_starve_a_failure_report(self, mock_get):
        """Why the two throttles are scoped separately rather than sharing a key.

        The poll runs every 3 seconds for the whole payment, so one shared key
        would mean a genuine failure report almost always arrives inside a
        window the poll just claimed — and the one sync that report exists to
        trigger would be dropped.
        """
        mock_get.return_value = {"id": "CHK_POLL", "status": "PENDING"}
        self.client.force_login(self.user)

        self.client.get(self.url)  # a poll claims its slot
        self.client.post(
            reverse(
                "crush_lu:sumup_widget_failure", kwargs={"checkout_id": "CHK_POLL"}
            ),
            data=json.dumps({"type": "fail", "message": "declined"}),
            content_type="application/json",
        )

        self.assertEqual(mock_get.call_count, 2)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_stranger_cannot_watch_someone_elses_payment(self, mock_get):
        mock_get.return_value = {"id": "CHK_POLL", "status": "PAID"}
        self.client.force_login(self.other)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        mock_get.assert_not_called()

    def test_it_is_not_reachable_anonymously(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)

    def test_the_widget_page_wires_the_poll_up(self):
        """Renders the template — a missing URL name is a 500 on the pay page."""
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("crush_lu:sumup_widget", kwargs={"checkout_id": "CHK_POLL"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/payments/sumup/widget/CHK_POLL/status/")
        # The page must react to auth-screen rather than sitting on it.
        self.assertContains(response, "watchCheckout")


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class FailureReasonLifecycleTests(SiteTestMixin, TestCase):
    """What the recorded reason does across a checkout's whole life.

    A declined attempt leaves the checkout payable, so one row can carry a
    refusal and then a success. The reason has to keep up with that, and the
    payload behind it has to be the one that explains the current state.
    """

    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="life@crush.lu", email="life@crush.lu", password="password123"
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.event = MeetupEvent.objects.create(
            title="Retry",
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
            transaction_reference="CRUSH-EVT-LIFE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_LIFE",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
            raw_response={"id": "CHK_LIFE", "status": "PENDING"},
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_pending_decline_stores_the_payload_it_was_read_from(self, mock_get):
        """The Coach Panel's raw response must be the one that explains it.

        Saving only the summary left "Raw provider response" showing the
        checkout-creation payload — no transactions array at all — while
        claiming to be the last thing SumUp returned.
        """
        from crush_lu.views_payments import _sync_checkout_with_sumup

        mock_get.return_value = {
            "id": "CHK_LIFE",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }

        _sync_checkout_with_sumup(self.tx)

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PENDING)
        self.assertIn("DECLINED", self.tx.failure_reason)
        self.assertEqual(
            self.tx.raw_response["transactions"][0]["failure_reason"], "DECLINED"
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_fresh_detail_lands_even_when_the_summary_reads_the_same(self, mock_get):
        """Whether there's something new to say ≠ whether the snapshot is stale.

        SumUp can return detail the summary does not mention — a later
        timestamp, a code it did not have before — and gating the write on the
        summary alone left "Raw provider response" claiming to be the last
        thing SumUp returned while holding an older payload.
        """
        from crush_lu.views_payments import _sync_checkout_with_sumup

        mock_get.return_value = {
            "id": "CHK_LIFE",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }
        _sync_checkout_with_sumup(self.tx)
        self.tx.refresh_from_db()
        reason_before = self.tx.failure_reason

        mock_get.return_value = {
            "id": "CHK_LIFE",
            "status": "PENDING",
            "transactions": [
                {
                    "status": "FAILED",
                    "failure_reason": "DECLINED",
                    "timestamp": "2026-08-04T09:00:00Z",
                }
            ],
        }
        _sync_checkout_with_sumup(self.tx)

        self.tx.refresh_from_db()
        # Same story, so the summary is unchanged...
        self.assertEqual(self.tx.failure_reason, reason_before)
        # ...but the payload behind it is the one just fetched.
        self.assertEqual(
            self.tx.raw_response["transactions"][0]["timestamp"],
            "2026-08-04T09:00:00Z",
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_an_unchanged_poll_does_not_write(self, mock_get):
        """This branch is re-entered every few seconds for a whole payment."""
        from crush_lu.views_payments import _sync_checkout_with_sumup

        mock_get.return_value = {
            "id": "CHK_LIFE",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }
        _sync_checkout_with_sumup(self.tx)
        self.tx.refresh_from_db()
        written_at = self.tx.updated_at

        _sync_checkout_with_sumup(self.tx)

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.updated_at, written_at)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_successful_retry_clears_the_earlier_refusal(self, mock_get):
        """Same checkout, second card. The row must not stay filed as failed."""
        from crush_lu.views_payments import _sync_checkout_with_sumup

        mock_get.return_value = {
            "id": "CHK_LIFE",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }
        _sync_checkout_with_sumup(self.tx)
        self.tx.refresh_from_db()
        self.assertNotEqual(self.tx.failure_reason, "")

        mock_get.return_value = {
            "id": "CHK_LIFE",
            "status": "PAID",
            "transactions": [
                {"status": "FAILED", "failure_reason": "DECLINED"},
                {"status": "SUCCESSFUL"},
            ],
        }
        with self.captureOnCommitCallbacks(execute=True):
            _sync_checkout_with_sumup(self.tx)

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.tx.failure_reason, "")
        # The refusal is not lost — it is still in the payload.
        self.assertEqual(len(self.tx.raw_response["transactions"]), 2)

    def test_a_late_decline_cannot_land_on_a_row_that_was_paid(self):
        """The poll and the failure report are allowed to ask SumUp at once.

        So a report holding a PENDING payload can be overtaken mid-call by a
        poll that comes back PAID and applies it. Writing blind then puts a
        failure reason and a superseded payload back onto a paid row — the
        exact state _apply_paid_checkout clears, reintroduced by a race.
        """
        from crush_lu.views_payments import _record_pending_failure

        # What the slower request is still holding when it resumes.
        stale = {
            "id": "CHK_LIFE",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }
        # Meanwhile the faster one has already paid the row.
        PaymentTransaction.objects.filter(pk=self.tx.pk).update(
            status=PaymentTransaction.Status.PAID,
            raw_response={"id": "CHK_LIFE", "status": "PAID"},
        )

        wrote = _record_pending_failure(self.tx, "a stale decline", stale)

        self.assertFalse(wrote)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.tx.failure_reason, "")
        self.assertEqual(self.tx.raw_response["status"], "PAID")

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_settled_row_can_still_be_refreshed_for_detail(self, mock_get):
        """_sync_checkout_with_sumup won't touch a terminal row — by design.

        Which left the Coach Panel's re-check doing nothing at all on a FAILED
        payment while reporting that it had checked, and gave rows that failed
        before this field existed no way to ever get a reason.
        """
        from crush_lu.views_payments import refresh_sumup_snapshot

        self.tx.status = PaymentTransaction.Status.FAILED
        self.tx.save(update_fields=["status"])
        mock_get.return_value = {
            "id": "CHK_LIFE",
            "status": "FAILED",
            "transactions": [{"status": "FAILED", "failure_reason": "EXPIRED_CARD"}],
        }

        reported = refresh_sumup_snapshot(self.tx)

        self.assertEqual(reported, "FAILED")
        self.tx.refresh_from_db()
        self.assertIn("EXPIRED_CARD", self.tx.failure_reason)
        self.assertEqual(self.tx.raw_response["status"], "FAILED")
        # Status is the caller's business, never this function's.
        self.assertEqual(self.tx.status, PaymentTransaction.Status.FAILED)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_snapshot_cannot_land_on_a_row_paid_while_sumup_was_read(self, mock_get):
        """The same race as its three siblings, in the last writer to get a lock.

        The Coach Panel's re-check holds this row as it was loaded while the
        provider answers, and that read is the slowest of them. A poll finishing
        inside that window pays the row and clears its reason; writing the
        snapshot blind afterwards puts a failed payload and a decline back onto
        a payment that has just been paid.
        """
        from crush_lu.views_payments import refresh_sumup_snapshot

        def a_poll_pays_the_row_mid_read(_checkout_id):
            PaymentTransaction.objects.filter(pk=self.tx.pk).update(
                status=PaymentTransaction.Status.PAID,
                raw_response={"id": "CHK_LIFE", "status": "PAID"},
                failure_reason="",
            )
            return {
                "id": "CHK_LIFE",
                "status": "FAILED",
                "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
            }

        mock_get.side_effect = a_poll_pays_the_row_mid_read

        # Still answers with what SumUp said — the caller compares the two
        # records and this disagreement is exactly what it needs to see.
        self.assertEqual(refresh_sumup_snapshot(self.tx), "FAILED")

        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.PAID)
        self.assertEqual(self.tx.failure_reason, "")
        self.assertEqual(self.tx.raw_response["status"], "PAID")

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_refreshing_never_quietly_confirms_a_seat(self, mock_get):
        """A written-off row that SumUp calls PAID is a discrepancy, not a fix.

        Money taken with nothing delivered needs a human. Reporting it is this
        path's job; granting the seat is not.
        """
        from crush_lu.views_payments import refresh_sumup_snapshot

        self.tx.status = PaymentTransaction.Status.FAILED
        self.tx.save(update_fields=["status"])
        mock_get.return_value = {"id": "CHK_LIFE", "status": "PAID"}

        reported = refresh_sumup_snapshot(self.tx)

        self.assertEqual(reported, "PAID")
        self.tx.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.FAILED)
        self.assertFalse(self.registration.payment_confirmed)
        # And it is not described as a failure, because it is not one.
        self.assertEqual(self.tx.failure_reason, "")


class RecheckActionBoundsTests(SiteTestMixin, TestCase):
    """The admin action runs inline, so it has to stop before the request does.

    Production has no task worker (AGENTS.md): every re-check is a 10s-timeout
    provider call inside the admin request, under a gunicorn timeout. Selecting
    a screenful of unreachable checkouts must not get the request killed
    part-way through applying payments.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="coach@crush.lu",
            email="coach@crush.lu",
            password="password123",
            is_staff=True,
            is_superuser=True,
        )
        self.event = MeetupEvent.objects.create(
            title="Bulk",
            description="d",
            event_type="speed_dating",
            location="Luxembourg",
            address="10 Grand Rue",
            date_time=timezone.now() + timezone.timedelta(days=2),
            registration_deadline=timezone.now() + timezone.timedelta(days=1),
            registration_fee=Decimal("1.00"),
            is_published=True,
        )
        for index in range(6):
            registration = EventRegistration.objects.create(
                user=User.objects.create_user(
                    username=f"bulk{index}@crush.lu",
                    email=f"bulk{index}@crush.lu",
                    password="password123",
                ),
                event=self.event,
                status="pending",
            )
            PaymentTransaction.objects.create(
                transaction_reference=f"CRUSH-EVT-BULK-{index}",
                provider=PaymentTransaction.Provider.SUMUP,
                sumup_checkout_id=f"CHK_BULK_{index}",
                amount=Decimal("1.00"),
                currency="EUR",
                status=PaymentTransaction.Status.PENDING,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                user=self.user,
                event_registration=registration,
            )

    def _run_action(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.payments import PaymentTransactionAdmin
        from crush_lu.admin.site import crush_admin_site

        request = RequestFactory().post("/crush-admin/")
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)

        admin_obj = PaymentTransactionAdmin(PaymentTransaction, crush_admin_site)
        admin_obj.recheck_with_sumup(
            request, PaymentTransaction.objects.order_by("transaction_reference")
        )
        return [str(message) for message in request._messages]

    @override_settings(SUMUP_ADMIN_RECHECK_LIMIT=2)
    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_it_stops_at_the_limit(self, mock_get):
        mock_get.return_value = {"status": "PENDING"}

        self._run_action()

        self.assertEqual(mock_get.call_count, 2)

    @override_settings(SUMUP_ADMIN_RECHECK_LIMIT=2)
    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_cap_is_never_reported_as_completion(self, mock_get):
        """Silent truncation reads as "all done" when it isn't."""
        mock_get.return_value = {"status": "PENDING"}

        messages_shown = " ".join(self._run_action())

        self.assertIn("not checked", messages_shown)
        self.assertIn("CRUSH-EVT-BULK-2", messages_shown)

    @override_settings(SUMUP_ADMIN_RECHECK_BUDGET_SECONDS=-1)
    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_an_exhausted_time_budget_stops_it_too(self, mock_get):
        mock_get.return_value = {"status": "PENDING"}

        messages_shown = " ".join(self._run_action())

        mock_get.assert_not_called()
        self.assertIn("not checked", messages_shown)


class PaymentAdminChangelistTests(SiteTestMixin, TestCase):
    """The changelist has to find and render staff-opened payments cheaply.

    Both of these are about the same mismatch: ``PaymentTransaction.user`` is
    whoever *opened* the checkout, which for a staff-opened one is the staff
    account — while the Buyer column deliberately shows the member the payment
    is for.
    """

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username="staff@crush.lu",
            email="staff@crush.lu",
            password="password123",
            is_staff=True,
            is_superuser=True,
        )
        self.member = User.objects.create_user(
            username="member@crush.lu",
            email="member@crush.lu",
            password="password123",
        )
        self.event = MeetupEvent.objects.create(
            title="Changelist",
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
            user=self.member, event=self.event, status="pending"
        )
        # Staff opened it on the member's behalf: user=staff, buyer=member.
        self.tx = PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-STAFFOPENED",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_STAFFOPENED",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.staff,
            event_registration=self.registration,
        )

    def _admin(self):
        from crush_lu.admin.payments import PaymentTransactionAdmin
        from crush_lu.admin.site import crush_admin_site

        return PaymentTransactionAdmin(PaymentTransaction, crush_admin_site)

    def test_it_is_findable_by_the_buyer_shown_in_the_column(self):
        """Searching the email printed on screen has to return the row.

        It didn't: search_fields covered only ``user``, so the payments most
        likely to need looking up were the ones that could not be found.
        """
        from django.test import RequestFactory

        admin_obj = self._admin()
        request = RequestFactory().get("/crush-admin/")
        request.user = self.staff

        queryset, _unused = admin_obj.get_search_results(
            request, admin_obj.get_queryset(request), "member@crush.lu"
        )

        self.assertIn(self.tx, queryset)

    def test_rendering_a_row_costs_no_extra_queries(self):
        """The display methods walk relations list_display cannot reveal.

        Without select_related this was a handful of queries per row, which on
        a full page is several hundred.
        """
        from django.test import RequestFactory

        admin_obj = self._admin()
        request = RequestFactory().get("/crush-admin/")
        request.user = self.staff

        row = admin_obj.get_queryset(request).get(pk=self.tx.pk)
        with self.assertNumQueries(0):
            admin_obj.get_buyer(row)
            admin_obj.get_bought(row)


class SumUpCheckoutStatusCommandTests(SiteTestMixin, TestCase):
    """The command an operator reaches for when SumUp says "Échec".

    It had no coverage at all, and it is the one piece of this that gets run
    by hand against production, where a wrong answer is acted on.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="cli@crush.lu", email="cli@crush.lu", password="password123"
        )
        self.event = MeetupEvent.objects.create(
            title="CLI",
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
            transaction_reference="CRUSH-EVT-CLI",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_CLI",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.FAILED,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

    def _run(self, **kwargs):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command(
            "sumup_checkout_status", reference="CRUSH-EVT-CLI", stdout=out, **kwargs
        )
        return out.getvalue()

    @patch("crush_lu.services.sumup.SumUpClient.get_checkout")
    def test_sync_gives_an_old_failure_its_reason(self, mock_get):
        """The point of running this: explaining payments that already failed.

        Rows that failed before failure_reason existed are exactly the ones
        someone is staring at, and the normal sync path refuses to touch them.
        """
        mock_get.return_value = {
            "id": "CHK_CLI",
            "status": "FAILED",
            "transactions": [{"status": "FAILED", "failure_reason": "DO_NOT_HONOR"}],
        }

        output = self._run(sync=True)

        self.tx.refresh_from_db()
        self.assertIn("DO_NOT_HONOR", self.tx.failure_reason)
        self.assertIn("detail recorded", output)
        # Status is not the refresh path's to change.
        self.assertEqual(self.tx.status, PaymentTransaction.Status.FAILED)

    @patch("crush_lu.services.sumup.SumUpClient.get_checkout")
    def test_it_shouts_when_sumup_says_paid_but_we_say_failed(self, mock_get):
        """Money taken with nothing delivered. Never resolve this quietly."""
        mock_get.return_value = {"id": "CHK_CLI", "status": "PAID"}

        output = self._run(sync=True)

        self.registration.refresh_from_db()
        self.assertIn("check whether this member was charged", output)
        self.assertFalse(self.registration.payment_confirmed)

    @patch("crush_lu.services.sumup.SumUpClient.get_checkout")
    def test_an_unreachable_provider_is_said_out_loud(self, mock_get):
        """Could-not-ask and nothing-to-report must not look the same.

        Narrow but real: the command reads the checkout once to print it and
        again to record it, so the provider can be there for the first and gone
        for the second. That second failure used to return in silence.
        """
        mock_get.side_effect = [
            {"id": "CHK_CLI", "status": "FAILED"},
            SumUpError("boom"),
        ]

        output = self._run(sync=True)

        self.assertIn("could not reach SumUp", output)

    @patch("crush_lu.services.sumup.SumUpClient.get_checkout")
    def test_a_swallowed_second_read_is_not_reported_as_unchanged(self, mock_get):
        """The command reads twice; the second read has its own failure mode.

        The reconciliation deliberately re-reads rather than trusting a payload
        handed to it, so --sync makes a second call whose error is swallowed
        inside. Unchecked, the command printed "synced unchanged (pending)"
        directly beneath a dump showing SumUp reported the checkout PAID —
        announcing that the repair had been considered and declined when it had
        never been attempted.
        """
        PaymentTransaction.objects.filter(pk=self.tx.pk).update(
            status=PaymentTransaction.Status.PENDING
        )
        mock_get.side_effect = [
            {"id": "CHK_CLI", "status": "PAID"},
            SumUpError("gone for the second read"),
        ]

        output = self._run(sync=True)

        self.assertIn("NOT synced", output)
        self.assertNotIn("unchanged", output)

    @patch("crush_lu.services.sumup.SumUpClient.get_checkout")
    def test_a_paid_row_sumup_agrees_about_is_not_flagged(self, mock_get):
        """The sibling of the admin bug — fixed there, still live here.

        Shouting "check whether this member was charged" at two records that
        agree makes a routine refresh look like a financial anomaly.
        """
        PaymentTransaction.objects.filter(pk=self.tx.pk).update(
            status=PaymentTransaction.Status.PAID
        )
        mock_get.return_value = {"id": "CHK_CLI", "status": "PAID"}

        output = self._run(sync=True)

        self.assertNotIn("check whether this member was charged", output)
        self.assertIn("detail recorded", output)

    def test_sync_without_a_selector_is_refused_not_ignored(self):
        """--help promises it applies things; listing mode never asks SumUp.

        So --sync on a bare listing quietly applied nothing at all. Refusing
        beats syncing the recent rows: that would fire a provider call per row
        and could confirm seats nobody asked about, off a command someone ran
        just to look around.
        """
        from io import StringIO

        from django.core.management import CommandError, call_command

        with self.assertRaises(CommandError) as caught:
            call_command("sumup_checkout_status", sync=True, stdout=StringIO())

        self.assertIn("--sync needs one payment to act on", str(caught.exception))

    @patch("crush_lu.services.sumup.SumUpClient.get_checkout")
    def test_it_prints_the_attempts_without_being_asked_to_sync(self, mock_get):
        """Read-only by default — looking must never change anything."""
        mock_get.return_value = {
            "id": "CHK_CLI",
            "status": "FAILED",
            "transactions": [{"status": "FAILED", "failure_reason": "LOST_CARD"}],
        }

        output = self._run()

        self.assertIn("LOST_CARD", output)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.failure_reason, "")


class RecheckActionHonestyTests(SiteTestMixin, TestCase):
    """The action must never report an outage as a successful verification."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="honest@crush.lu",
            email="honest@crush.lu",
            password="password123",
            is_staff=True,
            is_superuser=True,
        )
        self.event = MeetupEvent.objects.create(
            title="Honesty",
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
            transaction_reference="CRUSH-EVT-HONEST",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_HONEST",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

    def _admin(self):
        from crush_lu.admin.payments import PaymentTransactionAdmin
        from crush_lu.admin.site import crush_admin_site

        return PaymentTransactionAdmin(PaymentTransaction, crush_admin_site)

    def _run_action(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        request = RequestFactory().post("/crush-admin/")
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)

        self._admin().recheck_with_sumup(request, PaymentTransaction.objects.all())
        return [str(message) for message in request._messages]

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_an_outage_is_not_reported_as_a_clean_check(self, mock_get):
        """Both helpers swallow SumUpError, so the try/except never fires.

        A coach was told every row had been verified when not one had been
        asked about — a false all-clear on money.
        """
        mock_get.side_effect = SumUpError("SumUp is down")

        messages_shown = " ".join(self._run_action())

        self.assertIn("no answer from SumUp", messages_shown)
        self.assertIn("NOT checked", messages_shown)
        self.assertNotIn("Re-checked 1 transaction", messages_shown)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_real_check_still_reports_success(self, mock_get):
        mock_get.return_value = {"id": "CHK_HONEST", "status": "PENDING"}

        messages_shown = " ".join(self._run_action())

        self.assertIn("Re-checked 1 transaction", messages_shown)

    def test_the_action_survives_the_read_only_admin(self):
        """A read-only ModelAdmin must not hide its one action.

        Django only filters an action by permission when the action declares
        one via ``permissions=``; an action without ``allowed_permissions`` is
        kept regardless of has_change_permission. Pinned because the fix for a
        misreading of that rule — adding permissions=["change"] — would silently
        remove the action from a screen that denies change permission by design.
        """
        from django.test import RequestFactory

        request = RequestFactory().get("/crush-admin/")
        request.user = self.user
        admin_obj = self._admin()

        self.assertFalse(admin_obj.has_change_permission(request))
        self.assertIn("recheck_with_sumup", admin_obj.get_actions(request))


class RefreshAndReportHonestyTests(SiteTestMixin, TestCase):
    """Two ways a refresh can mislead the person reading it."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="refresh@crush.lu",
            email="refresh@crush.lu",
            password="password123",
            is_staff=True,
            is_superuser=True,
        )
        self.event = MeetupEvent.objects.create(
            title="Refresh",
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

    def _tx(self, status, reason=""):
        return PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-EVT-REF-{status}",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=f"CHK_REF_{status}",
            amount=Decimal("1.00"),
            currency="EUR",
            status=status,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
            failure_reason=reason,
        )

    def _run_action(self, queryset):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.payments import PaymentTransactionAdmin
        from crush_lu.admin.site import crush_admin_site

        request = RequestFactory().post("/crush-admin/")
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)

        PaymentTransactionAdmin(
            PaymentTransaction, crush_admin_site
        ).recheck_with_sumup(request, queryset)
        return " ".join(str(message) for message in request._messages)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_paid_row_that_sumup_also_calls_paid_is_not_an_anomaly(self, mock_get):
        """The most ordinary thing this action can find.

        Flagging it as "check whether this member was charged" would train
        coaches to ignore the warning that actually matters.
        """
        tx = self._tx(PaymentTransaction.Status.PAID)
        mock_get.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}

        messages_shown = self._run_action(PaymentTransaction.objects.filter(pk=tx.pk))

        self.assertNotIn("check whether this member was charged", messages_shown)
        self.assertIn("refreshed from SumUp", messages_shown)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_written_off_row_that_sumup_calls_paid_still_shouts(self, mock_get):
        """The case the warning exists for must survive the fix above."""
        tx = self._tx(PaymentTransaction.Status.FAILED)
        mock_get.return_value = {"id": tx.sumup_checkout_id, "status": "PAID"}

        messages_shown = self._run_action(PaymentTransaction.objects.filter(pk=tx.pk))

        self.assertIn("check whether this member was charged", messages_shown)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_refreshing_a_superseded_row_keeps_our_own_account_of_it(self, mock_get):
        """SumUp cannot know we deactivated it — it just says FAILED.

        Taking its wording would erase the one sentence separating a checkout
        we killed from a card a bank refused, which is why CANCELLED exists.
        """
        from crush_lu.views_payments import refresh_sumup_snapshot

        ours = "Superseded by a newer checkout — the member re-opened the page."
        tx = self._tx(PaymentTransaction.Status.CANCELLED, reason=ours)
        mock_get.return_value = {
            "id": tx.sumup_checkout_id,
            "status": "FAILED",
            "transactions": [],
        }

        refresh_sumup_snapshot(tx)

        tx.refresh_from_db()
        self.assertEqual(tx.failure_reason, ours)
        # The snapshot itself still refreshes; only the wording is ours to keep.
        self.assertEqual(tx.raw_response["status"], "FAILED")


class ThrottleFailureModeTests(TestCase):
    """The throttle guards a quota; the read it gates is how someone gets a seat."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()

    def test_an_unavailable_cache_does_not_block_the_provider_read(self):
        """django-redis with IGNORE_EXCEPTIONS returns None, not an exception.

        None is falsy, so "we lost the rate limiter" became "refuse every
        provider read" — a member who had just completed 3DS would sit on the
        polling screen for the whole five-minute window while SumUp had the
        payment marked paid the entire time.
        """
        from crush_lu.views_payments import _may_ask_sumup

        with patch("crush_lu.views_payments.cache.add", return_value=None):
            self.assertTrue(_may_ask_sumup("poll", "CHK_NOCACHE"))

    def test_a_genuine_throttle_hit_is_still_a_no(self):
        """Failing open must not mean never throttling."""
        from crush_lu.views_payments import _may_ask_sumup

        self.assertTrue(_may_ask_sumup("poll", "CHK_REAL"))
        self.assertFalse(_may_ask_sumup("poll", "CHK_REAL"))


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class WidgetNoteRaceTests(SiteTestMixin, TestCase):
    """The widget note is a second write, and needed its own guard."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()
        self.user = User.objects.create_user(
            username="race@crush.lu", email="race@crush.lu", password="password123"
        )
        self.event = MeetupEvent.objects.create(
            title="Race",
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
            transaction_reference="CRUSH-EVT-RACE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_RACE",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

    def test_a_note_cannot_land_on_a_payment_that_completed_first(self):
        """Locking only the provider-summary write closed half the hole."""
        from crush_lu.views_payments import _append_widget_note

        PaymentTransaction.objects.filter(pk=self.tx.pk).update(
            status=PaymentTransaction.Status.PAID, failure_reason=""
        )

        recorded = _append_widget_note(self.tx, "Card widget reported 'fail'")

        self.assertFalse(recorded)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.failure_reason, "")

    def test_a_note_on_a_still_pending_payment_is_kept(self):
        from crush_lu.views_payments import _append_widget_note

        self.assertTrue(_append_widget_note(self.tx, "Card widget reported 'fail'"))

        self.tx.refresh_from_db()
        self.assertIn("Card widget reported", self.tx.failure_reason)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DeclineDoesNotHangThePageTests(SiteTestMixin, TestCase):
    """A refused card must not leave the page spinning.

    This is the bug the whole polling change exists to prevent, in the one
    shape it did not cover: SumUp keeps a checkout PENDING after a decline so
    another card can be tried, so "settled" stays false — and if the SDK went
    quiet (exactly why the poll exists) the customer watched "Confirming your
    payment" for the full five-minute window over a card refused seconds after
    the 3DS screen.
    """

    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="hang@crush.lu", email="hang@crush.lu", password="password123"
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.event = MeetupEvent.objects.create(
            title="Hang",
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
            transaction_reference="CRUSH-EVT-HANG",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_HANG",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        self.url = reverse(
            "crush_lu:sumup_widget_status", kwargs={"checkout_id": "CHK_HANG"}
        )
        self.client.force_login(self.user)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_declined_card_tells_the_page_to_stop_waiting(self, mock_get):
        mock_get.return_value = {
            "id": "CHK_HANG",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }

        body = self.client.get(self.url).json()

        # Not settled — the checkout is still payable, and must stay that way.
        self.assertFalse(body["settled"])
        self.assertEqual(body["status"], PaymentTransaction.Status.PENDING)
        # But the page now knows to stop and let them try another card.
        self.assertTrue(body["attempt_failed"])

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_an_ordinary_wait_does_not_look_like_a_decline(self, mock_get):
        """Nobody has paid yet is not the same as somebody was refused."""
        mock_get.return_value = {"id": "CHK_HANG", "status": "PENDING"}

        body = self.client.get(self.url).json()

        self.assertFalse(body["settled"])
        self.assertFalse(body["attempt_failed"])

    def test_the_widget_page_reacts_to_a_failed_attempt(self):
        """It acts on the count rising past what it has already shown.

        Not on the server's "is anything on record" flag, which stays true
        across a retry and would stop the page watching a second card.
        """
        response = self.client.get(
            reverse("crush_lu:sumup_widget", kwargs={"checkout_id": "CHK_HANG"})
        )

        self.assertContains(response, "acknowledgedFailures")
        self.assertContains(response, "attempt_marker")

    def _widget_script(self):
        response = self.client.get(
            reverse("crush_lu:sumup_widget", kwargs={"checkout_id": "CHK_HANG"})
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_showing_a_decline_does_not_stop_the_watch(self):
        """The one invariant that keeps a retry from stranding somebody.

        A refused card leaves the checkout payable, so the customer's next card
        goes into the widget still on screen — and if that one goes behind a 3DS
        challenge the SDK never speaks again. Tearing the watch down when a
        decline is displayed means the retry that SUCCEEDS is never heard: money
        taken, seat not granted, page showing an error. That is the original
        hang, re-entered through the door marked "handled".
        """
        body = self._widget_script()

        # The poll's decline branch.
        branch = body.split("if (failures > acknowledgedFailures) {", 1)[1].split(
            "scheduleNextPoll();", 1
        )[0]
        self.assertNotIn("stopWatching", branch)

        # And the SDK's own, which reports the same kind of refusal.
        sdk = body.split('if (type === "error"', 1)[1].split("reportFailure(", 1)[0]
        self.assertNotIn("stopWatching", sdk)

    def test_no_flag_tries_to_guess_which_refusal_a_rise_is(self):
        """Three designs died here; none of them could have worked.

        Nothing on the page can tell SumUp catching up on the refusal the SDK
        just displayed from a second card refused since — they are the same
        event from the page's side. A flag that guessed wrong one way announced
        a stale decline over a card still in flight; wrong the other way it
        swallowed a real one and spun to the five-minute deadline. Showing is
        idempotent now, so there is nothing left to guess and nothing to carry
        between attempts.
        """
        self.assertNotIn("absorbNextFailure", self._widget_script())


class TerminalWriteRaceTests(SiteTestMixin, TestCase):
    """The last unlocked write, and the one that moved `status`."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="term@crush.lu", email="term@crush.lu", password="password123"
        )
        self.event = MeetupEvent.objects.create(
            title="Terminal",
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
            transaction_reference="CRUSH-EVT-TERM",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_TERM",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

    def test_a_late_failure_cannot_undo_a_supersession(self):
        """Deactivating a checkout makes SumUp report it FAILED.

        So a poll still in flight when the member opens a replacement would
        reset the row from CANCELLED back to FAILED and replace our own account
        of the supersession with a generic one — losing the distinction the
        CANCELLED status exists to record.
        """
        from crush_lu.views_payments import _record_terminal_failure

        ours = "Superseded by a newer checkout — the member re-opened the page."
        PaymentTransaction.objects.filter(pk=self.tx.pk).update(
            status=PaymentTransaction.Status.CANCELLED, failure_reason=ours
        )

        wrote = _record_terminal_failure(
            self.tx, "FAILED", {"id": "CHK_TERM", "status": "FAILED"}
        )

        self.assertFalse(wrote)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.CANCELLED)
        self.assertEqual(self.tx.failure_reason, ours)

    def test_a_genuine_terminal_failure_still_closes_the_row(self):
        from crush_lu.views_payments import _record_terminal_failure

        wrote = _record_terminal_failure(
            self.tx,
            "FAILED",
            {
                "id": "CHK_TERM",
                "status": "FAILED",
                "transactions": [{"status": "FAILED", "failure_reason": "LOST_CARD"}],
            },
        )

        self.assertTrue(wrote)
        self.tx.refresh_from_db()
        self.assertEqual(self.tx.status, PaymentTransaction.Status.FAILED)
        self.assertIn("LOST_CARD", self.tx.failure_reason)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class RetryAfterDeclineTests(SiteTestMixin, TestCase):
    """A second card must not be judged by the first card's failure.

    failure_reason is sticky for a pending checkout's whole life and SumUp's
    transactions array is cumulative, so "a failure is on record" stays true
    right through a retry. Acting on that alone stopped watching the second
    card seconds after it was submitted and called it failed while the bank was
    still deciding — this change's own bug, on the retry path.
    """

    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="retry@crush.lu", email="retry@crush.lu", password="password123"
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.event = MeetupEvent.objects.create(
            title="Retry",
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
            transaction_reference="CRUSH-EVT-RETRY",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_RETRY",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        self.url = reverse(
            "crush_lu:sumup_widget_status", kwargs={"checkout_id": "CHK_RETRY"}
        )
        self.client.force_login(self.user)

    def _poll(self):
        from django.core.cache import cache

        # The throttle is not what this is testing.
        cache.clear()
        return self.client.get(self.url).json()

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_an_unresolved_retry_carries_the_first_declines_marker(self, mock_get):
        """The marker must not move while the second card is still in flight.

        The page baselines on the marker when a new attempt starts, so an
        unchanged marker is what keeps it watching instead of bailing.
        """
        declined = {
            "id": "CHK_RETRY",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }
        mock_get.return_value = declined
        first = self._poll()
        self.assertTrue(first["attempt_failed"])
        self.assertTrue(first["attempt_marker"])

        # Card #2 submitted; its 3DS is still running, so SumUp still reports
        # only card #1's attempt.
        during_retry = self._poll()

        self.assertEqual(during_retry["attempt_marker"], first["attempt_marker"])

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_second_decline_moves_the_marker(self, mock_get):
        """And a genuinely new refusal must still reach the customer."""
        mock_get.return_value = {
            "id": "CHK_RETRY",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }
        first = self._poll()

        mock_get.return_value = {
            "id": "CHK_RETRY",
            "status": "PENDING",
            "transactions": [
                {"status": "FAILED", "failure_reason": "DECLINED"},
                {"status": "FAILED", "failure_reason": "INSUFFICIENT_FUNDS"},
            ],
        }
        second = self._poll()

        self.assertTrue(second["attempt_failed"])
        self.assertNotEqual(second["attempt_marker"], first["attempt_marker"])

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_widget_reports_never_move_the_count(self, mock_get):
        """The count is SumUp's alone, however often the widget reports.

        Counting reports made a duplicated SDK callback — or an owner posting
        twice — look like a second refused card. The server cannot tell one
        from the other, so it no longer tries: reconciling a failure the page
        already displayed is the page's job, since only the page knows.
        """
        from crush_lu.views_payments import attempt_marker

        mock_get.return_value = {"id": "CHK_RETRY", "status": "PENDING"}
        url = reverse(
            "crush_lu:sumup_widget_failure", kwargs={"checkout_id": "CHK_RETRY"}
        )

        for _unused in range(3):
            self.client.post(
                url,
                data=json.dumps({"type": "fail", "message": "Declined"}),
                content_type="application/json",
            )

        self.tx.refresh_from_db()
        self.assertEqual(attempt_marker(self.tx), "")
        # The notes are all still on the record for whoever reads it later.
        self.assertEqual(self.tx.failure_reason.count("Card widget reported"), 3)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_provider_record_arriving_late_does_not_double_count(self, mock_get):
        """SumUp catching up on a reported decline is one card, not two."""
        mock_get.return_value = {"id": "CHK_RETRY", "status": "PENDING"}
        self.client.post(
            reverse(
                "crush_lu:sumup_widget_failure", kwargs={"checkout_id": "CHK_RETRY"}
            ),
            data=json.dumps({"type": "fail", "message": "Declined"}),
            content_type="application/json",
        )

        mock_get.return_value = {
            "id": "CHK_RETRY",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }

        self.assertEqual(self._poll()["attempt_marker"], "1")

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_second_refused_card_moves_the_count(self, mock_get):
        """And a genuinely new refusal must still reach the customer."""
        mock_get.return_value = {
            "id": "CHK_RETRY",
            "status": "PENDING",
            "transactions": [{"status": "FAILED"}],
        }
        self.assertEqual(self._poll()["attempt_marker"], "1")

        mock_get.return_value = {
            "id": "CHK_RETRY",
            "status": "PENDING",
            "transactions": [{"status": "FAILED"}, {"status": "FAILED"}],
        }

        self.assertEqual(self._poll()["attempt_marker"], "2")

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_retry_that_succeeds_still_settles(self, mock_get):
        """The outcome the bug destroyed: told it failed, then paid anyway."""
        mock_get.return_value = {
            "id": "CHK_RETRY",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }
        self._poll()

        mock_get.return_value = {
            "id": "CHK_RETRY",
            "status": "PAID",
            "transactions": [
                {"status": "FAILED", "failure_reason": "DECLINED"},
                {"status": "SUCCESSFUL"},
            ],
        }
        with self.captureOnCommitCallbacks(execute=True):
            settled = self._poll()

        self.assertTrue(settled["settled"])
        self.assertTrue(settled["paid"])
        self.assertFalse(settled["attempt_failed"])
        self.registration.refresh_from_db()
        self.assertTrue(self.registration.payment_confirmed)

    def test_the_baseline_is_served_with_the_page_not_fetched(self):
        """It has to be true by construction, not by winning a race.

        Fetching it cannot be made safe however early it starts: the status
        endpoint does a live provider read, so a slow one can answer with state
        recorded AFTER a card was submitted, and the page would file that very
        failure as already-seen and never report it. Rendered with the HTML, it
        is the record as it stood before the page existed.
        """
        from crush_lu.views_payments import attempt_marker

        PaymentTransaction.objects.filter(pk=self.tx.pk).update(
            failure_reason="SumUp reports the checkout as PENDING; attempt FAILED",
            raw_response={
                "id": "CHK_RETRY",
                "status": "PENDING",
                "transactions": [{"status": "FAILED"}],
            },
        )
        self.tx.refresh_from_db()
        self.assertTrue(attempt_marker(self.tx))

        response = self.client.get(
            reverse("crush_lu:sumup_widget", kwargs={"checkout_id": "CHK_RETRY"})
        )

        self.assertContains(response, attempt_marker(self.tx))
        body = response.content.decode()
        seed = body.split("let acknowledgedFailures =", 1)[1].split(";", 1)[0]
        self.assertIn(attempt_marker(self.tx), seed)
        # And nothing fetches it — a fetched baseline is the bug above.
        watch = body.split("function watchCheckout()", 1)[1].split("function ", 1)[0]
        self.assertNotIn("attempt_marker", watch)

    def test_a_page_with_no_failure_yet_seeds_an_empty_baseline(self):
        response = self.client.get(
            reverse("crush_lu:sumup_widget", kwargs={"checkout_id": "CHK_RETRY"})
        )

        body = response.content.decode()
        seed = body.split("let acknowledgedFailures =", 1)[1].split(";", 1)[0]
        self.assertIn('""', seed)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_the_failure_report_agrees_with_the_poll(self, mock_get):
        """The report's marker must be what a poll would say for the same state.

        The SDK error path shows a failure the poll never told the page about,
        so the page takes the marker from its own report — and the two have to
        agree, or the next poll looks like a fresh refusal.
        """
        mock_get.return_value = {"id": "CHK_RETRY", "status": "PENDING"}

        response = self.client.post(
            reverse(
                "crush_lu:sumup_widget_failure", kwargs={"checkout_id": "CHK_RETRY"}
            ),
            data=json.dumps({"type": "fail", "message": "Declined"}),
            content_type="application/json",
        )

        self.assertEqual(
            self._poll()["attempt_marker"], response.json()["attempt_marker"]
        )

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_widget_note_never_moves_the_marker(self, mock_get):
        """The deterministic bug, not a timing window.

        The note was appended to the same field the marker hashed, so recording
        what the customer had been shown moved the marker — and the next
        refresh, re-deriving SumUp's half, moved it back. Either way the page
        read a refreshed record as a newly refused card and stopped watching a
        retry that was still in flight.
        """
        mock_get.return_value = {
            "id": "CHK_RETRY",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }
        before = self._poll()["attempt_marker"]
        self.assertTrue(before)

        self.client.post(
            reverse(
                "crush_lu:sumup_widget_failure", kwargs={"checkout_id": "CHK_RETRY"}
            ),
            data=json.dumps({"type": "fail", "message": "Declined"}),
            content_type="application/json",
        )
        after_note = self._poll()["attempt_marker"]

        # And a refresh that learns nothing new must not move it back.
        after_refresh = self._poll()["attempt_marker"]

        self.assertEqual(after_note, before)
        self.assertEqual(after_refresh, before)
        # The note is still on the record for whoever reads it later.
        self.tx.refresh_from_db()
        self.assertIn("Card widget reported", self.tx.failure_reason)
        self.assertIn("DECLINED", self.tx.failure_reason)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ErrorAfterCaptureTests(SiteTestMixin, TestCase):
    """An SDK error raised after the money was taken must not read as a decline."""

    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="late@crush.lu", email="late@crush.lu", password="password123"
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.event = MeetupEvent.objects.create(
            title="Late",
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
            transaction_reference="CRUSH-EVT-LATE",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_LATE",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        self.client.force_login(self.user)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_the_report_answers_with_the_payment_that_actually_happened(self, mock_get):
        """The page has stopped polling by the time it reports, so this reply
        is the last thing it hears. Saying only "recorded" left a paying
        customer looking at a decline with nothing left running to fix it."""
        mock_get.return_value = {"id": "CHK_LATE", "status": "PAID"}

        with self.captureOnCommitCallbacks(execute=True):
            body = self.client.post(
                reverse(
                    "crush_lu:sumup_widget_failure",
                    kwargs={"checkout_id": "CHK_LATE"},
                ),
                data=json.dumps({"type": "error", "message": "network blip"}),
                content_type="application/json",
            ).json()

        self.assertTrue(body["settled"])
        self.assertTrue(body["paid"])
        self.registration.refresh_from_db()
        self.assertTrue(self.registration.payment_confirmed)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_genuine_decline_still_reports_unsettled(self, mock_get):
        mock_get.return_value = {
            "id": "CHK_LATE",
            "status": "PENDING",
            "transactions": [{"status": "FAILED", "failure_reason": "DECLINED"}],
        }

        body = self.client.post(
            reverse(
                "crush_lu:sumup_widget_failure", kwargs={"checkout_id": "CHK_LATE"}
            ),
            data=json.dumps({"type": "fail", "message": "Declined"}),
            content_type="application/json",
        ).json()

        self.assertFalse(body["settled"])
        self.assertFalse(body["paid"])

    def test_the_page_leaves_when_its_own_report_comes_back_settled(self):
        response = self.client.get(
            reverse("crush_lu:sumup_widget", kwargs={"checkout_id": "CHK_LATE"})
        )

        body = response.content.decode()
        # The whole of reportFailure, not up to the next "function" — it has
        # inline callbacks of its own — and not a fixed slice either, which
        # silently stopped covering the tail the first time a comment grew.
        report = body.split("function reportFailure(", 1)[1].split(
            "if (typeof SumUpCard", 1
        )[0]
        self.assertIn("data.settled", report)
        self.assertIn("returnUrl", report)

    def test_the_report_answer_never_moves_the_poll_baseline(self):
        """It describes when it was read, not when it was asked.

        This request goes out the instant the SDK reports a decline, and the
        provider read behind it takes seconds — long enough for the customer to
        enter a second card. A response landing after SumUp recorded THAT card's
        refusal counts it too, and taking the number here would mark a decline
        as seen that nobody has seen. The poll would then find no rise, say
        nothing, and spin to the five-minute deadline.

        Nothing is lost by leaving it alone: the poll finds that record itself a
        few seconds later, while the message is still on screen, and the guard
        against overwriting a displayed message absorbs it.
        """
        response = self.client.get(
            reverse("crush_lu:sumup_widget", kwargs={"checkout_id": "CHK_LATE"})
        )

        report = (
            response.content.decode()
            .split("function reportFailure(", 1)[1]
            .split("if (typeof SumUpCard", 1)[0]
        )
        # The word may appear in the comment saying why not; an assignment
        # may not.
        self.assertNotIn("acknowledgedFailures =", report)


class RecheckMismatchDirectionTests(SiteTestMixin, TestCase):
    """Both directions of a money/entitlement mismatch deserve the same alarm."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="mismatch@crush.lu",
            email="mismatch@crush.lu",
            password="password123",
            is_staff=True,
            is_superuser=True,
        )
        self.event = MeetupEvent.objects.create(
            title="Mismatch",
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
            transaction_reference="CRUSH-EVT-MISMATCH",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_MISMATCH",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

    def _run_action(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        from crush_lu.admin.payments import PaymentTransactionAdmin
        from crush_lu.admin.site import crush_admin_site

        request = RequestFactory().post("/crush-admin/")
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)

        PaymentTransactionAdmin(
            PaymentTransaction, crush_admin_site
        ).recheck_with_sumup(request, PaymentTransaction.objects.all())
        return " ".join(str(message) for message in request._messages)

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_a_paid_row_sumup_calls_failed_is_flagged(self, mock_get):
        """A seat granted against a checkout that never settled.

        The mirror of "money taken, nothing delivered", and it used to pass as
        an ordinary refresh.
        """
        mock_get.return_value = {"id": "CHK_MISMATCH", "status": "FAILED"}

        self.assertIn("check whether this member was charged", self._run_action())

    @patch("crush_lu.views_payments.SumUpClient.get_checkout")
    def test_agreement_stays_quiet(self, mock_get):
        mock_get.return_value = {"id": "CHK_MISMATCH", "status": "PAID"}

        self.assertNotIn("check whether this member was charged", self._run_action())


class CommandSelectorAndMismatchTests(SiteTestMixin, TestCase):
    """Two ways the status command could mislead the operator running it."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="sel@crush.lu", email="sel@crush.lu", password="password123"
        )
        self.event = MeetupEvent.objects.create(
            title="Selector",
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
            transaction_reference="CRUSH-EVT-SEL",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_SEL",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

    def _run(self, **kwargs):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("sumup_checkout_status", stdout=out, **kwargs)
        return out.getvalue()

    def test_two_selectors_are_refused_rather_than_one_silently_winning(self):
        """The lookup takes the first and ignores the rest.

        With --sync that means reconciling the payment named by whichever won,
        while the operator believes the others narrowed it.
        """
        from django.core.management import CommandError

        with self.assertRaises(CommandError) as caught:
            self._run(reference="CRUSH-EVT-SEL", registration_id=self.registration.id)

        self.assertIn("Give one selector", str(caught.exception))

    @patch("crush_lu.services.sumup.SumUpClient.get_checkout")
    def test_a_paid_row_sumup_calls_failed_is_flagged(self, mock_get):
        """The mirror mismatch, in the command as well as the admin.

        A seat granted against a checkout that never settled — it used to print
        as an ordinary refresh here after the admin had been fixed.
        """
        mock_get.return_value = {"id": "CHK_SEL", "status": "FAILED"}

        output = self._run(reference="CRUSH-EVT-SEL", sync=True)

        self.assertIn("check whether this member was charged", output)

    @patch("crush_lu.services.sumup.SumUpClient.get_checkout")
    def test_agreement_stays_quiet(self, mock_get):
        mock_get.return_value = {"id": "CHK_SEL", "status": "PAID"}

        output = self._run(reference="CRUSH-EVT-SEL", sync=True)

        self.assertNotIn("check whether this member was charged", output)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class WidgetScriptEscapingTests(SiteTestMixin, TestCase):
    """Values interpolated into the page's JavaScript are escaped for JS.

    A <script> block is raw text: HTML escaping is both wrong there (entities
    are never decoded) and insufficient (it does not neutralise a quote or a
    backslash for a JS string). ``{% trans %}`` on a quoted literal returns
    SafeString, so without escapejs nothing escapes these at all.
    """

    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="esc@crush.lu", email="esc@crush.lu", password="password123"
        )
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.event = MeetupEvent.objects.create(
            title="Escaping",
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
        PaymentTransaction.objects.create(
            transaction_reference="CRUSH-EVT-ESC",
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id="CHK_ESC",
            amount=Decimal("1.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )
        self.client.force_login(self.user)

    def _script(self, language):
        with translation.override(language):
            response = self.client.get(
                reverse("crush_lu:sumup_widget", kwargs={"checkout_id": "CHK_ESC"}),
                HTTP_ACCEPT_LANGUAGE=language,
            )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_no_html_entity_reaches_the_script(self):
        """Entities are not decoded in raw text — one would be read literally."""
        for language in ("en", "fr", "de"):
            body = self._script(language)
            line = [row for row in body.splitlines() if "const defaultError" in row][0]
            self.assertNotIn("&#", line, f"{language}: HTML entity in a JS string")
            self.assertNotIn("&amp;", line)

    def test_the_error_text_is_the_translated_one(self):
        body = self._script("fr")
        self.assertIn("Le paiement", body)


class DonationCheckoutTests(SiteTestMixin, TestCase):
    """Opening a voluntary support checkout.

    The amount is the one thing here a member types, and it goes straight to a
    payment provider — so most of this is about what the view refuses.
    """

    def setUp(self):
        super().setUp()
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.user = User.objects.create_user(
            username="donor@crush.lu", email="donor@crush.lu", password="password123"
        )
        from crush_lu.models.profiles import UserDataConsent

        # Without this the consent middleware redirects every request here,
        # and each assertion below would be checking a 302 instead of the view.
        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        # The literal path, not reverse(). On crush.lu the routing middleware
        # swaps in the urls_crush urlconf, where this route is registered
        # un-prefixed -- so the namespaced /crush/... form reverse() builds
        # against ROOT_URLCONF 404s here. This is the path the support card
        # hardcodes and the one production serves.
        self.url = "/payments/sumup/create-donation-checkout/"
        self.client.force_login(self.user)
        # The create-checkout cooldown lives in the cache, which outlives the
        # per-test database rollback -- without this, whichever test ran first
        # would throttle the rest.
        cache.clear()

    def _post(self, amount):
        return self.client.post(
            self.url,
            data=json.dumps({"amount": amount}),
            content_type="application/json",
        )

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_opens_a_checkout_and_records_it_pending(self, mock_create):
        mock_create.return_value = {"id": "CHK_DON_123", "status": "PENDING"}

        response = self._post("10.00")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["checkout_id"], "CHK_DON_123")
        self.assertIn("/payments/sumup/widget/CHK_DON_123/", body["widget_url"])

        tx = PaymentTransaction.objects.get(sumup_checkout_id="CHK_DON_123")
        self.assertEqual(tx.amount, Decimal("10.00"))
        self.assertEqual(tx.purpose, PaymentTransaction.Purpose.DONATION)
        self.assertEqual(tx.status, PaymentTransaction.Status.PENDING)
        self.assertEqual(tx.user, self.user)
        # Nothing was bought, so neither link may be set — _payment_owner_ids
        # falls back to tx.user for exactly this shape of row.
        self.assertIsNone(tx.event_registration_id)
        self.assertIsNone(tx.premium_membership_id)

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_anonymous_visitors_cannot_open_one(self, mock_create):
        self.client.logout()

        response = self._post("10.00")

        self.assertIn(response.status_code, (302, 403))
        mock_create.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_refuses_an_amount_under_the_floor(self, mock_create):
        response = self._post("1.50")

        self.assertEqual(response.status_code, 400)
        mock_create.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_refuses_an_amount_over_the_ceiling(self, mock_create):
        """A slipped decimal point must not open a five-figure checkout."""
        response = self._post("50000.00")

        self.assertEqual(response.status_code, 400)
        mock_create.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_refuses_amounts_that_are_not_numbers(self, mock_create):
        for amount in ("abc", "", None, "10; DROP TABLE", []):
            with self.subTest(amount=amount):
                response = self._post(amount)
                self.assertEqual(response.status_code, 400)
        mock_create.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_refuses_the_decimal_literals_that_are_not_amounts(self, mock_create):
        """"Infinity" and "NaN" parse as Decimal — neither is a donation.

        Infinity is the dangerous one: no ``< min`` or ``> max`` comparison
        rejects it, so it would sail through to SumUp as ``inf``. NaN fails the
        other way — comparing it raises, which without the finite check would
        surface as a 500 rather than a 400.
        """
        for amount in ("Infinity", "-Infinity", "NaN"):
            with self.subTest(amount=amount):
                response = self._post(amount)
                self.assertEqual(response.status_code, 400)
        mock_create.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_a_provider_failure_leaves_no_transaction_behind(self, mock_create):
        mock_create.side_effect = SumUpError("SumUp is down")

        response = self._post("10.00")

        self.assertEqual(response.status_code, 500)
        self.assertFalse(PaymentTransaction.objects.exists())

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_one_donation_is_one_number_everywhere(self, mock_create):
        """Charged, shown and recorded must agree, to the cent.

        Un-quantized, 2.355 produced three different figures for one gift: the
        description said EUR 2.35, SumUp was handed 2.355, and the amount column
        (decimal_places=2) stored a rounded third.
        """
        mock_create.return_value = {"id": "CHK_DON_Q", "status": "PENDING"}

        response = self._post("2.355")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["amount"], 2.36)

        tx = PaymentTransaction.objects.get(sumup_checkout_id="CHK_DON_Q")
        self.assertEqual(tx.amount, Decimal("2.36"))

        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs["amount"], 2.36)
        self.assertIn("2.36", kwargs["description"])

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_rounding_up_to_the_floor_is_accepted(self, mock_create):
        """1.999 is charged as EUR 2.00, so it must not be judged against 1.999."""
        mock_create.return_value = {"id": "CHK_DON_F", "status": "PENDING"}

        response = self._post("1.999")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            PaymentTransaction.objects.get(sumup_checkout_id="CHK_DON_F").amount,
            Decimal("2.00"),
        )

    @override_settings(ANDROID_NATIVE_COMMERCE_ENABLED=False)
    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_a_native_shell_without_commerce_may_not_open_a_checkout(
        self, mock_create
    ):
        """Hiding the button does not close the endpoint the webview can reach.

        Apple and Google both treat payment taken outside their billing as
        grounds for rejection, so the gate _products.html applies to the Premium
        CTA has to hold on the server too.
        """
        response = self.client.post(
            f"{self.url}?source=android_app",
            data=json.dumps({"amount": "10.00"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        mock_create.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())

    @override_settings(ANDROID_NATIVE_COMMERCE_ENABLED=True)
    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_a_native_shell_with_commerce_enabled_may_open_one(self, mock_create):
        """The gate is about the commerce flag, not about being in the app."""
        mock_create.return_value = {"id": "CHK_DON_NATIVE", "status": "PENDING"}

        response = self.client.post(
            f"{self.url}?source=android_app",
            data=json.dumps({"amount": "10.00"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            PaymentTransaction.objects.filter(
                sumup_checkout_id="CHK_DON_NATIVE"
            ).exists()
        )

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_refuses_a_magnitude_quantize_cannot_represent(self, mock_create):
        """quantize() is not total, and it runs before the ceiling check.

        "1e30" parses, is finite, and would be caught by DONATION_MAX_EUR --
        except quantize() raises InvalidOperation first, which without its own
        guard is a 500 on an amount the endpoint means to refuse with a 400.
        """
        for amount in ("1e30", "1e400", "-1e30"):
            with self.subTest(amount=amount):
                response = self._post(amount)
                self.assertEqual(response.status_code, 400)
        mock_create.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_a_second_checkout_in_quick_succession_is_throttled(self, mock_create):
        """Every call costs a live SumUp request and leaves a PENDING row."""
        mock_create.return_value = {"id": "CHK_DON_T1", "status": "PENDING"}

        first = self._post("10.00")
        second = self._post("10.00")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(mock_create.call_count, 1)
        self.assertEqual(PaymentTransaction.objects.count(), 1)

    @patch("crush_lu.views_payments.SumUpClient.create_checkout")
    def test_refuses_a_json_body_that_is_not_an_object(self, mock_create):
        """A JSON body need not be a dict -- and a non-dict has no .get().

        That AttributeError is deliberately outside the caught set, so without
        the shape check this is a 500 on a merely malformed request.
        """
        for body in ("[1, 2]", '"just a string"', "42", "null"):
            with self.subTest(body=body):
                response = self.client.post(
                    self.url, data=body, content_type="application/json"
                )
                self.assertEqual(response.status_code, 400)
        mock_create.assert_not_called()
        self.assertFalse(PaymentTransaction.objects.exists())


class CommunitySupporterBadgeTests(SiteTestMixin, TestCase):
    """What a paid donation actually grants."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="badge@crush.lu", email="badge@crush.lu", password="password123"
        )
        self.profile = CrushProfile.objects.create(
            user=self.user, verification_status="verified"
        )

    def _donation(self, **kwargs):
        return PaymentTransaction.objects.create(
            transaction_reference=kwargs.pop("ref", "CRUSH-DON-TEST"),
            provider=PaymentTransaction.Provider.SUMUP,
            sumup_checkout_id=kwargs.pop("checkout_id", "CHK_DON_PAID"),
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.DONATION,
            **kwargs,
        )

    def test_a_paid_donation_grants_the_badge(self):
        from crush_lu.views_payments import _apply_paid_checkout

        tx = self._donation(user=self.user)
        self.assertFalse(self.profile.is_community_supporter)

        _apply_paid_checkout(tx, {"status": "PAID"})

        self.profile.refresh_from_db()
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
        self.assertTrue(self.profile.is_community_supporter)

    def test_applying_the_same_donation_twice_is_harmless(self):
        """The browser return and SumUp's callback routinely race each other."""
        from crush_lu.views_payments import _apply_paid_checkout

        tx = self._donation(user=self.user)

        _apply_paid_checkout(tx, {"status": "PAID"})
        _apply_paid_checkout(tx, {"status": "PAID"})

        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_community_supporter)

    def test_a_donor_without_a_profile_does_not_break_the_payment(self):
        """The badge is a nice-to-have; the money is not.

        Donating needs only an account — no CrushProfile, no onboarding — so
        this row is reachable in production. It must still be recorded PAID.
        """
        from crush_lu.views_payments import _apply_paid_checkout

        profileless = User.objects.create_user(
            username="noprofile@crush.lu",
            email="noprofile@crush.lu",
            password="password123",
        )
        tx = self._donation(
            user=profileless, ref="CRUSH-DON-NP", checkout_id="CHK_DON_NP"
        )

        _apply_paid_checkout(tx, {"status": "PAID"})

        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)

    def test_the_support_card_reaches_the_page_that_includes_it(self):
        """The card is inert without two things it does not render itself.

        It posts to a hardcoded path, and it reads the CSRF token out of the
        hidden input base.html renders -- CSRF_COOKIE_HTTPONLY is on, so there
        is no cookie to fall back to. Either one going missing leaves a button
        that looks fine and fails on click, which no unit test would catch.
        """
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.client.force_login(self.user)

        # Language-prefixed: on crush.lu the member-facing routes are inside
        # i18n_patterns, so the bare path redirects.
        response = self.client.get("/en/my-events/", follow=True)
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Support Crush.lu", body)
        self.assertIn("/payments/sumup/create-donation-checkout/", body)
        self.assertIn('name="csrfmiddlewaretoken"', body)

    @override_settings(ANDROID_NATIVE_COMMERCE_ENABLED=False)
    def test_the_card_offers_no_purchase_inside_a_native_shell(self):
        """Store compliance, checked on the surface the reviewer would see."""
        from crush_lu.models.profiles import UserDataConsent

        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.client.force_login(self.user)

        response = self.client.get("/en/my-events/?source=android_app", follow=True)
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        # The card still introduces itself; only the paying half is withheld.
        self.assertIn("Support Crush.lu", body)
        self.assertIn("Available outside the mobile app", body)
        self.assertNotIn("/payments/sumup/create-donation-checkout/", body)
        self.assertNotIn('id="js-submit-donation"', body)

    def test_the_badge_appears_in_the_gdpr_export(self):
        """The export promises all of a member's personal data.

        Supporter status is persisted on the profile and shown back to them on
        the card, so an export that omits it disagrees with what they can see.
        """
        from django.test import RequestFactory

        from crush_lu.views_payments import _apply_paid_checkout
        from crush_lu.views_account import export_user_data

        _apply_paid_checkout(self._donation(user=self.user), {"status": "PAID"})

        request = RequestFactory().get("/")
        # Re-fetch: setUp's CrushProfile.objects.create populated the reverse
        # relation cache on self.user, so user.crushprofile would hand the view
        # the pre-donation instance. A real request loads the user fresh.
        request.user = User.objects.get(pk=self.user.pk)
        data = json.loads(export_user_data(request).content)

        self.assertIn("is_community_supporter", data["profile"])
        self.assertTrue(data["profile"]["is_community_supporter"])

    def test_a_paid_donation_touches_no_seat_and_no_membership(self):
        """A donation shares _apply_paid_checkout with the two things it isn't."""
        from crush_lu.views_payments import _apply_paid_checkout

        tx = self._donation(user=self.user)

        _apply_paid_checkout(tx, {"status": "PAID"})

        self.assertFalse(EventRegistration.objects.exists())
        self.assertFalse(PremiumMembership.objects.exists())
