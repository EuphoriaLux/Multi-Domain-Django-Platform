"""Audit A17: conversions captured once at the transition, delivered once.

services/funnel_events.py queues GA4 events per member after commit;
{% funnel_events %} delivers them through ga4_event (Consent Mode, nonce, iOS
suppression unchanged). These tests pin the contracts that used to be wrong:
login on every dashboard render, purchase for free events by registration ID,
and nothing at all for transitions the member was not present for.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.template import Context, Template
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone

from crush_lu.models import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushProfile, UserDataConsent
from crush_lu.services import funnel_events

User = get_user_model()


def _names(user):
    return [e["name"] for e in cache.get(f"funnel_outbox:{user.pk}") or []]


class FunnelEventsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="funnel@example.com", email="funnel@example.com", password="x"
        )
        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
        )
        self.event = MeetupEvent.objects.create(
            title="Free Mixer",
            description="d",
            event_type="mixer",
            date_time=timezone.now() + timedelta(days=7),
            location="Luxembourg",
            address="1 Test Street",
            max_participants=20,
            registration_deadline=timezone.now() + timedelta(days=5),
            is_published=True,
            profile_requirement="none",
        )

    def _render_tag(self):
        request = RequestFactory().get("/en/events/", HTTP_HOST="crush.lu")
        request.user = self.user
        return Template("{% load funnel_tags %}{% funnel_events %}").render(
            Context({"request": request, "GOOGLE_ANALYTICS_GTAG_PROPERTY_ID": "G-T"})
        )

    # -- login / sign_up ----------------------------------------------------------

    def _login(self, host):
        from django.contrib.auth.signals import user_logged_in
        from django.contrib.sessions.middleware import SessionMiddleware

        request = RequestFactory().get("/", HTTP_HOST=host)
        SessionMiddleware(lambda r: None).process_request(request)
        with self.captureOnCommitCallbacks(execute=True):
            user_logged_in.send(sender=User, request=request, user=self.user)

    def test_login_is_captured_on_login_and_delivered_once(self):
        self._login("crush.lu")
        self.assertEqual(_names(self.user), ["login"])
        html = self._render_tag()
        self.assertIn("gtag('event',\"login\"", html)
        # A replay of the cached page is refused in the browser by event ID.
        self.assertIn("localStorage.getItem(k)", html)
        self.assertEqual(self._render_tag(), "")

    def test_login_on_another_platform_is_not_a_crush_conversion(self):
        self._login("powerup.lu")
        self.assertEqual(_names(self.user), [])

    def test_dashboard_render_no_longer_fires_login(self):
        template = open(
            "crush_lu/templates/crush_lu/dashboard.html", encoding="utf-8"
        ).read()
        self.assertNotIn('{% ga4_event "login" %}', template)

    def test_sign_up_queued_once_with_method(self):
        from allauth.account.signals import user_signed_up

        request = RequestFactory().get("/", HTTP_HOST="crush.lu")
        with self.captureOnCommitCallbacks(execute=True):
            user_signed_up.send(sender=User, request=request, user=self.user)
            user_signed_up.send(sender=User, request=request, user=self.user)
        queue = cache.get(f"funnel_outbox:{self.user.pk}")
        self.assertEqual([e["name"] for e in queue], ["sign_up"])
        self.assertEqual(queue[0]["params"], {"method": "email"})

    # -- registration / purchase -----------------------------------------------------

    def test_free_registration_is_a_registration_not_a_purchase(self):
        CrushProfile.objects.create(user=self.user, gender="F")
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(self.user)
        cache.clear()
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post(f"/en/events/{self.event.pk}/register/", {})
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(
            EventRegistration.objects.filter(event=self.event, user=self.user).exists()
        )
        self.assertIn("event_registration", _names(self.user))
        self.assertNotIn("purchase", _names(self.user))

    def _payment(self, provider, reference):
        return PaymentTransaction.objects.create(
            transaction_reference=reference,
            provider=provider,
            amount=Decimal("15.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PENDING,
            purpose=PaymentTransaction.Purpose.DONATION,
            user=self.user,
        )

    def test_payment_applied_twice_is_one_purchase_by_reference(self):
        from crush_lu.views_payments import _apply_paid_checkout

        tx = self._payment(PaymentTransaction.Provider.SUMUP, "CRUSH-DON-FUNNEL")
        with self.captureOnCommitCallbacks(execute=True):
            _apply_paid_checkout(tx, {"status": "PAID"})
            _apply_paid_checkout(tx, {"status": "PAID"})
        queue = cache.get(f"funnel_outbox:{self.user.pk}")
        purchases = [e for e in queue if e["name"] == "purchase"]
        self.assertEqual(len(purchases), 1)
        self.assertEqual(purchases[0]["params"]["transaction_id"], "CRUSH-DON-FUNNEL")
        self.assertEqual(purchases[0]["params"]["value"], 15.0)

    def test_credit_redemption_is_not_revenue(self):
        tx = self._payment(PaymentTransaction.Provider.CREDIT, "CRUSH-CREDIT-1")
        with self.captureOnCommitCallbacks(execute=True):
            tx.status = PaymentTransaction.Status.PAID
            tx.save()
        self.assertEqual(_names(self.user), [])

    def test_cash_payment_is_a_purchase_by_its_manual_reference(self):
        with self.captureOnCommitCallbacks(execute=True):
            PaymentTransaction.objects.create(
                transaction_reference="CRUSH-MANUAL-9-ab",
                provider=PaymentTransaction.Provider.MANUAL,
                amount=Decimal("15.00"),
                currency="EUR",
                status=PaymentTransaction.Status.PAID,
                purpose=PaymentTransaction.Purpose.DONATION,
                user=self.user,
            )
        queue = cache.get(f"funnel_outbox:{self.user.pk}")
        self.assertEqual(queue[0]["params"]["transaction_id"], "CRUSH-MANUAL-9-ab")
        # GA4 items need an item_id.
        self.assertEqual(queue[0]["params"]["items"][0]["item_id"], "donation")

    def test_refund_reverses_a_tracked_purchase(self):
        tx = self._payment(PaymentTransaction.Provider.SUMUP, "CRUSH-DON-REF")
        with self.captureOnCommitCallbacks(execute=True):
            tx.status = PaymentTransaction.Status.PAID
            tx.save()
            tx.status = PaymentTransaction.Status.REFUNDED
            tx.save()
        self.assertEqual(_names(self.user), ["purchase", "refund"])

    def test_purchase_goes_to_the_account_a_merge_kept(self):
        from crush_lu.models.account_merge import AccountMerge

        keeper = User.objects.create_user(username="keep@example.com", password="x")
        AccountMerge.objects.create(keeper=keeper, duplicate=self.user)
        tx = self._payment(PaymentTransaction.Provider.SUMUP, "CRUSH-DON-MRG")
        with self.captureOnCommitCallbacks(execute=True):
            tx.status = PaymentTransaction.Status.PAID
            tx.save()
        self.assertEqual(_names(keeper), ["purchase"])
        self.assertEqual(_names(self.user), [])

    def test_admission_from_waitlist_and_attendance_are_captured(self):
        reg = EventRegistration.objects.create(
            event=self.event, user=self.user, status="waitlist"
        )
        with self.captureOnCommitCallbacks(execute=True):
            reg.status = "confirmed"
            reg.save()
            reg.status = "attended"
            reg.save()
        self.assertEqual(_names(self.user), ["event_registration", "event_attended"])

    def test_concurrent_drain_delivers_once(self):
        with self.captureOnCommitCallbacks(execute=True):
            funnel_events.record(self.user.pk, "login")
        from unittest.mock import patch

        # The other request deleted the key between this one's read and delete.
        with patch.object(funnel_events.cache, "delete", return_value=False):
            self.assertEqual(funnel_events.pop(self.user.pk), [])
        self.assertEqual(len(funnel_events.pop(self.user.pk)), 1)

    # -- transitions the member is not present for ---------------------------------

    def test_profile_verified_once_across_paths(self):
        with self.captureOnCommitCallbacks(execute=True):
            funnel_events.record_profile_verified(self.user.pk, "luxid")
            funnel_events.record_profile_verified(self.user.pk, "admin")
        self.assertEqual(_names(self.user), ["profile_verified"])

    def test_mutual_meeting_for_both_without_the_partner(self):
        other = User.objects.create_user(username="o@example.com", password="x")
        with self.captureOnCommitCallbacks(execute=True):
            funnel_events.record_mutual_meeting(
                (self.user.pk, other.pk), pair_key="chat:1", source="connect_chat"
            )
        for user in (self.user, other):
            queue = cache.get(f"funnel_outbox:{user.pk}")
            self.assertEqual([e["name"] for e in queue], ["mutual_meeting"])
            self.assertEqual(queue[0]["params"], {"source": "connect_chat"})

    def test_rolled_back_transition_queues_nothing(self):
        from django.db import transaction

        try:
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    funnel_events.record(self.user.pk, "login")
                    raise RuntimeError("rollback")
        except RuntimeError:
            pass
        self.assertEqual(_names(self.user), [])

    def test_anonymous_render_emits_nothing(self):
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        html = Template("{% load funnel_tags %}{% funnel_events %}").render(
            Context({"request": request, "GOOGLE_ANALYTICS_GTAG_PROPERTY_ID": "G-T"})
        )
        self.assertEqual(html, "")
