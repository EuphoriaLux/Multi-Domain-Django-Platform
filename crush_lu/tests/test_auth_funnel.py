"""
Auth funnel regressions (UX review Wave 1, findings 2-01 and 2-02).

2-01  In German, the "Sign up" tab on the tabbed /login/ page read "anmelden"
      next to the "Login" tab's "Anmelden", so the two actions were
      indistinguishable. The collision check compares case-insensitively and
      dash-insensitively, because "Anmelden" vs "anmelden" is exactly how the
      bug looked on screen.

2-02  After clicking the confirmation link, anonymous crush.lu members landed
      on allauth's bare /accounts/login/ (no email prefill, no "Forgot your
      password?"). They now land on the tabbed /<lang>/login/ page, which
      prefills the address stashed by the email_confirmed handler. Every other
      domain keeps /accounts/login/.
"""

import re

import pytest
from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from allauth.core import context as allauth_context
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.utils import translation

from azureproject.adapters import MultiDomainAccountAdapter

User = get_user_model()

# (login msgid, signup msgid) pairs whose translations must never read alike.
LOGIN_VS_SIGNUP = [
    ("Login", "Sign up"),
    ("Log in", "Sign up"),
    ("Sign in", "Sign up"),
    # event_detail.html: "{Log in} or {sign up} to register for this event."
    ("Log in", "sign up"),
    ("log in", "sign up"),
    ("Login - Crush.lu", "Sign Up - Crush.lu"),
    ("Sign in with Google", "Sign up with Google"),
]


def _normalise(text):
    """Fold case and dash/space variants: the reader can't tell them apart."""
    return re.sub(r"[\s\-–—]+", " ", text).strip().casefold()


@pytest.mark.parametrize("lang", [code for code, _name in settings.LANGUAGES])
@pytest.mark.parametrize("login_msgid,signup_msgid", LOGIN_VS_SIGNUP)
def test_login_and_signup_labels_differ(lang, login_msgid, signup_msgid):
    with translation.override(lang):
        login = translation.gettext(login_msgid)
        signup = translation.gettext(signup_msgid)
    assert _normalise(login) != _normalise(signup), (
        f"[{lang}] {login_msgid!r} -> {login!r} and "
        f"{signup_msgid!r} -> {signup!r} read the same"
    )


def test_compiled_german_catalog_says_registrieren():
    """Proves the rebuilt .mo is the one Django loads, not just the .po."""
    with translation.override("de"):
        assert translation.gettext("Sign up") == "Registrieren"
        assert translation.gettext("sign up") == "registrieren"
        assert translation.gettext("Login") == "Anmelden"


def _input_tag(html, field_id):
    match = re.search(rf'<input\b[^>]*\bid="{field_id}"[^>]*>', html)
    assert match, f"#{field_id} is missing from the page"
    return match.group(0)


def _has_autofocus(html, field_id):
    return re.search(r"\sautofocus\b", _input_tag(html, field_id)) is not None


def _unverified_user(email):
    user = User.objects.create_user(
        username=email, email=email, password="Str0ng-pass-2026!"
    )
    address = EmailAddress.objects.create(
        user=user, email=email, primary=True, verified=False
    )
    return user, address


class EmailVerificationRedirectAdapterTests(TestCase):
    """Unit coverage of MultiDomainAccountAdapter.get_email_verification_redirect_url."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.user, self.address = _unverified_user("adapter@example.com")

    def _redirect_for(self, host, user=None):
        request = self.factory.post("/accounts/confirm-email/k/", HTTP_HOST=host)
        request.user = user or AnonymousUser()
        request.session = SessionStore()
        # allauth 65 adapters read the request from allauth's context var
        # (set by its middleware), not from the constructor argument.
        with allauth_context.request_context(request):
            adapter = MultiDomainAccountAdapter(request)
            return adapter.get_email_verification_redirect_url(self.address)

    def test_anonymous_crush_member_goes_to_tabbed_login_in_default_language(self):
        self.assertEqual(self._redirect_for("crush.lu"), "/en/login/")

    def test_anonymous_crush_member_keeps_the_active_language(self):
        for lang in ("de", "fr"):
            with self.subTest(lang=lang), translation.override(lang):
                self.assertEqual(self._redirect_for("crush.lu"), f"/{lang}/login/")

    def test_crush_subdomain_is_treated_as_crush(self):
        with translation.override("de"):
            self.assertEqual(self._redirect_for("test.crush.lu"), "/de/login/")

    def test_unsupported_active_language_falls_back_to_language_code(self):
        with translation.override("es"):
            self.assertEqual(
                self._redirect_for("crush.lu"), f"/{settings.LANGUAGE_CODE}/login/"
            )

    def test_authenticated_crush_member_keeps_allauth_default(self):
        # No ACCOUNT_EMAIL_CONFIRMATION_AUTHENTICATED_REDIRECT_URL is set, so
        # allauth falls back to get_login_redirect_url: no profile yet.
        self.assertEqual(
            self._redirect_for("crush.lu", user=self.user), "/create-profile/"
        )

    def test_other_domains_keep_the_global_setting(self):
        for host in (
            "entreprinder.lu",
            "power-up.lu",
            "delegations.lu",
            "vinsdelux.com",
            "arborist.lu",
        ):
            with self.subTest(host=host), translation.override("de"):
                self.assertEqual(self._redirect_for(host), "/accounts/login/")


class EmailConfirmationFlowTests(TestCase):
    """End to end: POST the real confirmation link, follow to the login page."""

    def setUp(self):
        cache.clear()

    def _confirm(self, host, email, **extra):
        _user, address = _unverified_user(email)
        key = EmailConfirmationHMAC(address).key
        client = Client(HTTP_HOST=host, **extra)
        response = client.post(f"/accounts/confirm-email/{key}/")
        address.refresh_from_db()
        self.assertTrue(address.verified)
        return client, response

    def test_crush_confirmation_lands_on_prefilled_tabbed_login(self):
        client, response = self._confirm("crush.lu", "member@example.com")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/en/login/")

        page = client.get(response.url)
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, "crush_lu/auth.html")
        html = page.content.decode()
        self.assertIn('value="member@example.com"', html)
        self.assertIn('href="/accounts/password/reset/"', html)

    def test_crush_confirmation_follows_the_browser_language(self):
        client, response = self._confirm(
            "crush.lu", "mitglied@example.com", HTTP_ACCEPT_LANGUAGE="de"
        )
        self.assertEqual(response.url, "/de/login/")

        page = client.get(response.url)
        html = page.content.decode()
        self.assertIn('value="mitglied@example.com"', html)
        # The two tab buttons: "Anmelden" (login) and "Registrieren" (signup).
        tabs = re.findall(
            r'@click="set(Login|Signup)"[^>]*>\s*([^<]+?)\s*</button>', html
        )
        self.assertEqual(tabs, [("Login", "Anmelden"), ("Signup", "Registrieren")])

    def test_crush_confirmation_focuses_the_password_field(self):
        """The email is already filled in, so the cursor waits in the password."""
        client, response = self._confirm("crush.lu", "focus@example.com")
        html = client.get(response.url).content.decode()
        self.assertTrue(_has_autofocus(html, "id_password"))
        self.assertFalse(_has_autofocus(html, "id_login"))

    def test_other_domain_confirmation_is_unchanged(self):
        for host in ("entreprinder.lu", "power-up.lu"):
            with self.subTest(host=host):
                _client, response = self._confirm(host, f"user@{host}")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, "/accounts/login/")


class TabbedLoginPageTests(TestCase):
    """The tabbed /<lang>/login/ page (crush_lu/auth.html) around the landing."""

    def setUp(self):
        cache.clear()

    def test_plain_visit_keeps_focus_on_the_email_field(self):
        html = Client(HTTP_HOST="crush.lu").get("/en/login/").content.decode()
        self.assertTrue(_has_autofocus(html, "id_login"))
        self.assertFalse(_has_autofocus(html, "id_password"))

    def test_failed_login_keeps_focus_on_the_email_field(self):
        """A bound form also carries the email; only the prefill moves focus."""
        response = Client(HTTP_HOST="crush.lu").post(
            "/en/login/", {"login": "nobody@example.com", "password": "wrong"}
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('value="nobody@example.com"', html)
        self.assertTrue(_has_autofocus(html, "id_login"))
        self.assertFalse(_has_autofocus(html, "id_password"))

    def test_tabs_shrink_below_sm_so_the_german_labels_fit(self):
        """
        "Anmelden | Registrieren" at text-lg needs more than the 230px pill a
        320px phone leaves (measured in Chromium: 24px past the border). Below
        `sm` the tabs drop to text-base and px-1, which fits EN/DE/FR at 320px;
        from `sm` up they keep text-lg and px-6.
        """
        html = (
            Client(HTTP_HOST="crush.lu", HTTP_ACCEPT_LANGUAGE="de")
            .get("/de/login/")
            .content.decode()
        )
        classes = re.findall(
            r'@click="set(?:Login|Signup)"\s+x-bind:class="\w+"\s+class="([^"]*)"',
            html,
        )
        self.assertEqual(len(classes), 2)
        for class_attr in classes:
            tokens = class_attr.split()
            for token in ("px-1", "sm:px-6", "text-base", "sm:text-lg"):
                self.assertIn(token, tokens)
            self.assertNotIn("text-lg", tokens)


class CrushAllauthLoginPageTests(TestCase):
    """crush.lu's /accounts/login/ (login_crush.html) offers a password reset."""

    def setUp(self):
        cache.clear()

    def test_login_crush_has_forgot_password_link(self):
        response = Client(HTTP_HOST="crush.lu").get("/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/login_crush.html")
        html = response.content.decode()
        self.assertIn('href="/accounts/password/reset/"', html)
        self.assertIn("Forgot your password?", html)

    def test_login_crush_forgot_password_link_is_translated(self):
        response = Client(HTTP_HOST="crush.lu", HTTP_ACCEPT_LANGUAGE="de").get(
            "/accounts/login/"
        )
        self.assertIn("Passwort vergessen?", response.content.decode())
