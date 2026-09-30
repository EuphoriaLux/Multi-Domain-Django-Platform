"""UX Wave 5 · WP12b account-followups (#1116 code items, #1052 copy).

Literal paths, not reverse(), per AGENTS.md.
"""

from decimal import Decimal
from unittest import mock

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core import mail
from django.core.cache import cache
from django.db.models.query import QuerySet
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone

from crush_lu.models.events import EventRegistration
from crush_lu.models.payments import PaymentTransaction
from crush_lu.tests.test_ux_wave4_decided_flows import (
    _event,
    _member,
    _unverified_user,
)

User = get_user_model()
RESEND = "/en/signup/resend-verification/"


def _hold(client, user, email):
    session = client.session
    session["pending_verification_email"] = email
    session["pending_verification_user_id"] = user.pk
    session["pending_verification_user_id_at"] = int(timezone.now().timestamp())
    session.save()


class ResendCooldownAcrossSessionsTests(TestCase):
    def setUp(self):
        cache.clear()
        _unverified_user("target@example.com")

    def test_second_session_keeps_the_address_and_counts_down(self):
        Client(HTTP_HOST="crush.lu").post(RESEND, {"email": "target@example.com"})
        self.assertEqual(len(mail.outbox), 1)

        second = Client(HTTP_HOST="crush.lu")
        second.post(RESEND, {"email": "target@example.com"})
        self.assertEqual(len(mail.outbox), 1)  # nothing more was sent
        session = second.session
        self.assertEqual(session["pending_verification_email"], "target@example.com")
        self.assertGreater(
            session["resend_verification_cooldown_until"],
            int(timezone.now().timestamp()),
        )
        html = second.get("/accounts/confirm-email/").content.decode()
        self.assertNotRegex(html, r'data-cooldown-until="0"')
        self.assertIn("@example.com", html)

    def test_countdown_is_the_real_remaining_time(self):
        first = Client(HTTP_HOST="crush.lu")
        first.post(RESEND, {"email": "target@example.com"})
        second = Client(HTTP_HOST="crush.lu")
        second.post(RESEND, {"email": "target@example.com"})
        self.assertAlmostEqual(
            second.session["resend_verification_cooldown_until"],
            first.session["resend_verification_cooldown_until"],
            delta=3,
        )


class ReplacePendingSocialAddressTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.held = _unverified_user("typo@exmaple.com")
        self.other = EmailAddress.objects.create(
            user=self.user, email="second@example.com", primary=False, verified=False
        )
        self.client = Client(HTTP_HOST="crush.lu")
        _hold(self.client, self.user, "typo@exmaple.com")

    def test_only_the_held_row_is_deleted(self):
        self.client.post(RESEND, {"email": "fixed@example.com"})
        emails = set(
            EmailAddress.objects.filter(user=self.user).values_list("email", flat=True)
        )
        self.assertEqual(emails, {"second@example.com", "fixed@example.com"})
        self.assertFalse(EmailAddress.objects.filter(pk=self.held.pk).exists())
        self.assertTrue(EmailAddress.objects.filter(pk=self.other.pk).exists())
        self.assertEqual(
            EmailAddress.objects.filter(user=self.user, primary=True).count(), 1
        )

    def test_address_rows_are_locked_before_the_user(self):
        """SQLite ignores FOR UPDATE, so assert the order structurally: allauth
        confirms EmailAddress then User, and so must the correction."""
        order = []
        real = QuerySet.select_for_update

        def recording(qs, *args, **kwargs):
            order.append(qs.model.__name__)
            return real(qs, *args, **kwargs)

        with mock.patch.object(QuerySet, "select_for_update", recording):
            self.client.post(RESEND, {"email": "fixed@example.com"})
        self.assertEqual(order, ["EmailAddress", "User"])


class DelegationDevAliasTests(TestCase):
    @override_settings(ALLOWED_HOSTS=["*"])
    def test_delegation_localhost_counts_as_the_delegation_domain(self):
        from delegations.adapter import (
            DelegationAccountAdapter,
            DelegationSocialAccountAdapter,
        )
        from delegations.signals import _is_delegation_domain

        request = RequestFactory().get("/", HTTP_HOST="delegation.localhost:8000")
        self.assertTrue(_is_delegation_domain(request))
        self.assertTrue(DelegationAccountAdapter()._is_delegation_domain(request))
        self.assertTrue(DelegationSocialAccountAdapter()._is_delegation_domain(request))

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_other_dev_aliases_are_not_delegations(self):
        from delegations.signals import _is_delegation_domain

        request = RequestFactory().get("/", HTTP_HOST="crush.localhost:8000")
        self.assertFalse(_is_delegation_domain(request))


class DeadSparkJourneyTests(TestCase):
    def test_the_dead_view_and_form_are_gone_but_the_url_redirects(self):
        from crush_lu import forms_crush_spark, views_crush_spark

        self.assertFalse(hasattr(views_crush_spark, "spark_create_journey"))
        self.assertFalse(hasattr(forms_crush_spark, "SparkJourneyForm"))


class OrganiserCancelRefusalCopyTests(TestCase):
    """#1052: unpaid registrants must not be told a credit is on its way."""

    def setUp(self):
        cache.clear()
        self.user, _profile = _member("cancelled@crush.lu")
        self.event = _event(is_cancelled=True)
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.user)

    def _texts(self, registration):
        response = self.client.get(f"/en/events/{self.event.pk}/cancel/")
        self.assertEqual(response.status_code, 302)
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_unpaid_registrant_gets_no_credit_promise(self):
        registration = EventRegistration.objects.create(
            user=self.user, event=self.event, status="pending"
        )
        texts = self._texts(registration)
        self.assertEqual(len(texts), 1)
        self.assertNotIn("on its way", texts[0])
        self.assertIn("No payment was recorded", texts[0])

    def test_paid_registrant_keeps_the_credit_message(self):
        registration = EventRegistration.objects.create(
            user=self.user,
            event=self.event,
            status="confirmed",
            payment_confirmed=True,
        )
        texts = self._texts(registration)
        self.assertIn("Your Crush Credit is on its way", texts[0])

    def test_already_credited_registrant_is_not_read_as_unpaid(self):
        """The sweep clears payment_confirmed as it credits; the PAID
        transaction is what still marks the seat as paid."""
        registration = EventRegistration.objects.create(
            user=self.user, event=self.event, status="confirmed"
        )
        PaymentTransaction.objects.create(
            transaction_reference="W5-PAID",
            sumup_checkout_id="CHK-W5-PAID",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=registration,
        )
        texts = self._texts(registration)
        self.assertIn("Your Crush Credit is on its way", texts[0])

    def test_translations_exist(self):
        from django.utils import translation

        msgid = (
            "This event has been cancelled — you don't need to do anything. "
            "No payment was recorded for your registration, so there is no "
            "Crush Credit to expect."
        )
        for lang in ("de", "fr"):
            with translation.override(lang):
                self.assertNotEqual(translation.gettext(msgid), msgid)
