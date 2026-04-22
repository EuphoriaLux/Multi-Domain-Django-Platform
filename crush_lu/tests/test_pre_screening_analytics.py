"""Tests for Phase 6 — pre-screening analytics dashboard + CSV export."""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()


def _make_profile(username: str, phone_suffix: str) -> CrushProfile:
    user = User.objects.create_user(
        username=username, email=username, password="pw", first_name="Test"
    )
    UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
    return CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 1, 1),
        gender="F",
        location="Luxembourg City",
        bio="bio",
        phone_number=f"+35266{phone_suffix}",
        event_languages=["en"],
        is_approved=False,
    )


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=True,
)
class DashboardAnalyticsTests(TestCase):
    def setUp(self):
        self.coach_user = User.objects.create_superuser(
            username="super@example.com",
            email="super@example.com",
            password="pw",
            first_name="Super",
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, bio="b", is_active=True, max_active_reviews=10
        )

        # 3 submitted + 1 pending, varied scores/flags
        self.p1 = _make_profile("a@example.com", "1234501")
        self.s1 = ProfileSubmission.objects.create(
            profile=self.p1, coach=self.coach, status="pending",
            pre_screening_submitted_at=timezone.now(),
            pre_screening_version=1,
            pre_screening_readiness_score=9,
            pre_screening_flags=[],
            pre_screening_responses={
                "residence": "lu_city", "languages": ["en"], "age_confirm": True,
                "source": "friend", "what_is_crush": "events",
                "event_frequency": "monthly", "relationship_goal": "meaningful",
                "coach_attitude": "loves", "looking_forward_to": ["events"],
                "hoping_to_meet": "Someone who loves walks and asks good questions.",
                "consent_events": True, "consent_coach": True,
                "consent_no_show": True, "consent_terms": True,
            },
        )
        self.p2 = _make_profile("b@example.com", "1234502")
        self.s2 = ProfileSubmission.objects.create(
            profile=self.p2, coach=self.coach, status="pending",
            pre_screening_submitted_at=timezone.now(),
            pre_screening_version=1,
            pre_screening_readiness_score=3,
            pre_screening_flags=["concept_misalignment", "low_effort_text"],
            pre_screening_responses={
                "residence": "fr_border", "languages": ["fr"], "age_confirm": True,
                "source": "social", "what_is_crush": "tinder",
                "event_frequency": "few_per_year", "relationship_goal": "many_people",
                "coach_attitude": "curious", "looking_forward_to": ["exploring"],
                "hoping_to_meet": "idk",
                "consent_events": True, "consent_coach": True,
                "consent_no_show": True, "consent_terms": True,
            },
        )
        self.p3 = _make_profile("c@example.com", "1234503")
        self.s3 = ProfileSubmission.objects.create(
            profile=self.p3, coach=self.coach, status="pending",
            pre_screening_submitted_at=timezone.now(),
            pre_screening_version=1,
            pre_screening_readiness_score=6,
            pre_screening_flags=["concept_misalignment"],
            pre_screening_responses={"what_is_crush": "tinder"},
        )
        self.p4 = _make_profile("d@example.com", "1234504")
        self.s4 = ProfileSubmission.objects.create(
            profile=self.p4, coach=self.coach, status="pending"
        )

        self.client.login(username="super@example.com", password="pw")

    def test_dashboard_renders_prescreening_section(self):
        resp = self.client.get(reverse("crush_admin_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Pre-Screening Analytics")
        self.assertContains(resp, "Completion rate")
        self.assertContains(resp, "75.0%")  # 3 of 4 pending submitted
        self.assertContains(resp, "concept_misalignment")

    def test_csv_export_returns_rows(self):
        resp = self.client.get(reverse("crush_admin_pre_screening_csv"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp["Content-Type"])
        body = resp.content.decode()
        # Header row
        self.assertIn("section,question,answer,count", body)
        # what_is_crush=tinder appears twice (s2 + s3)
        self.assertIn("concept,what_is_crush,tinder,2", body)
        # Free-text stats trailer
        self.assertIn("free-text answer stats", body)

    def test_csv_export_requires_staff(self):
        self.client.logout()
        regular = _make_profile("regular@example.com", "1234599")
        self.client.login(username="regular@example.com", password="pw")
        resp = self.client.get(reverse("crush_admin_pre_screening_csv"))
        self.assertEqual(resp.status_code, 403)


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=False,
)
class DashboardFeatureFlagOffTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin2@example.com",
            email="admin2@example.com",
            password="pw",
        )
        UserDataConsent.objects.filter(user=self.admin).update(
            crushlu_consent_given=True
        )
        CrushCoach.objects.create(
            user=self.admin, bio="b", is_active=True, max_active_reviews=10
        )
        self.client.login(username="admin2@example.com", password="pw")

    def test_dashboard_hides_prescreening_when_flag_off(self):
        resp = self.client.get(reverse("crush_admin_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Pre-Screening Analytics")

    def test_csv_export_blocked_when_flag_off(self):
        resp = self.client.get(reverse("crush_admin_pre_screening_csv"))
        self.assertEqual(resp.status_code, 403)
