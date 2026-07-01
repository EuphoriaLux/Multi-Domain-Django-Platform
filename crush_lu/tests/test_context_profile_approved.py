"""
Regression tests for the navbar profile-completion indicator.

A LuxID-verified member never goes through paid coach review, so they have
NO ProfileSubmission row. The context processor used to expose
`profile_is_approved` only inside the `if profile_submission:` branch, which
left verified-but-unsubmitted users falling through to the navbar's
"PROFILE INCOMPLETE" branch — showing a stale "Complete Profile 3/4" badge
even though the dashboard already said "You're verified".

`crush_user_context` must expose `profile_is_approved` (the legacy
`profile.is_approved` boolean, which every verification path sets to True)
for every approved profile regardless of whether a submission exists. It
mirrors the same boolean the downstream Edit-Profile and Connect gates
enforce, so the navbar never advertises links those views would 403.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from crush_lu.context_processors import crush_user_context
from crush_lu.models import CrushProfile
from crush_lu.models.profiles import ProfileSubmission


class ProfileApprovedContextTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="luxid@example.com",
            email="luxid@example.com",
            password="pass123",
            first_name="Lux",
        )

    def _context(self):
        request = self.factory.get("/en/dashboard/")
        request.user = self.user
        return crush_user_context(request)

    def test_luxid_verified_without_submission_is_approved(self):
        """The reported bug: verified via LuxID, no ProfileSubmission."""
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1993, 3, 15),
            gender="M",
            location="Luxembourg City",
            verification_status="verified",
            verification_method="luxid",
            is_approved=True,
            is_active=True,
        )

        context = self._context()

        self.assertTrue(context["profile_is_approved"])
        # verified maps to step 3, but the navbar must NOT render the
        # "Complete Profile" progress branch for an approved user.
        self.assertNotIn("profile_submission", context)

    def test_verified_but_legacy_flag_false_stays_unapproved_in_navbar(self):
        """The navbar must mirror the legacy `is_approved` boolean that the
        downstream gates still enforce (_render_edit_profile_form, the Connect
        entry points). A verified-but-is_approved=False profile — which no real
        verification path produces, since all set both fields together — must
        NOT get approved-looking navigation, or its Edit/Connect links would
        400/403 when followed."""
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1993, 3, 15),
            gender="M",
            location="Luxembourg City",
            verification_status="verified",
            verification_method="luxid",
            is_approved=False,  # legacy flag intentionally out of sync
            is_active=True,
        )

        self.assertFalse(self._context()["profile_is_approved"])

    def test_incomplete_profile_is_not_approved(self):
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1993, 3, 15),
            gender="M",
            location="Luxembourg City",
            verification_status="incomplete",
            is_active=True,
        )

        context = self._context()
        self.assertFalse(context["profile_is_approved"])
        self.assertEqual(context["profile_completion_step"], 1)

    def test_pending_profile_is_not_approved(self):
        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1993, 3, 15),
            gender="M",
            location="Luxembourg City",
            verification_status="pending",
            is_active=True,
        )
        ProfileSubmission.objects.create(profile=profile, status="pending")

        context = self._context()
        self.assertFalse(context["profile_is_approved"])
        self.assertEqual(context["profile_status"], "pending")

    def test_coach_approved_with_submission_still_approved(self):
        """The pre-existing coach-review path keeps working."""
        profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1993, 3, 15),
            gender="M",
            location="Luxembourg City",
            verification_status="verified",
            verification_method="coach_event",
            is_approved=True,
            is_active=True,
        )
        ProfileSubmission.objects.create(profile=profile, status="approved")

        context = self._context()
        self.assertTrue(context["profile_is_approved"])
        self.assertEqual(context["profile_status"], "approved")
