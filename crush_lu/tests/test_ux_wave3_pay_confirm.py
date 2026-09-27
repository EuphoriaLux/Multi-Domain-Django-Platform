"""UX Wave 3 · WP8 pay-confirm regression tests (findings 4-05, 4-09, 4-10).

Uses literal paths, not reverse(), per AGENTS.md: reverse() resolves against
the default urlconf, not the crush.lu one the HTTP_HOST override selects.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.profiles import CrushProfile, UserDataConsent

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
