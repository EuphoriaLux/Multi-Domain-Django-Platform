"""Tests for the self-service "Take a break" feature (UX Wave 3 · WP13).

Covers: the model pause/resume methods (and their mirror onto Crush Connect
matching), exclusion from campaign/newsletter audiences, and the account
settings / dashboard views.
"""

from datetime import date

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from crush_lu.models import CrushProfile
from crush_lu.models.crush_connect import CrushConnectMembership
from crush_lu.models.profiles import UserDataConsent
from crush_lu.newsletter_service import exclude_on_break_users
from crush_lu.services.campaigns import resolve_campaign_audience

User = get_user_model()


def _grant_crush_access(user):
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"verified": True, "primary": True},
    )


def _make_profile(user, **overrides):
    defaults = dict(
        user=user,
        date_of_birth=date(1995, 1, 1),
        gender="M",
        location="Luxembourg",
        is_approved=True,
        verification_status="verified",
    )
    defaults.update(overrides)
    return CrushProfile.objects.create(**defaults)


class TakeABreakModelTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="onbreak@example.com",
            email="onbreak@example.com",
            password="testpass123",
        )
        self.profile = _make_profile(self.user)

    def test_is_on_break_false_by_default(self):
        self.assertFalse(self.profile.is_on_break)

    def test_take_a_break_sets_on_break_at(self):
        self.profile.take_a_break()
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_on_break)
        self.assertIsNotNone(self.profile.on_break_at)

    def test_take_a_break_is_idempotent(self):
        self.profile.take_a_break()
        self.profile.refresh_from_db()
        first_timestamp = self.profile.on_break_at
        self.profile.take_a_break()
        self.profile.refresh_from_db()
        self.assertEqual(first_timestamp, self.profile.on_break_at)

    def test_resume_from_break_clears_on_break_at(self):
        self.profile.take_a_break()
        self.profile.resume_from_break()
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_on_break)
        self.assertIsNone(self.profile.on_break_at)

    def test_take_a_break_pauses_connect_membership(self):
        membership = CrushConnectMembership.objects.create(user=self.user)
        self.profile.take_a_break()
        membership.refresh_from_db()
        self.assertTrue(membership.is_paused)

    def test_resume_from_break_reactivates_connect_membership(self):
        membership = CrushConnectMembership.objects.create(user=self.user)
        self.profile.take_a_break()
        self.profile.resume_from_break()
        membership.refresh_from_db()
        self.assertFalse(membership.is_paused)

    def test_take_a_break_without_connect_membership_does_not_error(self):
        # No CrushConnectMembership row for this user — must not raise.
        self.profile.take_a_break()
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_on_break)


class AudienceExclusionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.active_user = User.objects.create_user(
            username="active@example.com",
            email="active@example.com",
            password="testpass123",
        )
        self.break_user = User.objects.create_user(
            username="break@example.com",
            email="break@example.com",
            password="testpass123",
        )
        _make_profile(self.active_user)
        break_profile = _make_profile(self.break_user)
        break_profile.take_a_break()

    def test_exclude_on_break_users_drops_on_break_member(self):
        users = exclude_on_break_users(User.objects.all())
        self.assertIn(self.active_user, users)
        self.assertNotIn(self.break_user, users)

    def test_campaign_audience_excludes_on_break_member(self):
        from crush_lu.models.campaigns import Campaign

        campaign = Campaign(audience="all_profiles", segment_key="", language="all")
        users = resolve_campaign_audience(campaign)
        self.assertIn(self.active_user, users)
        self.assertNotIn(self.break_user, users)

    def test_newsletter_recipients_exclude_on_break_member(self):
        from crush_lu.models.newsletter import Newsletter
        from crush_lu.newsletter_service import get_newsletter_recipients

        newsletter = Newsletter.objects.create(
            subject="Test",
            body_html="Hello",
            audience="all_profiles",
            language="all",
        )
        users = get_newsletter_recipients(newsletter)
        self.assertIn(self.active_user, users)
        self.assertNotIn(self.break_user, users)


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class TakeABreakViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = User.objects.create_user(
            username="viewbreak@example.com",
            email="viewbreak@example.com",
            password="testpass123",
        )
        self.profile = _make_profile(self.user)
        _grant_crush_access(self.user)
        self.client.force_login(self.user)

    def test_get_confirm_page(self):
        response = self.client.get("/en/account/take-a-break/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Take a Break")

    def test_post_sets_break_and_redirects_to_dashboard(self):
        response = self.client.post("/en/account/take-a-break/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_on_break)

    def test_resume_view_clears_break(self):
        self.profile.take_a_break()
        response = self.client.post(
            "/en/account/resume-from-break/", HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_on_break)

    def test_resume_view_requires_post(self):
        response = self.client.get(
            "/en/account/resume-from-break/", HTTP_HOST="crush.lu"
        )
        self.assertEqual(response.status_code, 405)

    def test_dashboard_shows_resume_banner_when_on_break(self):
        self.profile.take_a_break()
        response = self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")
        self.assertContains(response, "taking a break")
        self.assertContains(response, "Resume")

    def test_dashboard_hides_banner_when_not_on_break(self):
        response = self.client.get("/en/dashboard/", HTTP_HOST="crush.lu")
        self.assertNotContains(response, "taking a break")

    def test_account_settings_shows_take_a_break_link_when_active(self):
        response = self.client.get("/en/account/settings/", HTTP_HOST="crush.lu")
        self.assertContains(response, "/account/take-a-break/")

    def test_account_settings_shows_badge_when_on_break(self):
        self.profile.take_a_break()
        response = self.client.get("/en/account/settings/", HTTP_HOST="crush.lu")
        self.assertContains(response, "Currently on a break")
