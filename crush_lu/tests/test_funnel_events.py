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


def _queue(user):
    """The events queued for ``user``, oldest first, without delivering them."""
    return [event for _key, event in funnel_events._pending(user.pk)]


def _names(user):
    return [e["name"] for e in _queue(user)]


def _deliver(user):
    """One page view that renders and succeeds: claim, then acknowledge."""
    from django.http import HttpRequest

    request = HttpRequest()
    events = funnel_events.claim(request, user.pk)
    funnel_events.acknowledge(request, delivered=True)
    return [e["name"] for e in events]


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

    def _render_tag(self, request=None):
        if request is None:
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
        request = RequestFactory().get("/en/events/", HTTP_HOST="crush.lu")
        request.user = self.user
        html = self._render_tag(request)
        self.assertIn("gtag('event',\"login\"", html)
        # A replay of the cached page is refused in the browser by event ID.
        self.assertIn("localStorage.getItem(k)", html)
        # Claimed by this page: another page rendering meanwhile skips it.
        self.assertEqual(self._render_tag(), "")
        funnel_events.acknowledge(request, delivered=True)
        self.assertEqual(_names(self.user), [])

    def test_page_view_delivers_and_deletes_queued_events(self):
        # Wiring: FunnelDeliveryMiddleware settles what base.html's tag claimed.
        import os
        from unittest.mock import patch

        with self.captureOnCommitCallbacks(execute=True):
            funnel_events.record(self.user.pk, "login")
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(self.user)
        with patch.dict(os.environ, {"GA4_CRUSH_LU": "G-T"}):
            response = client.get("/en/events/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "gtag('event',\"login\"")
        self.assertEqual(_names(self.user), [])

    def test_failed_page_keeps_claimed_events_for_the_next_page(self):
        from django.http import HttpResponseServerError

        from crush_lu.middleware import FunnelDeliveryMiddleware

        with self.captureOnCommitCallbacks(execute=True):
            funnel_events.record(self.user.pk, "login")

        def page(outcome):
            def view(request):
                self.assertIn("login", self._render_tag(request))
                # Something later on the page fails, e.g. a NoReverseMatch.
                if outcome == "raise":
                    raise RuntimeError("template error after the tag")
                return HttpResponseServerError()

            request = RequestFactory().get("/en/events/", HTTP_HOST="crush.lu")
            request.user = self.user
            return FunnelDeliveryMiddleware(view), request

        middleware, request = page("raise")
        with self.assertRaises(RuntimeError):
            middleware(request)
        self.assertEqual(_names(self.user), ["login"])
        middleware, request = page("500")
        self.assertEqual(middleware(request).status_code, 500)
        self.assertEqual(_names(self.user), ["login"])
        # Released, so the next page that renders delivers it, once.
        self.assertEqual(_deliver(self.user), ["login"])
        self.assertEqual(_deliver(self.user), [])

    def test_login_on_another_platform_is_not_a_crush_conversion(self):
        self._login("powerup.lu")
        self.assertEqual(_names(self.user), [])

    def test_dashboard_render_no_longer_fires_login(self):
        template = open(
            "crush_lu/templates/crush_lu/dashboard.html", encoding="utf-8"
        ).read()
        self.assertNotIn('{% ga4_event "login" %}', template)

    def test_invitation_sign_up_is_queued_before_its_login(self):
        from crush_lu.models import EventInvitation

        self.event.is_private_invitation = True
        self.event.invitation_code = "vip-funnel"
        self.event.invitation_expires_at = timezone.now() + timedelta(days=30)
        self.event.save()
        invitation = EventInvitation.objects.create(
            event=self.event,
            guest_email="guest-funnel@example.com",
            guest_first_name="Gina",
            guest_last_name="Guest",
            status="pending",
            approval_status="pending_approval",
        )
        dob = timezone.localdate() - timedelta(days=365 * 30)
        with self.captureOnCommitCallbacks(execute=True):
            Client(HTTP_HOST="crush.lu").post(
                f"/en/invite/{invitation.invitation_code}/accept/",
                {"date_of_birth": dob.isoformat(), "agree_to_terms": True},
            )
        guest = User.objects.get(email="guest-funnel@example.com")
        # The order of an allauth signup: the account, then its login.
        self.assertEqual(_names(guest), ["sign_up", "login"])

    def test_sign_up_queued_once_with_method(self):
        from allauth.account.signals import user_signed_up

        request = RequestFactory().get("/", HTTP_HOST="crush.lu")
        with self.captureOnCommitCallbacks(execute=True):
            user_signed_up.send(sender=User, request=request, user=self.user)
            user_signed_up.send(sender=User, request=request, user=self.user)
        queue = _queue(self.user)
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
        queue = _queue(self.user)
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
        queue = _queue(self.user)
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

    def test_purchase_tracking_cannot_fail_the_payment_save(self):
        from unittest.mock import patch

        from crush_lu.models.account_merge import AccountMerge

        tx = self._payment(PaymentTransaction.Provider.SUMUP, "CRUSH-DON-ISO")
        with patch.object(
            AccountMerge, "surviving_user_id", side_effect=RuntimeError("db down")
        ) as lookup:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                tx.status = PaymentTransaction.Status.PAID
                tx.save()
            # The payment's post_save only registered a commit callback.
            lookup.assert_not_called()
            with self.assertLogs("crush_lu.services.funnel_events", "WARNING"):
                for callback in callbacks:
                    callback()
        tx.refresh_from_db()
        self.assertEqual(tx.status, PaymentTransaction.Status.PAID)
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

    def test_later_admission_carries_the_event_type(self):
        # The same parameters as a direct registration in event_register.
        reg = EventRegistration.objects.create(
            event=self.event, user=self.user, status="applied"
        )
        with self.captureOnCommitCallbacks(execute=True):
            reg.status = "confirmed"
            reg.save()
        self.assertEqual(
            _queue(self.user)[0]["params"],
            {"event_id": self.event.pk, "event_type": "mixer"},
        )

    def test_walk_up_admitted_from_waitlist_counts_as_registration(self):
        # views_checkin admits a walk-up straight from waitlist to attended.
        reg = EventRegistration.objects.create(
            event=self.event, user=self.user, status="waitlist"
        )
        with self.captureOnCommitCallbacks(execute=True):
            reg.status = "attended"
            reg.save()
            # An undo and a re-scan of the same walk-up count nothing new.
            reg.status = "waitlist"
            reg.save()
            reg.status = "attended"
            reg.save()
        self.assertEqual(_names(self.user), ["event_registration", "event_attended"])

    def test_one_registration_is_one_event_registration(self):
        CrushProfile.objects.create(user=self.user, gender="F")
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(self.user)
        cache.clear()
        with self.captureOnCommitCallbacks(execute=True):
            client.post(f"/en/events/{self.event.pk}/register/", {})
        reg = EventRegistration.objects.get(event=self.event, user=self.user)
        self.assertEqual(reg.status, "confirmed")
        # An admin moving the seat to the waitlist and back, twice.
        with self.captureOnCommitCallbacks(execute=True):
            for status in ("waitlist", "confirmed", "waitlist", "confirmed"):
                reg.status = status
                reg.save()
        self.assertEqual(_names(self.user).count("event_registration"), 1)

    def test_capture_during_a_drain_is_kept_for_the_next_page(self):
        from unittest.mock import patch

        with self.captureOnCommitCallbacks(execute=True):
            funnel_events.record(self.user.pk, "login")
        real_get_many = funnel_events.cache.get_many

        def get_many_then_capture(keys):
            found = real_get_many(keys)
            # Another request captures a conversion after this page's read.
            funnel_events._enqueue(self.user.pk, "purchase", None, {})
            return found

        with patch.object(funnel_events.cache, "get_many", get_many_then_capture):
            first = _deliver(self.user)
        self.assertEqual(first, ["login"])
        self.assertEqual(_deliver(self.user), ["purchase"])
        self.assertEqual(_deliver(self.user), [])

    def test_concurrent_pages_deliver_once(self):
        from django.http import HttpRequest

        with self.captureOnCommitCallbacks(execute=True):
            funnel_events.record(self.user.pk, "login")
        first, second = HttpRequest(), HttpRequest()
        self.assertEqual(len(funnel_events.claim(first, self.user.pk)), 1)
        # A second page rendering before the first one's response is built.
        self.assertEqual(funnel_events.claim(second, self.user.pk), [])
        funnel_events.acknowledge(second, delivered=True)
        funnel_events.acknowledge(first, delivered=True)
        self.assertEqual(_deliver(self.user), [])

    # -- transitions the member is not present for ---------------------------------

    def test_profile_verified_once_across_paths(self):
        with self.captureOnCommitCallbacks(execute=True):
            funnel_events.record_profile_verified(self.user.pk, "luxid")
            funnel_events.record_profile_verified(self.user.pk, "admin")
        self.assertEqual(_names(self.user), ["profile_verified"])

    def _approve_from_admin(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage

        from crush_lu.admin.profiles import CrushProfileAdmin
        from crush_lu.models import ProfileSubmission

        profile = CrushProfile.objects.create(
            user=self.user, gender="F", verification_status="pending"
        )
        ProfileSubmission.objects.create(
            profile=profile, status="pending", review_call_completed=True
        )
        request = RequestFactory().post("/admin/")
        request.session = {}
        request._messages = FallbackStorage(request)
        CrushProfileAdmin(CrushProfile, AdminSite()).approve_profiles(
            request, CrushProfile.objects.filter(pk=profile.pk)
        )

    def test_admin_approval_records_verification(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._approve_from_admin()
        self.assertEqual(_names(self.user), ["profile_verified"])

    def test_failed_admin_approval_records_nothing(self):
        from unittest.mock import patch

        from django.db import DatabaseError

        real_save = CrushProfile.save

        def save_fails_on_approval(profile, *args, **kwargs):
            if profile.verification_status == "verified":
                raise DatabaseError("write failed")
            return real_save(profile, *args, **kwargs)

        with self.assertRaises(DatabaseError):
            with self.captureOnCommitCallbacks(execute=True):
                with patch.object(CrushProfile, "save", save_fails_on_approval):
                    self._approve_from_admin()
        self.assertEqual(_names(self.user), [])
        # No dedupe marker either: a retry that succeeds is still counted.
        self.assertIsNone(cache.get(f"funnel_sent:profile_verified:{self.user.pk}"))

    def _coffee_date(self, other):
        from crush_lu.models.crush_connect_cycle import (
            ConnectCoffeeDate,
            ConnectTemporaryChat,
            ConnectWeeklyRequest,
            ConnectWeekSession,
        )

        req = ConnectWeeklyRequest.objects.create(
            session=ConnectWeekSession.objects.create(user=self.user),
            requester=self.user,
            recipient=other,
            status=ConnectWeeklyRequest.Status.ACCEPTED,
        )
        chat = ConnectTemporaryChat.objects.create(
            request=req, participant_1=self.user, participant_2=other
        )
        return ConnectCoffeeDate.objects.create(
            chat=chat,
            proposer=self.user,
            custom_venue_name="Cafe",
            proposed_date=timezone.localdate() + timedelta(days=2),
            status=ConnectCoffeeDate.Status.ACCEPTED,
        )

    def test_coffee_date_confirmed_by_both_is_a_mutual_meeting(self):
        other = User.objects.create_user(username="o@example.com", password="x")
        coffee_date = self._coffee_date(other)
        with self.captureOnCommitCallbacks(execute=True):
            coffee_date.confirm_by_user(self.user)
        self.assertEqual(_names(self.user), [])
        with self.captureOnCommitCallbacks(execute=True):
            coffee_date.confirm_by_user(other)
        self.assertEqual(_names(self.user), ["mutual_meeting"])
        self.assertEqual(_names(other), ["mutual_meeting"])

    def test_failed_coffee_date_confirmation_records_nothing(self):
        from unittest.mock import patch

        from django.db import DatabaseError

        from crush_lu.models.crush_connect_cycle import ConnectCoffeeDate

        other = User.objects.create_user(username="o@example.com", password="x")
        coffee_date = self._coffee_date(other)
        coffee_date.confirm_by_user(self.user)
        with self.assertRaises(DatabaseError):
            with self.captureOnCommitCallbacks(execute=True):
                with patch.object(
                    ConnectCoffeeDate, "save", side_effect=DatabaseError("lost")
                ):
                    coffee_date.confirm_by_user(other)
        coffee_date.refresh_from_db()
        self.assertNotEqual(
            coffee_date.status, ConnectCoffeeDate.Status.CONFIRMED_BY_BOTH
        )
        self.assertEqual(_names(self.user), [])
        self.assertEqual(_names(other), [])
        self.assertIsNone(
            cache.get(f"funnel_sent:mutual_meeting:chat:{coffee_date.pk}:{other.pk}")
        )

    def test_mutual_meeting_for_both_without_the_partner(self):
        other = User.objects.create_user(username="o@example.com", password="x")
        with self.captureOnCommitCallbacks(execute=True):
            funnel_events.record_mutual_meeting(
                (self.user.pk, other.pk), pair_key="chat:1", source="connect_chat"
            )
        for user in (self.user, other):
            queue = _queue(user)
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
