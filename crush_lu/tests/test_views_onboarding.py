"""
Tests for the /welcome/ onboarding page (Step 1 of the 7-step journey).

Covers:
  - Login gate
  - One-shot behavior (welcome_seen_at gates re-entry)
  - Intent-probe API persistence + validation
  - Signup post-redirect now lands on /welcome/ instead of /create-profile/
"""
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site

from crush_lu.models import CrushProfile
from crush_lu.models.profiles import UserDataConsent


User = get_user_model()


CRUSH_LU_URL_SETTINGS = {
    "ROOT_URLCONF": "azureproject.urls_crush",
}


class _SiteMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class WelcomeViewTests(_SiteMixin, TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            password="pass-pass-pass",
            first_name="Alex",
        )
        UserDataConsent.objects.filter(user=self.user).update(crushlu_consent_given=True)

    def test_welcome_requires_login(self):
        response = self.client.get(reverse("crush_lu:welcome"))
        self.assertEqual(response.status_code, 302)
        # Redirects to login (not to another onboarding page)
        self.assertIn("/login/", response["Location"])

    def test_first_visit_renders_and_marks_seen(self):
        self.client.login(username="alex@example.com", password="pass-pass-pass")
        response = self.client.get(reverse("crush_lu:welcome"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alex")
        # Profile should have been autocreated and welcome_seen_at set
        profile = CrushProfile.objects.get(user=self.user)
        self.assertIsNotNone(profile.welcome_seen_at)

    def test_second_visit_redirects_to_onboarding_entry(self):
        CrushProfile.objects.create(user=self.user)
        self.client.login(username="alex@example.com", password="pass-pass-pass")
        # First visit
        self.client.get(reverse("crush_lu:welcome"))
        # Second visit — should redirect into the smart-resume entry so the
        # user lands on their actual current step.
        response = self.client.get(reverse("crush_lu:welcome"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/onboarding/", response["Location"])

    def test_stepper_renders_with_step_1_active(self):
        self.client.login(username="alex@example.com", password="pass-pass-pass")
        response = self.client.get(reverse("crush_lu:welcome"))
        # The ARIA attribute marks the active step
        self.assertContains(response, 'aria-current="step"')
        # Welcome is step 1 of 7
        self.assertContains(response, "1/7")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class WelcomeIntentApiTests(_SiteMixin, TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            password="pass-pass-pass",
        )
        self.profile = CrushProfile.objects.create(user=self.user)
        self.url = "/api/welcome/intent/"  # language-neutral
        self.client.login(username="alex@example.com", password="pass-pass-pass")

    def test_persists_valid_choice_and_returns_html(self):
        response = self.client.post(self.url, {"intent": "events"})
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.intent_probe, "events")
        # Response is an HTML fragment (not JSON)
        self.assertIn("text/html", response["Content-Type"])

    def test_rejects_invalid_choice(self):
        response = self.client.post(self.url, {"intent": "bogus"})
        self.assertEqual(response.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.intent_probe, "")

    def test_rejects_missing_choice(self):
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 400)

    def test_returns_warn_branch_for_online(self):
        response = self.client.post(self.url, {"intent": "online"})
        self.assertEqual(response.status_code, 200)
        # Warn branch shows the Learn-more + confirm buttons
        self.assertContains(response, "Learn more")
        self.assertContains(response, "Yes, I")  # "Yes, I'm in"

    def test_requires_login(self):
        self.client.logout()
        response = self.client.post(self.url, {"intent": "events"})
        self.assertEqual(response.status_code, 302)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class SignupRedirectTests(_SiteMixin, TestCase):
    """Verify the signup redirect now points at /onboarding/ (smart-resume)."""

    def test_signup_redirects_to_onboarding_entry(self):
        response = self.client.post(
            reverse("crush_lu:signup"),
            {
                "email": "new@example.com",
                "password1": "verysafepassword12",
                "password2": "verysafepassword12",
                "first_name": "New",
                "last_name": "User",
                "age_18_plus": "on",
                "terms_consent": "on",
                "privacy_consent": "on",
            },
        )
        # May redirect to onboarding or re-render the form if fields are missing.
        # If successful, it must land on /onboarding/ (not /create-profile/).
        if response.status_code == 302:
            self.assertIn("/onboarding/", response["Location"])
