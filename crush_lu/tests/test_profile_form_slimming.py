"""Profile form slimming (2026-11-20 onboarding UI review).

- Event languages are asked in section 1 and saved by save-step1.
- The site language is pre-selected when nothing is chosen yet.
- "Ask me about" chips start hidden unless the interest is selected (the
  wrapper must not carry a display class that beats ``hidden`` in the CSS).
- The bottom menu bar is hidden while a member builds the first profile.
"""

import json
import re
from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from crush_lu.models import CrushProfile
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()


def step1_payload(**extra):
    payload = {
        "phone_number": "+352621000111",
        "date_of_birth": "1991-05-02",
        "gender": "F",
        "location": "canton-luxembourg",
    }
    payload.update(extra)
    return json.dumps(payload)


class ProfileFormSlimmingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="slim@example.com", email="slim@example.com", password="pass123"
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            phone_number="+352621000111",
            phone_verified=True,
            phone_verified_at=timezone.now(),
            welcome_seen_at=timezone.now(),
            coach_intro_seen_at=timezone.now(),
            verification_status="incomplete",
            date_of_birth=date(1991, 5, 2),
        )
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": True}
        )
        self.client = Client()
        self.client.force_login(self.user)

    def post_step1(self, body):
        return self.client.post(
            "/api/profile/save-step1/",
            data=body,
            content_type="application/json",
            HTTP_HOST="crush.lu",
        )

    def test_step1_saves_event_languages(self):
        response = self.post_step1(step1_payload(event_languages=["de", "fr", "xx", "de"]))
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.event_languages, ["de", "fr"])

    def test_step1_requires_a_language_when_sent(self):
        response = self.post_step1(step1_payload(event_languages=[]))
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("event_languages", body["errors"])
        self.assertIn("event language", body["error"])

    def test_step1_without_key_still_works_for_cached_js(self):
        response = self.post_step1(step1_payload())
        self.assertEqual(response.status_code, 200)

    def render(self, lang="de"):
        response = self.client.get(f"/{lang}/create-profile/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_languages_rendered_in_section_1_with_site_language_preselected(self):
        html = self.render("de")
        step1 = html[html.index('data-wizard-step="1"') : html.index('data-wizard-step="2"')]
        self.assertIn("data-event-languages", step1)
        checked = re.findall(r'name="event_languages" value="(\w+)"[^>]*?\schecked', step1, re.S)
        self.assertEqual(checked, ["de"])

    def test_saved_languages_win_over_site_language(self):
        self.profile.event_languages = ["fr"]
        self.profile.save()
        html = self.render("de")
        checked = re.findall(r'name="event_languages" value="(\w+)"[^>]*?\schecked', html, re.S)
        self.assertEqual(checked, ["fr"])

    def test_ask_me_about_chips_start_hidden(self):
        html = self.render("en")
        wraps = re.findall(r"<span data-askme-wrap [^>]*>", html)
        self.assertTrue(wraps)
        for wrap in wraps:
            self.assertIn('class="hidden"', wrap)
            self.assertNotIn("inline-block", wrap)

    def test_no_bottom_nav_while_building_first_profile(self):
        self.assertNotIn('class="bottom-nav"', self.render("en"))
        self.profile.verification_status = "pending"
        self.profile.save()
        response = self.client.get("/en/create-profile/?edit=1", HTTP_HOST="crush.lu")
        if response.status_code == 200:
            self.assertIn('class="bottom-nav"', response.content.decode())
