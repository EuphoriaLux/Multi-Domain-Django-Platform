"""Tests for Phase 4 — calibration (3-section) screening call checklist."""
from __future__ import annotations

import json
from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=True,
)
class CalibrationModeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.coach_user = User.objects.create_user(
            username="coach_calib@example.com",
            email="coach_calib@example.com",
            password="pw",
            first_name="Coach",
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, bio="b", is_active=True, max_active_reviews=10
        )
        self.user = User.objects.create_user(
            username="ucalib@example.com",
            email="ucalib@example.com",
            password="pw",
            first_name="User",
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg City",
            bio="bio",
            phone_number="+352661234568",
            phone_verified=True,
            event_languages=["en"],
            is_approved=False,
        )
        self.submission = ProfileSubmission.objects.create(
            profile=self.profile, coach=self.coach, status="pending"
        )

    # --------- Auto-set on finalize ---------

    def test_finalize_sets_calibration_mode(self):
        self.client.login(username="ucalib@example.com", password="pw")
        self.submission.pre_screening_responses = {
            "residence": "lu_city",
            "languages": ["en"],
            "age_confirm": True,
            "source": "friend",
            "what_is_crush": "events",
            "event_frequency": "monthly",
            "relationship_goal": "meaningful",
            "coach_attitude": "loves",
            "looking_forward_to": ["events"],
            "hoping_to_meet": "Someone curious who asks great questions.",
            "consent_events": True,
            "consent_coach": True,
            "consent_no_show": True,
            "consent_terms": True,
        }
        self.submission.save()
        self.assertEqual(self.submission.screening_call_mode, "legacy")
        self.client.post(reverse("crush_lu:pre_screening_finalize"))
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.screening_call_mode, "calibration")

    # --------- Tab branching ---------

    def test_review_page_renders_calibration_tab_when_mode_set(self):
        self.submission.screening_call_mode = "calibration"
        self.submission.pre_screening_submitted_at = timezone.now()
        self.submission.pre_screening_responses = {"what_is_crush": "tinder"}
        self.submission.save()
        self.client.login(username="coach_calib@example.com", password="pw")
        resp = self.client.get(
            reverse("crush_lu:coach_review_profile", args=[self.submission.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Short Calibration Call")
        self.assertContains(resp, "screeningCallCalibration")
        self.assertContains(resp, 'data-concept-answer="tinder"')
        # Legacy 5-section component must not also be rendered on the tab.
        self.assertNotContains(resp, "screeningCallGuideline")

    def test_review_page_renders_legacy_tab_when_mode_legacy(self):
        self.submission.screening_call_mode = "legacy"
        self.submission.save()
        self.client.login(username="coach_calib@example.com", password="pw")
        resp = self.client.get(
            reverse("crush_lu:coach_review_profile", args=[self.submission.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "screeningCallGuideline")
        self.assertNotContains(resp, "screeningCallCalibration")

    # --------- Mode toggle ---------

    def test_coach_can_switch_to_calibration_when_prescreening_submitted(self):
        self.submission.pre_screening_submitted_at = timezone.now()
        self.submission.screening_call_mode = "legacy"
        self.submission.save()
        self.client.login(username="coach_calib@example.com", password="pw")
        resp = self.client.post(
            reverse(
                "crush_lu:coach_set_screening_mode", args=[self.submission.id]
            ),
            {"mode": "calibration"},
        )
        self.assertEqual(resp.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.screening_call_mode, "calibration")

    def test_coach_can_revert_to_legacy(self):
        self.submission.pre_screening_submitted_at = timezone.now()
        self.submission.screening_call_mode = "calibration"
        self.submission.save()
        self.client.login(username="coach_calib@example.com", password="pw")
        resp = self.client.post(
            reverse(
                "crush_lu:coach_set_screening_mode", args=[self.submission.id]
            ),
            {"mode": "legacy"},
        )
        self.assertEqual(resp.status_code, 200)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.screening_call_mode, "legacy")

    def test_cannot_set_calibration_without_prescreening(self):
        self.client.login(username="coach_calib@example.com", password="pw")
        resp = self.client.post(
            reverse(
                "crush_lu:coach_set_screening_mode", args=[self.submission.id]
            ),
            {"mode": "calibration"},
        )
        self.assertEqual(resp.status_code, 400)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.screening_call_mode, "legacy")

    def test_cannot_change_mode_after_call_completed(self):
        self.submission.pre_screening_submitted_at = timezone.now()
        self.submission.review_call_completed = True
        self.submission.save()
        self.client.login(username="coach_calib@example.com", password="pw")
        resp = self.client.post(
            reverse(
                "crush_lu:coach_set_screening_mode", args=[self.submission.id]
            ),
            {"mode": "calibration"},
        )
        self.assertEqual(resp.status_code, 410)

    # --------- Completion flow accepts calibration shape ---------

    def test_complete_call_accepts_calibration_checklist_shape(self):
        self.submission.screening_call_mode = "calibration"
        self.submission.save()
        self.client.login(username="coach_calib@example.com", password="pw")
        calibration_data = {
            "mode": "calibration",
            "warm_intro_complete": True,
            "concept_calibration_complete": True,
            "concept_notes": "They were open to events.",
            "discretion_notes": "Friendly and responsive.",
            "concept_answer": "tinder",
        }
        resp = self.client.post(
            reverse(
                "crush_lu:coach_mark_review_call_complete",
                args=[self.submission.id],
            ),
            {
                "call_notes": "Good call, calibration done.",
                "checklist_data": json.dumps(calibration_data),
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.review_call_completed)
        self.assertEqual(
            self.submission.review_call_checklist["mode"], "calibration"
        )
        self.assertTrue(
            self.submission.review_call_checklist["warm_intro_complete"]
        )
