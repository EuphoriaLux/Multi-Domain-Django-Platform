import json

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings

from crush_lu.models import CrushProfile
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class ConsentMiddlewareApiBanTests(TestCase):
    def setUp(self):
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.user = User.objects.create_user(
            username="deleted@example.com",
            email="deleted@example.com",
            password="test-password",
        )
        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={
                "crushlu_consent_given": False,
                "crushlu_banned": True,
                "crushlu_ban_reason": "user_deletion",
            },
        )
        self.client.force_login(self.user)

    def test_deleted_member_cannot_create_profile_through_api(self):
        response = self.client.post(
            "/api/profile/save-step1/",
            data=json.dumps(
                {
                    "date_of_birth": "1990-01-01",
                    "gender": "M",
                    "location": "",
                    "phone_number": "",
                }
            ),
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "banned"})
        self.assertFalse(CrushProfile.objects.filter(user=self.user).exists())

    def test_banned_member_can_still_fetch_csrf_token(self):
        response = self.client.get("/api/csrf-token/", HTTP_HOST="crush.lu")

        self.assertEqual(response.status_code, 200)
