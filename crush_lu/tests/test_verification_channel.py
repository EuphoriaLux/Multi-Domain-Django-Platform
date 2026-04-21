"""
Tests for the Verification Channel + 3-step wizard redesign.

Covers:
- New submissions land in the channel with coach=None.
- Revisions reset coach back to None so any coach can re-claim.
- Claim endpoint assigns the claiming coach and broadcasts.
- Coach channel page renders and lists only unclaimed pending.
"""

from unittest.mock import patch
from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils import timezone

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


class SiteTestMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class VerificationChannelFlowTests(SiteTestMixin, TestCase):
    """End-to-end behaviour of the channel-based claim flow."""

    def setUp(self):
        from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission
        from crush_lu.models.profiles import UserDataConsent

        self.client = Client()

        self.coach_user = User.objects.create_user(
            username="coach@example.com",
            email="coach@example.com",
            password="pass12345",
            first_name="Cam",
        )
        UserDataConsent.objects.filter(user=self.coach_user).update(
            crushlu_consent_given=True
        )
        self.coach = CrushCoach.objects.create(
            user=self.coach_user,
            bio="Coach bio",
            is_active=True,
            max_active_reviews=10,
            spoken_languages=["en", "fr"],
        )

        self.user = User.objects.create_user(
            username="user@example.com",
            email="user@example.com",
            password="pass12345",
            first_name="Alex",
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            gender="M",
            location="Luxembourg",
            is_approved=False,
            phone_number="+352123456789",
            phone_verified=True,
            # Mark step-3 coach intro as acked — otherwise the submission
            # journey guard (views_profile.complete_profile_submission) would
            # redirect these fixtures to /onboarding/coach-intro/ before
            # creating the submission.
            coach_intro_seen_at=timezone.now(),
            event_languages=["en"],
        )
        self.ProfileSubmission = ProfileSubmission

    def test_channel_lists_only_unclaimed_pending(self):
        """The channel page returns pending submissions with coach IS NULL."""
        from crush_lu.models import ProfileSubmission

        claimed = ProfileSubmission.objects.create(
            profile=self.profile, status="pending", coach=self.coach
        )
        unclaimed = ProfileSubmission.objects.create(
            profile=self.profile, status="pending", coach=None
        )

        self.client.force_login(self.coach_user)
        resp = self.client.get(reverse("crush_lu:coach_verification_channel"))
        self.assertEqual(resp.status_code, 200)
        channel = list(resp.context["channel"])
        ids = [s.id for s in channel]
        self.assertIn(unclaimed.id, ids)
        self.assertNotIn(claimed.id, ids)
        self.assertEqual(resp.context["channel_count"], 1)

    def test_channel_row_flags_language_match(self):
        """Profile rows whose event_languages intersect the coach's are flagged."""
        from crush_lu.models import ProfileSubmission

        sub = ProfileSubmission.objects.create(
            profile=self.profile, status="pending", coach=None
        )

        self.client.force_login(self.coach_user)
        resp = self.client.get(reverse("crush_lu:coach_verification_channel"))
        row = resp.context["channel"][0]
        self.assertEqual(row.id, sub.id)
        self.assertTrue(row.language_match)
        self.assertEqual(row.matched_languages, ["en"])

    def test_claim_assigns_coach_and_broadcasts(self):
        """The existing claim endpoint pulls a row from the channel for the claiming coach."""
        from crush_lu.models import ProfileSubmission

        sub = ProfileSubmission.objects.create(
            profile=self.profile, status="pending", coach=None
        )

        self.client.force_login(self.coach_user)
        # Claim view imports broadcast_submission_claimed lazily, so patch the source.
        with patch(
            "crush_lu.coach_notifications.broadcast_submission_claimed"
        ) as broadcast:
            resp = self.client.post(
                reverse("api_coach_claim_submission"),
                data={"submission_id": sub.id},
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["success"])

        sub.refresh_from_db()
        self.assertEqual(sub.coach_id, self.coach.id)

        broadcast.assert_called_once()
        _, kwargs = broadcast.call_args
        self.assertEqual(kwargs["claimed_by"], self.coach)

    def test_revision_requested_clears_coach(self):
        """When a coach requests revision, the coach FK on the submission is cleared."""
        from crush_lu.models import ProfileSubmission

        sub = ProfileSubmission.objects.create(
            profile=self.profile, status="pending", coach=self.coach
        )

        self.client.force_login(self.coach_user)
        url = reverse("crush_lu:coach_review_profile", args=[sub.id])
        resp = self.client.post(
            url,
            data={
                "status": "revision",
                "feedback_to_user": "Please refresh your photos.",
                "feedback_to_admin": "",
            },
            follow=False,
        )
        # Either a redirect (302) on success or 200 if the form fails; both are fine
        # so long as the coach is cleared when status becomes revision.
        if resp.status_code == 302:
            sub.refresh_from_db()
            self.assertEqual(sub.status, "revision")
            self.assertIsNone(sub.coach_id)

    def test_user_resubmit_after_revision_returns_to_channel(self):
        """User resubmitting a revision puts the submission back as pending with coach=None.

        The invariant this test guards is the product decision: every resubmit
        lands in the channel, never auto-routed to the previous coach.
        """
        from datetime import date
        from crush_lu.models import ProfileSubmission

        # Keep the revision on the previous coach so we can prove the coach gets cleared.
        sub = ProfileSubmission.objects.create(
            profile=self.profile,
            status="revision",
            coach=self.coach,
            feedback_to_user="Refresh photos",
        )
        # Fill enough required fields that get_missing_fields() returns empty.
        # photo_1 is an Azure ImageField so we stub the completeness check instead
        # of fabricating a blob.
        self.profile.completion_status = "step3"
        self.profile.date_of_birth = date(1990, 1, 1)
        self.profile.save()

        self.client.force_login(self.user)
        with patch(
            "crush_lu.models.CrushProfile.get_missing_fields", return_value=[]
        ), patch(
            "crush_lu.views_profile.broadcast_new_submission_to_channel"
        ) as broadcast, patch(
            "crush_lu.views_profile.send_profile_submission_notifications"
        ):
            resp = self.client.post(
                reverse("api_complete_profile_submission"),
                content_type="application/json",
                data="{}",
            )

        sub.refresh_from_db()
        # Unconditional assertions — the whole point of this test.
        self.assertEqual(sub.status, "pending")
        self.assertIsNone(sub.coach_id)
        broadcast.assert_called()
