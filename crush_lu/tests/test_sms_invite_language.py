"""
Test that SMS invite bodies use the recipient's preferred language,
not the coach's active language — covers event title (modeltranslation)
and event URL prefix.
"""
from datetime import date, timedelta
from urllib.parse import unquote

from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.utils import timezone

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


@override_settings(**CRUSH_LU_URL_SETTINGS)
class SmsInviteLanguageTests(TestCase):
    """Verify event title and URL in SMS body use the recipient's language."""

    def setUp(self):
        from crush_lu.models import CrushCoach, CrushProfile, MeetupEvent
        from crush_lu.models.profiles import UserDataConsent
        from crush_lu.models.site_config import CrushSiteConfig

        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )

        # --- Coach ---
        self.coach_user = User.objects.create_user(
            username="coach@test.com",
            email="coach@test.com",
            password="coachpass",
            first_name="Sophie",
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, is_active=True, max_active_reviews=10
        )

        # --- French-speaking candidate ---
        self.fr_user = User.objects.create_user(
            username="fr@test.com",
            email="fr@test.com",
            password="pass",
            first_name="Marie",
        )
        UserDataConsent.objects.filter(user=self.fr_user).update(
            crushlu_consent_given=True
        )
        self.fr_profile = CrushProfile.objects.create(
            user=self.fr_user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg",
            is_approved=True,
            phone_number="+352691000001",
            phone_verified=True,
            preferred_language="fr",
        )

        # --- Event with French title set ---
        self.event = MeetupEvent.objects.create(
            title="Test Speed Dating",        # default / English
            title_de="Test Speed Dating DE",
            title_fr="Test Speed Dating FR",
            description="desc",
            event_type="speed_dating",
            date_time=timezone.now() + timedelta(days=3),
            location="Luxembourg",
            address="1 Test St",
            max_participants=20,
            min_age=18,
            max_age=45,
            registration_deadline=timezone.now() + timedelta(days=2),
            registration_fee=0,
            is_published=True,
            profile_requirement="approved",
        )
        self.event.coaches.add(self.coach)

        # Ensure SMS templates exist
        CrushSiteConfig.get_config()

        # Client browsing as coach in GERMAN
        self.client = Client()
        self.client.login(username="coach@test.com", password="coachpass")

    def _get_sms_invite_page(self):
        """Fetch the SMS invite page with German as active language (coach's language)."""
        url = f"/de/coach/events/{self.event.id}/sms-invite/"
        return self.client.get(url, HTTP_ACCEPT_LANGUAGE="de")

    def test_sms_body_uses_recipient_language_not_coach_language(self):
        """French recipient must get FR event title and /fr/ URL, even when coach browses in DE."""
        response = self._get_sms_invite_page()
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()

        # Find the sms: URI for the French candidate
        # It appears in href="sms:+352691000001?body=..."
        import re
        match = re.search(r'href="(sms:\+352691000001[^"]*)"', content)
        self.assertIsNotNone(match, "Could not find SMS URI for French candidate in page")

        sms_uri = unquote(match.group(1))

        # Title must be the French title, NOT the German one
        self.assertIn(
            "Test Speed Dating FR", sms_uri,
            f"Expected French event title in SMS body, got: {sms_uri}"
        )
        self.assertNotIn(
            "Test Speed Dating DE", sms_uri,
            f"German event title must NOT appear in French candidate's SMS: {sms_uri}"
        )

        # URL must use /fr/ prefix, not /de/
        self.assertIn(
            "/fr/", sms_uri,
            f"Expected /fr/ URL prefix in SMS body, got: {sms_uri}"
        )
        self.assertNotIn(
            "/de/", sms_uri,
            f"/de/ URL prefix must NOT appear in French candidate's SMS: {sms_uri}"
        )
