"""UX Wave 4 · WP12 followups-flows.

#1059 resend cooldown (atomic, per address), "Use a different address" for a
held social account, crush.localhost as crush, the read-only
count_social_without_email command; #1079 SumUp return (unpublished event,
staff viewer) and the donation CTA msgid; #1085 App Insights on anonymous
pages. Decisions 5-13, 8-13 and 4-18 live in test_ux_wave4_decided_flows.py
(WP12b).

Literal paths, not reverse(), per AGENTS.md.
"""

import os
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock
from uuid import uuid4

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from cookie_consent.cache import delete_cache
from cookie_consent.models import CookieGroup
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

from crush_lu.models import CrushCoach, EventInvitation, ProfileSubmission
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
        session["pending_verification_user_id_at"] = int(timezone.now().timestamp())
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

    def test_rejected_correction_keeps_the_held_account_for_a_later_one(self):
        """A correction to an address another account owns is skipped, but
        the session stays bound to the held account, so a second, available
        address still repairs it without repeating the provider login."""
        _unverified_user("taken@example.com")
        self.client.post(RESEND, {"email": "taken@example.com"})
        session = self.client.session
        self.assertEqual(session["pending_verification_email"], "typo@exmaple.com")
        self.assertEqual(session["pending_verification_user_id"], self.user.pk)

        self.client.post(RESEND, {"email": "fixed@example.com"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "fixed@example.com")
        self.assertEqual(
            list(
                EmailAddress.objects.filter(user=self.user).values_list(
                    "email", flat=True
                )
            ),
            ["fixed@example.com"],
        )

    def test_expired_hold_cannot_rewrite_the_account(self):
        """The hold is honoured only briefly: on a shared device a later
        visitor of the same long-lived session gets the generic response."""
        other = Client(HTTP_HOST="crush.lu").post(RESEND, {"email": "nobody@x.lu"})
        # SOCIAL_ADDRESS_REWRITE_WINDOW_SECONDS is 30 minutes.
        later = timezone.now() + timezone.timedelta(minutes=31)
        with mock.patch("django.utils.timezone.now", return_value=later):
            response = self.client.post(RESEND, {"email": "fixed@example.com"})
        self.assertEqual(response.url, other.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "typo@exmaple.com")
        self.assertTrue(EmailAddress.objects.filter(pk=self.address.pk).exists())
        self.assertNotIn("pending_verification_user_id", self.client.session)

    def test_hold_is_consumed_by_one_successful_rewrite(self):
        self.client.post(RESEND, {"email": "fixed@example.com"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "fixed@example.com")

        cache.clear()  # the second POST must not be stopped by the cooldown
        response = self.client.post(RESEND, {"email": "attacker@example.com"})
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "fixed@example.com")
        self.assertEqual(
            list(
                EmailAddress.objects.filter(user=self.user).values_list(
                    "email", flat=True
                )
            ),
            ["fixed@example.com"],
        )
        self.assertNotIn("pending_verification_user_id", self.client.session)
        self.assertNotIn("pending_verification_user_id_at", self.client.session)

    def test_over_long_address_is_rejected_before_any_save(self):
        """validate_email allows 320 characters, the columns 254: Postgres
        would raise DataError (a 500), so the correction is refused first
        and the hold survives for a usable address."""
        long_email = "a" * 60 + "@" + ".".join(["b" * 60] * 4) + ".com"
        self.assertGreater(len(long_email), 254)
        with mock.patch.object(User, "save") as user_save:
            response = self.client.post(RESEND, {"email": long_email})
        self.assertEqual(response.status_code, 302)
        user_save.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "typo@exmaple.com")
        self.assertEqual(
            list(EmailAddress.objects.filter(user=self.user).values_list("pk")),
            [(self.address.pk,)],
        )
        self.assertFalse(EmailAddress.objects.filter(email=long_email).exists())
        self.assertEqual(
            self.client.session["pending_verification_user_id"], self.user.pk
        )

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

    def test_rejected_correction_keeps_the_restored_address_cooldown(self):
        """The page shows the held address again after a rejected
        correction, so its countdown must stay the held address's, not
        the rejected typed one's."""
        import hashlib

        _unverified_user("taken@example.com")
        session = self.client.session
        session["resend_verification_cooldown_until"] = 0
        session["resend_verification_cooldown_hash"] = hashlib.sha256(
            b"typo@exmaple.com"
        ).hexdigest()
        session.save()
        self.client.post(RESEND, {"email": "taken@example.com"})
        session = self.client.session
        self.assertEqual(session["pending_verification_email"], "typo@exmaple.com")
        self.assertEqual(session["resend_verification_cooldown_until"], 0)
        self.assertEqual(
            session["resend_verification_cooldown_hash"],
            hashlib.sha256(b"typo@exmaple.com").hexdigest(),
        )

    def test_expired_or_consumed_hold_shows_the_sign_up_again_link(self):
        """The link is hidden only while the hold can still rewrite the
        account: once the window passes, or the one rewrite is spent, the
        page offers account recovery again."""
        later = timezone.now() + timezone.timedelta(minutes=31)
        with mock.patch("django.utils.timezone.now", return_value=later):
            expired = self.client.get("/accounts/confirm-email/").content.decode()
        self.assertIn('href="/en/signup/"', expired)
        self.assertNotIn("pending_verification_user_id", self.client.session)

        consumed = Client(HTTP_HOST="crush.lu")
        session = consumed.session
        session["pending_verification_email"] = "typo@exmaple.com"
        session["pending_verification_user_id"] = self.user.pk
        session.save()  # no issued-at: a hold that was already used
        html = consumed.get("/accounts/confirm-email/").content.decode()
        self.assertIn('href="/en/signup/"', html)

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
        self.assertLessEqual(
            abs(
                request.session["pending_verification_user_id_at"]
                - timezone.now().timestamp()
            ),
            5,
        )
        self.assertFalse(claim_resend_cooldown("held@example.com"))


# ---------------------------------------------------------------------------
# #1059: crush.localhost
# ---------------------------------------------------------------------------


class CrushLocalhostDomainTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_crush_localhost_counts_as_crush(self):
        from azureproject.adapters import _is_crush_domain
        from crush_lu.signals import _is_crush_domain as signals_is_crush

        request = RequestFactory().get("/", HTTP_HOST="crush.localhost:8000")
        self.assertTrue(_is_crush_domain(request))
        self.assertTrue(signals_is_crush(request))
        other = RequestFactory().get("/", HTTP_HOST="power-up.localhost:8000")
        self.assertFalse(_is_crush_domain(other))
        self.assertFalse(signals_is_crush(other))

    def test_crush_localhost_is_open_for_social_signup(self):
        """First-time social login on the dev alias passes the same signup
        gate as crush.lu; production hosts are gated exactly as before."""
        from azureproject.adapters import MultiDomainSocialAccountAdapter

        adapter = MultiDomainSocialAccountAdapter()
        results = {
            host: adapter.is_open_for_signup(
                RequestFactory().get("/", HTTP_HOST=host), mock.Mock()
            )
            for host in (
                "crush.localhost:8000",
                "crush.lu",
                "delegations.lu",
                "power-up.lu",
                "entreprinder.lu",
                "power-up.localhost:8000",
            )
        }
        self.assertEqual(
            results,
            {
                "crush.localhost:8000": True,
                "crush.lu": True,
                "delegations.lu": True,
                "power-up.lu": False,
                "entreprinder.lu": False,
                "power-up.localhost:8000": False,
            },
        )


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

    def test_staff_message_reports_the_status_the_sync_just_recorded(self, _sync):
        """The sync marks a separately locked row paid; the response must
        not report the caller's stale in-memory "Pending"."""
        tx = self._tx()
        _sync.side_effect = lambda obj: PaymentTransaction.objects.filter(
            pk=obj.pk
        ).update(status=PaymentTransaction.Status.PAID)
        staff, _ = _member("staff3@crush.lu")
        User.objects.filter(pk=staff.pk).update(is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(
            "/payments/sumup/return/", {"ref": tx.transaction_reference}
        )
        texts = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(
            texts,
            ["This checkout belongs to another member. Payment status: Paid."],
        )

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


class AnonymousAppInsightsAllowlistTests(TestCase):
    """Codex P1 on #1105: page views send the full URL, so anonymous visitors
    get App Insights only on the allowlisted login and signup pages, never on
    pages whose URL is a credential (/book/<token>/, /invite/<code>/)."""

    ENV = AnonymousAppInsightsTests.ENV
    LIVE = "Azure Application Insights Browser SDK v3 -->"
    MARKERS = (LIVE, "Azure Application Insights (waiting", "ai.3.gbl.min.js")

    def setUp(self):
        cache.clear()
        CookieGroup.objects.create(varname="analytics", name="Analytics")
        delete_cache()
        self.client = Client(HTTP_HOST="crush.lu")
        # Real analytics consent through django-cookie-consent's own view.
        response = self.client.post("/cookies/accept/", {"cookie_groups": "analytics"})
        self.assertEqual(response.status_code, 302)

    def tearDown(self):
        delete_cache()

    def _html(self, path):
        with mock.patch.dict(os.environ, self.ENV):
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        return response.content.decode()

    def _assert_no_loader(self, html, path):
        for marker in self.MARKERS:
            self.assertNotIn(marker, html, f"{path}: {marker}")

    def test_consenting_anonymous_visitor_gets_the_sdk_on_login(self):
        self.assertIn(self.LIVE, self._html("/en/login/"))

    def test_consenting_anonymous_visitor_gets_the_sdk_on_signup(self):
        self.assertIn(self.LIVE, self._html("/en/signup/"))

    def test_booking_token_page_has_no_loader_for_anonymous_visitor(self):
        member, profile = _member("booker@crush.lu", gender="M")
        coach_user = User.objects.create_user(
            username="coach-ai@crush.lu", email="coach-ai@crush.lu", password="x"
        )
        coach = CrushCoach.objects.create(
            user=coach_user,
            is_active=True,
            hybrid_features_enabled=True,
            working_mode="hybrid",
            availability_windows=[
                {"day": "monday", "start": "09:00", "end": "17:00", "label": ""}
            ],
        )
        submission = ProfileSubmission.objects.create(
            profile=profile,
            status="pending",
            coach=coach,
            booking_token=uuid4(),
            booking_token_expires_at=timezone.now() + timedelta(days=7),
        )
        path = f"/en/book/{submission.booking_token}/"
        self._assert_no_loader(self._html(path), path)

    def test_invitation_code_page_has_no_loader_for_anonymous_visitor(self):
        inviter = User.objects.create_user(
            username="inviter@crush.lu", email="inviter@crush.lu", password="x"
        )
        event = _event(
            is_private_invitation=True,
            invitation_code="vip-ai",
            invitation_expires_at=timezone.now() + timedelta(days=30),
        )
        invitation = EventInvitation.objects.create(
            event=event,
            guest_email="guest-ai@example.com",
            guest_first_name="Gia",
            guest_last_name="Guest",
            invited_by=inviter,
            status="pending",
            approval_status="pending_approval",
        )
        path = f"/en/invite/{invitation.invitation_code}/"
        self._assert_no_loader(self._html(path), path)

    def test_other_anonymous_pages_have_no_loader(self):
        self._assert_no_loader(self._html("/en/"), "/en/")

    def test_authenticated_member_still_gets_the_sdk(self):
        member, _ = _member("signed-in-ai@crush.lu")
        self.client.force_login(member)
        self.assertIn(self.LIVE, self._html("/en/my-events/"))
