"""
UX review Wave 3, WP1 -- verify-email (findings 2-03, 2-14, 2-15).

2-03  account/verification_sent_crush.html showed no clue which address the
      link went to, resend silently no-opped once the session was empty, and
      the expired-link CTA pointed at a login-required page. It now masks
      the pending address from the session, offers a "wrong address?" link,
      accepts a typed-in address with a generic response when the session is
      empty, enforces a server-side 60s cooldown, and points the expired-link
      CTA at the public resend page.

2-14  socialaccount/authentication_error_crush.html dumped the raw
      ``auth_error`` dict and showed a fixed three-reason list. The adapter
      now treats a genuine provider cancel (AuthError.CANCELLED) as a
      friendly redirect back to login instead of an error page; anything
      else keeps rendering the crush template, whose raw details now sit
      behind a <details> disclosure instead of printing the dict.

2-15  socialaccount/signup_crush.html always rendered the email readonly
      (even when the provider gave none / it conflicted with an existing
      account) and its Terms checkbox had no `name`, so consent was never
      submitted. Both are fixed; consent is persisted via a new
      `user_signed_up` receiver that only fires for the interactive
      completion form (auto-signup never posts to socialaccount_signup).
"""

from unittest.mock import Mock

from allauth.account.models import EmailAddress
from allauth.core import context as allauth_context
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.providers.base import AuthError
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve
from django.utils import translation

from azureproject.adapters import MultiDomainSocialAccountAdapter
from crush_lu.models.profiles import UserDataConsent
from crush_lu.signals import record_interactive_social_signup_consent

User = get_user_model()


def _render_crush_page(template_name, context):
    """render_to_string, but through a real (crush.lu) request so base.html's
    context processors run -- notably i18n's LANGUAGE_CODE, which the JS
    catalog URL in base.html reverses. A bare render_to_string() call has no
    request, so that context var is undefined (silently '') and reverse()
    fails; see AuthenticationErrorTemplateTests' docstring for the urlconf
    half of this same trap.
    """
    request = RequestFactory().get("/", HTTP_HOST="crush.lu")
    request.user = AnonymousUser()
    request.session = SessionStore()
    with translation.override("en"), override_settings(
        ROOT_URLCONF="azureproject.urls_crush"
    ):
        return render_to_string(template_name, context, request=request)


def _unverified_user(email):
    user = User.objects.create_user(
        username=email, email=email, password="Str0ng-pass-2026!"
    )
    address = EmailAddress.objects.create(
        user=user, email=email, primary=True, verified=False
    )
    return user, address


# ---------------------------------------------------------------------------
# 2-03: masked email, wrong-address link, resend with/without session, cooldown
# ---------------------------------------------------------------------------


class VerificationSentPageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_masks_the_pending_email_from_session(self):
        client = Client(HTTP_HOST="crush.lu")
        session = client.session
        session["pending_verification_email"] = "tomasz@example.com"
        session.save()

        html = client.get("/accounts/confirm-email/").content.decode()
        self.assertIn("t*****@example.com", html)
        self.assertNotIn("tomasz@example.com", html)

    def test_wrong_address_link_goes_to_signup(self):
        client = Client(HTTP_HOST="crush.lu")
        session = client.session
        session["pending_verification_email"] = "member@example.com"
        session.save()

        html = client.get("/accounts/confirm-email/").content.decode()
        self.assertIn('href="/en/signup/"', html)

    def test_empty_session_shows_an_email_field(self):
        html = (
            Client(HTTP_HOST="crush.lu")
            .get("/accounts/confirm-email/")
            .content.decode()
        )
        self.assertIn('name="email"', html)
        self.assertNotIn("t***", html)

    def test_localhost_with_a_port_still_gets_the_crush_page(self):
        """account/verification_sent.html's router matched the bare string
        'localhost' exactly, so live_server's dev host (localhost:PORT --
        what Playwright and `manage.py runserver` on the documented port
        both use) fell through to the generic neutral template instead of
        this one. Caught while shooting screenshots for this same finding;
        fixed alongside account/email_confirm.html, which routes the same
        way."""
        html = (
            Client(HTTP_HOST="localhost:54321")
            .get("/accounts/confirm-email/")
            .content.decode()
        )
        self.assertIn("Check Your Inbox", html)

    def test_expired_link_cta_points_at_the_public_resend_page(self):
        html = (
            Client(HTTP_HOST="crush.lu")
            .get("/accounts/confirm-email/does-not-exist/")
            .content.decode()
        )
        self.assertIn('href="/accounts/confirm-email/"', html)
        self.assertNotIn('href="/account/settings/', html)


class ResendVerificationEmailViewTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_resend_uses_the_session_email(self):
        _user, address = _unverified_user("resend@example.com")
        client = Client(HTTP_HOST="crush.lu")
        session = client.session
        session["pending_verification_email"] = "resend@example.com"
        session.save()

        response = client.post("/en/signup/resend-verification/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_does_not_leak_the_raw_email_in_a_message_banner(self):
        """EmailAddress.send_confirmation() fires allauth's stock
        'Confirmation email sent to {email}.' message regardless of caller.
        On crush.lu that would print the full, unmasked address in a toast
        directly above the *masked* one this page shows -- see
        MultiDomainAccountAdapter.add_message. Follow the redirect so the
        message actually renders into the page (messages are consumed on
        read, not fired-and-forgotten)."""
        _user, _address = _unverified_user("noleak@example.com")
        client = Client(HTTP_HOST="crush.lu")
        session = client.session
        session["pending_verification_email"] = "noleak@example.com"
        session.save()

        client.post("/en/signup/resend-verification/")
        html = client.get("/accounts/confirm-email/").content.decode()
        self.assertNotIn("noleak@example.com", html)

    def test_resend_accepts_a_typed_email_when_session_is_empty(self):
        _user, _address = _unverified_user("typed@example.com")
        client = Client(HTTP_HOST="crush.lu")

        response = client.post(
            "/en/signup/resend-verification/", {"email": "typed@example.com"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_unknown_email_gets_the_same_generic_response(self):
        """No account exists for this address -- response must not differ."""
        response_known = self._resend_for("known@example.com", seed=True)
        cache.clear()
        response_unknown = self._resend_for("nobody@example.com", seed=False)
        self.assertEqual(response_known.status_code, response_unknown.status_code)
        self.assertEqual(response_known.url, response_unknown.url)

    def _resend_for(self, email, seed):
        if seed:
            _unverified_user(email)
        client = Client(HTTP_HOST="crush.lu")
        return client.post("/en/signup/resend-verification/", {"email": email})

    def test_second_resend_within_60s_does_not_send_again(self):
        """Server-side cooldown: a same-session repeat click is a no-op send,
        but still returns the identical generic success response so it can't
        be used to probe whether the address exists."""
        _user, _address = _unverified_user("cooldown@example.com")
        client = Client(HTTP_HOST="crush.lu")
        session = client.session
        session["pending_verification_email"] = "cooldown@example.com"
        session.save()

        first = client.post("/en/signup/resend-verification/")
        second = client.post("/en/signup/resend-verification/")

        self.assertEqual(first.status_code, second.status_code)
        self.assertEqual(first.url, second.url)
        # Only the first POST actually triggered a send.
        self.assertEqual(len(mail.outbox), 1)

    def test_no_email_resolved_does_not_start_a_cooldown(self):
        """A POST with an empty session and no ``email`` field resolves no
        address at all -- nothing is sent, so it must not self-lock the
        visitor's own resend button for 60s. A follow-up POST that *does*
        supply a real address must still be able to send."""
        _user, _address = _unverified_user("late@example.com")
        client = Client(HTTP_HOST="crush.lu")

        first = client.post("/en/signup/resend-verification/")
        self.assertEqual(first.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

        html = client.get("/accounts/confirm-email/").content.decode()
        self.assertRegex(html, r'data-cooldown-until="0"')

        second = client.post(
            "/en/signup/resend-verification/", {"email": "late@example.com"}
        )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_cooldown_is_visible_on_the_next_page_load(self):
        _user, _address = _unverified_user("visible@example.com")
        client = Client(HTTP_HOST="crush.lu")
        session = client.session
        session["pending_verification_email"] = "visible@example.com"
        session.save()

        client.post("/en/signup/resend-verification/")
        html = client.get("/accounts/confirm-email/").content.decode()
        self.assertIn("data-cooldown-until=", html)
        self.assertNotRegex(html, r'data-cooldown-until="0"')


# ---------------------------------------------------------------------------
# 2-14: social-login cancel vs. other errors
# ---------------------------------------------------------------------------


class AuthenticationErrorAdapterTests(TestCase):
    """Unit coverage of MultiDomainSocialAccountAdapter.on_authentication_error.

    Mirrors EmailVerificationRedirectAdapterTests in test_auth_funnel.py:
    the adapter reads request.session/request.user itself, so a bare
    RequestFactory request (with session + message storage attached) is
    enough -- no need to drive a real OAuth2 provider round trip just to
    reach this one hook.
    """

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def _request(self, host="crush.lu"):
        request = self.factory.get("/accounts/google/login/callback/", HTTP_HOST=host)
        request.user = AnonymousUser()
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        return request

    def test_cancelled_login_redirects_to_login_with_a_friendly_message(self):
        request = self._request()
        adapter = MultiDomainSocialAccountAdapter()
        with allauth_context.request_context(request):
            with self.assertRaises(ImmediateHttpResponse) as caught:
                adapter.on_authentication_error(
                    request, "google", error=AuthError.CANCELLED
                )
        response = caught.exception.response
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("/login/"))
        messages = list(request._messages)
        self.assertEqual(len(messages), 1)
        self.assertIn("cancelled", str(messages[0]).lower())

    def test_cancelled_login_on_other_domains_is_unchanged(self):
        request = self._request(host="power-up.lu")
        adapter = MultiDomainSocialAccountAdapter()
        with allauth_context.request_context(request):
            result = adapter.on_authentication_error(
                request, "microsoft", error=AuthError.CANCELLED
            )
        self.assertIsNone(result)
        self.assertEqual(len(list(request._messages)), 0)

    def test_other_errors_do_not_redirect(self):
        request = self._request()
        adapter = MultiDomainSocialAccountAdapter()
        with allauth_context.request_context(request):
            result = adapter.on_authentication_error(
                request, "google", error=AuthError.DENIED
            )
        self.assertIsNone(result)
        self.assertEqual(len(list(request._messages)), 0)


class AuthenticationErrorTemplateTests(TestCase):
    """The details of a non-cancel error must not leak the raw dict."""

    def test_raw_auth_error_dict_is_never_printed(self):
        html = _render_crush_page(
            "socialaccount/authentication_error_crush.html",
            {
                "auth_error": {
                    "provider": "google",
                    "code": AuthError.DENIED,
                    "exception": None,
                }
            },
        )
        self.assertNotIn("{'provider'", html)
        self.assertIn("<details", html)
        self.assertIn("btn-crush-outline", html)


# ---------------------------------------------------------------------------
# 2-15: editable email + recorded consent on the social-signup completion form
# ---------------------------------------------------------------------------


class _FakeSocialSignupForm(forms.Form):
    """Stand-in for allauth.socialaccount.forms.SignupForm's email field.

    A real bound/unbound Form (rather than unittest.mock.Mock) so
    BoundField.value()/.errors behave exactly as the template expects --
    a bare Mock is auto-callable and auto-vivifies attributes like
    do_not_call_in_templates, which silently changes how Django's template
    engine resolves it.
    """

    email = forms.EmailField()


class SignupCrushTemplateTests(TestCase):
    def _render(self, email_value="", email_error=None, provider="google"):
        if email_error:
            form = _FakeSocialSignupForm({"email": email_value})
            form.is_valid()
            form.add_error("email", email_error)
        else:
            # Unbound (no POST yet): mirrors the first GET of the completion
            # page, where .errors is empty and .value() returns the initial.
            form = _FakeSocialSignupForm(initial={"email": email_value})
        account = Mock()
        account.provider = provider
        return _render_crush_page(
            "socialaccount/signup_crush.html",
            {"form": form, "account": account},
        )

    def test_checkbox_has_a_name_so_consent_is_actually_submitted(self):
        html = self._render(email_value="member@example.com")
        self.assertIn('name="crushlu_consent"', html)

    def test_email_is_editable_when_the_provider_gave_none(self):
        html = self._render(email_value="")
        self.assertNotIn("readonly", html)
        self.assertIn('name="email"', html)

    def test_email_is_editable_on_a_duplicate_error(self):
        html = self._render(
            email_value="taken@example.com", email_error="Already registered."
        )
        self.assertNotIn("readonly", html)
        self.assertIn("Already registered.", html)
        self.assertIn("/login/", html)
        # No more raw "Email: <message>" field-key dump.
        self.assertNotIn("Email: Already registered.", html)

    def test_email_stays_readonly_when_the_provider_gave_a_clean_one(self):
        html = self._render(email_value="member@example.com")
        self.assertIn("readonly", html)


class SocialSignupConsentSignalTests(TestCase):
    """record_interactive_social_signup_consent (crush_lu/signals.py)."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="social@example.com", email="social@example.com"
        )
        # create_user_data_consent() (post_save on User) already created the
        # row; set it to the implicit OAuth default (True) it would carry
        # coming out of pre_social_login, for this receiver to then act on.
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )

    def _post(self, data, host="crush.lu"):
        # Not /accounts/social/signup/ -- that legacy path is an unnamed
        # RedirectView (allauth/urls.py), so its resolver_match.url_name is
        # None. The real signup_crush.html form posts to {% url
        # 'socialaccount_signup' %}, which is accounts/3rdparty/signup/.
        path = "/accounts/3rdparty/signup/"
        request = RequestFactory().post(path, data, HTTP_HOST=host)
        request.resolver_match = resolve(path)
        return request

    def test_unchecked_box_overwrites_the_implicit_true_with_false(self):
        """The real bug this closes: browsers never send an unticked
        checkbox, so detecting "did they submit the form" by the field's
        presence in POST could only ever write True. Route-based detection
        lets a real "no" through."""
        request = self._post({})  # checkbox not ticked -> absent from POST
        record_interactive_social_signup_consent(
            sender=User, request=request, user=self.user, sociallogin=Mock()
        )
        self.user.data_consent.refresh_from_db()
        self.assertFalse(self.user.data_consent.crushlu_consent_given)

    def test_checked_box_is_recorded_as_true(self):
        request = self._post({"crushlu_consent": "on"})
        record_interactive_social_signup_consent(
            sender=User, request=request, user=self.user, sociallogin=Mock()
        )
        self.user.data_consent.refresh_from_db()
        self.assertTrue(self.user.data_consent.crushlu_consent_given)

    def test_any_value_the_form_accepts_is_recorded_as_true(self):
        """The required BooleanField accepts any truthy value ("yes", "1",
        even "no"), so the stored consent must be normalised the same way."""
        for value in ("yes", "1", "true", "no"):
            with self.subTest(value=value):
                request = self._post({"crushlu_consent": value})
                record_interactive_social_signup_consent(
                    sender=User, request=request, user=self.user, sociallogin=Mock()
                )
                self.user.data_consent.refresh_from_db()
                self.assertTrue(self.user.data_consent.crushlu_consent_given)

    def test_auto_signup_is_not_touched(self):
        """No sociallogin present at all -- pre_social_login's implicit
        default (True, seeded in setUp) must survive untouched."""
        request = self._post({})
        record_interactive_social_signup_consent(
            sender=User, request=request, user=self.user, sociallogin=None
        )
        self.user.data_consent.refresh_from_db()
        self.assertTrue(self.user.data_consent.crushlu_consent_given)

    def test_non_crush_domain_is_not_touched(self):
        request = self._post({}, host="power-up.lu")
        record_interactive_social_signup_consent(
            sender=User, request=request, user=self.user, sociallogin=Mock()
        )
        self.user.data_consent.refresh_from_db()
        self.assertTrue(self.user.data_consent.crushlu_consent_given)


# ---------------------------------------------------------------------------
# Review follow-ups (PR #1051): typed social emails must be verified, resend
# must answer identically for known and unknown addresses, and the resend
# button keeps a label without JavaScript.
# ---------------------------------------------------------------------------


class SocialLoginRequiresVerifiedEmailTests(TestCase):
    """MultiDomainAccountAdapter.pre_login holds crush.lu social logins until
    the account has a verified email (the completion form can now take a
    typed, unproven address)."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def _request(self, host="crush.lu"):
        request = self.factory.get("/accounts/google/login/callback/", HTTP_HOST=host)
        request.user = AnonymousUser()
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        return request

    def _pre_login(self, request, user, sociallogin=True, signup=True):
        from azureproject.adapters import MultiDomainAccountAdapter

        signal_kwargs = {"sociallogin": Mock()} if sociallogin else {}
        with allauth_context.request_context(request):
            return MultiDomainAccountAdapter(request).pre_login(
                request,
                user,
                email_verification="none",
                signal_kwargs=signal_kwargs,
                email=None,
                signup=signup,
                redirect_url=None,
            )

    def test_typed_unverified_email_is_held_for_verification(self):
        user, _address = _unverified_user("typed@example.com")
        request = self._request()
        response = self._pre_login(request, user)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["typed@example.com"])
        self.assertEqual(
            request.session["pending_verification_email"], "typed@example.com"
        )

    def test_later_social_login_is_held_too(self):
        user, _address = _unverified_user("typed2@example.com")
        response = self._pre_login(self._request(), user, signup=False)
        self.assertIsNotNone(response)

    def test_provider_verified_email_passes(self):
        user, address = _unverified_user("verified@example.com")
        address.verified = True
        address.save()
        self.assertIsNone(self._pre_login(self._request(), user))
        self.assertEqual(len(mail.outbox), 0)

    def test_password_login_is_not_affected(self):
        user, _address = _unverified_user("pw@example.com")
        self.assertIsNone(self._pre_login(self._request(), user, sociallogin=False))

    def test_other_domains_are_not_affected(self):
        user, _address = _unverified_user("other@example.com")
        self.assertIsNone(self._pre_login(self._request(host="power-up.lu"), user))


class ResendFailureIsIndistinguishableTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_a_mail_failure_gives_the_same_redirect_as_an_unknown_address(self):
        from unittest.mock import patch

        _unverified_user("known@example.com")
        with patch.object(
            EmailAddress, "send_confirmation", side_effect=RuntimeError("graph down")
        ):
            known = self.client.post(
                "/en/signup/resend-verification/", {"email": "known@example.com"}
            )
        other = Client(HTTP_HOST="crush.lu").post(
            "/en/signup/resend-verification/", {"email": "nobody@example.com"}
        )
        self.assertEqual(known.status_code, 302)
        self.assertEqual(known.status_code, other.status_code)
        self.assertEqual(known.url, other.url)


class ResendButtonWithoutJavascriptTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_default_label_is_not_cloaked(self):
        response = Client(HTTP_HOST="crush.lu").get("/accounts/confirm-email/")
        html = response.content.decode()
        self.assertIn('<span x-show="enabled">', html)


# ---------------------------------------------------------------------------
# Second review round (PR #1051)
# ---------------------------------------------------------------------------


class SocialLoginVerificationRateLimitTests(SocialLoginRequiresVerifiedEmailTests):
    def test_repeated_social_logins_send_only_one_email(self):
        user, _address = _unverified_user("spam@example.com")
        self.assertIsNotNone(self._pre_login(self._request(), user))
        self.assertIsNotNone(self._pre_login(self._request(), user, signup=False))
        self.assertEqual(len(mail.outbox), 1)

    def test_second_session_shows_the_shared_cooldown_immediately(self):
        # A second browser logs in during the first one's cooldown: no mail is
        # sent (limiter not consumed), but its page must still count down
        # instead of opening with a live Resend button (#1116).
        user, _address = _unverified_user("shared@example.com")
        first = self._request()
        self.assertIsNotNone(self._pre_login(first, user))
        second = self._request()
        self.assertIsNotNone(self._pre_login(second, user, signup=False))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            second.session["resend_verification_cooldown_until"],
            first.session["resend_verification_cooldown_until"],
        )
        self.assertIn("resend_verification_cooldown_hash", second.session)


class ConfirmationBannerScopeTests(TestCase):
    def setUp(self):
        cache.clear()

    def _add(self, user):
        from django.contrib import messages as dj_messages

        from azureproject.adapters import MultiDomainAccountAdapter

        request = RequestFactory().get("/accounts/email/", HTTP_HOST="crush.lu")
        request.user = user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        MultiDomainAccountAdapter(request).add_message(
            request,
            dj_messages.INFO,
            "account/messages/email_confirmation_sent.txt",
            {"email": "someone@example.com"},
        )
        return list(request._messages)

    def test_signed_out_visitors_do_not_get_the_raw_email_banner(self):
        self.assertEqual(self._add(AnonymousUser()), [])

    def test_signed_in_email_management_keeps_its_success_banner(self):
        user, _address = _unverified_user("member@example.com")
        self.assertEqual(len(self._add(user)), 1)


class SocialSignupConsentValidationTests(TestCase):
    def setUp(self):
        cache.clear()

    def _form(self, host, data):
        from allauth.socialaccount.models import SocialAccount, SocialLogin

        from azureproject.social_forms import MultiDomainSocialSignupForm

        request = RequestFactory().post("/accounts/social/signup/", HTTP_HOST=host)
        request.user = AnonymousUser()
        request.session = SessionStore()
        sociallogin = SocialLogin(
            user=User(email=""), account=SocialAccount(provider="google", uid="1")
        )
        with allauth_context.request_context(request):
            form = MultiDomainSocialSignupForm(data=data, sociallogin=sociallogin)
            form.is_valid()
        return form

    def test_crush_signup_without_consent_is_rejected(self):
        form = self._form("crush.lu", {"email": "new@example.com"})
        self.assertIn("crushlu_consent", form.errors)

    def test_crush_signup_with_consent_passes_the_consent_check(self):
        form = self._form(
            "crush.lu", {"email": "new@example.com", "crushlu_consent": "on"}
        )
        self.assertNotIn("crushlu_consent", form.errors)

    def test_other_domains_do_not_ask_for_crush_consent(self):
        form = self._form("power-up.lu", {"email": "new@example.com"})
        self.assertNotIn("crushlu_consent", form.fields)


class ResendAddressCorrectionTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_typed_address_overrides_a_mistyped_session_address(self):
        _unverified_user("right@example.com")
        client = Client(HTTP_HOST="crush.lu")
        session = client.session
        session["pending_verification_email"] = "wrnog@example.com"
        session.save()
        client.post("/en/signup/resend-verification/", {"email": "right@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["right@example.com"])
        self.assertEqual(
            client.session["pending_verification_email"], "right@example.com"
        )

    def test_page_offers_a_correction_field_when_an_address_is_pending(self):
        client = Client(HTTP_HOST="crush.lu")
        session = client.session
        session["pending_verification_email"] = "someone@example.com"
        session.save()
        html = client.get("/accounts/confirm-email/").content.decode()
        self.assertIn("Use a different address", html)
        self.assertIn('name="email"', html)


class PublicResendPerAddressLimitTests(TestCase):
    """Fresh sessions must not bypass the per-address resend limit."""

    def setUp(self):
        cache.clear()

    def test_two_fresh_sessions_send_only_one_email_to_the_same_address(self):
        _unverified_user("target@example.com")
        for _ in range(2):
            Client(HTTP_HOST="crush.lu").post(
                "/en/signup/resend-verification/", {"email": "target@example.com"}
            )
        self.assertEqual(len(mail.outbox), 1)


class CooldownMatchesAllauthLimiterTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_resend_cooldown_is_allauths_confirmation_cooldown(self):
        from allauth.account import app_settings as allauth_settings

        from crush_lu.views_account import RESEND_VERIFICATION_COOLDOWN_SECONDS

        expected = allauth_settings.RATE_LIMITS["confirm_email"]
        self.assertEqual(expected, f"1/{RESEND_VERIFICATION_COOLDOWN_SECONDS}s/key")

    def test_resend_starts_the_full_cooldown(self):
        from django.utils import timezone

        from crush_lu.views_account import RESEND_VERIFICATION_COOLDOWN_SECONDS

        _unverified_user("cool@example.com")
        client = Client(HTTP_HOST="crush.lu")
        before = int(timezone.now().timestamp())
        client.post("/en/signup/resend-verification/", {"email": "cool@example.com"})
        until = client.session["resend_verification_cooldown_until"]
        self.assertGreaterEqual(until, before + RESEND_VERIFICATION_COOLDOWN_SECONDS)


class SocialLoginMailFailureTests(SocialLoginRequiresVerifiedEmailTests):
    def test_a_mail_failure_still_holds_the_login_without_a_500(self):
        from unittest.mock import patch

        user, _address = _unverified_user("broken@example.com")
        request = self._request()
        with patch.object(
            EmailAddress, "send_confirmation", side_effect=RuntimeError("graph down")
        ):
            response = self._pre_login(request, user)
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 302)
        self.assertIn("resend_verification_cooldown_until", request.session)


class SocialLoginRateLimitedRetryTests(SocialLoginRequiresVerifiedEmailTests):
    def test_a_rate_limited_retry_keeps_the_existing_deadline(self):
        user, _address = _unverified_user("retry@example.com")
        request = self._request()
        self.assertIsNotNone(self._pre_login(request, user))  # sends, sets deadline
        request.session["resend_verification_cooldown_until"] = 12345
        self.assertIsNotNone(self._pre_login(request, user, signup=False))  # limited
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(request.session["resend_verification_cooldown_until"], 12345)
