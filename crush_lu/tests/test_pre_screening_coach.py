"""Integration tests for the Coach-facing pre-screening display (Phase 3)."""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import CallAttempt, CrushCoach, CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()


def _complete_responses() -> dict:
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
        "hoping_to_meet": "Someone thoughtful, curious, and kind.",
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
class CoachPreScreeningDisplayTests(TestCase):
    def setUp(self):
        cache.clear()
        self.coach_user = User.objects.create_user(
            username="coach3@example.com",
            email="coach3@example.com",
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
            username="u3@example.com",
            email="u3@example.com",
            password="pw",
            first_name="Tester",
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
            phone_number="+352661234567",
            phone_verified=True,
            event_languages=["en"],
            is_approved=False,
            preferred_language="en",
        )
        self.submission = ProfileSubmission.objects.create(
            profile=self.profile, coach=self.coach, status="pending"
        )
        self.client.login(username="coach3@example.com", password="pw")

    # -------- Display when submitted --------

    def test_review_page_shows_pre_screening_when_submitted(self):
        self.submission.pre_screening_responses = _complete_responses()
        self.submission.pre_screening_submitted_at = timezone.now()
        self.submission.pre_screening_version = 1
        self.submission.pre_screening_readiness_score = 9
        self.submission.pre_screening_flags = []
        self.submission.save()
        resp = self.client.get(
            reverse("crush_lu:coach_review_profile", args=[self.submission.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Pre-Screening Answers")
        self.assertContains(resp, "Readiness")
        self.assertContains(resp, "9/10")
        # Choice labels should be resolved, not raw values
        self.assertContains(resp, "A way to meet people in real life through events")
        # Free-text answer renders
        self.assertContains(resp, "Someone thoughtful, curious, and kind.")

    def test_review_page_shows_not_submitted_state(self):
        resp = self.client.get(
            reverse("crush_lu:coach_review_profile", args=[self.submission.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Pre-screening not yet submitted")
        self.assertContains(resp, "Send SMS reminder")

    def test_flag_chips_render_with_tooltip(self):
        self.submission.pre_screening_responses = _complete_responses()
        self.submission.pre_screening_submitted_at = timezone.now()
        self.submission.pre_screening_flags = ["concept_misalignment", "low_effort_text"]
        self.submission.pre_screening_readiness_score = 4
        self.submission.save()
        resp = self.client.get(
            reverse("crush_lu:coach_review_profile", args=[self.submission.id])
        )
        self.assertContains(resp, "concept_misalignment")
        self.assertContains(resp, "low_effort_text")
        # Tooltip text from FLAG_DESCRIPTIONS
        self.assertContains(resp, "User thinks Crush.lu is a swipe app")

    # -------- SMS reminder endpoint --------

    def test_send_sms_reminder_creates_call_attempt(self):
        url = reverse(
            "crush_lu:coach_send_pre_screening_reminder",
            args=[self.submission.id],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200)
        attempts = CallAttempt.objects.filter(submission=self.submission)
        self.assertEqual(attempts.count(), 1)
        self.assertEqual(attempts.first().result, "sms_sent")
        body = resp.content.decode()
        self.assertIn("sms:+352661234567", body)
        self.assertIn("Crush.lu", body)

    def test_send_sms_reminder_rejects_unverified_phone(self):
        # CrushProfile.save() preserves phone_verified once True; bypass via QuerySet.update().
        CrushProfile.objects.filter(pk=self.profile.pk).update(phone_verified=False)
        url = reverse(
            "crush_lu:coach_send_pre_screening_reminder",
            args=[self.submission.id],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(
            CallAttempt.objects.filter(submission=self.submission).count(), 0
        )

    def test_send_sms_reminder_uses_user_preferred_language(self):
        self.profile.preferred_language = "fr"
        self.profile.save()
        url = reverse(
            "crush_lu:coach_send_pre_screening_reminder",
            args=[self.submission.id],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # French template phrase: "Bonjour"
        self.assertIn("Bonjour", body)


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=False,
)
class CoachDisplayFeatureFlagOffTests(TestCase):
    def setUp(self):
        cache.clear()
        self.coach_user = User.objects.create_user(
            username="coach4@example.com", email="coach4@example.com", password="pw"
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, bio="b", is_active=True, max_active_reviews=10
        )
        self.user = User.objects.create_user(
            username="u4@example.com", email="u4@example.com", password="pw"
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
            phone_number="+352661234567",
            phone_verified=True,
            event_languages=["en"],
            is_approved=False,
        )
        self.submission = ProfileSubmission.objects.create(
            profile=self.profile, coach=self.coach, status="pending"
        )
        self.client.login(username="coach4@example.com", password="pw")

    def test_display_hidden_when_flag_off(self):
        resp = self.client.get(
            reverse("crush_lu:coach_review_profile", args=[self.submission.id])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Pre-Screening Answers")
        self.assertNotContains(resp, "Pre-screening not yet submitted")

    def test_sms_reminder_blocked_when_flag_off(self):
        url = reverse(
            "crush_lu:coach_send_pre_screening_reminder",
            args=[self.submission.id],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(
            CallAttempt.objects.filter(submission=self.submission).count(), 0
        )


@override_settings(
    ROOT_URLCONF="azureproject.urls_crush",
    PRE_SCREENING_ENABLED=True,
)
class CoachQueueBadgeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.coach_user = User.objects.create_user(
            username="coach5@example.com", email="coach5@example.com", password="pw"
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user, bio="b", is_active=True, max_active_reviews=10
        )

    _phone_counter = 0

    def _make_submission(self, username: str, *, submitted: bool, score: int = 0):
        CoachQueueBadgeTests._phone_counter += 1
        phone = f"+35266{CoachQueueBadgeTests._phone_counter:07d}"
        user = User.objects.create_user(
            username=username, email=username, password="pw", first_name=username
        )
        UserDataConsent.objects.filter(user=user).update(crushlu_consent_given=True)
        profile = CrushProfile.objects.create(
            user=user,
            date_of_birth=date(1995, 1, 1),
            gender="F",
            location="Luxembourg City",
            bio="bio",
            phone_number=phone,
            event_languages=["en"],
            is_approved=False,
        )
        sub = ProfileSubmission.objects.create(
            profile=profile, coach=self.coach, status="pending"
        )
        if submitted:
            sub.pre_screening_submitted_at = timezone.now()
            sub.pre_screening_readiness_score = score
            sub.pre_screening_version = 1
            sub.save()
        return sub

    def test_queue_shows_badge_states(self):
        self._make_submission("alice@example.com", submitted=True, score=9)
        self._make_submission("bob@example.com", submitted=False)
        self.client.login(username="coach5@example.com", password="pw")
        resp = self.client.get(reverse("crush_lu:coach_profiles"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        # Submitted user: numeric score badge
        self.assertIn("9/10", body)
        # Pending user: dash placeholder
        self.assertIn("📋 —", body)
