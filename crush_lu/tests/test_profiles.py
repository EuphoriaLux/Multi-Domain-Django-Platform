from datetime import timedelta
from unittest.mock import patch
import json

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import Trait
from crush_lu.models.journey import JourneyConfiguration
from crush_lu.models.profiles import CrushProfile, ProfileSubmission, SpecialUserExperience, UserDataConsent


class SpecialUserExperienceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="alice", first_name="Alice", last_name="Wonder")

    def test_matches_user_is_case_insensitive_and_active(self):
        special = SpecialUserExperience.objects.create(first_name="alice", last_name="wonder", is_active=True)

        self.assertTrue(special.matches_user(self.user))

    def test_matches_user_respects_inactive_state(self):
        special = SpecialUserExperience.objects.create(first_name="Alice", last_name="Wonder", is_active=False)

        self.assertFalse(special.matches_user(self.user))

    def test_trigger_updates_timestamp_and_count(self):
        special = SpecialUserExperience.objects.create(first_name="Alice", last_name="Wonder")

        self.assertIsNone(special.last_triggered_at)
        self.assertEqual(special.trigger_count, 0)

        special.trigger()
        special.refresh_from_db()

        self.assertIsNotNone(special.last_triggered_at)
        self.assertEqual(special.trigger_count, 1)

    def test_journey_helpers_return_expected_records(self):
        special = SpecialUserExperience.objects.create(first_name="Alice", last_name="Wonder")
        wonderland = JourneyConfiguration.objects.create(
            special_experience=special,
            journey_type="wonderland",
        )
        advent = JourneyConfiguration.objects.create(
            special_experience=special,
            journey_type="advent_calendar",
        )

        self.assertEqual(special.journey, wonderland)
        self.assertEqual(special.advent_calendar_journey, advent)
        self.assertEqual(special.get_journey("advent_calendar"), advent)
        self.assertTrue(special.has_journey("wonderland"))
        self.assertFalse(special.has_journey("custom"))


class CrushProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="test.user@example.com",
            first_name="Test",
            last_name="User",
        )
        self.profile = CrushProfile.objects.create(user=self.user, location="canton-luxembourg")

    def test_age_and_age_range_calculation(self):
        today = timezone.now().date()
        dob = today.replace(year=today.year - 30) - timedelta(days=1)
        self.profile.date_of_birth = dob
        self.profile.save()

        self.assertEqual(self.profile.age, 30)
        self.assertEqual(self.profile.age_range, "30-34")
        self.assertEqual(self.profile.get_age_range(), "30-34")

    def test_display_name_respects_full_name_setting(self):
        # Default: show_first_name
        self.assertEqual(self.profile.display_name, "Test")

        self.profile.show_full_name = True
        self.profile.save()

        self.assertEqual(self.profile.display_name, "Test User")

    def test_city_alias_returns_location(self):
        self.assertEqual(self.profile.city, "canton-luxembourg")


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class RegistrationFlowTests(TestCase):
    def test_user_can_register_and_create_profile(self):
        signup_data = {
            "email": "newuser@example.com",
            "password1": "SecurePass123!",
            "password2": "SecurePass123!",
            "first_name": "New",
            "last_name": "User",
            "crushlu_consent": True,  # Required consent checkbox
        }

        response = self.client.post(reverse("crush_lu:signup"), signup_data, follow=True, HTTP_HOST="crush.lu")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(email="newuser@example.com").exists())
        user = User.objects.get(email="newuser@example.com")
        self.assertGreater(len(response.redirect_chain), 0)
        # Under ACCOUNT_EMAIL_VERIFICATION='mandatory', signup hands off to
        # allauth's complete_signup which lands the unauthenticated user on
        # the verification-sent page instead of forwarding to /welcome/.
        self.assertEqual(
            response.redirect_chain[-1][0],
            reverse("account_email_verification_sent"),
        )

        # Simulate the user clicking the verification link, then logging in,
        # so the rest of the registration flow can proceed.
        EmailAddress.objects.filter(user=user, email__iexact=user.email).update(verified=True, primary=True)
        self.client.force_login(user)

        # Get or create the profile and mark phone + coach_intro as done so
        # the submission gate (phone_verified + coach_intro_seen_at) passes.
        profile, _ = CrushProfile.objects.get_or_create(user=user)
        profile.phone_number = "+35212345678"
        profile.phone_verified = True
        profile.phone_verified_at = timezone.now()
        profile.coach_intro_seen_at = timezone.now()
        profile.save()

        # Mark the email as verified to clear the submission gate. Allauth
        # creates an EmailAddress row during signup but leaves it unverified
        # until the user clicks the confirmation link. This test simulates
        # the post-confirmation state.
        from allauth.account.models import EmailAddress
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )

        profile_data = {
            "phone_number": "+35212345678",
            "date_of_birth": (timezone.now().date() - timedelta(days=30 * 365)).isoformat(),
            "gender": "F",
            "location": "canton-luxembourg",  # Canton-based location (from interactive map)
            "bio": "Testing bio",
            "interests": "Reading, Hiking",
            "event_languages": ["en", "fr"],
        }

        profile_response = self.client.post(reverse("crush_lu:create_profile"), profile_data, follow=True, HTTP_HOST="crush.lu")

        self.assertEqual(profile_response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.location, "canton-luxembourg")
        self.assertEqual(profile.gender, "F")
        self.assertEqual(profile.completion_status, "submitted")
        self.assertTrue(ProfileSubmission.objects.filter(profile=profile).exists())


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CrushPreferencesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="pref@example.com",
            email="pref@example.com",
            password="testpass123",
        )

    def test_unapproved_user_redirected(self):
        """Unapproved users cannot access the preferences page."""
        CrushProfile.objects.create(
            user=self.user,
            location="canton-luxembourg",
            is_approved=False,
        )
        self.client.login(username="pref@example.com", password="testpass123")
        response = self.client.get(
            reverse("crush_lu:crush_preferences"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 302)

    def test_old_url_redirects_to_section(self):
        """Old crush_preferences URL redirects to new section-based URL."""
        CrushProfile.objects.create(
            user=self.user,
            location="canton-luxembourg",
            is_approved=True,
        )
        self._grant_consent()
        self.client.login(username="pref@example.com", password="testpass123")
        response = self.client.get(
            reverse("crush_lu:crush_preferences"), HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("section=preferences", response.url)

    def _grant_consent(self):
        """Grant consent + mark primary email verified so submission-gated
        views accept this fixture user. Skips email creation if the user
        has no email (some tests use username-only)."""
        from allauth.account.models import EmailAddress
        consent, _ = UserDataConsent.objects.get_or_create(user=self.user)
        consent.crushlu_consent_given = True
        consent.save()
        if self.user.email:
            EmailAddress.objects.update_or_create(
                user=self.user,
                email=self.user.email,
                defaults={"verified": True, "primary": True},
            )

    def test_approved_user_can_view(self):
        """Approved users can access the preferences section."""
        CrushProfile.objects.create(
            user=self.user,
            location="canton-luxembourg",
            is_approved=True,
        )
        self._grant_consent()
        self.client.login(username="pref@example.com", password="testpass123")
        url = reverse("crush_lu:edit_profile") + "?section=preferences"
        response = self.client.get(url, HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)

    def test_save_preferences(self):
        """Approved users can save preferences via section URL."""
        profile = CrushProfile.objects.create(
            user=self.user,
            location="canton-luxembourg",
            is_approved=True,
        )
        self._grant_consent()
        self.client.login(username="pref@example.com", password="testpass123")
        url = reverse("crush_lu:edit_profile") + "?section=preferences"
        response = self.client.post(
            url,
            {
                "preferred_age_min": 25,
                "preferred_age_max": 35,
                "preferred_genders": ["F", "NB"],
                "first_step_preference": "i_initiate",
            },
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.preferred_age_min, 25)
        self.assertEqual(profile.preferred_age_max, 35)
        self.assertEqual(profile.preferred_genders, ["F", "NB"])
        self.assertEqual(profile.first_step_preference, "i_initiate")

    def test_first_step_preference_is_optional(self):
        """Submitting without first_step_preference keeps it blank."""
        profile = CrushProfile.objects.create(
            user=self.user,
            location="canton-luxembourg",
            is_approved=True,
        )
        self._grant_consent()
        self.client.login(username="pref@example.com", password="testpass123")
        url = reverse("crush_lu:edit_profile") + "?section=preferences"
        response = self.client.post(
            url,
            {
                "preferred_age_min": 18,
                "preferred_age_max": 99,
            },
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.first_step_preference, "")


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ProfileSettingsAutosaveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="autosave@example.com",
            email="autosave@example.com",
            password="testpass123",
            first_name="Auto",
            last_name="Save",
        )
        consent, _ = UserDataConsent.objects.get_or_create(user=self.user)
        consent.crushlu_consent_given = True
        consent.save()

        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=timezone.now().date() - timedelta(days=30 * 365),
            gender="F",
            location="canton-luxembourg",
            bio="Original bio",
            interests="Original interests",
            phone_number="+35212345678",
            phone_verified=True,
            phone_verified_at=timezone.now(),
            is_approved=True,
            event_languages=["en"],
            preferred_age_min=18,
            preferred_age_max=99,
            preferred_genders=[],
            first_step_preference="",
            astro_enabled=True,
            show_full_name=False,
            show_exact_age=True,
        )

        self.quality, _ = Trait.objects.get_or_create(
            slug="kind",
            defaults={"trait_type": "quality", "label": "Kind", "category": "personality"},
        )
        self.defect, _ = Trait.objects.get_or_create(
            slug="stubborn",
            defaults={"trait_type": "defect", "label": "Stubborn", "category": "personality"},
        )
        self.sought, _ = Trait.objects.get_or_create(
            slug="curious",
            defaults={"trait_type": "quality", "label": "Curious", "category": "personality"},
        )

        self.client.login(username="autosave@example.com", password="testpass123")
        self.url = reverse("api_profile_settings_autosave")

    def test_about_autosave_updates_profile_fields(self):
        response = self.client.post(
            self.url,
            data=json.dumps({
                "section": "about",
                "bio": "Updated bio",
                "interests": "Music, Travel",
                "location": "border-france",
                "event_languages": ["fr", "en"],
                "qualities_ids": str(self.quality.pk),
                "defects_ids": str(self.defect.pk),
            }),
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.bio, "Updated bio")
        self.assertEqual(self.profile.interests, "Music, Travel")
        self.assertEqual(self.profile.location, "border-france")
        self.assertEqual(self.profile.event_languages, ["fr", "en"])
        self.assertEqual(list(self.profile.qualities.values_list("pk", flat=True)), [self.quality.pk])
        self.assertEqual(list(self.profile.defects.values_list("pk", flat=True)), [self.defect.pk])

    def test_about_autosave_rejects_invalid_location_without_overwrite(self):
        response = self.client.post(
            self.url,
            data=json.dumps({
                "section": "about",
                "location": "invalid-location",
            }),
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(response.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.location, "canton-luxembourg")

    @patch("crush_lu.matching.update_match_scores_for_user")
    def test_preferences_autosave_updates_profile_and_triggers_match_recalc(self, mock_update):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url,
                data=json.dumps({
                    "section": "preferences",
                    "preferred_age_min": 25,
                    "preferred_age_max": 35,
                    "preferred_genders": ["F", "NB"],
                    "first_step_preference": "i_initiate",
                    "astro_enabled": False,
                    "sought_qualities_ids": str(self.sought.pk),
                }),
                content_type="application/json",
                HTTP_HOST="crush.lu",
            )

        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.preferred_age_min, 25)
        self.assertEqual(self.profile.preferred_age_max, 35)
        self.assertEqual(self.profile.preferred_genders, ["F", "NB"])
        self.assertEqual(self.profile.first_step_preference, "i_initiate")
        self.assertFalse(self.profile.astro_enabled)
        self.assertEqual(list(self.profile.sought_qualities.values_list("pk", flat=True)), [self.sought.pk])
        mock_update.assert_called_once_with(self.user)

    def test_privacy_autosave_updates_booleans(self):
        response = self.client.post(
            self.url,
            data=json.dumps({
                "section": "privacy",
                "show_full_name": True,
                "show_exact_age": False,
            }),
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.show_full_name)
        self.assertFalse(self.profile.show_exact_age)
