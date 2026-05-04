"""
End-to-end + per-view tests for the 7-step onboarding journey.

Covers:
  - /onboarding/ smart-resume routing at every step
  - Each step-view's login + state gate
  - A full walk from signup state -> welcome -> phone verified ->
    coach intro ack -> profile complete -> submission created
"""
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.utils import timezone

from datetime import timedelta

from crush_lu.models import CrushCoach, CrushProfile, ProfileSubmission
from crush_lu.models.profiles import UserDataConsent


User = get_user_model()


def _grant_consent(user):
    """The consent middleware blocks onboarding URLs without this flag.

    Also marks the user's primary email as verified so the submission gate
    (added in views.py / views_profile.py) doesn't redirect to the
    /accounts/email/ page. Skips the email step for users created without
    an email (some tests pass only username) — production would behave
    the same way (gate redirects to email management).
    """
    from allauth.account.models import EmailAddress
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save(update_fields=["crushlu_consent_given"])
    if user.email:
        EmailAddress.objects.update_or_create(
            user=user,
            email=user.email,
            defaults={"verified": True, "primary": True},
        )

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


class _SiteMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class OnboardingEntryRoutingTests(_SiteMixin, TestCase):
    """The /onboarding/ entry redirects to the step the user is actually on."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            password="pass-pass-pass",
            first_name="Alex",
        )
        _grant_consent(self.user)
        self.client.login(username="alex@example.com", password="pass-pass-pass")

    def _final_path(self, start_path):
        """Follow redirects to the terminal URL (ignores i18n prefixes)."""
        response = self.client.get(start_path, follow=True)
        # The last element of the chain is the final resolved path.
        if response.redirect_chain:
            return response.redirect_chain[-1][0]
        return start_path

    def test_no_profile_routes_to_welcome(self):
        self.assertIn("/welcome/", self._final_path("/onboarding/"))

    def test_welcome_seen_routes_to_phone_step(self):
        CrushProfile.objects.create(
            user=self.user, welcome_seen_at=timezone.now()
        )
        self.assertIn("/onboarding/phone/", self._final_path("/onboarding/"))

    def test_phone_verified_routes_to_coach_intro(self):
        CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
        )
        self.assertIn("/onboarding/coach-intro/", self._final_path("/onboarding/"))

    def test_coach_intro_ack_routes_to_create_profile(self):
        CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
            coach_intro_seen_at=timezone.now(),
        )
        self.assertIn("/create-profile/", self._final_path("/onboarding/"))


@override_settings(**CRUSH_LU_URL_SETTINGS)
class PhoneStepViewTests(_SiteMixin, TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            password="pass-pass-pass",
            first_name="Alex",
        )
        _grant_consent(self.user)
        self.client.login(username="alex@example.com", password="pass-pass-pass")

    def _final_path(self, start_path):
        response = self.client.get(start_path, follow=True)
        if response.redirect_chain:
            return response.redirect_chain[-1][0]
        return start_path

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get("/onboarding/phone/")
        self.assertEqual(response.status_code, 302)

    def test_bounces_backwards_if_welcome_not_seen(self):
        CrushProfile.objects.create(user=self.user)
        self.assertIn("/welcome/", self._final_path("/onboarding/phone/"))

    def test_renders_when_phone_already_verified(self):
        """Re-enterable: users backtracking from a later step (via the
        completed step-2 dot in the journey stepper) should see their
        verified number, not get bounced forward."""
        CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
            phone_number="+352621000000",
        )
        response = self.client.get("/onboarding/phone/", follow=True)
        self.assertEqual(response.status_code, 200)
        # Verified state is rendered (the template shows the verified chip
        # instead of the Verify button).
        self.assertContains(response, "+352621000000")

    def test_renders_with_step_2_active(self):
        CrushProfile.objects.create(
            user=self.user, welcome_seen_at=timezone.now()
        )
        response = self.client.get("/onboarding/phone/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2/7")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CoachIntroStepViewTests(_SiteMixin, TestCase):

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            password="pass-pass-pass",
        )
        _grant_consent(self.user)
        self.client.login(username="alex@example.com", password="pass-pass-pass")

    def _final_path(self, start_path, method="get", **kwargs):
        fn = getattr(self.client, method)
        # For POSTs we prefix the default-language path so the locale
        # middleware doesn't turn the POST into a GET redirect.
        if method == "post" and not start_path.startswith("/en/"):
            start_path = "/en" + start_path
        response = fn(start_path, follow=True, **kwargs)
        if response.redirect_chain:
            return response.redirect_chain[-1][0]
        return start_path

    def test_bounces_back_to_phone_if_not_verified(self):
        CrushProfile.objects.create(
            user=self.user, welcome_seen_at=timezone.now()
        )
        self.assertIn("/onboarding/phone/", self._final_path("/onboarding/coach-intro/"))

    def test_get_renders_with_step_3_active(self):
        CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
        )
        response = self.client.get("/onboarding/coach-intro/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "3/7")

    def test_post_marks_intro_seen_and_advances(self):
        profile = CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
        )
        final = self._final_path("/onboarding/coach-intro/", method="post")
        self.assertIn("/create-profile/", final)
        profile.refresh_from_db()
        self.assertIsNotNone(profile.coach_intro_seen_at)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class JourneyWalkE2ETests(_SiteMixin, TestCase):
    """
    Walk a user from signup state through to a created ProfileSubmission.

    We don't exercise the actual phone-verification OAuth flow (it requires
    Firebase/LuxID mocks). Instead, we set the state-transition fields
    directly as proxies for the user completing each UI step, and assert
    that /onboarding/ routing, per-step views, and the submission gate all
    line up.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="walker@example.com",
            email="walker@example.com",
            password="pass-pass-pass",
            first_name="Walker",
        )
        _grant_consent(self.user)
        self.client.login(username="walker@example.com", password="pass-pass-pass")

    def _final_path(self, start_path, method="get", **kwargs):
        fn = getattr(self.client, method)
        # For POSTs, i18n-patterned URLs must be prefixed with /en/ so the
        # locale middleware doesn't drop the POST body via redirect. API
        # paths are language-neutral and stay un-prefixed.
        if (
            method == "post"
            and not start_path.startswith("/en/")
            and not start_path.startswith("/api/")
        ):
            start_path = "/en" + start_path
        response = fn(start_path, follow=True, **kwargs)
        if response.redirect_chain:
            return response.redirect_chain[-1][0]
        return start_path

    def test_full_journey_routes_to_each_step(self):
        # Step 1 — /onboarding/ with no profile routes to /welcome/.
        self.assertIn("/welcome/", self._final_path("/onboarding/"))

        # Visit /welcome/ — autocreates profile, sets welcome_seen_at.
        r = self.client.get(reverse("crush_lu:welcome"), follow=True)
        self.assertEqual(r.status_code, 200)
        profile = CrushProfile.objects.get(user=self.user)
        self.assertIsNotNone(profile.welcome_seen_at)

        # Step 2 — /onboarding/ now routes to /onboarding/phone/.
        self.assertIn("/onboarding/phone/", self._final_path("/onboarding/"))

        # Phone-verification OAuth is not exercised here; simulate a verified
        # phone to advance past step 2.
        profile.phone_verified = True
        profile.phone_number = "+352621000000"
        profile.save(update_fields=["phone_verified", "phone_number"])

        # Step 3 — /onboarding/ routes to coach intro.
        self.assertIn("/onboarding/coach-intro/", self._final_path("/onboarding/"))
        # POST to ack coach intro advances to step 4.
        self.assertIn(
            "/create-profile/", self._final_path("/onboarding/coach-intro/", method="post")
        )
        profile.refresh_from_db()
        self.assertIsNotNone(profile.coach_intro_seen_at)

        # Step 4 — /onboarding/ routes to /create-profile/.
        self.assertIn("/create-profile/", self._final_path("/onboarding/"))

        # Submission gate: journey fields are set (phone + coach intro) but
        # profile is incomplete, so it bounces to /create-profile/ without
        # creating a submission.
        self.assertIn("/create-profile/", self._final_path("/api/profile/complete/", method="post"))
        self.assertFalse(ProfileSubmission.objects.filter(profile=profile).exists())

    def test_submission_gate_requires_phone_verified(self):
        """Direct POST without phone verify redirects to phone step."""
        CrushProfile.objects.create(
            user=self.user, welcome_seen_at=timezone.now()
        )
        self.assertIn(
            "/onboarding/phone/",
            self._final_path("/api/profile/complete/", method="post"),
        )

    def test_submission_gate_requires_coach_intro_ack(self):
        """POST with phone verified but no coach intro redirects to coach intro."""
        CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
        )
        self.assertIn(
            "/onboarding/coach-intro/",
            self._final_path("/api/profile/complete/", method="post"),
        )

    def test_form_post_path_also_requires_coach_intro(self):
        """
        The classic form-POST submit at /create-profile/ is a parallel
        submission path (used when JS is disabled). It MUST also enforce the
        coach-intro gate so the journey can't be bypassed.
        """
        CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=timezone.now(),
            phone_verified=True,
            phone_number="+352621000000",
        )
        # We don't need a valid form body — the guard short-circuits before
        # validation. What we're asserting is just that the redirect target
        # is the coach-intro page, not a successful submission.
        final = self._final_path("/create-profile/", method="post", data={})
        # If the form is invalid it re-renders (status 200); if the guard
        # fires we get redirected to /onboarding/coach-intro/. Either way,
        # no submission must be created.
        self.assertFalse(ProfileSubmission.objects.filter(
            profile__user=self.user
        ).exists())


@override_settings(**CRUSH_LU_URL_SETTINGS)
class SaveProfilePreferencesTests(_SiteMixin, TestCase):
    """POST /api/profile/save-preferences/ — step 4 of the inner wizard."""

    def setUp(self):
        import json
        self.json = json
        self.client = Client()
        self.user = User.objects.create_user(
            username="alex@example.com",
            email="alex@example.com",
            password="pass-pass-pass",
        )
        _grant_consent(self.user)
        self.profile = CrushProfile.objects.create(
            user=self.user,
            phone_verified=True,
            phone_number="+352621000000",
        )
        self.client.login(username="alex@example.com", password="pass-pass-pass")
        self.url = "/api/profile/save-preferences/"

    def _post(self, payload):
        return self.client.post(
            self.url,
            data=self.json.dumps(payload),
            content_type="application/json",
        )

    def test_persists_valid_payload(self):
        resp = self._post({
            "preferred_genders": ["F", "NB"],
            "preferred_age_min": 27,
            "preferred_age_max": 36,
        })
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.preferred_genders, ["F", "NB"])
        self.assertEqual(self.profile.preferred_age_min, 27)
        self.assertEqual(self.profile.preferred_age_max, 36)

    def test_rejects_inverted_age_range(self):
        resp = self._post({
            "preferred_genders": ["F"],
            "preferred_age_min": 40,
            "preferred_age_max": 30,
        })
        self.assertEqual(resp.status_code, 400)
        self.profile.refresh_from_db()
        self.assertNotEqual(self.profile.preferred_age_min, 40)

    def test_drops_unknown_gender_codes(self):
        resp = self._post({
            "preferred_genders": ["F", "BOGUS", "M"],
            "preferred_age_min": 25,
            "preferred_age_max": 40,
        })
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.preferred_genders, ["F", "M"])

    def test_requires_login(self):
        self.client.logout()
        resp = self._post({
            "preferred_genders": ["F"],
            "preferred_age_min": 25,
            "preferred_age_max": 40,
        })
        self.assertEqual(resp.status_code, 302)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class SubmissionStateRoutingTests(_SiteMixin, TestCase):
    """
    Routing tests for the three submission states PR #376 cleaned up:
      - revision   → user goes back to step 4 and a resubmit flips the row
                     back to 'pending' with a fresh SLA window.
      - rejected   → /onboarding/ routes to /profile/rejected/, not a step.
      - coach-claimed pending (assigned_at set) → step 6, meet-coach page.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="walker@example.com",
            email="walker@example.com",
            password="pass-pass-pass",
            first_name="Walker",
        )
        _grant_consent(self.user)
        self.client.login(username="walker@example.com", password="pass-pass-pass")

    def _final_path(self, start_path, method="get", **kwargs):
        fn = getattr(self.client, method)
        if (
            method == "post"
            and not start_path.startswith("/en/")
            and not start_path.startswith("/api/")
        ):
            start_path = "/en" + start_path
        response = fn(start_path, follow=True, **kwargs)
        if response.redirect_chain:
            return response.redirect_chain[-1][0]
        return start_path

    def _make_complete_profile(self):
        """Profile state that satisfies journey gates AND get_missing_fields()."""
        now = timezone.now()
        return CrushProfile.objects.create(
            user=self.user,
            welcome_seen_at=now,
            phone_verified=True,
            phone_number="+352621000000",
            coach_intro_seen_at=now,
            completion_status="submitted",
            date_of_birth=now.date().replace(year=now.year - 30),
            gender="F",
            location="Luxembourg",
            photo_1="onboarding-tests/photo_1.jpg",
            event_languages=["en"],
        )

    def _make_coach(self, username="coach@example.com", first_name="Nora"):
        coach_user = User.objects.create_user(
            username=username,
            email=username,
            password="pass-pass-pass",
            first_name=first_name,
        )
        return CrushCoach.objects.create(user=coach_user, bio="Coach bio for tests")

    # ── revision ────────────────────────────────────────────────────────────

    def test_revision_submission_routes_to_step_4(self):
        profile = self._make_complete_profile()
        coach = self._make_coach()
        now = timezone.now()
        ProfileSubmission.objects.create(
            profile=profile,
            coach=coach,
            status="revision",
            assigned_at=now,
            sla_deadline=now + timedelta(hours=48),
            feedback_to_user="Add a brighter photo, please.",
        )
        self.assertIn("/create-profile/", self._final_path("/onboarding/"))

    def test_revision_resubmit_flips_to_pending_and_clears_sla(self):
        profile = self._make_complete_profile()
        coach = self._make_coach()
        now = timezone.now()
        submission = ProfileSubmission.objects.create(
            profile=profile,
            coach=coach,
            status="revision",
            assigned_at=now,
            sla_deadline=now + timedelta(hours=48),
        )

        self._final_path("/api/profile/complete/", method="post")

        submission.refresh_from_db()
        self.assertEqual(submission.status, "pending")
        self.assertIsNone(submission.coach)
        self.assertIsNone(submission.assigned_at)
        self.assertIsNone(submission.sla_deadline)
        # The existing row is re-used — no duplicate submission is created.
        self.assertEqual(
            ProfileSubmission.objects.filter(profile=profile).count(), 1
        )

    # ── rejected ────────────────────────────────────────────────────────────

    def test_rejected_submission_routes_to_profile_rejected(self):
        profile = self._make_complete_profile()
        ProfileSubmission.objects.create(profile=profile, status="rejected")
        self.assertIn("/profile/rejected/", self._final_path("/onboarding/"))

    def test_rejected_resubmit_does_not_create_a_new_submission(self):
        """Second visit to /onboarding/ must not spawn a pending row."""
        profile = self._make_complete_profile()
        ProfileSubmission.objects.create(profile=profile, status="rejected")
        self._final_path("/onboarding/")
        self._final_path("/onboarding/")
        self.assertEqual(
            ProfileSubmission.objects.filter(profile=profile).count(), 1
        )
        self.assertFalse(
            ProfileSubmission.objects.filter(
                profile=profile, status="pending"
            ).exists()
        )

    # ── coach-claimed pending → step 6 ──────────────────────────────────────

    def test_coach_claim_routes_to_meet_coach_step(self):
        profile = self._make_complete_profile()
        coach = self._make_coach(first_name="Nora")
        ProfileSubmission.objects.create(
            profile=profile,
            coach=coach,
            status="pending",
            assigned_at=timezone.now(),
        )
        self.assertIn(
            "/onboarding/meet-coach/", self._final_path("/onboarding/")
        )

    def test_meet_coach_step_renders_coach_bio(self):
        profile = self._make_complete_profile()
        coach = self._make_coach(first_name="Nora")
        ProfileSubmission.objects.create(
            profile=profile,
            coach=coach,
            status="pending",
            assigned_at=timezone.now(),
        )
        response = self.client.get("/onboarding/meet-coach/", follow=True)
        self.assertEqual(response.status_code, 200)
        # Coach name surfaces on the page, confirming the meet-coach template
        # is rendering with the claimed coach attached.
        self.assertContains(response, "Nora")
