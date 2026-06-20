"""Tests for the WhatsApp OTP phone-verification channel.

Covers ``crush_lu.views_phone_verification.send_whatsapp_otp`` and
``verify_whatsapp_otp`` plus the ``PhoneOTP`` model. WhatsApp delivery
(``crush_lu.services.whatsapp.send_otp``) is mocked — no live Graph calls.
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import CrushProfile, PhoneOTP
from crush_lu.services.whatsapp import WhatsAppSendResult, ERROR_NOT_ON_WHATSAPP

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}

PHONE = "+352621123456"


class SiteTestMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(WHATSAPP_OTP_TTL_MINUTES=3, **CRUSH_LU_URL_SETTINGS)
class WhatsAppOTPTests(SiteTestMixin, TestCase):
    def setUp(self):
        cache.clear()  # @ratelimit uses the cache; isolate counts per test
        self.client = Client()
        self.user = User.objects.create_user(
            username="otp@example.com", email="otp@example.com", password="pw"
        )
        self.client.force_login(self.user)
        self.send_url = reverse("api_phone_whatsapp_send")
        self.verify_url = reverse("api_phone_whatsapp_verify")

    def _post(self, url, body):
        return self.client.post(
            url, data=json.dumps(body), content_type="application/json"
        )

    def _has_verified_profile(self):
        return CrushProfile.objects.filter(
            user=self.user, phone_verified=True
        ).exists()

    # --- send ---------------------------------------------------------------

    @patch("crush_lu.services.whatsapp.is_configured", return_value=True)
    @patch("crush_lu.services.whatsapp.send_otp")
    def test_send_success_stores_hashed_code(self, mock_send, _cfg):
        captured = {}

        def _capture(recipient, code, language="en"):
            captured["code"] = code
            captured["recipient"] = recipient
            return WhatsAppSendResult(ok=True, wa_message_id="wamid.X")

        mock_send.side_effect = _capture

        resp = self._post(self.send_url, {"phone_number": PHONE})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        otp = PhoneOTP.objects.get(user=self.user)
        self.assertEqual(otp.channel, PhoneOTP.Channel.WHATSAPP)
        self.assertFalse(otp.consumed)
        # The recipient gets the +; storage keeps it normalized.
        self.assertEqual(captured["recipient"], PHONE)
        # Raw code is never stored — only a salted hash.
        self.assertNotIn(captured["code"], otp.code_hash)
        self.assertTrue(check_password(captured["code"], otp.code_hash))

    @patch("crush_lu.services.whatsapp.is_configured", return_value=True)
    @patch("crush_lu.services.whatsapp.send_otp")
    def test_send_not_on_whatsapp_signals_fallback(self, mock_send, _cfg):
        mock_send.return_value = WhatsAppSendResult(
            ok=False, error_code=ERROR_NOT_ON_WHATSAPP, error_message="undeliverable"
        )
        resp = self._post(self.send_url, {"phone_number": PHONE})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error_code"], "not_on_whatsapp")
        self.assertFalse(PhoneOTP.objects.filter(user=self.user).exists())

    @patch("crush_lu.services.whatsapp.is_configured", return_value=False)
    def test_send_unconfigured_returns_503(self, _cfg):
        resp = self._post(self.send_url, {"phone_number": PHONE})
        self.assertEqual(resp.status_code, 503)

    @patch("crush_lu.services.whatsapp.is_configured", return_value=True)
    @patch("crush_lu.services.whatsapp.send_otp")
    def test_send_rejects_number_verified_elsewhere(self, mock_send, _cfg):
        other = User.objects.create_user(username="o@e.com", email="o@e.com", password="pw")
        CrushProfile.objects.create(
            user=other, phone_number=PHONE, phone_verified=True
        )
        resp = self._post(self.send_url, {"phone_number": PHONE})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error_code"], "phone_already_in_use")
        mock_send.assert_not_called()

    @patch("crush_lu.services.whatsapp.is_configured", return_value=True)
    @patch("crush_lu.services.whatsapp.send_otp")
    def test_send_rejects_same_number_different_spelling(self, mock_send, _cfg):
        # Existing verified profile stored in canonical "+" form.
        other = User.objects.create_user(username="o2@e.com", email="o2@e.com", password="pw")
        CrushProfile.objects.create(
            user=other, phone_number="+352621123456", phone_verified=True
        )
        # Same real number entered without the leading "+" and with spaces.
        resp = self._post(self.send_url, {"phone_number": "352 621 123 456"})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error_code"], "phone_already_in_use")
        mock_send.assert_not_called()

    @patch("crush_lu.services.whatsapp.is_configured", return_value=True)
    @patch("crush_lu.services.whatsapp.send_otp")
    def test_send_canonicalizes_stored_number(self, mock_send, _cfg):
        captured = {}
        mock_send.side_effect = lambda recipient, code, language="en": (
            captured.__setitem__("recipient", recipient)
            or WhatsAppSendResult(ok=True, wa_message_id="z")
        )
        # "00" international prefix collapses to the same canonical "+" form.
        resp = self._post(self.send_url, {"phone_number": "00352621123456"})
        self.assertEqual(resp.status_code, 200)
        otp = PhoneOTP.objects.get(user=self.user)
        self.assertEqual(otp.phone_number, "+352621123456")
        self.assertEqual(captured["recipient"], "+352621123456")

    def test_send_rejects_too_short_number(self):
        resp = self._post(self.send_url, {"phone_number": "12345"})
        self.assertEqual(resp.status_code, 400)

    @patch("crush_lu.services.whatsapp.is_configured", return_value=True)
    @patch("crush_lu.services.whatsapp.send_otp")
    def test_send_short_circuits_when_already_verified(self, mock_send, _cfg):
        CrushProfile.objects.create(
            user=self.user, phone_number="+352600000000", phone_verified=True
        )
        resp = self._post(self.send_url, {"phone_number": "+352621123456"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["already_verified"])
        mock_send.assert_not_called()
        self.assertFalse(PhoneOTP.objects.filter(user=self.user).exists())

    def test_send_missing_number_is_400(self):
        resp = self._post(self.send_url, {})
        self.assertEqual(resp.status_code, 400)

    # --- verify -------------------------------------------------------------

    def _issue(self, code="123456", **kwargs):
        return PhoneOTP.issue(
            user=self.user,
            phone_number=PHONE,
            code=code,
            channel=PhoneOTP.Channel.WHATSAPP,
            ttl_minutes=kwargs.get("ttl_minutes", 3),
        )

    def test_verify_success_marks_phone_verified(self):
        self._issue(code="654321")
        resp = self._post(self.verify_url, {"code": "654321"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["phone_verified"])

        profile = self.user.crushprofile
        self.assertTrue(profile.phone_verified)
        self.assertEqual(profile.phone_number, PHONE)
        self.assertTrue(profile.phone_verification_uid.startswith("whatsapp:"))

    def test_verify_wrong_code_counts_attempt(self):
        otp = self._issue(code="111111")
        resp = self._post(self.verify_url, {"code": "000000"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "otp_invalid")
        otp.refresh_from_db()
        self.assertEqual(otp.attempts, 1)
        self.assertFalse(otp.consumed)

    def test_verify_expired_code_rejected(self):
        otp = self._issue(code="222222")
        otp.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        otp.save(update_fields=["expires_at"])
        resp = self._post(self.verify_url, {"code": "222222"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "otp_expired")
        self.assertFalse(self._has_verified_profile())

    def test_verify_locks_out_after_max_attempts(self):
        otp = self._issue(code="333333")
        for _ in range(PhoneOTP.MAX_ATTEMPTS):
            self._post(self.verify_url, {"code": "999999"})
        otp.refresh_from_db()
        self.assertEqual(otp.attempts, PhoneOTP.MAX_ATTEMPTS)
        # Even the correct code now fails — the row is locked.
        resp = self._post(self.verify_url, {"code": "333333"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(self._has_verified_profile())

    def test_verify_retry_after_success_is_idempotent(self):
        # A double-submit/retry after the first verify succeeded must not look
        # like a failure even though the OTP is now consumed.
        self._issue(code="444444")
        self.assertEqual(
            self._post(self.verify_url, {"code": "444444"}).status_code, 200
        )
        resp = self._post(self.verify_url, {"code": "444444"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["phone_verified"])

    def test_verify_unverified_user_with_no_open_otp_is_expired(self):
        # No prior issue() and the user isn't verified -> genuine expired/missing.
        resp = self._post(self.verify_url, {"code": "444444"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error_code"], "otp_expired")

    @patch("crush_lu.services.whatsapp.is_configured", return_value=True)
    @patch("crush_lu.services.whatsapp.send_otp")
    def test_full_send_then_verify_flow(self, mock_send, _cfg):
        captured = {}
        mock_send.side_effect = lambda recipient, code, language="en": (
            captured.__setitem__("code", code)
            or WhatsAppSendResult(ok=True, wa_message_id="wamid.Y")
        )
        self.assertEqual(self._post(self.send_url, {"phone_number": PHONE}).status_code, 200)
        resp = self._post(self.verify_url, {"code": captured["code"]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.user.crushprofile.phone_verified)


class PhoneOTPModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="m@e.com", email="m@e.com", password="pw"
        )

    def test_generate_code_is_six_digits(self):
        for _ in range(50):
            code = PhoneOTP.generate_code()
            self.assertEqual(len(code), PhoneOTP.CODE_LENGTH)
            self.assertTrue(code.isdigit())

    def test_issue_supersedes_prior_open_codes(self):
        first = PhoneOTP.issue(
            user=self.user, phone_number=PHONE, code="111111",
            channel=PhoneOTP.Channel.WHATSAPP, ttl_minutes=3,
        )
        PhoneOTP.issue(
            user=self.user, phone_number=PHONE, code="222222",
            channel=PhoneOTP.Channel.WHATSAPP, ttl_minutes=3,
        )
        first.refresh_from_db()
        self.assertTrue(first.consumed)  # old code invalidated
        self.assertFalse(first.verify("111111"))

    def test_verify_consumes_on_success(self):
        otp = PhoneOTP.issue(
            user=self.user, phone_number=PHONE, code="555555",
            channel=PhoneOTP.Channel.WHATSAPP, ttl_minutes=3,
        )
        self.assertTrue(otp.verify("555555"))
        self.assertTrue(otp.consumed)
        self.assertFalse(otp.verify("555555"))  # single-use
