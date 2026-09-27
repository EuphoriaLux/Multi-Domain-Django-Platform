"""
UX Wave 3 · WP3 (finding 2-04): rate-limited auth actions used to return a
bare text/plain page with no branding, no navigation and, on the login and
password-reset paths, no translation.

These tests pin the fix on all three affected code paths:

* Login (crush_lu/urls.py UnifiedAuthView.dispatch) - re-renders auth.html
  inline with a translated non-field error instead of a bare 429 text page.
* Signup (crush_lu/views_account.py signup) - same idea, via the shared
  ``ratelimit`` decorator's ``block=False`` + view-side re-render.
* Password reset (azureproject/middleware.py AuthRateLimitMiddleware) - no
  form to re-render into, so it gets the new branded
  crush_lu/rate_limited.html fallback instead.
* Resend verification email (crush_lu/views_account.py
  resend_verification_email) - same branded fallback, wired through the
  decorator's new ``rate_limited_template`` option; its JSON/XHR contract for
  API-style callers must be unaffected.

Uses literal paths (not reverse()) with HTTP_HOST='crush.lu', per AGENTS.md -
these routes sit inside i18n_patterns(prefix_default_language=True).
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import Client, TestCase, override_settings


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-ux-wave3-rate-limited",
        }
    },
)
class LoginRateLimitTests(TestCase):
    """LoginRateThrottle allows 5/minute (settings.py DEFAULT_THROTTLE_RATES)."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_sixth_attempt_reuses_auth_template_with_inline_error(self):
        for _ in range(5):
            self.client.post(
                "/en/login/", {"login": "nobody@example.com", "password": "wrong"}
            )

        response = self.client.post(
            "/en/login/", {"login": "nobody@example.com", "password": "wrong"}
        )

        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        # Old behaviour: a bare text/plain page with no branding at all.
        self.assertNotEqual(response.get("Content-Type", ""), "text/plain")
        self.assertTemplateUsed(response, "crush_lu/auth.html")
        content = response.content.decode()
        self.assertIn("Too many login attempts", content)
        # Still the branded shell, not a stripped-down error page.
        self.assertIn("Crush.lu", content)

    def test_message_is_translated_for_french(self):
        for _ in range(5):
            self.client.post(
                "/fr/login/", {"login": "nobody@example.com", "password": "wrong"}
            )

        response = self.client.post(
            "/fr/login/", {"login": "nobody@example.com", "password": "wrong"}
        )

        self.assertEqual(response.status_code, 429)
        self.assertIn("Trop de tentatives de connexion", response.content.decode())


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-ux-wave3-rate-limited-signup",
        }
    },
)
class SignupRateLimitTests(TestCase):
    """The custom @ratelimit decorator defaults to 5/h for signup."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_sixth_attempt_reuses_auth_template_with_inline_error(self):
        for _ in range(5):
            self.client.post("/en/signup/", {})

        response = self.client.post("/en/signup/", {})

        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.assertTemplateUsed(response, "crush_lu/auth.html")
        content = response.content.decode()
        self.assertIn("Too many signup attempts", content)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-ux-wave3-rate-limited-pwreset",
        }
    },
)
class PasswordResetRateLimitTests(TestCase):
    """PasswordResetRateThrottle allows 3/hour."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_fourth_attempt_gets_branded_page_not_bare_text(self):
        for _ in range(3):
            self.client.post(
                "/accounts/password/reset/", {"email": "nobody@example.com"}
            )

        response = self.client.post(
            "/accounts/password/reset/", {"email": "nobody@example.com"}
        )

        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.assertTemplateUsed(response, "crush_lu/rate_limited.html")
        content = response.content.decode()
        # Old behaviour: 'Too many password reset requests. Please try
        # again in N minutes.' as bare text/plain, no way back.
        self.assertIn("Back to login", content)
        self.assertIn("Too many attempts", content)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-ux-wave3-rate-limited-resend",
        }
    },
)
class ResendVerificationRateLimitTests(TestCase):
    """The shared @ratelimit decorator defaults to 3/h for this endpoint."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_fourth_browser_attempt_gets_branded_page(self):
        for _ in range(3):
            self.client.post("/en/signup/resend-verification/")

        response = self.client.post("/en/signup/resend-verification/")

        self.assertEqual(response.status_code, 429)
        self.assertTemplateUsed(response, "crush_lu/rate_limited.html")
        self.assertIn("Back to login", response.content.decode())

    def test_fourth_xhr_attempt_still_gets_json(self):
        """The XHR/JSON contract (used by the resend button's fetch call)
        must be untouched by the new browser-facing branded fallback."""
        for _ in range(3):
            self.client.post(
                "/en/signup/resend-verification/",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        response = self.client.post(
            "/en/signup/resend-verification/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("rate_limited", response.content.decode())
