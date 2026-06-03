"""
Tests for the verification_method field stamping.

The LuxID path is stamped in ``crush_lu.signals._execute_luxid_direct_verify``;
the coach/premium paths are covered in test_coach_mark_verified.py.
"""

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings

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
class VerificationMethodTests(SiteTestMixin, TestCase):
    def setUp(self):
        from crush_lu.models import CrushProfile

        self.user = User.objects.create_user(
            username="lux@example.com", email="lux@example.com", password="pass12345"
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            gender="M",
            location="Luxembourg",
            verification_status="pending",
        )

    def test_luxid_direct_verify_stamps_method(self):
        from crush_lu.signals import _execute_luxid_direct_verify

        _execute_luxid_direct_verify(
            self.user, self.profile, submission=None, request=None
        )

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "verified")
        self.assertEqual(self.profile.verification_method, "luxid")
        self.assertTrue(self.profile.is_approved)

    def test_save_defaults_method_to_admin_on_verified(self):
        """A profile that reaches verified without a method gets 'admin' so the
        UI always has a badge to show (legacy is_approved-only writers)."""
        self.profile.is_approved = True
        self.profile.save()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "verified")
        self.assertEqual(self.profile.verification_method, "admin")

    def test_save_does_not_clobber_existing_method(self):
        self.profile.verification_method = "luxid"
        self.profile.is_approved = True
        self.profile.save()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_method, "luxid")

    def test_bulk_approve_stamps_admin(self):
        from crush_lu.admin.profiles import ProfileSubmissionAdmin
        from crush_lu.models import ProfileSubmission
        from django.contrib.admin.sites import AdminSite
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        submission = ProfileSubmission.objects.create(
            profile=self.profile, status="pending", review_call_completed=True
        )
        admin = ProfileSubmissionAdmin(ProfileSubmission, AdminSite())
        request = RequestFactory().post("/admin/")
        # messages framework needs a session/storage; attach a no-op.
        setattr(request, "session", {})
        setattr(request, "_messages", FallbackStorage(request))

        admin.bulk_approve_profiles(
            request, ProfileSubmission.objects.filter(pk=submission.pk)
        )

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "verified")
        self.assertEqual(self.profile.verification_method, "admin")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class VerificationJourneyRenderTests(SiteTestMixin, TestCase):
    """The pending journey strip speaks to the path the user actually chose."""

    def _render(self, profile, **ctx):
        from django.template.loader import render_to_string

        return render_to_string(
            "crush_lu/partials/_verification_journey.html",
            {"profile": profile, **ctx},
        )

    def setUp(self):
        from crush_lu.models import CrushProfile

        self.user = User.objects.create_user(
            username="j@example.com", email="j@example.com", password="pass12345"
        )
        self.profile = CrushProfile.objects.create(
            user=self.user, gender="M", verification_status="pending"
        )

    def test_generic_pending_mentions_all_three_paths(self):
        html = self._render(self.profile, chosen_path="", premium_pending=None)
        self.assertIn("Verify your identity", html)
        self.assertIn("premium coach", html)

    def test_event_path_pending(self):
        html = self._render(self.profile, chosen_path="event", premium_pending=None)
        self.assertIn("verified at your next event", html)

    def test_premium_path_pending(self):
        from crush_lu.models import CrushCoach, PremiumMembership

        coach_user = User.objects.create_user(
            username="coach@example.com", email="coach@example.com", first_name="Robin"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        membership = PremiumMembership.objects.create(
            user=self.user, coach=coach, status="pending"
        )
        html = self._render(
            self.profile, chosen_path="premium", premium_pending=membership
        )
        self.assertIn("payment pending", html.lower())
        self.assertIn("Robin", html)

    def test_verified_without_method_still_shows_a_badge(self):
        self.profile.verification_status = "verified"
        self.profile.verification_method = ""
        html = self._render(self.profile)
        self.assertIn("Verified by Crush.lu", html)
