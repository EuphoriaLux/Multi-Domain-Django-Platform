"""What happens after Submit: one story, one verdict (UX Wave 2 · WP4).

- The legacy screening-call / meet-coach steps only exist for a live
  (Premium coach-path) ProfileSubmission; everyone else lands on the
  get-verified page.
- profile_submitted leads with one status line, then the verification paths,
  then the Premium teaser.
- A rejected submission shows one verdict page with a self-serve delete path.
- The signup promise names LuxID only when the LuxID button is rendered.

Spec: ai-memory-hub/reviews (Crush.lu UX review Wave 2, findings 3-02, 3-16, 2-06)
"""

import re
from datetime import date

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    PremiumMembership,
    ProfileSubmission,
)
from crush_lu.models.profiles import UserDataConsent

User = get_user_model()

HOST = "crush.lu"


def _add_luxid_app():
    Site.objects.get_or_create(domain=HOST, defaults={"name": "Crush.lu"})
    app = SocialApp.objects.create(
        provider="luxid", name="LuxID", client_id="test", secret="test"
    )
    # Which Site is "current" differs by runner (pytest forces SITE_ID=1), so
    # bind every Site, as test_profile_submitted_luxid_state.py does.
    app.sites.set(Site.objects.all())
    return app


class _MemberMixin:
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="after-submit@example.com",
            email="after-submit@example.com",
            password="pass-pass-pass",
            first_name="Alex",
        )
        EmailAddress.objects.create(
            user=self.user, email=self.user.email, verified=True, primary=True
        )
        UserDataConsent.objects.update_or_create(
            user=self.user, defaults={"crushlu_consent_given": True}
        )
        now = timezone.now()
        self.profile = CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=now,
            phone_verified=True,
            phone_number="+352621009911",
            coach_intro_seen_at=now,
            completion_status="submitted",
            verification_status="pending",
            date_of_birth=date(1994, 4, 4),
            gender="F",
            location="Luxembourg",
            is_active=True,
        )
        self.client.force_login(self.user)

    def _get(self, path):
        return self.client.get(path, HTTP_HOST=HOST)

    def _make_coach(self):
        coach_user = User.objects.create_user(
            username="coach-nora@example.com",
            email="coach-nora@example.com",
            password="pass-pass-pass",
            first_name="Nora",
        )
        return CrushCoach.objects.create(user=coach_user, bio="Coach bio")


class LegacyStepRedirectTests(_MemberMixin, TestCase):
    """3-02: stale onboarding steps only render for a live coach-path review."""

    def test_screening_call_without_submission_redirects_to_get_verified(self):
        response = self._get("/en/onboarding/screening-call/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/profile-submitted/", response["Location"])

    def test_screening_call_with_expired_submission_redirects(self):
        ProfileSubmission.objects.create(profile=self.profile, status="expired")
        response = self._get("/en/onboarding/screening-call/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/profile-submitted/", response["Location"])

    def test_screening_call_with_premium_submission_renders(self):
        ProfileSubmission.objects.create(
            profile=self.profile, coach=self._make_coach(), status="pending"
        )
        response = self._get("/en/onboarding/screening-call/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/onboarding/screening_call.html")

    def test_meet_coach_without_submission_redirects_to_get_verified(self):
        response = self._get("/en/onboarding/meet-coach/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/profile-submitted/", response["Location"])

    def test_meet_coach_with_premium_submission_renders(self):
        ProfileSubmission.objects.create(
            profile=self.profile,
            coach=self._make_coach(),
            status="pending",
            assigned_at=timezone.now(),
        )
        response = self._get("/en/onboarding/meet-coach/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nora")


class ProfileSubmittedOrderTests(_MemberMixin, TestCase):
    """3-02: one status line, then the verification paths, then Premium."""

    def test_status_line_leads_then_paths_then_premium_teaser(self):
        response = self._get("/en/profile-submitted/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        status = html.index("data-status-line")
        event_path = html.index('data-verification-option="event"')
        teaser = html.index("Discover Premium")
        edit = html.index("Edit Profile Details")
        self.assertLess(status, event_path)
        self.assertLess(event_path, teaser)
        # The edit link no longer sits between the status and the paths.
        self.assertGreater(edit, event_path)

        self.assertContains(
            response, "Your profile is ready. Next: get verified at an event."
        )
        self.assertNotContains(
            response, "One last step to activate your Crush.lu profile"
        )
        self.assertContains(
            response, "A hand-picked match every week — first month free"
        )
        self.assertNotContains(response, "4 weeks, a new hand-picked match")

    def test_status_line_names_luxid_only_when_configured(self):
        _add_luxid_app()
        response = self._get("/en/profile-submitted/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Your profile is ready. Next: get verified at an event or with LuxID.",
        )
        self.assertContains(response, 'data-verification-option="luxid"')

    def test_status_line_follows_locked_premium_path(self):
        # A pending PremiumMembership without a live submission locks the path:
        # the partial hides the event/LuxID cards, so the status line must not
        # send the member there either (Codex #1047).
        _add_luxid_app()
        PremiumMembership.objects.create(
            user=self.user, coach=self._make_coach(), status="pending"
        )
        response = self._get("/en/profile-submitted/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "re on the Premium path")
        self.assertContains(
            response,
            "Your profile is ready. Next: complete your Premium membership.",
        )
        self.assertNotContains(response, "Next: get verified at an event")
        self.assertNotContains(response, 'data-verification-option="event"')

    def test_status_line_without_premium_keeps_verification_paths(self):
        _add_luxid_app()
        PremiumMembership.objects.create(
            user=self.user, coach=self._make_coach(), status="active"
        )
        response = self._get("/en/profile-submitted/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Your profile is ready. Next: get verified at an event or with LuxID.",
        )
        self.assertNotContains(response, "complete your Premium membership")


class RejectedVerdictTests(_MemberMixin, TestCase):
    """3-16: a rejection is one consistent verdict with a delete path."""

    def setUp(self):
        super().setUp()
        self.profile.verification_status = "rejected"
        self.profile.save(update_fields=["verification_status"])
        ProfileSubmission.objects.create(profile=self.profile, status="rejected")

    def test_profile_submitted_redirects_rejected_to_verdict_page(self):
        response = self._get("/en/profile-submitted/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/profile/rejected/", response["Location"])

    def test_rejected_page_offers_self_serve_deletion(self):
        response = self._get("/en/profile/rejected/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Delete my profile and data")
        match = re.search(
            r"<a [^>]*data-rejected-delete[^>]*>", response.content.decode()
        )
        self.assertIsNotNone(match)
        anchor = match.group(0)
        self.assertIn('href="/en/account/delete-profile/"', anchor)
        self.assertIn("btn-crush-outline", anchor)
        self.assertNotContains(response, "Profile Needs Updates")

    def test_screening_call_with_rejected_submission_shows_verdict(self):
        response = self._get("/en/onboarding/screening-call/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/profile/rejected/", response["Location"])

    def test_rejected_page_says_what_is_kept(self):
        response = self._get("/en/profile/rejected/")
        self.assertContains(
            response, "Your PowerUp account remains active for other platforms."
        )

    def test_delete_flow_is_reachable_for_a_rejected_member(self):
        response = self._get("/en/account/delete-profile/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "crush_lu/delete_crushlu_profile_confirm.html"
        )


class RejectedProfileWithoutSubmissionTests(_MemberMixin, TestCase):
    """Codex #1047: a door rejection of a free-path member flips only the
    profile status (no ProfileSubmission) and must still reach the verdict."""

    def setUp(self):
        super().setUp()
        self.profile.verification_status = "rejected"
        self.profile.save(update_fields=["verification_status"])
        self.assertFalse(
            ProfileSubmission.objects.filter(profile=self.profile).exists()
        )

    def test_profile_submitted_redirects_to_verdict_page(self):
        response = self._get("/en/profile-submitted/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/profile/rejected/", response["Location"])

    def test_rejected_page_renders_with_delete_path(self):
        response = self._get("/en/profile/rejected/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/profile_rejected.html")
        match = re.search(
            r"<a [^>]*data-rejected-delete[^>]*>", response.content.decode()
        )
        self.assertIsNotNone(match)
        self.assertIn('href="/en/account/delete-profile/"', match.group(0))

    def test_pending_member_without_rejection_is_sent_away(self):
        self.profile.verification_status = "pending"
        self.profile.save(update_fields=["verification_status"])
        response = self._get("/en/profile/rejected/")
        self.assertEqual(response.status_code, 302)


class SignupLuxidPromiseTests(TestCase):
    """2-06: the signup promise matches the buttons actually rendered."""

    LUXID_PROMISE = "Get verified instantly with LuxID"
    FALLBACK = "≈ 15 min to set up · Get verified at your first event"

    def setUp(self):
        cache.clear()

    def test_no_luxid_promise_without_luxid_provider(self):
        response = self.client.get("/en/signup/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.LUXID_PROMISE)
        self.assertContains(response, self.FALLBACK)

    def test_luxid_promise_and_button_with_luxid_provider(self):
        _add_luxid_app()
        response = self.client.get("/en/signup/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.LUXID_PROMISE)
        self.assertNotContains(response, self.FALLBACK)
        html = response.content.decode()
        self.assertIn("luxid-btn", html)
        # The third-party gradient lives in .luxid-btn, not inline.
        self.assertNotIn("#8B5CF6", html)
        self.assertNotIn("width: 20px; height: 20px", html)


class LoginLuxidButtonTests(TestCase):
    """2-06: the login page's LuxID button shares .luxid-btn, no utility gradient."""

    def setUp(self):
        cache.clear()

    def test_login_luxid_button_uses_shared_class_only(self):
        _add_luxid_app()
        response = self.client.get("/accounts/login/", HTTP_HOST=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/login_crush.html")
        match = re.search(
            r'<a [^>]*data-provider="LuxID"[^>]*>', response.content.decode()
        )
        self.assertIsNotNone(match)
        anchor = match.group(0)
        self.assertIn("luxid-btn", anchor)
        # Utility gradients would be overridden by (and fight) .luxid-btn.
        self.assertNotIn("bg-gradient-to-r", anchor)
        self.assertNotIn("hover:from-", anchor)
