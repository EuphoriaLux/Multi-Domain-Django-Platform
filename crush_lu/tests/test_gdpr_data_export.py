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
        self.assertEqual(profile["ask_me_about"], [])
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

    def test_account_block_exports_names_even_when_full_name_is_hidden(self):
        User.objects.filter(pk=self.user.pk).update(
            first_name="Ada", last_name="Lovelace"
        )
        CrushProfile.objects.filter(user=self.user).update(show_full_name=False)

        account = self._export()["account"]

        self.assertEqual(account["first_name"], "Ada")
        self.assertEqual(account["last_name"], "Lovelace")

    def test_nonexistent_fields_are_gone(self):
        profile = self._export()["profile"]
        self.assertNotIn("canton", profile)
        self.assertNotIn("status", profile)

    def test_secrets_and_internal_identifiers_are_not_exported(self):
        body = json.dumps(self._export())
        self.assertNotIn("SECRET-APPLE", body)
        self.assertNotIn("SECRET-UID", body)
        profile = self._export()["profile"]
        for key in ("apple_auth_token", "phone_verification_uid"):
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

    def test_app_devices_export_metadata_without_tokens(self):
        from crush_lu.models import (
            AndroidAppDevice, IOSAppDevice, PWADeviceInstallation,
        )

        PWADeviceInstallation.objects.create(
            user=self.user, device_fingerprint="FP-SECRET", device_category="mobile",
            browser="Chrome",
        )
        IOSAppDevice.objects.create(
            user=self.user, device_token="IOS-TOKEN-SECRET", device_id="IOS-ID-SECRET",
            device_name="iPhone", app_version="1.0",
        )
        AndroidAppDevice.objects.create(
            user=self.user, registration_token="AND-TOKEN-SECRET",
            device_id="AND-ID-SECRET", device_name="Pixel 9",
        )
        data = self._export()
        platforms = sorted(d["platform"] for d in data["app_devices"])
        self.assertEqual(platforms, ["android", "ios", "pwa"])
        body = json.dumps(data)
        for secret in (
            "FP-SECRET", "IOS-TOKEN-SECRET", "IOS-ID-SECRET",
            "AND-TOKEN-SECRET", "AND-ID-SECRET",
        ):
            self.assertNotIn(secret, body)
        self.assertIn("iPhone", body)
        self.assertIn("Pixel 9", body)

    def test_curated_interests_exported_by_name_not_id(self):
        from crush_lu.models import Interest

        hiking = Interest.objects.create(
            slug="t-hiking", label="Hiking", category="outdoors"
        )
        jazz = Interest.objects.create(slug="t-jazz", label="Jazz", category="culture")
        Interest.objects.create(slug="t-chess", label="Chess", category="games")
        profile = CrushProfile.objects.get(user=self.user)
        profile.interests_new.set([hiking, jazz])
        profile.ask_me_about = [jazz.pk]
        profile.save()
        exported = self._export()["profile"]
        self.assertEqual(sorted(exported["interests_selected"]), ["Hiking", "Jazz"])
        self.assertEqual(exported["ask_me_about"], ["Jazz"])

    def test_device_notification_category_flags_are_exported(self):
        PushSubscription.objects.create(
            user=self.user, endpoint="https://p.example/x", p256dh_key="k",
            auth_key="a", notify_new_messages=False, notify_event_reminders=True,
        )
        device = self._export()["push_devices"][0]
        for key in (
            "notify_new_messages", "notify_event_reminders",
            "notify_new_connections", "notify_profile_updates",
        ):
            self.assertIn(key, device)
        self.assertFalse(device["notify_new_messages"])

    def test_trait_selections_are_exported_by_label(self):
        from crush_lu.models import Trait

        quality = Trait.objects.create(
            slug="t-kind", label="Kind", trait_type="quality", category="social"
        )
        defect = Trait.objects.create(
            slug="t-messy", label="Messy", trait_type="defect", category="social"
        )
        profile = CrushProfile.objects.get(user=self.user)
        profile.qualities.set([quality])
        profile.sought_qualities.set([quality])
        profile.defects.set([defect])
        exported = self._export()["profile"]
        self.assertEqual(exported["qualities"], ["Kind"])
        self.assertEqual(exported["sought_qualities"], ["Kind"])
        self.assertEqual(exported["defects"], ["Messy"])

    def test_other_platform_profiles_are_exported_without_tokens(self):
        from delegations.models import DelegationProfile
        from entreprinder.models import EntrepreneurProfile
        from hub.models import HubProfile

        HubProfile.objects.create(
            user=self.user, organization="Acme", primary_contact="Ann",
            phone="+352621000111",
        )
        EntrepreneurProfile.objects.create(user=self.user, tagline="Hi")
        DelegationProfile.objects.create(
            user=self.user, microsoft_id="MS-ID-1", microsoft_tenant_id="TENANT-1",
            job_title="Dev",
        )
        from delegations.models import AccessLog

        AccessLog.objects.create(
            profile=DelegationProfile.objects.get(user=self.user),
            action=AccessLog.ACTION_CHOICES[0][0], details="login ok",
            ip_address="9.9.9.9", user_agent="UA-1",
        )
        other = self._export()["other_platforms"]
        log = other["delegations"]["access_log"][0]
        self.assertEqual(
            (log["details"], log["ip_address"], log["user_agent"]),
            ("login ok", "9.9.9.9", "UA-1"),
        )
        self.assertEqual(other["hub"]["organization"], "Acme")
        self.assertEqual(other["entreprinder"]["tagline"], "Hi")
        self.assertEqual(other["delegations"]["microsoft_id"], "MS-ID-1")
        self.assertEqual(other["delegations"]["job_title"], "Dev")

    def test_no_other_platform_key_for_plain_members(self):
        self.assertNotIn("other_platforms", self._export())

    def test_onboarding_choices_are_exported_readably(self):
        CrushProfile.objects.filter(user=self.user).update(
            intent_probe="events", first_step_preference="i_initiate",
            astro_enabled=False,
        )
        profile = self._export()["profile"]
        self.assertEqual(profile["intent_probe"], "I want to meet people at real events")
        self.assertEqual(
            profile["first_step_preference"], "I prefer to make the first step"
        )
        self.assertFalse(profile["astro_enabled"])


class ExportRegistryCompletenessTests(TestCase):
    """Every field of the personal-data models is exported or explicitly excluded."""

    def setUp(self):
        from crush_lu.models import CrushCoach, UserDataConsent

        cache.clear()
        self.user = User.objects.create_user(
            "reg@example.com", "reg@example.com", "pw12345678"
        )
        CrushProfile.objects.create(
            user=self.user, date_of_birth=date(1995, 1, 1), gender="F",
            location="canton-luxembourg",
        )
        EmailPreference.get_or_create_for_user(self.user)
        CrushCoach.objects.create(user=self.user, bio="b")
        UserDataConsent.objects.get_or_create(user=self.user)
        request = RequestFactory().get("/")
        request.user = User.objects.get(pk=self.user.pk)
        self.data = json.loads(export_user_data(request).content)

    def _export(self):
        request = RequestFactory().get("/")
        request.user = User.objects.get(pk=self.user.pk)
        return json.loads(export_user_data(request).content)

    def _missing(self, model, block, excluded, aliases=None):
        aliases = aliases or {}
        exported = set(self.data[block])
        names = [f.name for f in model._meta.concrete_fields]
        names += [f.name for f in model._meta.many_to_many]
        return sorted(
            name for name in names
            if aliases.get(name, name) not in exported and name not in excluded
        )

    def test_profile_fields(self):
        from crush_lu.views_account import (
            ACCOUNT_EXPORT_KEY_ALIASES, ACCOUNT_EXPORT_PROFILE_EXCLUDED,
        )

        self.assertEqual(
            self._missing(
                CrushProfile, "profile", ACCOUNT_EXPORT_PROFILE_EXCLUDED,
                ACCOUNT_EXPORT_KEY_ALIASES,
            ),
            [],
        )

    def test_consent_fields(self):
        from crush_lu.models import UserDataConsent
        from crush_lu.views_account import ACCOUNT_EXPORT_CONSENT_EXCLUDED

        self.assertEqual(
            self._missing(UserDataConsent, "consent", ACCOUNT_EXPORT_CONSENT_EXCLUDED),
            [],
        )

    def test_email_preference_fields(self):
        from crush_lu.views_account import ACCOUNT_EXPORT_EMAIL_PREF_EXCLUDED

        self.assertEqual(
            self._missing(
                EmailPreference, "email_preferences",
                ACCOUNT_EXPORT_EMAIL_PREF_EXCLUDED,
            ),
            [],
        )

    def test_coach_fields(self):
        from crush_lu.models import CrushCoach
        from crush_lu.views_account import ACCOUNT_EXPORT_COACH_EXCLUDED

        self.assertEqual(
            self._missing(CrushCoach, "coach_profile", ACCOUNT_EXPORT_COACH_EXCLUDED),
            [],
        )

    def test_excluded_lists_name_real_fields_and_secrets_stay_out(self):
        from crush_lu.models import CrushCoach, UserDataConsent
        from crush_lu.views_account import (
            ACCOUNT_EXPORT_COACH_EXCLUDED, ACCOUNT_EXPORT_CONSENT_EXCLUDED,
            ACCOUNT_EXPORT_EMAIL_PREF_EXCLUDED, ACCOUNT_EXPORT_PROFILE_EXCLUDED,
        )

        for model, excluded in (
            (CrushProfile, ACCOUNT_EXPORT_PROFILE_EXCLUDED),
            (UserDataConsent, ACCOUNT_EXPORT_CONSENT_EXCLUDED),
            (EmailPreference, ACCOUNT_EXPORT_EMAIL_PREF_EXCLUDED),
            (CrushCoach, ACCOUNT_EXPORT_COACH_EXCLUDED),
        ):
            real = {f.name for f in model._meta.get_fields()}
            for name, reason in excluded.items():
                self.assertIn(name, real, f"{model.__name__}.{name} does not exist")
                self.assertTrue(reason)
        self.assertNotIn("unsubscribe_token", self.data["email_preferences"])
        self.assertNotIn("apple_auth_token", self.data["profile"])

    def test_draft_data_hub_requests_timeline_and_skills_are_exported(self):
        from django.utils import timezone

        from entreprinder.models import EntrepreneurProfile, Skill
        from hub.models import HubRequest, HubTimelineEvent

        CrushProfile.objects.filter(user=self.user).update(
            draft_data={"bio": "newer bio from autosave"}
        )
        HubRequest.objects.create(user=self.user, subject="Need help", summary="details")
        HubTimelineEvent.objects.create(
            user=self.user, title="Called", body="we spoke", occurred_at=timezone.now()
        )
        entrepreneur = EntrepreneurProfile.objects.create(user=self.user)
        entrepreneur.skills.add(Skill.objects.create(name="Welding"))
        data = self._export()
        self.assertEqual(data["profile"]["draft_data"], {"bio": "newer bio from autosave"})
        hub = data["other_platforms"]["hub"]
        self.assertEqual(hub["requests"][0]["subject"], "Need help")
        self.assertEqual(hub["timeline"][0]["title"], "Called")
        self.assertEqual(data["other_platforms"]["entreprinder"]["skills"], ["Welding"])


class ExportPixelActivityTests(TestCase):
    def test_pixel_canvas_activity_is_exported_without_an_entrepreneur_profile(self):
        from django.utils import timezone

        from entreprinder.vibe.models import (
            Pixel,
            PixelCanvas,
            PixelHistory,
            UserPixelStats,
        )

        cache.clear()
        user = User.objects.create_user("px@example.com", "px@example.com", "pw12345678")
        canvas = PixelCanvas.objects.create(name="c", width=10, height=10)
        Pixel.objects.create(canvas=canvas, x=1, y=2, color="#FF0000", placed_by=user)
        PixelHistory.objects.create(
            canvas=canvas, x=1, y=2, color="#FF0000", placed_by=user
        )
        UserPixelStats.objects.create(
            user=user, canvas=canvas, total_pixels_placed=3,
            last_pixel_placed=timezone.now(),
        )
        request = RequestFactory().get("/")
        request.user = user

        data = json.loads(export_user_data(request).content)

        pixel = data["other_platforms"]["entreprinder"]["pixel_canvas"]
        self.assertEqual(pixel["stats"][0]["total_pixels_placed"], 3)
        self.assertEqual(pixel["placements"][0]["color"], "#FF0000")
        self.assertEqual((pixel["current_pixels"][0]["x"], pixel["current_pixels"][0]["y"]), (1, 2))
