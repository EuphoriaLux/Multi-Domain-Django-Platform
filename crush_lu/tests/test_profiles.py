from datetime import timedelta
import json

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import Trait
from crush_lu.models.journey import JourneyConfiguration
from crush_lu.models.profiles import (
    CrushProfile,
    ProfileSubmission,
    SpecialUserExperience,
    UserDataConsent,
)


class SpecialUserExperienceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="alice", first_name="Alice", last_name="Wonder"
        )

    def test_matches_user_is_case_insensitive_and_active(self):
        special = SpecialUserExperience.objects.create(
            first_name="alice", last_name="wonder", is_active=True
        )

        self.assertTrue(special.matches_user(self.user))

    def test_matches_user_respects_inactive_state(self):
        special = SpecialUserExperience.objects.create(
            first_name="Alice", last_name="Wonder", is_active=False
        )

        self.assertFalse(special.matches_user(self.user))

    def test_trigger_updates_timestamp_and_count(self):
        special = SpecialUserExperience.objects.create(
            first_name="Alice", last_name="Wonder"
        )

        self.assertIsNone(special.last_triggered_at)
        self.assertEqual(special.trigger_count, 0)

        special.trigger()
        special.refresh_from_db()

        self.assertIsNotNone(special.last_triggered_at)
        self.assertEqual(special.trigger_count, 1)

    def test_journey_helpers_return_expected_records(self):
        special = SpecialUserExperience.objects.create(
            first_name="Alice", last_name="Wonder"
        )
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
        self.profile = CrushProfile.objects.create(
            user=self.user, location="canton-luxembourg"
        )

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

        response = self.client.post(
            reverse("crush_lu:signup"), signup_data, follow=True, HTTP_HOST="crush.lu"
        )

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
        EmailAddress.objects.filter(user=user, email__iexact=user.email).update(
            verified=True, primary=True
        )
        self.client.force_login(user)

        # Get or create the profile and mark phone + coach_intro as done so
        # the submission gate (phone_verified + coach_intro_seen_at) passes.
        profile, _ = CrushProfile.objects.get_or_create(user=user)
        profile.phone_number = "+35212345678"
        profile.phone_verified = True
        profile.phone_verified_at = timezone.now()
        profile.coach_intro_seen_at = timezone.now()
        profile.save()

        profile_data = {
            "phone_number": "+35212345678",
            "date_of_birth": (
                timezone.now().date() - timedelta(days=30 * 365)
            ).isoformat(),
            "gender": "F",
            "location": "canton-luxembourg",  # Canton-based location (from interactive map)
            "bio": "Testing bio",
            "interests": "Reading, Hiking",
            "event_languages": ["en", "fr"],
        }

        profile_response = self.client.post(
            reverse("crush_lu:create_profile"),
            profile_data,
            follow=True,
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(profile_response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.location, "canton-luxembourg")
        self.assertEqual(profile.gender, "F")
        self.assertEqual(profile.completion_status, "submitted")
        self.assertEqual(profile.verification_status, "pending")
        # The free path no longer creates a ProfileSubmission up front — the
        # user verifies via LuxId (free) or purchases a coach review (paid),
        # so none should exist yet at this point.
        self.assertFalse(ProfileSubmission.objects.filter(profile=profile).exists())


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CrushPreferencesTests(TestCase):
    """The standalone "Ideal Crush" preferences page has been retired — its data
    now lives in the opt-in Crush Connect onboarding. The old URL redirects to
    the dashboard and edit_profile no longer serves a preferences section."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="pref@example.com",
            email="pref@example.com",
            password="testpass123",
        )

    def _grant_consent(self):
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

    def test_old_url_redirects_to_dashboard(self):
        CrushProfile.objects.create(
            user=self.user, location="canton-luxembourg", is_approved=True
        )
        self._grant_consent()
        self.client.login(username="pref@example.com", password="testpass123")
        response = self.client.get(
            reverse("crush_lu:crush_preferences"), HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("crush_lu:dashboard"), response.url)

    def test_preferences_section_retired(self):
        """edit_profile no longer serves an Ideal Crush preferences form — a
        stale ?section=preferences link falls through to the section card list."""
        CrushProfile.objects.create(
            user=self.user, location="canton-luxembourg", is_approved=True
        )
        self._grant_consent()
        self.client.login(username="pref@example.com", password="testpass123")
        url = reverse("crush_lu:edit_profile") + "?section=preferences"
        response = self.client.get(url, HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("selected_sought_json", response.context)


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
            defaults={
                "trait_type": "quality",
                "label": "Kind",
                "category": "personality",
            },
        )
        self.defect, _ = Trait.objects.get_or_create(
            slug="stubborn",
            defaults={
                "trait_type": "defect",
                "label": "Stubborn",
                "category": "personality",
            },
        )
        self.sought, _ = Trait.objects.get_or_create(
            slug="curious",
            defaults={
                "trait_type": "quality",
                "label": "Curious",
                "category": "personality",
            },
        )

        self.client.login(username="autosave@example.com", password="testpass123")
        self.url = reverse("api_profile_settings_autosave")

    def test_about_autosave_section_retired(self):
        """The free-text 'about' (bio/interests) autosave section was retired by
        the Event Identity redesign — a direct POST is rejected and writes
        nothing (spec §6.2)."""
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "section": "about",
                    "bio": "Updated bio",
                    "interests": "Music, Travel",
                }
            ),
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(response.status_code, 400)
        self.profile.refresh_from_db()
        # Legacy free-text fields are untouched by the retired section.
        self.assertEqual(self.profile.bio, "Original bio")
        self.assertEqual(self.profile.interests, "Original interests")

    def test_event_identity_autosave_round_trips(self):
        """The merged 'event_identity' section round-trips interests_new (M2M),
        ask_me_about (JSON) and event_vibe (choice) through the autosave
        contract (spec §8.3, §13)."""
        from crush_lu.models import Interest

        yoga = Interest.objects.get(slug="yoga")
        city = Interest.objects.get(slug="city-trips")
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "section": "event_identity",
                    "interests_new": [yoga.pk, city.pk],
                    "ask_me_about": [yoga.pk],
                    "event_vibe": "quiet_corner",
                    "event_languages": ["en"],
                }
            ),
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(
            set(self.profile.interests_new.values_list("slug", flat=True)),
            {"yoga", "city-trips"},
        )
        self.assertEqual(self.profile.ask_me_about, [yoga.pk])
        self.assertEqual(self.profile.event_vibe, "quiet_corner")
        # The response echoes the structured values back for the client.
        values = response.json()["values"]
        self.assertEqual(sorted(values["interests_new"]), sorted([yoga.pk, city.pk]))
        self.assertEqual(values["ask_me_about"], [yoga.pk])
        self.assertEqual(values["event_vibe"], "quiet_corner")

    def test_contact_autosave_rejects_invalid_location(self):
        """Invalid location sent to the contact section should be rejected."""
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "section": "contact",
                    "location": "invalid-location",
                }
            ),
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(response.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.location, "canton-luxembourg")

    def test_preferences_autosave_section_retired(self):
        """The 'preferences' (Ideal Crush) autosave section has been removed —
        those answers now live in Crush Connect, so the endpoint rejects it."""
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "section": "preferences",
                    "preferred_age_min": 25,
                    "preferred_age_max": 35,
                }
            ),
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )
        self.assertEqual(response.status_code, 400)
        self.profile.refresh_from_db()
        # Profile preferences are untouched by the retired endpoint.
        self.assertNotEqual(self.profile.preferred_age_min, 25)

    def test_privacy_autosave_updates_booleans(self):
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "section": "privacy",
                    "show_full_name": True,
                    "show_exact_age": False,
                }
            ),
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.show_full_name)
        self.assertFalse(self.profile.show_exact_age)
