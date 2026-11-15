"""The Crush.lu consent gate returns to the page it interrupted.

Before, confirming consent always ended on the dashboard, which is where a
coach's first "Sign in with crush.lu" on test.crush.lu landed instead of the
playground. Review: ai-memory-hub/reviews/2026-11-15-coach-playground-local-test.md
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from crush_lu.models.profiles import UserDataConsent

HOST = {"HTTP_HOST": "crush.lu"}


class ConsentNextTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            "nc", "nc@example.com", "pw12345678"
        )
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": False}
        )
        self.client.force_login(self.user)

    def test_gate_keeps_the_requested_page(self):
        response = self.client.get("/en/events/?page=2", **HOST)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            "/en/consent/confirm/?next=%2Fen%2Fevents%2F%3Fpage%3D2",
        )

    def test_form_carries_the_target(self):
        response = self.client.get("/en/consent/confirm/?next=/en/events/", **HOST)
        self.assertContains(response, 'name="next" value="/en/events/"')

    def test_consent_returns_to_the_target(self):
        response = self.client.post(
            "/en/consent/confirm/",
            {"crushlu_consent": "on", "next": "/en/events/"},
            **HOST,
        )
        self.assertEqual(response["Location"], "/en/events/")
        self.user.data_consent.refresh_from_db()
        self.assertTrue(self.user.data_consent.crushlu_consent_given)

    def test_without_a_target_it_is_still_the_dashboard(self):
        response = self.client.post(
            "/en/consent/confirm/", {"crushlu_consent": "on"}, **HOST
        )
        self.assertEqual(response["Location"], "/en/dashboard/")

    def test_unsafe_targets_fall_back_to_the_dashboard(self):
        for target in (
            "https://evil.example/",
            "//evil.example/",
            "javascript:alert(1)",
            "/en/consent/confirm/?next=/en/events/",
        ):
            UserDataConsent.objects.filter(user=self.user).update(
                crushlu_consent_given=False
            )
            response = self.client.post(
                "/en/consent/confirm/",
                {"crushlu_consent": "on", "next": target},
                **HOST,
            )
            self.assertEqual(response["Location"], "/en/dashboard/", target)

    def test_unticked_box_keeps_the_target(self):
        response = self.client.post(
            "/en/consent/confirm/", {"next": "/en/events/"}, **HOST
        )
        self.assertEqual(
            response["Location"], "/en/consent/confirm/?next=%2Fen%2Fevents%2F"
        )

    def test_already_consented_goes_to_the_target(self):
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        response = self.client.get("/en/consent/confirm/?next=/en/events/", **HOST)
        self.assertEqual(response["Location"], "/en/events/")
