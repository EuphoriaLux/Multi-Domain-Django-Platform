"""GDPR Art. 15/20 export completeness (#1194)."""

import json
from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from crush_lu.models import CrushProfile, EmailPreference, PushSubscription
from crush_lu.views_account import export_user_data

User = get_user_model()


class ExportProfileCompletenessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            "exp@example.com", "exp@example.com", "pw12345678"
        )
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            phone_number="+352621123456",
            phone_verified=True,
            location="canton-luxembourg",
            bio="hi",
            preferred_age_min=25,
            preferred_age_max=40,
            preferred_genders=["M"],
            ask_me_about=["cats"],
            event_vibe="chill",
            event_languages=["en", "fr"],
            apple_auth_token="SECRET-APPLE",
            phone_verification_uid="SECRET-UID",
        )

    def _export(self):
        request = RequestFactory().get("/")
        request.user = User.objects.get(pk=self.user.pk)
        return json.loads(export_user_data(request).content)

    def test_profile_block_contains_phone_location_and_preferences(self):
        profile = self._export()["profile"]
        self.assertEqual(profile["phone_number"], "+352621123456")
        self.assertTrue(profile["phone_verified"])
        self.assertEqual(profile["location"], "canton-luxembourg")
        self.assertEqual(profile["preferred_age_min"], 25)
        self.assertEqual(profile["preferred_age_max"], 40)
        self.assertEqual(profile["preferred_genders"], ["M"])
        self.assertEqual(profile["ask_me_about"], ["cats"])
        self.assertEqual(profile["event_vibe"], "chill")
        self.assertEqual(profile["event_languages"], ["en", "fr"])
        for key in (
            "preferred_language",
            "completion_status",
            "verification_status",
            "membership_tier",
            "referral_points",
        ):
            self.assertIn(key, profile)

    def test_nonexistent_fields_are_gone(self):
        profile = self._export()["profile"]
        self.assertNotIn("canton", profile)
        self.assertNotIn("status", profile)

    def test_secrets_and_internal_identifiers_are_not_exported(self):
        body = json.dumps(self._export())
        self.assertNotIn("SECRET-APPLE", body)
        self.assertNotIn("SECRET-UID", body)
        profile = self._export()["profile"]
        for key in ("apple_auth_token", "phone_verification_uid", "draft_data"):
            self.assertNotIn(key, profile)

    def test_email_preferences_and_push_devices_are_exported(self):
        EmailPreference.get_or_create_for_user(self.user)
        PushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example/secret-endpoint",
            p256dh_key="secret-p256",
            auth_key="secret-auth",
            device_name="Pixel",
        )
        data = self._export()
        self.assertIn("email_newsletter", data["email_preferences"])
        self.assertEqual(data["push_devices"][0]["device_name"], "Pixel")
        body = json.dumps(data)
        self.assertNotIn("secret-endpoint", body)
        self.assertNotIn("secret-p256", body)
        self.assertNotIn("secret-auth", body)
