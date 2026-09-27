"""UX Wave 3 · WP8 pay-confirm regression tests (findings 4-05, 4-09, 4-10).

Uses literal paths, not reverse(), per AGENTS.md: reverse() resolves against
the default urlconf, not the crush.lu one the HTTP_HOST override selects.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushProfile, UserDataConsent
from crush_lu.services.event_payments import registration_is_payable

User = get_user_model()


class PayConfirmTestBase(TestCase):
    """Shared fixture for the tests below: one member with one paid event
    registration, mirroring SumUpPaymentViewsTests.setUp in
    test_sumup_payments.py without inheriting its unrelated test_* methods.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "crush.lu", "name": "Crush.lu"}
        )

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.client.defaults["HTTP_HOST"] = "crush.lu"

        self.user = User.objects.create_user(
            username="payconfirm@crush.lu",
            email="payconfirm@crush.lu",
            password="password123",
        )
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
            title="Pay Confirm Regression Event",
            description="WP8 regression event",
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


class EventDetailStatusToneTests(PayConfirmTestBase):
    """4-09: the "already registered" status card must be tone-coded and
    carry a next action, not read identically for an unpaid and a paid seat.
    """

    def test_unpaid_confirmed_registration_shows_amber_payment_due_banner(self):
        # "confirmed but unpaid" is what a real signup produces -- not
        # "pending" (event_register sets status="confirmed" on signup; see
        # test_confirmed_but_unpaid_registration_can_still_pay in
        # test_sumup_payments.py). The tone chain must catch this before the
        # confirmed/attended green branch, or the banner never appears for
        # the case that matters.
        self.registration.status = "confirmed"
        self.registration.payment_confirmed = False
        self.registration.save()
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{self.event.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment due")
        self.assertContains(response, "#event-payment-actions")
        self.assertContains(response, 'id="event-payment-actions"')
        self.assertNotContains(response, "You're in!")

    def test_paid_confirmed_registration_shows_green_confirmed_banner(self):
        self.registration.status = "confirmed"
        self.registration.payment_confirmed = True
        self.registration.save()
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{self.event.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're in!")
        self.assertNotContains(response, "Payment due")

    def test_free_event_confirmed_registration_has_no_payment_due_banner(self):
        # registration_fee == 0: the payment-due branch must not fire just
        # because payment_confirmed happens to be False on a free event.
        self.event.registration_fee = Decimal("0.00")
        self.event.save()
        self.registration.status = "confirmed"
        self.registration.payment_confirmed = False
        self.registration.save()
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{self.event.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Payment due")


class EventTicketPaymentDueStripTests(PayConfirmTestBase):
    """4-09: the ticket page never said payment was still owed."""

    def test_unpaid_ticket_shows_payment_due_strip_with_pay_link(self):
        self.registration.status = "confirmed"
        self.registration.payment_confirmed = False
        self.registration.save()
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{self.event.id}/ticket/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment due")
        self.assertContains(response, "#event-payment-actions")

    def test_paid_ticket_has_no_payment_due_strip(self):
        self.registration.status = "confirmed"
        self.registration.payment_confirmed = True
        self.registration.save()
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{self.event.id}/ticket/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Payment due")


class MyEventsSupportCardPositionTests(PayConfirmTestBase):
    """4-10: the donation widget must not lead the page above the member's
    own registrations, including above an empty upcoming state."""

    def test_support_card_renders_after_past_section_not_before_upcoming(self):
        self.client.force_login(self.user)

        response = self.client.get("/en/my-events/")

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        upcoming_idx = content.find("Upcoming (")
        past_idx = content.find("Past (")
        support_idx = content.find("Support Crush.lu")

        self.assertNotEqual(upcoming_idx, -1)
        self.assertNotEqual(past_idx, -1)
        self.assertNotEqual(support_idx, -1)
        self.assertLess(
            upcoming_idx,
            support_idx,
            "Support card must render after the Upcoming heading",
        )
        self.assertLess(
            past_idx,
            support_idx,
            "Support card must render after the Past heading, not lead the page",
        )

    def test_support_card_button_label_is_shortened(self):
        self.client.force_login(self.user)

        response = self.client.get("/en/my-events/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Support the Project")


class EventDetailWaitlistToneTests(PayConfirmTestBase):
    """Wave 3 WP8 follow-up: a waitlisted registration on a PAID event was
    falling into the payment-due content branch, because that chain checked
    "fee > 0 and not payment_confirmed" before checking status == "waitlist"
    (a waitlisted registration is unpaid too). The banner claimed "your spot
    is reserved" and offered a "Pay now" link/buttons the server rejects.
    registration_tone() resolves "waitlist" before "payment_due" so this
    can't regress again.
    """

    def test_waitlisted_paid_registration_shows_waitlist_banner(self):
        self.registration.status = "waitlist"
        self.registration.payment_confirmed = False
        self.registration.save()
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{self.event.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're on the waitlist")
        self.assertNotContains(response, "Payment due")
        self.assertNotContains(response, "Your spot is reserved")

    def test_waitlisted_paid_registration_has_no_pay_now_anchor_or_buttons(self):
        self.registration.status = "waitlist"
        self.registration.payment_confirmed = False
        self.registration.save()
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{self.event.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "#event-payment-actions")
        self.assertNotContains(response, 'id="event-payment-actions"')
        self.assertNotContains(response, "Pay with Card")


class RegistrationIsPayableTests(PayConfirmTestBase):
    """Codex review findings on PR #1071: three surfaces (the pay-confirm
    retry message, the ticket page's "Pay now" strip, and the event-detail
    status card's payment-due tone) each independently decided whether a
    registration was still payable, and disagreed with
    create_sumup_event_checkout's own allowlist (status in "pending"/
    "confirmed", event not cancelled). registration_is_payable()
    (services/event_payments.py) is now the single source of truth all three
    call; these tests exercise the shared helper directly and each surface
    that consumes it.
    """

    def test_pending_registration_on_live_event_is_payable(self):
        self.assertTrue(registration_is_payable(self.registration, self.event))

    def test_confirmed_registration_on_live_event_is_payable(self):
        self.registration.status = "confirmed"
        self.assertTrue(registration_is_payable(self.registration, self.event))

    def test_cancelled_registration_is_not_payable(self):
        self.registration.status = "cancelled"
        self.assertFalse(registration_is_payable(self.registration, self.event))

    def test_no_show_registration_is_not_payable(self):
        self.registration.status = "no_show"
        self.assertFalse(registration_is_payable(self.registration, self.event))

    def test_attended_registration_is_not_payable(self):
        self.registration.status = "attended"
        self.assertFalse(registration_is_payable(self.registration, self.event))

    def test_pending_registration_on_cancelled_event_is_not_payable(self):
        self.event.is_cancelled = True
        self.assertFalse(registration_is_payable(self.registration, self.event))


class SumUpReturnRetryCopyTests(PayConfirmTestBase):
    """Codex finding (views_payments.py, sumup_payment_return): a member who
    cancels their registration and then reopens the checkout return URL
    (still carrying the old, uncleared transaction) was told "your spot is
    reserved — you can retry payment below", although the registration is no
    longer payable and create_sumup_event_checkout would refuse the retry."""

    def _make_pending_tx(self, ref="CRUSH-WP8-RETRY-1"):
        return PaymentTransaction.objects.create(
            transaction_reference=ref,
            sumup_checkout_id=f"CHK-{ref}",
            amount=self.event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

    @patch("crush_lu.views_payments._sync_checkout_with_sumup", return_value="")
    def test_still_pending_registration_gets_retry_copy(self, _mock_sync):
        tx = self._make_pending_tx()
        self.client.force_login(self.user)

        response = self.client.get(
            "/payments/sumup/return/", {"ref": tx.transaction_reference}, follow=True
        )

        texts = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any("Your spot is reserved" in t for t in texts),
            texts,
        )

    @patch("crush_lu.views_payments._sync_checkout_with_sumup", return_value="")
    def test_cancelled_registration_does_not_get_retry_copy(self, _mock_sync):
        self.registration.status = "cancelled"
        self.registration.save()
        tx = self._make_pending_tx(ref="CRUSH-WP8-RETRY-2")
        self.client.force_login(self.user)

        response = self.client.get(
            "/payments/sumup/return/", {"ref": tx.transaction_reference}, follow=True
        )

        texts = [str(m) for m in response.context["messages"]]
        self.assertFalse(
            any("Your spot is reserved" in t for t in texts),
            texts,
        )
        self.assertTrue(
            any("Payment is pending or was not completed." in t for t in texts),
            texts,
        )


class EventTicketPayNowGatingTests(PayConfirmTestBase):
    """Codex finding (event_ticket.html): the ticket page's "Pay now" strip
    rendered for any unpaid registration, including an "attended" seat —
    supported because check-in accepts pending-payment seats — even though
    create_sumup_event_checkout refuses to charge a non pending/confirmed
    registration."""

    def test_confirmed_unpaid_ticket_shows_pay_now_link(self):
        self.registration.status = "confirmed"
        self.registration.payment_confirmed = False
        self.registration.save()
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{self.event.id}/ticket/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Payment due")
        self.assertContains(response, "#event-payment-actions")

    def test_attended_unpaid_ticket_hides_pay_now_link(self):
        self.registration.status = "attended"
        self.registration.payment_confirmed = False
        self.registration.checked_in_at = timezone.now()
        self.registration.save()
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{self.event.id}/ticket/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Payment due")
        self.assertNotContains(response, "#event-payment-actions")


class EventDetailNoShowToneTests(PayConfirmTestBase):
    """Codex finding (registration_status_tags.py): a "no_show" registration
    on a paid event also has payment_confirmed=False, so the tone chain fell
    into "payment_due" and rendered checkout buttons the endpoint refuses
    (only "pending"/"confirmed" are accepted)."""

    def test_no_show_unpaid_registration_has_no_payment_due_banner_or_buttons(self):
        self.registration.status = "no_show"
        self.registration.payment_confirmed = False
        self.registration.save()
        self.client.force_login(self.user)

        response = self.client.get(f"/en/events/{self.event.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Payment due")
        self.assertNotContains(response, "#event-payment-actions")
        self.assertNotContains(response, "Pay with Card")
