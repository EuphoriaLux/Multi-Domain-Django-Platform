"""UX Wave 4 · WP12 followups-flows.

#1059 resend cooldown (atomic, per address), "Use a different address" for a
held social account, crush.localhost as crush, the read-only
count_social_without_email command; #1079 SumUp return (unpublished event,
staff viewer) and the donation CTA msgid; #1085 App Insights on anonymous
pages; decisions 5-13 (spark_create_journey), 8-13 (on-break invite warning)
and 4-18 (event share uses the referral link).

Literal paths, not reverse(), per AGENTS.md.
"""

import os
from decimal import Decimal
from io import StringIO
from unittest import mock

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase
from django.utils import timezone, translation
from django.utils.translation import pgettext

from crush_lu.models import CrushCoach, EventInvitation, ReferralCode
from crush_lu.models.crush_spark import CrushSpark
from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import CrushProfile, UserDataConsent

User = get_user_model()
RESEND = "/en/signup/resend-verification/"
SEND = (
    "allauth.account.internal.flows.email_verification."
    "send_verification_email_to_address"
)


def _unverified_user(email):
    user = User.objects.create_user(username=email, email=email, password="x-Pa55!")
    address = EmailAddress.objects.create(
        user=user, email=email, primary=True, verified=False
    )
    return user, address


def _member(email, **profile_kwargs):
    user = User.objects.create_user(
        username=email, email=email, password="password123", first_name="Mia"
    )
    UserDataConsent.objects.update_or_create(
        user=user,
        defaults={"powerup_consent_given": True, "crushlu_consent_given": True},
    )
    profile = CrushProfile.objects.create(
        user=user,
        verification_status="verified",
        completion_status="step4",
        **profile_kwargs,
    )
    return user, profile


def _event(**kwargs):
    defaults = dict(
        title="WP12 Event",
        description="WP12 event",
        event_type="speed_dating",
        location="Luxembourg City",
        address="10 Grand Rue",
        date_time=timezone.now() + timezone.timedelta(days=3),
        registration_deadline=timezone.now() + timezone.timedelta(days=2),
        registration_fee=Decimal("15.00"),
        is_published=True,
    )
    defaults.update(kwargs)
    return MeetupEvent.objects.create(**defaults)


# ---------------------------------------------------------------------------
# #1059: resend cooldown
# ---------------------------------------------------------------------------


class ResendCooldownTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_overlapping_posts_from_two_sessions_send_once(self):
        """The claim is keyed on the address, so a second session (or a
        double-click racing the first send) cannot send again."""
        _unverified_user("race@example.com")
        with mock.patch(SEND) as send:
            Client(HTTP_HOST="crush.lu").post(RESEND, {"email": "race@example.com"})
            Client(HTTP_HOST="crush.lu").post(RESEND, {"email": "RACE@example.com "})
        self.assertEqual(send.call_count, 1)

    def test_corrected_address_is_not_held_by_the_typo_cooldown(self):
        _unverified_user("right@example.com")
        client = Client(HTTP_HOST="crush.lu")
        with mock.patch(SEND) as send:
            client.post(RESEND, {"email": "rihgt@example.com"})
            client.post(RESEND, {"email": "right@example.com"})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1].email, "right@example.com")

    def test_cache_outage_fails_open(self):
        _unverified_user("open@example.com")
        with mock.patch("crush_lu.views_account.cache.add", return_value=None):
            with mock.patch(SEND) as send:
                Client(HTTP_HOST="crush.lu").post(RESEND, {"email": "open@example.com"})
        self.assertEqual(send.call_count, 1)

    def test_page_exposes_only_a_hash_of_the_cooled_address(self):
        client = Client(HTTP_HOST="crush.lu")
        client.post(RESEND, {"email": "hash@example.com"})
        html = client.get("/accounts/confirm-email/").content.decode()
        import hashlib

        digest = hashlib.sha256(b"hash@example.com").hexdigest()
        self.assertIn(f'data-cooldown-hash="{digest}"', html)
        self.assertIn('x-on:input="onEmailInput"', html)


# ---------------------------------------------------------------------------
# #1059: "Use a different address" for a held social account
# ---------------------------------------------------------------------------


class SocialAddressCorrectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user, self.address = _unverified_user("typo@exmaple.com")
        SocialAccount.objects.create(user=self.user, provider="google", uid="g-1")
        self.client = Client(HTTP_HOST="crush.lu")
        session = self.client.session
        session["pending_verification_email"] = "typo@exmaple.com"
        session["pending_verification_user_id"] = self.user.pk
        session.save()

    def test_typed_address_replaces_the_unverified_one_and_is_mailed(self):
        response = self.client.post(RESEND, {"email": "fixed@example.com"})
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "fixed@example.com")
        rows = list(EmailAddress.objects.filter(user=self.user))
        self.assertEqual([r.email for r in rows], ["fixed@example.com"])
        self.assertFalse(rows[0].verified)
        # A new row, so the HMAC link mailed to the typo (signs the pk) dies.
        self.assertNotEqual(rows[0].pk, self.address.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["fixed@example.com"])

    def test_address_of_another_account_is_skipped_with_the_same_response(self):
        _unverified_user("taken@example.com")
        other = Client(HTTP_HOST="crush.lu").post(RESEND, {"email": "nobody@x.lu"})
        response = self.client.post(RESEND, {"email": "Taken@example.com"})
        self.assertEqual(response.url, other.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "typo@exmaple.com")

    def test_without_the_session_hold_nothing_is_replaced(self):
        client = Client(HTTP_HOST="crush.lu")
        client.post(RESEND, {"email": "fixed@example.com"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "typo@exmaple.com")

    def test_verified_account_is_never_rewritten(self):
        self.address.verified = True
        self.address.save()
        self.client.post(RESEND, {"email": "fixed@example.com"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "typo@exmaple.com")

    def test_page_drops_the_sign_up_again_link_for_a_held_social_account(self):
        html = self.client.get("/accounts/confirm-email/").content.decode()
        self.assertNotIn('href="/en/signup/"', html)
        self.assertIn("Use a different address", html)

    def test_later_email_signup_in_the_session_cannot_rewrite_the_held_account(
        self,
    ):
        """An abandoned social hold (A) followed by an email signup (B) in
        the same browser: B's confirmation mail drops A's stashed id, so B
        typing an address never rewrites account A."""
        from allauth.account.internal.flows.email_verification import (
            send_verification_email_to_address,
        )
        from allauth.core import context as allauth_context

        other, other_address = _unverified_user("b-signup@example.com")
        request = RequestFactory().get("/", HTTP_HOST="crush.lu")
        request.user = AnonymousUser()
        request.session = SessionStore(session_key=self.client.session.session_key)
        request._messages = FallbackStorage(request)
        with allauth_context.request_context(request):
            send_verification_email_to_address(request, other_address, signup=True)
        request.session.save()

        session = self.client.session
        self.assertEqual(session["pending_verification_email"], "b-signup@example.com")
        self.assertNotIn("pending_verification_user_id", session)
        html = self.client.get("/accounts/confirm-email/").content.decode()
        self.assertIn('href="/en/signup/"', html)

        self.client.post(RESEND, {"email": "b-controls@example.com"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "typo@exmaple.com")
        self.assertEqual(
            list(EmailAddress.objects.filter(user=self.user).values_list("pk")),
            [(self.address.pk,)],
        )
        other.refresh_from_db()
        self.assertEqual(other.email, "b-signup@example.com")

    def test_stale_held_id_for_another_pending_address_is_dropped(self):
        """Defence in depth in the view: the stashed id only counts while
        the session's pending address is that user's unverified row."""
        session = self.client.session
        session["pending_verification_email"] = "someone-else@example.com"
        session.save()
        self.client.post(RESEND, {"email": "b-controls@example.com"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "typo@exmaple.com")
        self.assertTrue(EmailAddress.objects.filter(pk=self.address.pk).exists())
        self.assertNotIn("pending_verification_user_id", self.client.session)

    def test_pre_login_hold_stashes_the_user_and_the_cooldown(self):
        from allauth.core import context as allauth_context

        from azureproject.adapters import MultiDomainAccountAdapter
        from crush_lu.views_account import claim_resend_cooldown

        user, _address = _unverified_user("held@example.com")
        request = RequestFactory().get("/", HTTP_HOST="crush.lu")
        request.user = AnonymousUser()
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        with allauth_context.request_context(request):
            MultiDomainAccountAdapter(request).pre_login(
                request,
                user,
                email_verification="none",
                signal_kwargs={"sociallogin": mock.Mock()},
                email=None,
                signup=True,
                redirect_url=None,
            )
        self.assertEqual(request.session["pending_verification_user_id"], user.pk)
        self.assertFalse(claim_resend_cooldown("held@example.com"))


# ---------------------------------------------------------------------------
# #1059: crush.localhost
# ---------------------------------------------------------------------------


class CrushLocalhostDomainTests(TestCase):
    def test_crush_localhost_counts_as_crush(self):
        from azureproject.adapters import _is_crush_domain
        from crush_lu.signals import _is_crush_domain as signals_is_crush

        request = RequestFactory().get("/", HTTP_HOST="crush.localhost:8000")
        self.assertTrue(_is_crush_domain(request))
        self.assertTrue(signals_is_crush(request))
        other = RequestFactory().get("/", HTTP_HOST="power-up.localhost:8000")
        self.assertFalse(_is_crush_domain(other))
        self.assertFalse(signals_is_crush(other))


# ---------------------------------------------------------------------------
# #1059: read-only count command
# ---------------------------------------------------------------------------


class CountSocialWithoutEmailCommandTests(TestCase):
    def test_counts_and_breaks_down_by_provider_without_writing(self):
        bare = User.objects.create_user(username="bare", email="bare@example.com")
        SocialAccount.objects.create(user=bare, provider="google", uid="1")
        SocialAccount.objects.create(user=bare, provider="facebook", uid="2")
        bare2 = User.objects.create_user(username="bare2", email="b2@example.com")
        SocialAccount.objects.create(user=bare2, provider="google", uid="3")
        synced, _ = _unverified_user("synced@example.com")
        SocialAccount.objects.create(user=synced, provider="google", uid="4")
        User.objects.create_user(username="plain", email="plain@example.com")
        before = EmailAddress.objects.count()

        out = StringIO()
        call_command("count_social_without_email", stdout=out)

        text = out.getvalue()
        self.assertIn("Users with a social account but no EmailAddress: 2", text)
        self.assertIn("  facebook: 1", text)
        self.assertIn("  google: 2", text)
        self.assertEqual(EmailAddress.objects.count(), before)


# ---------------------------------------------------------------------------
# #1079: SumUp return
# ---------------------------------------------------------------------------


@mock.patch("crush_lu.views_payments._sync_checkout_with_sumup", return_value="")
class SumUpReturnFollowupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.user, _ = _member("payer@crush.lu")
        self.event = _event()
        self.registration = EventRegistration.objects.create(
            user=self.user, event=self.event, status="pending"
        )

    def _tx(self, status=PaymentTransaction.Status.PENDING):
        return PaymentTransaction.objects.create(
            transaction_reference=f"WP12-{status}",
            sumup_checkout_id=f"CHK-WP12-{status}",
            amount=Decimal("15.00"),
            currency="EUR",
            status=status,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=self.user,
            event_registration=self.registration,
        )

    def test_unpublished_event_pending_return_goes_to_my_events(self, _sync):
        tx = self._tx()
        MeetupEvent.objects.filter(pk=self.event.pk).update(is_published=False)
        self.client.force_login(self.user)
        response = self.client.get(
            "/payments/sumup/return/", {"ref": tx.transaction_reference}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/my-events/", response.url)
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertIn("Payment is pending or was not completed.", texts)
        self.assertFalse(any("retry" in t for t in texts), texts)

    def test_unpublished_event_paid_return_goes_to_my_events(self, _sync):
        tx = self._tx(PaymentTransaction.Status.PAID)
        MeetupEvent.objects.filter(pk=self.event.pk).update(is_published=False)
        self.client.force_login(self.user)
        response = self.client.get(
            "/payments/sumup/return/", {"ref": tx.transaction_reference}
        )
        self.assertIn("/my-events/", response.url)

    def test_published_event_still_returns_to_event_detail(self, _sync):
        tx = self._tx()
        self.client.force_login(self.user)
        response = self.client.get(
            "/payments/sumup/return/", {"ref": tx.transaction_reference}
        )
        self.assertIn(f"/events/{self.event.pk}/", response.url)

    def test_staff_viewer_gets_neutral_copy_and_the_admin_page(self, _sync):
        tx = self._tx()
        from django.contrib.auth.models import Permission

        staff, _ = _member("staff@crush.lu")
        User.objects.filter(pk=staff.pk).update(is_staff=True)
        staff.user_permissions.add(
            Permission.objects.get(
                codename="view_paymenttransaction", content_type__app_label="crush_lu"
            )
        )
        self.client.force_login(staff)
        response = self.client.get(
            "/payments/sumup/return/", {"ref": tx.transaction_reference}
        )
        self.assertEqual(
            response.url,
            f"/crush-admin/crush_lu/paymenttransaction/{tx.pk}/change/",
        )
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(
            texts,
            ["This checkout belongs to another member. Payment status: Pending."],
        )

    def test_staff_without_admin_view_access_goes_to_my_events(self, _sync):
        """No 403: plain staff without the PaymentTransaction view
        permission land on My Events, still with the neutral status."""
        tx = self._tx()
        staff, _ = _member("staff2@crush.lu")
        User.objects.filter(pk=staff.pk).update(is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(
            "/payments/sumup/return/", {"ref": tx.transaction_reference}
        )
        self.assertIn("/my-events/", response.url)
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(
            texts,
            ["This checkout belongs to another member. Payment status: Pending."],
        )

    def test_staff_owner_keeps_the_member_flow(self, _sync):
        tx = self._tx()
        User.objects.filter(pk=self.user.pk).update(is_staff=True)
        self.client.force_login(self.user)
        response = self.client.get(
            "/payments/sumup/return/", {"ref": tx.transaction_reference}
        )
        self.assertIn(f"/events/{self.event.pk}/", response.url)

    def test_other_member_is_still_refused(self, _sync):
        tx = self._tx()
        stranger, _ = _member("stranger@crush.lu")
        self.client.force_login(stranger)
        response = self.client.get(
            "/payments/sumup/return/", {"ref": tx.transaction_reference}
        )
        self.assertNotIn("crush-admin", response.url)
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertIn("Payment transaction reference not found.", texts)


class DonationCtaMsgidTests(TestCase):
    def test_donation_button_has_its_own_translated_msgid(self):
        for lang, expected in (("de", "Unterstützen"), ("fr", "Soutenir")):
            with translation.override(lang):
                self.assertEqual(pgettext("donation button", "Support"), expected)
        path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "templates/crush_lu/components/support_card.html",
        )
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(source.count("context 'donation button'"), 1)
        self.assertEqual(source.count('context "donation button"'), 1)

    def test_my_events_renders_the_german_donation_verb(self):
        cache.clear()
        user, _ = _member("donor@crush.lu")
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(user)
        response = client.get("/de/my-events/")
        self.assertContains(response, 'data-label-support="Unterstützen"')


# ---------------------------------------------------------------------------
# #1085: App Insights on anonymous pages
# ---------------------------------------------------------------------------


class AnonymousAppInsightsTests(TestCase):
    ENV = {"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=test-key"}

    def setUp(self):
        cache.clear()

    def test_anonymous_signup_page_gets_the_consent_gated_loader(self):
        with mock.patch.dict(os.environ, self.ENV):
            html = Client(HTTP_HOST="crush.lu").get("/en/signup/").content.decode()
        self.assertIn(
            "Azure Application Insights (waiting for analytics consent)", html
        )
        self.assertIn("cookie_consent_updated", html)
        self.assertIn("flushPendingEvents", html)
        self.assertIn('"signup_page_viewed"', html)
        # Consent-gated: without a stored choice the SDK does not start.
        self.assertNotIn("Browser SDK v3 -->", html)

    def test_anonymous_login_page_gets_the_loader_too(self):
        with mock.patch.dict(os.environ, self.ENV):
            html = Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()
        self.assertIn(
            "Azure Application Insights (waiting for analytics consent)", html
        )


# ---------------------------------------------------------------------------
# 5-13: spark_create_journey
# ---------------------------------------------------------------------------


class SparkCreateJourneyRedirectTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_create_journey_is_a_301_to_the_connect_hub(self):
        user, _ = _member("spark@crush.lu")
        spark = CrushSpark.objects.create(
            event=_event(), sender=user, sender_description="red dress"
        )
        client = Client(HTTP_HOST="crush.lu")
        client.force_login(user)
        response = client.get(f"/en/sparks/{spark.id}/create-journey/")
        self.assertEqual(response.status_code, 301)
        self.assertIn("/crush-connect/home/", response.url)

    def test_event_spark_links_keep_their_teaser_target(self):
        response = Client(HTTP_HOST="crush.lu").get("/en/events/1/spark/request/")
        self.assertNotIn("/crush-connect/home/", response.url)


# ---------------------------------------------------------------------------
# 8-13: warn when inviting an on-break member
# ---------------------------------------------------------------------------


class OnBreakInvitationWarningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.member, self.profile = _member(
            "resting@crush.lu", on_break_at=timezone.now()
        )
        coach_user, _ = _member("coach@crush.lu")
        self.coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        self.event = _event(is_private_invitation=True, registration_fee=Decimal("0"))
        self.event.coaches.add(self.coach)
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(coach_user)

    def _invite(self, email):
        with mock.patch(
            "crush_lu.email_notifications.send_external_guest_invitation_email",
            return_value=True,
        ):
            return self.client.post(
                f"/en/coach/event/{self.event.pk}/invitations/",
                {
                    "action": "send_invitation",
                    "email": email,
                    "first_name": "Mia",
                    "last_name": "Rest",
                },
            )

    def test_inviting_an_on_break_member_warns_but_still_invites(self):
        response = self._invite("Resting@crush.lu")
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("taking a break" in t for t in texts), texts)
        self.assertTrue(
            EventInvitation.objects.filter(
                event=self.event, guest_email="Resting@crush.lu"
            ).exists()
        )

    def test_active_member_gets_no_warning(self):
        CrushProfile.objects.filter(pk=self.profile.pk).update(on_break_at=None)
        response = self._invite("resting@crush.lu")
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertFalse(any("taking a break" in t for t in texts), texts)

    def test_warning_is_translated(self):
        from crush_lu.services.on_break import warn_if_inviting_on_break

        request = RequestFactory().get("/")
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        with translation.override("de"):
            warn_if_inviting_on_break(request, users=[self.member])
        texts = [str(m) for m in get_messages(request)]
        self.assertEqual(len(texts), 1)
        self.assertIn("Pause", texts[0])
        self.assertIn("Mia", texts[0])

    def test_admin_invitation_save_warns(self):
        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.events import EventInvitationAdmin

        request = RequestFactory().post("/")
        request.user = self.coach.user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        invitation = EventInvitation(
            event=self.event,
            guest_email="resting@crush.lu",
            guest_first_name="Mia",
            guest_last_name="Rest",
        )
        EventInvitationAdmin(EventInvitation, crush_admin_site).save_model(
            request, invitation, form=mock.Mock(changed_data=[]), change=False
        )
        texts = [str(m) for m in get_messages(request)]
        self.assertTrue(any("taking a break" in t for t in texts), texts)

    def _event_admin_save_related(self, initial_users, users, guest_forms=()):
        from django.contrib import admin

        from crush_lu.admin import crush_admin_site
        from crush_lu.admin.events import MeetupEventAdmin

        request = RequestFactory().post("/")
        request.user = self.coach.user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        form = mock.Mock(
            instance=self.event,
            initial={"invited_users": initial_users},
            cleaned_data={"invited_users": users},
        )
        formsets = [mock.Mock(model=EventInvitation, forms=list(guest_forms))]
        with mock.patch.object(admin.ModelAdmin, "save_related"):
            MeetupEventAdmin(MeetupEvent, crush_admin_site).save_related(
                request, form, formsets, change=True
            )
        return [str(m) for m in get_messages(request)]

    def test_event_admin_warns_for_a_newly_invited_user(self):
        texts = self._event_admin_save_related([], [self.member])
        self.assertTrue(any("taking a break" in t for t in texts), texts)

    def test_event_admin_does_not_rewarn_an_already_invited_user(self):
        texts = self._event_admin_save_related([self.member], [self.member])
        self.assertEqual(texts, [])

    def test_event_admin_warns_for_a_new_inline_guest_row(self):
        row = mock.Mock(
            changed_data=["guest_email"],
            cleaned_data={"guest_email": "resting@crush.lu", "DELETE": False},
        )
        texts = self._event_admin_save_related([], [], guest_forms=[row])
        self.assertTrue(any("taking a break" in t for t in texts), texts)


# ---------------------------------------------------------------------------
# 4-18: the share button uses the referral link
# ---------------------------------------------------------------------------


class EventShareReferralTests(TestCase):
    def setUp(self):
        cache.clear()
        self.event = _event()
        self.client = Client(HTTP_HOST="crush.lu")

    def _share_url(self):
        from html.parser import HTMLParser

        found = {}

        class Finder(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get("id") == "shareEventBtn":
                    found["url"] = attrs.get("data-share-url")

        Finder().feed(self.client.get(f"/en/events/{self.event.pk}/").content.decode())
        return found["url"]

    def test_member_with_a_code_shares_the_referral_link_to_the_event(self):
        user, profile = _member("sharer@crush.lu")
        code = ReferralCode.objects.create(referrer=profile)
        self.client.force_login(user)
        self.assertEqual(
            self._share_url(),
            f"http://crush.lu/en/r/{code.code}/?next=%2Fen%2Fevents%2F{self.event.pk}%2F",
        )

    def test_member_without_a_code_shares_the_plain_url(self):
        user, _ = _member("nocode@crush.lu")
        self.client.force_login(user)
        self.assertEqual(
            self._share_url(), f"http://crush.lu/en/events/{self.event.pk}/"
        )
        self.assertFalse(ReferralCode.objects.exists())

    def test_visitor_shares_the_plain_url(self):
        self.assertEqual(
            self._share_url(), f"http://crush.lu/en/events/{self.event.pk}/"
        )

    def test_referral_link_lands_on_the_event_and_keeps_the_code(self):
        _user, profile = _member("referrer@crush.lu")
        code = ReferralCode.objects.create(referrer=profile)
        response = self.client.get(
            f"/en/r/{code.code}/", {"next": f"/en/events/{self.event.pk}/"}
        )
        self.assertEqual(response.url, f"/en/events/{self.event.pk}/")
        self.assertEqual(self.client.session["referral_code"], code.code)

    def test_offsite_next_is_ignored(self):
        _user, profile = _member("referrer2@crush.lu")
        code = ReferralCode.objects.create(referrer=profile)
        response = self.client.get(
            f"/en/r/{code.code}/", {"next": "https://evil.example/"}
        )
        self.assertIn("/signup/", response.url)
