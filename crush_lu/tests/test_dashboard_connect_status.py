"""
Tests for the Crush Connect status strip on the crush.lu dashboard
(the verified NON-premium slot).

The strip is TIERED by verification (Task 13.4): what a member can do in
Connect depends on how they were verified, not on Premium.

States exercised (see crush_lu/templates/crush_lu/partials/_connect_status_strip.html):
  X.  Coach-excluded         → nothing (exclusion is never surfaced).
  0.  Not verified           → "Get verified" card, both routes (even pre-launch).
  1.  Verified, not opted-in → purple CTA, worded per tier (flag / staff only).
  2a. Opted-in, no photo     → amber "Almost in Crush Connect".
  2b. Opted-in + event       → green Connect Week card (active discovery).
  2c. Opted-in, LuxID only   → blue "You're in the Mix" (visible, reply-only).

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
    ALMOST = "Almost in Crush Connect"
    # State 0's heading. Named for the LuxID route it still offers, but the
    # card now offers the event route beside it — verification, not LuxID, is
    # what the member is missing.
    LUXID_BANNER = "Get verified to join Crush Connect"
    CONNECT_WEEK = "Your Connect Week is open"
    WEEK_CTA = "Open my Connect Week"

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
    def test_crush_connect_tab_uses_compact_connect_label(self):
        _link_luxid(self.user)
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), photo_share_consent=True
        )

        response = self._get()

        self.assertContains(response, "<span>Connect</span>", html=True)
        self.assertContains(response, 'aria-label="Crush Connect"')
        self.assertContains(response, reverse("crush_lu:crush_connect_hub"))

        for language in ("de", "fr"):
            localized = self.client.get(
                f"/{language}/dashboard/", HTTP_HOST="crush.lu"
            )
            self.assertEqual(localized.status_code, 200)
            self.assertContains(localized, "<span>Connect</span>", html=True)
            self.assertContains(localized, 'aria-label="Crush Connect"')

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

    @override_settings(
        CRUSH_CONNECT_LAUNCHED=False, CRUSH_CONNECT_CANDIDATE_OPEN=False
    )
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

    @override_settings(
        CRUSH_CONNECT_LAUNCHED=False, CRUSH_CONNECT_CANDIDATE_OPEN=False
    )
    def test_prelaunch_staff_sees_join_cta(self):
        staff = _make_member("staff@example.com", is_staff=True)
        _link_luxid(staff)
        self.client.login(username="staff@example.com", password="testpass123")
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.JOIN_CTA)

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_premium_member_gets_no_strip(self):
        from crush_lu.models import PremiumMembership

        coach = _make_coach("coach_p", "+352999999")
        profile = self.user.crushprofile
        profile.assigned_coach = coach
        profile.save()
        # Premium = active membership, not the coach assignment alone.
        PremiumMembership.objects.create(
            user=self.user, coach=coach, status="active", payment_confirmed=True
        )
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

    @override_settings(
        CRUSH_CONNECT_LAUNCHED=False, CRUSH_CONNECT_CANDIDATE_OPEN=False
    )
    def test_products_footer_not_in_mix_prelaunch_non_staff(self):
        """Pre-launch (flags off, non-staff), a full membership is still bounced
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

    # ------------------------------------------------------------------
    # Tiered states (Task 13.4) — the strip's offer follows verification.
    # ------------------------------------------------------------------

    def _mark_event_verified(self, user):
        """Give ``user`` coach-verified in-person attendance, which is what
        ``has_attended_event`` (and so ``cycle_access_open``) reads. Reuses
        the Connect suite's own fixtures rather than hand-building the event,
        so the two stay in step."""
        from crush_lu.tests.test_crush_connect import _mark_attended

        return _mark_attended(user)

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_event_verified_member_is_offered_the_connect_week(self):
        """Event verification is what opens active discovery
        (connect_phase.cycle_access_open), so an onboarded event-verified
        member must be sent to the Week — not to the reply-only Mix card."""
        self._mark_event_verified(self.user)
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), photo_share_consent=True
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_attended_event"])
        self.assertContains(response, self.CONNECT_WEEK)
        self.assertContains(response, self.WEEK_CTA)
        self.assertNotContains(response, self.IN_MIX)

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_dual_verified_member_gets_the_connect_week_tier(self):
        """LuxID + event is the strictly larger capability, so the dual-verified
        member is shown the Week, never given a second, smaller Mix card."""
        _link_luxid(self.user)
        self._mark_event_verified(self.user)
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), photo_share_consent=True
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_luxid_connected"])
        self.assertTrue(response.context["has_attended_event"])
        self.assertContains(response, self.CONNECT_WEEK)
        self.assertNotContains(response, self.IN_MIX)

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_luxid_only_member_is_never_linked_to_the_connect_week(self):
        """The regression this tier exists to prevent: cycle_access_open is
        False without event verification, so offering a LuxID-only member the
        Week would be a link straight back to the teaser."""
        _link_luxid(self.user)
        CrushConnectMembership.objects.create(
            user=self.user, onboarded_at=timezone.now(), photo_share_consent=True
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_attended_event"])
        self.assertContains(response, self.IN_MIX)
        self.assertNotContains(response, self.CONNECT_WEEK)
        # Asserted on the strip's own CTA label, not on the week_home URL: an
        # onboarded member also gets that URL from the persistent Connect nav
        # (crush_connect_nav_visible), so a page-wide URL check would say
        # nothing about the strip.
        self.assertNotContains(response, self.WEEK_CTA)

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_event_verified_not_opted_in_gets_week_worded_invitation(self):
        """State 1 is tier-aware too: an event-verified member is invited to
        the Week they have already unlocked, not merely into the Mix."""
        self._mark_event_verified(self.user)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["connect_onboarded"])
        self.assertContains(response, "Your Connect Week is waiting")
        self.assertContains(response, "Start my Connect Week")
        self.assertContains(response, reverse("crush_lu:crush_connect_onboarding"))
        # NOT asserted via JOIN_CTA: _products.html emits that label for any
        # verified, not-onboarded member regardless of tier, so its absence
        # would not be evidence about the strip. The strip's own heading is.
        self.assertNotContains(response, "You're not in Crush Connect yet")

    @override_settings(CRUSH_CONNECT_LAUNCHED=True)
    def test_unverified_card_offers_both_verification_routes(self):
        """Neither LuxID nor event → the member is not in Connect at all, and
        the card must name both doors, not only LuxID."""
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_identity_verified"])
        self.assertContains(response, self.LUXID_BANNER)
        self.assertContains(response, reverse("crush_lu:event_list"))
        self.assertNotContains(response, self.CONNECT_WEEK)
        self.assertNotContains(response, self.IN_MIX)
