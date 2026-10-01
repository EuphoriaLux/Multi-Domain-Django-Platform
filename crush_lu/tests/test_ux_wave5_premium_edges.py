"""UX review Wave 5, WP5 (premium edges), issue #1099 / owner decision B.

* choose_coach hides the SumUp pay button from a beta-refused pending member
  (checkout would answer 403) and shows the invite-only note instead.
* membership.html gives native shells that may not sell an "available outside
  the app" hint in place of the buy CTA; ordering (Your plan > native hint >
  pending states) is pinned.
"""

from datetime import date

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from crush_lu.models import (
    CrushCoach,
    CrushConnectWaitlist,
    CrushProfile,
    PremiumMembership,
)
from crush_lu.models.profiles import UserDataConsent
from crush_lu.tests.test_ux_wave4_pricing import _premium_card


class PremiumEdgesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="pe@example.com", email="pe@example.com", password="pw12345!"
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender="F",
            location="canton-luxembourg",
        )
        coach_user = User.objects.create_user(
            username="pec@example.com", email="pec@example.com", password="x"
        )
        self.coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.user)

    def _pending(self):
        return PremiumMembership.objects.create(
            user=self.user, coach=self.coach, status="pending"
        )

    def _membership_card(self, lang="en"):
        response = self.client.get(
            f"/{lang}/membership/", HTTP_X_CRUSH_CLIENT="ios-app"
        )
        return _premium_card(response.content.decode())

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_refused_pending_member_gets_no_pay_button(self):
        CrushConnectWaitlist.objects.create(user=self.user, selected_as_tester=False)
        self._pending()
        response = self.client.get("/en/premium/coaches/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-membership-id=")
        self.assertNotContains(response, "Complete your payment below")
        self.assertContains(response, "Premium is invite-only during the beta.")
        self.assertContains(response, 'action="/en/premium/cancel/"')

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_selected_tester_keeps_pay_button(self):
        CrushConnectWaitlist.objects.create(user=self.user, selected_as_tester=True)
        self._pending()
        response = self.client.get("/en/premium/coaches/")
        self.assertContains(response, "data-membership-id=")

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=False)
    def test_open_purchase_keeps_pay_button(self):
        self._pending()
        response = self.client.get("/en/premium/coaches/")
        self.assertContains(response, "data-membership-id=")

    @override_settings(
        PREMIUM_REDIRECTS_TO_BETA=True, IOS_NATIVE_COMMERCE_ENABLED=False
    )
    def test_native_shell_gets_outside_app_hint_even_with_pending(self):
        CrushConnectWaitlist.objects.create(user=self.user, selected_as_tester=False)
        self._pending()
        card = self._membership_card()
        self.assertIn("Available outside the mobile app", card)
        self.assertNotIn("Manage your Premium request", card)
        self.assertNotIn("/premium/coaches/", card)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=False)
    def test_web_has_no_outside_app_hint(self):
        card = _premium_card(self.client.get("/en/membership/").content.decode())
        self.assertNotIn("Available outside the mobile app", card)

    @override_settings(IOS_NATIVE_COMMERCE_ENABLED=False)
    def test_native_hint_translated(self):
        self.assertIn("Außerhalb der Mobile-App verfügbar", self._membership_card("de"))
        self.assertIn(
            "Disponible en dehors de l'application mobile",
            self._membership_card("fr"),
        )

    @override_settings(
        PREMIUM_REDIRECTS_TO_BETA=False, IOS_NATIVE_COMMERCE_ENABLED=False
    )
    def test_your_plan_wins_over_native_hint(self):
        PremiumMembership.objects.create(
            user=self.user, coach=self.coach, status="active", payment_confirmed=True
        )
        card = self._membership_card()
        self.assertIn("Your plan", card)
        self.assertNotIn("Available outside the mobile app", card)
