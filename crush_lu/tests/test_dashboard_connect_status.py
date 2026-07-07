"""
Tests for the Crush Connect opt-in status strip on the crush.lu dashboard
(the verified NON-premium slot).

States exercised (see crush_lu/templates/crush_lu/partials/_connect_status_strip.html):
  0. No LuxID            → "Connect LuxID" banner (visible even pre-launch).
  X. Coach-excluded      → nothing (exclusion is never surfaced to the member).
  1. LuxID, not opted-in → purple "Join the Mix" CTA (launch flag / staff only).
  2. LuxID, opted-in     → green "You're in the Mix" confirmation (flag / staff).

Plus the matching "LuxID Verify" card footer in _products.html.
"""

from allauth.socialaccount.models import SocialAccount
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import CrushConnectMembership
from crush_lu.tests.test_profile_edit_connect_card import _make_coach, _make_member


def _link_luxid(user):
    """Give ``user`` a native LuxID social account (makes has_luxid_connected)."""
    return SocialAccount.objects.create(
        user=user, provider="luxid", uid=f"lux-{user.pk}"
    )


@override_settings(ROOT_URLCONF="azureproject.urls_crush")
class DashboardConnectStatusStripTests(TestCase):
    JOIN_CTA = "Join the Mix"
    IN_MIX = "You're in the Mix"
    ALMOST = "Almost in the Mix"
    LUXID_BANNER = "Connect LuxID to join Crush Connect"

    def setUp(self):
        self.user = _make_member("dash@example.com")
        self.client.login(username="dash@example.com", password="testpass123")

    def _get(self):
        return self.client.get(reverse("crush_lu:dashboard"), HTTP_HOST="crush.lu")

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_no_luxid_shows_connect_banner_not_mix_states(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.LUXID_BANNER)
        self.assertNotContains(response, self.JOIN_CTA)
        self.assertNotContains(response, self.IN_MIX)
        self.assertNotContains(
            response, reverse("crush_lu:crush_connect_onboarding")
        )
        self.assertNotContains(
            response, reverse("crush_lu:crush_connect_catalogue_status")
        )

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_luxid_not_opted_in_shows_join_cta(self):
        _link_luxid(self.user)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["connect_onboarded"])
        self.assertFalse(response.context["connect_excluded"])
        self.assertContains(response, self.JOIN_CTA)
        self.assertContains(response, reverse("crush_lu:crush_connect_onboarding"))
        self.assertNotContains(response, self.IN_MIX)
        self.assertNotContains(
            response, reverse("crush_lu:crush_connect_catalogue_status")
        )

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_opted_in_shows_youre_in_strip(self):
        _link_luxid(self.user)
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), photo_share_consent=True
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["connect_onboarded"])
        self.assertContains(response, self.IN_MIX)
        self.assertContains(
            response, reverse("crush_lu:crush_connect_catalogue_status")
        )
        self.assertNotContains(response, self.JOIN_CTA)

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_onboarded_without_photo_consent_shows_action_needed(self):
        """Opted in but photo sharing off → not actually discoverable, so the
        strip nudges to re-enable it instead of claiming "in the Mix"."""
        _link_luxid(self.user)
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), photo_share_consent=False
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["connect_onboarded"])
        self.assertFalse(response.context["connect_photo_consent"])
        self.assertContains(response, self.ALMOST)
        # The nudge must land on the questions section, where the
        # photo_share_consent toggle actually renders (not the section index).
        self.assertContains(
            response,
            reverse("crush_lu:crush_connect_profile_edit") + "?section=questions",
        )
        self.assertNotContains(response, self.IN_MIX)
        self.assertNotContains(response, self.JOIN_CTA)

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_excluded_member_sees_neither_strip(self):
        _link_luxid(self.user)
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), excluded_by_coach=True
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["connect_excluded"])
        self.assertFalse(response.context["connect_onboarded"])
        self.assertNotContains(response, self.JOIN_CTA)
        self.assertNotContains(response, self.IN_MIX)
        self.assertNotContains(response, reverse("crush_lu:crush_connect_onboarding"))

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_excluded_without_luxid_gets_no_luxid_prompt(self):
        """A coach-excluded member with no LuxID must see *nothing* — exclusion
        is checked before the LuxID banner, so they're never invited to connect
        LuxID for a Connect flow the onboarding gate would only bounce."""
        # Deliberately no _link_luxid → has_luxid_connected is False.
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), excluded_by_coach=True
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["connect_excluded"])
        self.assertFalse(response.context["has_luxid_connected"])
        self.assertNotContains(response, self.LUXID_BANNER)
        self.assertNotContains(response, self.JOIN_CTA)
        self.assertNotContains(response, self.IN_MIX)

    @override_settings(CRUSH_CONNECT_LAUNCHED=False)
    def test_prelaunch_hides_mix_states_for_non_staff(self):
        _link_luxid(self.user)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.JOIN_CTA)
        self.assertNotContains(response, self.IN_MIX)

        # A member without LuxID still sees the (ungated) LuxID banner.
        other = _make_member("nolux@example.com")
        self.client.login(username="nolux@example.com", password="testpass123")
        response = self._get()
        self.assertContains(response, self.LUXID_BANNER)

    @override_settings(CRUSH_CONNECT_LAUNCHED=False)
    def test_prelaunch_staff_sees_join_cta(self):
        staff = _make_member("staff@example.com", is_staff=True)
        _link_luxid(staff)
        self.client.login(username="staff@example.com", password="testpass123")
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.JOIN_CTA)

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_premium_member_gets_no_strip(self):
        coach = _make_coach("coach_p", "+352999999")
        profile = self.user.crushprofile
        profile.assigned_coach = coach  # premium
        profile.save()
        _link_luxid(self.user)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_premium"])
        self.assertNotContains(response, self.JOIN_CTA)
        self.assertNotContains(response, self.IN_MIX)

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_strip_renders_in_german(self):
        """New strings must ship translated — staging showed English fallbacks
        for untranslated Connect strings on /de/ pages."""
        _link_luxid(self.user)
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), photo_share_consent=True
        )
        response = self.client.get("/de/dashboard/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Du bist mit dabei")

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_products_footer_reflects_optin(self):
        _link_luxid(self.user)

        # Not opted in → the LuxID Verify card invites joining, not "Active".
        response = self._get()
        self.assertContains(response, self.JOIN_CTA)
        self.assertNotContains(response, "Active — In the Mix")

        # Opted in → the card footer confirms membership.
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), photo_share_consent=True
        )
        response = self._get()
        self.assertContains(response, "Active — In the Mix")

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_products_footer_not_in_mix_without_luxid(self):
        """Onboarded + photo consent but LuxID unlinked → not catalogue-eligible
        (services.crush_connect.is_catalogue_eligible requires has_luxid_connected),
        so the card must fall back to a plain "Active", never "In the Mix"."""
        # Deliberately no _link_luxid → has_luxid_connected is False.
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), photo_share_consent=True
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_luxid_connected"])
        self.assertTrue(response.context["connect_onboarded"])
        self.assertNotContains(response, "Active — In the Mix")
        self.assertContains(response, "✓ Active")
        # The strip prompts to link LuxID instead of confirming discoverability.
        self.assertContains(response, self.LUXID_BANNER)

    def test_products_footer_not_in_mix_prelaunch_non_staff(self):
        """Pre-launch (flag off, non-staff), a full membership is still bounced
        by the launch gate — matching the status strip, the card must not claim
        "In the Mix" and falls back to a plain "Active"."""
        _link_luxid(self.user)
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), photo_share_consent=True
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["connect_onboarded"])
        self.assertNotContains(response, "Active — In the Mix")
        self.assertContains(response, "✓ Active")
        # Launch gate off for a non-staff member → the strip shows nothing.
        self.assertNotContains(response, self.IN_MIX)
        self.assertNotContains(response, self.JOIN_CTA)
