from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models.journey import JourneyConfiguration
from crush_lu.models.profiles import CrushProfile, ProfileSubmission, SpecialUserExperience


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
        self.profile = CrushProfile.objects.create(user=self.user, location="Luxembourg City")

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
        self.assertEqual(self.profile.city, "Luxembourg City")


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class RegistrationFlowTests(TestCase):
    def test_user_can_register_and_create_profile(self):
        signup_data = {
            "email": "newuser@example.com",
            "password1": "SecurePass123!",
            "password2": "SecurePass123!",
            "first_name": "New",
            "last_name": "User",
        }

        response = self.client.post(reverse("crush_lu:signup"), signup_data, follow=True, HTTP_HOST="crush.lu")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(email="newuser@example.com").exists())
        user = User.objects.get(email="newuser@example.com")
        self.assertGreater(len(response.redirect_chain), 0)
        self.assertEqual(response.redirect_chain[-1][0], reverse("crush_lu:create_profile"))

        profile_data = {
            "phone_number": "+35212345678",
            "date_of_birth": (timezone.now().date() - timedelta(days=30 * 365)).isoformat(),
            "gender": "F",
            "location": "Luxembourg City",
            "looking_for": "dating",
            "bio": "Testing bio",
            "interests": "Reading, Hiking",
        }

        profile_response = self.client.post(reverse("crush_lu:create_profile"), profile_data, follow=True, HTTP_HOST="crush.lu")

        self.assertEqual(profile_response.status_code, 200)
        profile = CrushProfile.objects.get(user=user)
        self.assertEqual(profile.location, "Luxembourg City")
        self.assertEqual(profile.gender, "F")
        self.assertEqual(profile.completion_status, "submitted")
        self.assertTrue(ProfileSubmission.objects.filter(profile=profile).exists())
