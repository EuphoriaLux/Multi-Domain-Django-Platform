"""Race coverage for the coach-review profile verification path."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.forms import ProfileReviewForm
from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class CoachReviewAtomicVerificationTests(TestCase):
    def setUp(self):
        self.coach_user = User.objects.create_user(
            username="atomic-coach@example.com",
            email="atomic-coach@example.com",
            password="pw12345678",
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            bio="Atomic coach",
            is_active=True,
        )
        self.member = User.objects.create_user(
            username="atomic-member@example.com",
            email="atomic-member@example.com",
            password="pw12345678",
        )
        UserDataConsent.objects.filter(user=self.member).update(
            crushlu_consent_given=True
        )
        self.profile = CrushProfile.objects.create(
            user=self.member,
            date_of_birth=date(1990, 1, 1),
            gender="F",
            location="Luxembourg",
            verification_status="pending",
            is_approved=False,
        )
        self.submission = ProfileSubmission.objects.create(
            profile=self.profile,
            coach=self.coach,
            status="pending",
            review_call_completed=True,
        )
        self.client.force_login(self.coach_user)

    def _approve(self):
        return self.client.post(
            reverse("crush_lu:coach_review_profile", args=[self.submission.pk]),
            data={
                "status": "approved",
                "coach_notes": "Identity reviewed.",
                "feedback_to_user": "Welcome!",
            },
        )

    @patch("crush_lu.views_coach.notify_profile_approved")
    @patch("crush_lu.views_coach.check_and_apply_profile_approved_reward")
    def test_winning_coach_claim_stamps_admin_and_runs_side_effects_once(
        self, reward, notify
    ):
        notify.return_value = SimpleNamespace(any_delivered=False)

        response = self._approve()

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertTrue(self.profile.is_approved)
        self.assertEqual(self.profile.verification_status, "verified")
        self.assertEqual(self.profile.verification_method, "admin")
        self.assertEqual(self.submission.status, "approved")
        reward.assert_called_once()
        notify.assert_called_once()

    @patch("crush_lu.views_coach.notify_profile_approved")
    @patch("crush_lu.views_coach.check_and_apply_profile_approved_reward")
    def test_door_claim_between_form_fetch_and_save_is_never_overwritten(
        self, reward, notify
    ):
        """A door scan that commits mid-review owns method and side effects."""
        original_save = ProfileReviewForm.save

        def save_after_door_claim(form, commit=True):
            submission = original_save(form, commit=commit)
            CrushProfile.objects.filter(
                pk=self.profile.pk,
                verification_status="pending",
            ).update(
                is_approved=True,
                approved_at=timezone.now(),
                verification_status="verified",
                verification_method="coach_event",
            )
            return submission

        with patch.object(ProfileReviewForm, "save", new=save_after_door_claim):
            response = self._approve()

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertTrue(self.profile.is_approved)
        self.assertEqual(self.profile.verification_status, "verified")
        self.assertEqual(self.profile.verification_method, "coach_event")
        self.assertEqual(self.submission.status, "approved")
        reward.assert_not_called()
        notify.assert_not_called()
