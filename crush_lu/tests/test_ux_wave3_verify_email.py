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
