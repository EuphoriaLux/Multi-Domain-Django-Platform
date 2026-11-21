"""Signup bot traps (2026-11 bot wave on /en/signup/).

The bot loaded the signup page and posted it ~1.5 s later, from rotating
addresses, signing up strangers' emails ("email bombing"). CrushSignupForm
now carries a hidden honeypot field and a signed render time:

* honeypot filled  -> looks like a normal signup (redirect to the
  "check your email" page) but creates no account and sends no email;
* posted too fast, or without / with a broken / expired render time ->
  the form comes back with "please press Create Account again" and a fresh token,
  since that can be a person too (a page opened before a deploy).

Literal paths with HTTP_HOST='crush.lu', per AGENTS.md.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail, signing
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from crush_lu.forms import CrushSignupForm

User = get_user_model()
SIGNUP = "/en/signup/"


def _token(age_seconds=10.0):
    return signing.dumps(
        round(time.time() - age_seconds, 3), salt=CrushSignupForm._STARTED_SALT
    )


@override_settings(
    CRUSH_SIGNUP_MIN_FILL_SECONDS=3,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-signup-bot-traps",
        }
    },
)
class SignupBotTrapTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def _post(self, **overrides):
        data = {
            "first_name": "Lea",
            "email": "lea@example.com",
            "password1": "Sup3r-secret-pw!",
            "crushlu_consent": "on",
            CrushSignupForm.STARTED_FIELD: _token(),
        }
        data.update(overrides)
        data = {k: v for k, v in data.items() if v is not None}
        return self.client.post(SIGNUP, data)

    def test_form_renders_both_traps(self):
        content = self.client.get(SIGNUP).content.decode()
        self.assertIn('name="form_started"', content)
        self.assertIn('name="signup_note"', content)

    def test_person_who_took_their_time_gets_an_account(self):
        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(email="lea@example.com").exists())

    def test_honeypot_fakes_success_without_account_or_email(self):
        with self.assertLogs("crush_lu.views_account", "WARNING") as logs:
            response = self._post(signup_note="http://spam.example")
        self.assertRedirects(
            response,
            "/accounts/confirm-email/",
            fetch_redirect_response=False,
        )
        self.assertFalse(User.objects.filter(email="lea@example.com").exists())
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn("reason=honeypot email_domain=example.com", logs.output[0])

    def test_too_fast_is_sent_back_with_a_fresh_token(self):
        response = self._post(form_started=_token(age_seconds=1.5))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="lea@example.com").exists())
        self.assertEqual(len(mail.outbox), 0)
        content = response.content.decode()
        self.assertIn("press Create Account again", content)
        # What they typed is kept, and the new token restarts the clock.
        self.assertIn('value="lea@example.com"', content)
        self.assertIn('name="form_started"', content)

    def test_missing_and_tampered_tokens_are_sent_back(self):
        for reason, token in {"no_token": None, "bad_token": _token() + "x"}.items():
            with self.subTest(reason=reason):
                with self.assertLogs("crush_lu.views_account", "WARNING") as logs:
                    response = self._post(form_started=token)
                self.assertEqual(response.status_code, 200)
                self.assertIn(f"reason={reason}", logs.output[0])
                self.assertFalse(User.objects.filter(email="lea@example.com").exists())

    @override_settings(CRUSH_SIGNUP_FORM_MAX_AGE_SECONDS=5)
    def test_expired_token_is_sent_back(self):
        # Signed 10 s ago per the signature's own timestamp, with a 5 s window.
        with patch("django.core.signing.time.time", return_value=time.time() - 10):
            token = _token(age_seconds=0)
        with self.assertLogs("crush_lu.views_account", "WARNING") as logs:
            response = self._post(form_started=token)
        self.assertEqual(response.status_code, 200)
        self.assertIn("reason=expired", logs.output[0])
        self.assertFalse(User.objects.filter(email="lea@example.com").exists())

    @override_settings(CRUSH_SIGNUP_MIN_FILL_SECONDS=0)
    def test_timing_check_can_be_switched_off(self):
        response = self._post(form_started=None)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(email="lea@example.com").exists())

    @override_settings(CRUSH_SIGNUP_MIN_FILL_SECONDS=0)
    def test_honeypot_still_checked_when_timing_is_off(self):
        response = self._post(form_started=None, signup_note="x")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(email="lea@example.com").exists())

    def test_retry_message_is_translated(self):
        response = self.client.post(
            "/fr/signup/",
            {
                "first_name": "Lea",
                "email": "lea@example.com",
                "password1": "Sup3r-secret-pw!",
                "crushlu_consent": "on",
                CrushSignupForm.STARTED_FIELD: _token(age_seconds=0),
            },
        )
        self.assertIn(
            "cliquer à nouveau sur Créer un compte", response.content.decode()
        )
