"""
Tests for the LuxID auto-approval flow.

Covers:
  - auto_approve_profile_on_luxid_connect signal: happy path + each guard
  - Adapter get_connect_redirect_url: pending/session-flag/no-pending branches
  - profile_submitted view: LuxID CTA context injection
  - update_crush_profile_from_luxid: authoritative DOB/gender overwrite
"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sites.models import Site
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.signals import social_account_added

from crush_lu.models import CrushProfile
from crush_lu.models.profiles import ProfileSubmission, UserDataConsent
from crush_lu import signals as crush_signals

User = get_user_model()

CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(host="crush.lu"):
    """RequestFactory GET with session and messages support."""
    factory = RequestFactory()
    request = factory.get("/", HTTP_HOST=host)
    request.session = SessionStore()
    messages_storage = FallbackStorage(request)
    request._messages = messages_storage
    return request


def _make_sociallogin(user, provider="luxid"):
    """Minimal sociallogin-like object that the signal reads."""
    account = MagicMock()
    account.provider = provider
    sl = MagicMock()
    sl.user = user
    sl.account = account
    return sl


def _make_user_with_pending_profile(gender="M", dob=None):
    """Create a user + unapproved CrushProfile + pending ProfileSubmission."""
    user = User.objects.create_user(
        username=f"luxid_{User.objects.count()}@example.com",
        email=f"luxid_{User.objects.count()}@example.com",
        password="pass123",
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=dob or date(1993, 3, 15),
        gender=gender,
        location="Luxembourg City",
        is_approved=False,
        verification_status="pending",  # submitted, awaiting LuxId/paid coach
        is_active=True,
    )
    submission = ProfileSubmission.objects.create(profile=profile, status="pending")
    return user, profile, submission


# ---------------------------------------------------------------------------
# Signal tests
# ---------------------------------------------------------------------------

@override_settings(**CRUSH_LU_URL_SETTINGS)
class TestAutoApproveSignalHappyPath(TestCase):
    """Signal correctly approves a pending profile on genuine LuxID connect."""

    def setUp(self):
        # Guarantee thread-local is unset from any previous test.
        crush_signals._thread_local.is_crush_luxid_login = True

        self.user, self.profile, self.submission = _make_user_with_pending_profile()
        self.request = _make_request(host="crush.lu")

    def tearDown(self):
        crush_signals._thread_local.is_crush_luxid_login = False

    @patch("crush_lu.referrals.check_and_apply_profile_approved_reward")
    @patch("crush_lu.notification_service.notify_profile_approved")
    def test_submission_and_profile_approved(self, mock_notify, mock_reward):
        sl = _make_sociallogin(self.user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )

        self.submission.refresh_from_db()
        self.profile.refresh_from_db()

        self.assertEqual(self.submission.status, "approved")
        self.assertIsNotNone(self.submission.reviewed_at)
        self.assertTrue(self.profile.is_approved)
        self.assertIsNotNone(self.profile.approved_at)

    @patch("crush_lu.referrals.check_and_apply_profile_approved_reward")
    @patch("crush_lu.notification_service.notify_profile_approved")
    def test_review_call_completed_set(self, mock_notify, mock_reward):
        sl = _make_sociallogin(self.user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.review_call_completed)

    @patch("crush_lu.referrals.check_and_apply_profile_approved_reward")
    @patch("crush_lu.notification_service.notify_profile_approved")
    def test_coach_notes_written(self, mock_notify, mock_reward):
        sl = _make_sociallogin(self.user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        self.submission.refresh_from_db()
        self.assertIn("Auto-approved via LuxID identity verification", self.submission.coach_notes)

    @patch("crush_lu.referrals.check_and_apply_profile_approved_reward")
    @patch("crush_lu.notification_service.notify_profile_approved")
    def test_coach_notes_appended_not_overwritten(self, mock_notify, mock_reward):
        self.submission.coach_notes = "Existing coach note"
        self.submission.save(update_fields=["coach_notes"])

        sl = _make_sociallogin(self.user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        self.submission.refresh_from_db()
        self.assertIn("Existing coach note", self.submission.coach_notes)
        self.assertIn("Auto-approved via LuxID identity verification", self.submission.coach_notes)

    @patch("crush_lu.referrals.check_and_apply_profile_approved_reward")
    @patch("crush_lu.notification_service.notify_profile_approved")
    def test_session_flag_set_for_redirect(self, mock_notify, mock_reward):
        sl = _make_sociallogin(self.user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        self.assertTrue(self.request.session.get("luxid_just_auto_approved"))

    @patch("crush_lu.referrals.check_and_apply_profile_approved_reward")
    @patch("crush_lu.notification_service.notify_profile_approved")
    def test_reward_called(self, mock_notify, mock_reward):
        sl = _make_sociallogin(self.user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        mock_reward.assert_called_once_with(self.profile)

    @patch("crush_lu.referrals.check_and_apply_profile_approved_reward")
    @patch("crush_lu.notification_service.notify_profile_approved")
    def test_notification_called(self, mock_notify, mock_reward):
        sl = _make_sociallogin(self.user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        mock_notify.assert_called_once_with(
            user=self.user,
            profile=self.profile,
            coach_notes=None,
            request=self.request,
        )

    @patch("crush_lu.referrals.check_and_apply_profile_approved_reward")
    @patch("crush_lu.notification_service.notify_profile_approved")
    def test_openid_connect_provider_also_triggers(self, mock_notify, mock_reward):
        """Generic openid_connect provider also triggers if thread-local is set."""
        sl = _make_sociallogin(self.user, provider="openid_connect")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_approved)


@override_settings(**CRUSH_LU_URL_SETTINGS)
class TestAutoApproveSignalGuards(TestCase):
    """Each guard in the signal must independently block approval."""

    def setUp(self):
        self.request = _make_request(host="crush.lu")

    def tearDown(self):
        crush_signals._thread_local.is_crush_luxid_login = False

    def _assert_not_approved(self, submission):
        submission.refresh_from_db()
        self.assertEqual(submission.status, "pending")

    def test_guard_wrong_provider_skips(self):
        crush_signals._thread_local.is_crush_luxid_login = True
        user, profile, submission = _make_user_with_pending_profile()
        sl = _make_sociallogin(user, provider="google")  # wrong provider
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        self._assert_not_approved(submission)

    def test_guard_thread_local_false_skips(self):
        crush_signals._thread_local.is_crush_luxid_login = False
        user, profile, submission = _make_user_with_pending_profile()
        sl = _make_sociallogin(user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        self._assert_not_approved(submission)

    def test_guard_non_crush_domain_skips(self):
        crush_signals._thread_local.is_crush_luxid_login = True
        user, profile, submission = _make_user_with_pending_profile()
        request = _make_request(host="entreprinder.lu")
        sl = _make_sociallogin(user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=request, sociallogin=sl
        )
        self._assert_not_approved(submission)

    def test_guard_user_without_profile_skips(self):
        crush_signals._thread_local.is_crush_luxid_login = True
        user = User.objects.create_user(
            username="noprofile@example.com",
            email="noprofile@example.com",
            password="pass123",
        )
        sl = _make_sociallogin(user, provider="luxid")
        # Should not raise, just silently skip
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        self.assertFalse(CrushProfile.objects.filter(user=user).exists())

    def test_guard_already_approved_profile_skips(self):
        crush_signals._thread_local.is_crush_luxid_login = True
        user, profile, submission = _make_user_with_pending_profile()
        profile.is_approved = True
        profile.approved_at = timezone.now()
        profile.save(update_fields=["is_approved", "approved_at"])
        submission.status = "approved"
        submission.save(update_fields=["status"])

        sl = _make_sociallogin(user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        # No change (was already approved)
        submission.refresh_from_db()
        self.assertEqual(submission.status, "approved")
        self.assertEqual(submission.coach_notes, "")  # not modified

    def test_guard_incomplete_profile_skips(self):
        """An incomplete profile (never submitted) must not be verified just
        by connecting LuxID — it has to go through submission first."""
        crush_signals._thread_local.is_crush_luxid_login = True
        user, profile, submission = _make_user_with_pending_profile()
        profile.verification_status = "incomplete"
        profile.save(update_fields=["verification_status"])

        sl = _make_sociallogin(user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        profile.refresh_from_db()
        self.assertFalse(profile.is_approved)
        self.assertEqual(profile.verification_status, "incomplete")

    def test_guard_rejected_profile_skips(self):
        """A rejected profile must not be able to self-clear by connecting
        LuxID."""
        crush_signals._thread_local.is_crush_luxid_login = True
        user, profile, submission = _make_user_with_pending_profile()
        profile.verification_status = "rejected"
        profile.save(update_fields=["verification_status"])

        sl = _make_sociallogin(user, provider="luxid")
        social_account_added.send(
            sender=SocialAccount, request=self.request, sociallogin=sl
        )
        profile.refresh_from_db()
        self.assertFalse(profile.is_approved)
        self.assertEqual(profile.verification_status, "rejected")

    def test_no_pending_submission_verifies_directly(self):
        """LuxId now verifies the profile directly even with no pending submission.
        Coach review is a paid feature — LuxId verification is independent."""
        crush_signals._thread_local.is_crush_luxid_login = True
        user, profile, submission = _make_user_with_pending_profile()
        submission.status = "rejected"
        submission.save(update_fields=["status"])

        with patch("crush_lu.notification_service.notify_profile_approved"):
            sl = _make_sociallogin(user, provider="luxid")
            social_account_added.send(
                sender=SocialAccount, request=self.request, sociallogin=sl
            )
        profile.refresh_from_db()
        self.assertTrue(profile.is_approved)
        self.assertEqual(profile.verification_status, "verified")

    def test_reward_failure_does_not_abort_approval(self):
        """Reward errors are swallowed; the approval DB writes must persist."""
        crush_signals._thread_local.is_crush_luxid_login = True
        user, profile, submission = _make_user_with_pending_profile()
        sl = _make_sociallogin(user, provider="luxid")

        with patch(
            "crush_lu.referrals.check_and_apply_profile_approved_reward",
            side_effect=Exception("reward service down"),
        ):
            with patch("crush_lu.notification_service.notify_profile_approved"):
                social_account_added.send(
                    sender=SocialAccount, request=self.request, sociallogin=sl
                )

        submission.refresh_from_db()
        self.assertEqual(submission.status, "approved")


# ---------------------------------------------------------------------------
# Adapter redirect tests
# ---------------------------------------------------------------------------

@override_settings(**CRUSH_LU_URL_SETTINGS)
class TestGetConnectRedirectUrl(TestCase):
    """get_connect_redirect_url routes correctly for LuxID connections."""

    def setUp(self):
        self.user, self.profile, self.submission = _make_user_with_pending_profile()
        self.adapter = _make_adapter()

    def test_pending_submission_redirects_to_profile_submitted(self):
        request = _make_request(host="crush.lu")
        sa = _make_social_account(self.user, provider="luxid")
        url = self.adapter.get_connect_redirect_url(request, sa)
        self.assertEqual(url, "/profile-submitted/")

    def test_session_flag_redirects_to_profile_submitted_when_no_pending(self):
        """Session flag (set by signal) redirects even after submission is approved."""
        self.submission.status = "approved"
        self.submission.save(update_fields=["status"])

        request = _make_request(host="crush.lu")
        request.session["luxid_just_auto_approved"] = True
        sa = _make_social_account(self.user, provider="luxid")

        url = self.adapter.get_connect_redirect_url(request, sa)
        self.assertEqual(url, "/profile-submitted/")

    def test_session_flag_is_consumed_after_redirect(self):
        """Pop() ensures the flag is one-shot."""
        self.submission.status = "approved"
        self.submission.save(update_fields=["status"])

        request = _make_request(host="crush.lu")
        request.session["luxid_just_auto_approved"] = True
        sa = _make_social_account(self.user, provider="luxid")

        self.adapter.get_connect_redirect_url(request, sa)
        self.assertNotIn("luxid_just_auto_approved", request.session)

    def test_no_pending_no_flag_falls_through_to_settings(self):
        self.submission.status = "approved"
        self.submission.save(update_fields=["status"])

        request = _make_request(host="crush.lu")
        sa = _make_social_account(self.user, provider="luxid")

        url = self.adapter.get_connect_redirect_url(request, sa)
        self.assertEqual(url, "/account/settings/")

    def test_non_luxid_provider_goes_to_settings(self):
        request = _make_request(host="crush.lu")
        sa = _make_social_account(self.user, provider="google")
        url = self.adapter.get_connect_redirect_url(request, sa)
        self.assertEqual(url, "/account/settings/")

    def test_non_crush_domain_does_not_go_to_profile_submitted(self):
        """Non-crush domains fall through to allauth's default connections page."""
        request = _make_request(host="entreprinder.lu")
        sa = _make_social_account(self.user, provider="luxid")
        url = self.adapter.get_connect_redirect_url(request, sa)
        self.assertNotEqual(url, "/profile-submitted/")

    def test_crush_subdomain_redirects_to_profile_submitted(self):
        """*.crush.lu subdomains are treated as crush domains."""
        request = _make_request(host="test.crush.lu")
        sa = _make_social_account(self.user, provider="luxid")
        url = self.adapter.get_connect_redirect_url(request, sa)
        self.assertEqual(url, "/profile-submitted/")

    def test_mid_onboarding_no_submission_redirects_to_onboarding(self):
        """Connecting LuxID during Step 2 (before submission) routes back to onboarding."""
        self.submission.delete()  # simulate pre-submission state

        request = _make_request(host="crush.lu")
        sa = _make_social_account(self.user, provider="luxid")
        url = self.adapter.get_connect_redirect_url(request, sa)
        self.assertIn("/onboarding/", url)


def _make_adapter():
    from azureproject.adapters import MultiDomainSocialAccountAdapter
    return MultiDomainSocialAccountAdapter()


def _make_social_account(user, provider="luxid"):
    sa = SocialAccount(user=user, provider=provider, uid="fake-uid")
    sa.save()
    return sa


# ---------------------------------------------------------------------------
# View context tests: LuxID CTA on profile_submitted
# ---------------------------------------------------------------------------

class _SiteMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )


@override_settings(**CRUSH_LU_URL_SETTINGS)
class TestProfileSubmittedLuxidContext(_SiteMixin, TestCase):
    """profile_submitted view injects LuxID CTA context correctly."""

    def setUp(self):
        self.client = Client()
        self.user, self.profile, self.submission = _make_user_with_pending_profile()
        UserDataConsent.objects.update_or_create(
            user=self.user,
            defaults={"crushlu_consent_given": True},
        )
        self.client.force_login(self.user)

    def _get_profile_submitted(self):
        return self.client.get("/en/profile-submitted/", HTTP_HOST="crush.lu")

    def test_no_luxid_configured_no_cta(self):
        """When no LuxID SocialApp is configured, luxid_connect_url is None."""
        response = self._get_profile_submitted()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["luxid_connect_url"])

    def test_already_has_luxid_account_redirects_to_dashboard(self):
        """User with existing LuxID account is verified immediately and redirected to dashboard."""
        from allauth.socialaccount.models import SocialApp
        site = Site.objects.get_current()
        app = SocialApp.objects.create(
            provider="luxid", name="LuxID", client_id="test", secret="test"
        )
        app.sites.add(site)
        SocialAccount.objects.create(user=self.user, provider="luxid", uid="lux-123")

        with patch("crush_lu.notification_service.notify_profile_approved"):
            response = self._get_profile_submitted()

        # Verified immediately → redirect to dashboard
        self.assertEqual(response.status_code, 302)
        self.assertIn("dashboard", response["Location"])

    def test_approved_submission_no_cta(self):
        """CTA is only shown for pending submissions."""
        self.submission.status = "approved"
        self.submission.save(update_fields=["status"])

        response = self._get_profile_submitted()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["luxid_connect_url"])

    def test_approved_submission_shows_stepper_step_7(self):
        """Approved submissions render stepper at step 7, not 5."""
        self.submission.status = "approved"
        self.submission.save(update_fields=["status"])

        response = self._get_profile_submitted()
        self.assertEqual(response.status_code, 200)
        stepper = response.context.get("stepper_steps", [])
        # current step should be 7
        current_steps = [s for s in stepper if s.get("is_current")]
        if current_steps:
            self.assertEqual(current_steps[0].get("step"), 7)

    def test_pending_submission_shows_stepper_step_5(self):
        """Pending submissions render stepper at step 5."""
        response = self._get_profile_submitted()
        self.assertEqual(response.status_code, 200)
        stepper = response.context.get("stepper_steps", [])
        current_steps = [s for s in stepper if s.get("is_current")]
        if current_steps:
            self.assertEqual(current_steps[0].get("step"), 5)


# ---------------------------------------------------------------------------
# DOB / gender authoritative overwrite tests
# ---------------------------------------------------------------------------

@override_settings(**CRUSH_LU_URL_SETTINGS)
class TestLuxidProfileDataOverwrite(TestCase):
    """update_crush_profile_from_luxid overwrites DOB/gender for luxid provider."""

    def setUp(self):
        crush_signals._thread_local.is_crush_luxid_login = False

    def tearDown(self):
        crush_signals._thread_local.is_crush_luxid_login = False

    def _run_pre_social_login(self, user, claims, provider="luxid", host="crush.lu"):
        from allauth.socialaccount.signals import pre_social_login
        account = MagicMock()
        account.provider = provider
        account.extra_data = claims
        sl = MagicMock()
        sl.user = user
        sl.account = account
        sl.token = MagicMock()
        sl.token.token = "fake-token"
        # Build sociallogin.account.extra_data used by the signal
        request = _make_request(host=host)
        pre_social_login.send(sender=SocialAccount, request=request, sociallogin=sl)
        return request

    def test_luxid_provider_overwrites_existing_dob(self):
        user, profile, _ = _make_user_with_pending_profile(dob=date(1990, 1, 1))
        self.assertEqual(profile.date_of_birth, date(1990, 1, 1))

        # Simulate pre_social_login with a different DOB from LuxID
        self._run_pre_social_login(
            user,
            claims={"birthdate": "1993-03-15"},
            provider="luxid",
        )
        profile.refresh_from_db()
        self.assertEqual(profile.date_of_birth, date(1993, 3, 15))

    def test_openid_connect_provider_does_not_overwrite_existing_dob(self):
        user, profile, _ = _make_user_with_pending_profile(dob=date(1990, 1, 1))

        self._run_pre_social_login(
            user,
            claims={"birthdate": "1993-03-15"},
            provider="openid_connect",
        )
        profile.refresh_from_db()
        self.assertEqual(profile.date_of_birth, date(1990, 1, 1))

    def test_luxid_provider_overwrites_existing_gender(self):
        user, profile, _ = _make_user_with_pending_profile(gender="M")

        self._run_pre_social_login(
            user,
            claims={"gender": "female"},
            provider="luxid",
        )
        profile.refresh_from_db()
        self.assertEqual(profile.gender, "F")

    def test_luxid_maps_diverse_to_nb(self):
        user, profile, _ = _make_user_with_pending_profile(gender="M")

        self._run_pre_social_login(
            user,
            claims={"gender": "diverse"},
            provider="luxid",
        )
        profile.refresh_from_db()
        self.assertEqual(profile.gender, "NB")

    def test_luxid_maps_divers_french_to_nb(self):
        user, profile, _ = _make_user_with_pending_profile(gender="M")

        self._run_pre_social_login(
            user,
            claims={"gender": "divers"},
            provider="luxid",
        )
        profile.refresh_from_db()
        self.assertEqual(profile.gender, "NB")

    def test_empty_gender_claim_leaves_profile_unchanged(self):
        """LuxID sends no gender claim when user opts out — profile must not change."""
        user, profile, _ = _make_user_with_pending_profile(gender="F")

        self._run_pre_social_login(
            user,
            claims={"gender": ""},
            provider="luxid",
        )
        profile.refresh_from_db()
        self.assertEqual(profile.gender, "F")
