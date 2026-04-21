"""Integration tests for the pre-screening questionnaire views."""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent
from crush_lu.pre_screening_schema import PRE_SCREENING_SCHEMA

User = get_user_model()


def _valid_final_responses() -> dict:
    return {
        "residence": "lu_city",
        "languages": ["en", "fr"],
        "age_confirm": True,
        "source": "friend",
        "what_is_crush": "events",
        "event_frequency": "monthly",
        "relationship_goal": "meaningful",
        "coach_attitude": "loves",
        "looking_forward_to": ["events", "discovery"],
        "hoping_to_meet": "Someone curious who likes walking and honest conversation and good coffee.",
        "note_to_coach": "",
        "consent_events": True,
        "consent_coach": True,
        "consent_no_show": True,
        "consent_terms": True,
    }


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=True,
)
class PreScreeningFormTests(TestCase):
    def setUp(self):
        cache.clear()
        self.coach_user = User.objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="coachpw",
            first_name="Coach",
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, bio="Bio", is_active=True, max_active_reviews=5
        )
        self.user = User.objects.create_user(
            username="u@example.com",
            email="u@example.com",
            password="userpw",
            first_name="User",
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        # Schema v2: residence, languages, and age_confirm are readonly_confirm
        # questions derived from the CrushProfile. Fixture values here are chosen
        # so the derivation yields the assertions below:
        #   location="canton-luxembourg" → residence="lu_city"
        #   event_languages=["en","fr"]  → languages=["en","fr"]
        #   date_of_birth=1995-01-01     → age_confirm=True
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="M",
            location="canton-luxembourg",
            bio="Test bio",
            phone_number="+352661234567",
            event_languages=["en", "fr"],
            is_approved=False,
        )
        self.submission = ProfileSubmission.objects.create(
            profile=self.profile, coach=self.coach, status="pending"
        )
        self.client.login(username="u@example.com", password="userpw")

    # --------------- GET form ---------------

    def test_get_renders_form(self):
        resp = self.client.get(reverse("crush_lu:pre_screening"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Help your Coach prepare")
        # Schema v2 renamed Section A to "Confirm your details".
        self.assertContains(resp, "Confirm your details")
        self.assertContains(resp, "0 of 4 sections complete")
        self.assertContains(resp, 'id="prescreening-progress"')
        self.assertContains(resp, 'id="prescreening-finalize"')

    def test_redirect_when_no_submission(self):
        self.submission.delete()
        self.profile.delete()
        resp = self.client.get(reverse("crush_lu:pre_screening"))
        self.assertEqual(resp.status_code, 302)

    def test_redirect_when_already_approved(self):
        self.submission.status = "approved"
        self.submission.save()
        resp = self.client.get(reverse("crush_lu:pre_screening"))
        self.assertRedirects(
            resp, reverse("crush_lu:profile_submitted"), fetch_redirect_response=False
        )

    def test_redirect_when_call_completed(self):
        self.submission.review_call_completed = True
        self.submission.save()
        resp = self.client.get(reverse("crush_lu:pre_screening"))
        self.assertRedirects(
            resp, reverse("crush_lu:profile_submitted"), fetch_redirect_response=False
        )

    # --------------- HTMX per-section save ---------------

    def test_save_section_persists_valid_answers(self):
        url = reverse("crush_lu:pre_screening_save_section", args=["logistics"])
        resp = self.client.post(url, {
            "residence": "lu_city",
            "languages": ["en", "fr"],
            "age_confirm": "yes",
            "source": "friend",
        })
        self.assertEqual(resp.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.pre_screening_responses["residence"], "lu_city")
        self.assertEqual(
            sorted(self.submission.pre_screening_responses["languages"]),
            ["en", "fr"],
        )
        self.assertIs(self.submission.pre_screening_responses["age_confirm"], True)
        # Partial save must NOT set finalize timestamp
        self.assertIsNone(self.submission.pre_screening_submitted_at)

    def test_save_section_returns_oob_progress_and_finalize(self):
        url = reverse("crush_lu:pre_screening_save_section", args=["logistics"])
        resp = self.client.post(url, {
            "residence": "lu_city",
            "languages": ["en"],
            "age_confirm": "yes",
            "source": "friend",
        })
        body = resp.content.decode()
        self.assertIn('hx-swap-oob="outerHTML:#prescreening-progress"', body)
        self.assertIn('hx-swap-oob="outerHTML:#prescreening-finalize"', body)
        self.assertIn("1 of 4 sections complete", body)

    def test_all_four_sections_save_independently(self):
        section_payloads = {
            "logistics": {
                "residence": "lu_city",
                "languages": ["en"],
                "age_confirm": "yes",
                "source": "friend",
            },
            "concept": {
                "what_is_crush": "events",
                "event_frequency": "monthly",
                "relationship_goal": "meaningful",
                "coach_attitude": "loves",
                "looking_forward_to": ["events"],
            },
            "own_words": {
                "hoping_to_meet": "Someone curious who likes walking and honest talk.",
            },
            "consents": {
                "consent_events": "1",
                "consent_coach": "1",
                "consent_no_show": "1",
                "consent_terms": "1",
            },
        }
        for section_id, payload in section_payloads.items():
            url = reverse(
                "crush_lu:pre_screening_save_section", args=[section_id]
            )
            resp = self.client.post(url, payload)
            self.assertEqual(resp.status_code, 200, f"{section_id} save failed")
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.pre_screening_responses["residence"], "lu_city")
        self.assertEqual(
            self.submission.pre_screening_responses["what_is_crush"], "events"
        )
        self.assertTrue(self.submission.pre_screening_responses["consent_terms"])
        # Finalize is now possible.
        final = self.client.post(reverse("crush_lu:pre_screening_finalize"))
        self.assertEqual(final.status_code, 302)
        self.submission.refresh_from_db()
        self.assertIsNotNone(self.submission.pre_screening_submitted_at)

    def test_save_section_unknown_id_404s(self):
        url = reverse(
            "crush_lu:pre_screening_save_section", args=["not-a-section"]
        )
        resp = self.client.post(url, {"residence": "lu_city"})
        self.assertEqual(resp.status_code, 404)

    def test_save_section_clears_prior_answers_in_same_section(self):
        self.submission.pre_screening_responses = {"source": "friend"}
        self.submission.save(update_fields=["pre_screening_responses"])
        url = reverse("crush_lu:pre_screening_save_section", args=["logistics"])
        self.client.post(url, {
            "residence": "lu_esch",
            "languages": ["de"],
            "age_confirm": "yes",
            "source": "social",
        })
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.pre_screening_responses["source"], "social")

    # --------------- Finalize ---------------

    def test_finalize_requires_full_valid_set(self):
        self.submission.pre_screening_responses = {"residence": "lu_city"}
        self.submission.save(update_fields=["pre_screening_responses"])
        resp = self.client.post(reverse("crush_lu:pre_screening_finalize"))
        self.assertEqual(resp.status_code, 302)
        self.submission.refresh_from_db()
        self.assertIsNone(self.submission.pre_screening_submitted_at)

    def test_finalize_persists_score_and_flags(self):
        self.submission.pre_screening_responses = _valid_final_responses()
        self.submission.save(update_fields=["pre_screening_responses"])
        resp = self.client.post(reverse("crush_lu:pre_screening_finalize"))
        self.assertRedirects(
            resp,
            reverse("crush_lu:profile_submitted"),
            fetch_redirect_response=False,
        )
        self.submission.refresh_from_db()
        self.assertIsNotNone(self.submission.pre_screening_submitted_at)
        self.assertEqual(
            self.submission.pre_screening_version,
            PRE_SCREENING_SCHEMA["version"],
        )
        self.assertIsNotNone(self.submission.pre_screening_readiness_score)
        self.assertIsInstance(self.submission.pre_screening_flags, list)


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=False,
)
class PreScreeningFeatureFlagTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="u2@example.com", email="u2@example.com", password="pw"
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1990, 1, 1),
            gender="M",
            location="Luxembourg City",
            bio="x",
            phone_number="+352661234567",
            event_languages=["en"],
            is_approved=False,
        )
        ProfileSubmission.objects.create(profile=self.profile, status="pending")
        self.client.login(username="u2@example.com", password="pw")

    def test_flag_off_redirects_away(self):
        resp = self.client.get(reverse("crush_lu:pre_screening"))
        self.assertRedirects(
            resp,
            reverse("crush_lu:profile_submitted"),
            fetch_redirect_response=False,
        )
