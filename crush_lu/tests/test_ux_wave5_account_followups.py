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
    """#1116: the live adapters (azureproject.adapters, the ones settings.py
    wires in) must read delegation.localhost as delegations.lu."""

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_live_adapter_helper_reads_the_dev_alias(self):
        from azureproject.adapters import _is_delegation_domain

        request = RequestFactory().get("/", HTTP_HOST="delegation.localhost:8000")
        self.assertTrue(_is_delegation_domain(request))

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_login_redirect_routes_delegation_alias_users(self):
        from django.contrib.auth import get_user_model

        from azureproject.adapters import MultiDomainAccountAdapter

        user = get_user_model().objects.create_user(
            username="deleg@example.com", email="deleg@example.com", password="x"
        )
        adapter = MultiDomainAccountAdapter()
        urls = {}
        for host in ("delegations.lu", "delegation.localhost:8000"):
            request = RequestFactory().get("/", HTTP_HOST=host)
            request.user = user
            request.session = {}
            urls[host] = adapter.get_login_redirect_url(request)
        self.assertEqual(urls["delegations.lu"], urls["delegation.localhost:8000"])

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_signals_helper_and_other_aliases(self):
        from azureproject.adapters import _is_delegation_domain
        from delegations.signals import _is_delegation_domain as signals_check

        alias = RequestFactory().get("/", HTTP_HOST="delegation.localhost:8000")
        self.assertTrue(signals_check(alias))
        crush = RequestFactory().get("/", HTTP_HOST="crush.localhost:8000")
        self.assertFalse(signals_check(crush))
        self.assertFalse(_is_delegation_domain(crush))


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

    def test_refunded_registrant_is_not_told_nothing_was_paid(self):
        """Cash already went back (credits.py / SumUp reconcile flip the
        payment to REFUNDED): they paid, so no 'no payment' claim, and no
        credit promise either."""
        registration = EventRegistration.objects.create(
            user=self.user, event=self.event, status="confirmed"
        )
        PaymentTransaction.objects.create(
            transaction_reference="W5-REFUNDED",
            sumup_checkout_id="CHK-W5-REFUNDED",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.REFUNDED,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=registration,
        )
        texts = self._texts(registration)
        self.assertEqual(len(texts), 1)
        self.assertNotIn("No payment was recorded", texts[0])
        self.assertNotIn("on its way", texts[0])
        self.assertIn("already been handled", texts[0])

    def test_payment_from_an_earlier_registration_cycle_is_ignored(self):
        """Cancel + credit + re-register reuses the row and resets
        registered_at; the old PAID row must not promise a credit the sweep
        (payment_confirmed rows only) will never issue."""
        registration = EventRegistration.objects.create(
            user=self.user, event=self.event, status="pending"
        )
        old = PaymentTransaction.objects.create(
            transaction_reference="W5-OLD-CYCLE",
            sumup_checkout_id="CHK-W5-OLD-CYCLE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=registration,
        )
        PaymentTransaction.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=3)
        )
        registration.registered_at = timezone.now()
        registration.save(update_fields=["registered_at"])
        texts = self._texts(registration)
        self.assertEqual(len(texts), 1)
        self.assertNotIn("on its way", texts[0])
        self.assertIn("No payment was recorded", texts[0])

    def test_late_capture_of_an_earlier_checkout_counts(self):
        """A checkout opened before cancel + re-register, captured afterwards,
        confirmed the reused seat: the member did pay in this cycle."""
        registration = EventRegistration.objects.create(
            user=self.user, event=self.event, status="pending"
        )
        late = PaymentTransaction.objects.create(
            transaction_reference="W5-LATE",
            sumup_checkout_id="CHK-W5-LATE",
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=registration,
        )
        registration.registered_at = timezone.now() - timezone.timedelta(hours=1)
        registration.save(update_fields=["registered_at"])
        PaymentTransaction.objects.filter(pk=late.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=3),
            paid_at=timezone.now(),
        )
        texts = self._texts(registration)
        self.assertEqual(len(texts), 1)
        self.assertIn("on its way", texts[0])

    def test_translations_exist(self):
        from django.utils import translation

        msgid = (
            "This event has been cancelled — you don't need to do anything. "
            "No payment was recorded for your registration, so there is no "
            "Crush Credit to expect."
        )
        handled = (
            "This event has been cancelled — you don't need to do anything. "
            "Your payment has already been handled. If you have questions, "
            "reply to the cancellation email."
        )
        for lang in ("de", "fr"):
            with translation.override(lang):
                self.assertNotEqual(translation.gettext(msgid), msgid)
                self.assertNotEqual(translation.gettext(handled), handled)
