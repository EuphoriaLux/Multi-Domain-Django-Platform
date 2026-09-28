"""
UX review Wave 4, WP5 (pricing): finding 1-01, Decision A.

  /membership/ becomes the Free vs Premium page. The Premium price is rendered
  from SUMUP_PREMIUM_MONTHLY_FEE (never hardcoded), no trial is promised, the
  CTA follows PREMIUM_REDIRECTS_TO_BETA, and native shells that may not sell
  get neither price nor buy CTA. The referral points ladder stays below it as
  "Rewards". Home's "That's Premium." links there, and the how-it-works
  microcopy no longer says "Cancel anytime".

Literal paths + HTTP_HOST='crush.lu' per AGENTS.md (reverse() resolves against
the default urlconf, not the host-selected one).
"""

import re
from datetime import date

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from crush_lu.models import CrushProfile
from crush_lu.models.profiles import UserDataConsent


def _premium_card(html):
    match = re.search(r'id="premium-plan".*?</section>', html, re.S)
    assert match, "Premium plan card missing"
    return match.group(0)


class PricingPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    @override_settings(SUMUP_PREMIUM_MONTHLY_FEE="12.50")
    def test_price_comes_from_setting(self):
        html = self.client.get("/en/membership/").content.decode()
        self.assertIn("Free vs Premium", html)
        self.assertIn("€12.50 / month", _premium_card(html))
        self.assertNotIn("10.00", _premium_card(html))

    @override_settings(SUMUP_PREMIUM_MONTHLY_FEE="12.50")
    def test_price_is_localized(self):
        html = self.client.get("/de/membership/").content.decode()
        self.assertIn("12,50 € / Monat", _premium_card(html))

    def test_feature_columns_and_no_trial(self):
        html = self.client.get("/en/membership/").content.decode()
        for feature in (
            "Everything in Standard",
            "Personal coach",
            "In-person verification before the event",
            "Reserved event seats",
            "Crush Connect — Connect Week + human Coach Pick",
        ):
            self.assertIn(feature, _premium_card(html))
        self.assertIn("No personal coach", html)
        self.assertNotIn("first month free", html.lower())
        self.assertNotIn("trial", html.lower())

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_beta_cta_points_to_connect_waitlist(self):
        card = _premium_card(self.client.get("/en/membership/").content.decode())
        self.assertIn("Premium is invite-only during the beta.", card)
        self.assertIn('href="/en/crush-connect/"', card)
        self.assertNotIn("/premium/coaches/", card)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=False)
    def test_open_cta_points_to_choose_coach(self):
        card = _premium_card(self.client.get("/en/membership/").content.decode())
        self.assertIn('href="/en/premium/coaches/"', card)
        self.assertNotIn("invite-only", card)

    @override_settings(
        PREMIUM_REDIRECTS_TO_BETA=False,
        IOS_NATIVE_COMMERCE_ENABLED=False,
        SUMUP_PREMIUM_MONTHLY_FEE="12.50",
    )
    def test_native_app_hides_price_and_buy_cta(self):
        html = self.client.get(
            "/en/membership/", HTTP_X_CRUSH_CLIENT="ios-app"
        ).content.decode()
        card = _premium_card(html)
        self.assertIn("Personal coach", card)
        self.assertNotIn("12.50", card)
        self.assertNotIn("/premium/coaches/", card)
        self.assertNotIn("/crush-connect/", card)

    def test_rewards_ladder_kept_below_comparison(self):
        html = self.client.get("/en/membership/").content.decode()
        self.assertIn('id="rewards-heading"', html)
        self.assertLess(html.index('id="pricing"'), html.index('id="rewards"'))
        rewards = html[html.index('id="rewards"') :]
        for tier in ("Basic", "Bronze", "Silver", "Gold"):
            self.assertIn(tier, rewards)
        self.assertIn("How to Earn Points", rewards)


class PricingPageMemberTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="pm@example.com", email="pm@example.com", password="pw12345!"
        )
        UserDataConsent.objects.filter(user=self.user).update(
            crushlu_consent_given=True
        )
        self.profile = CrushProfile.objects.create(
            user=self.user,
            date_of_birth=date(1995, 5, 15),
            gender="F",
            location="canton-luxembourg",
        )
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(self.user)

    def test_referral_context_still_rendered(self):
        html = self.client.get("/en/membership/").content.decode()
        self.assertIn('id="share-referral-btn"', html)
        self.assertIn('id="rewards"', html)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=False)
    def test_active_premium_member_sees_your_plan(self):
        from crush_lu.models import CrushCoach, PremiumMembership

        coach_user = User.objects.create_user(
            username="c@example.com", email="c@example.com", password="x"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        PremiumMembership.objects.create(
            user=self.user, coach=coach, status="active", payment_confirmed=True
        )
        card = _premium_card(self.client.get("/en/membership/").content.decode())
        self.assertIn("Your plan", card)
        self.assertNotIn("/premium/coaches/", card)


class PricingEntryPointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_home_thats_premium_links_to_pricing(self):
        html = self.client.get("/en/").content.decode()
        self.assertRegex(
            html,
            r"""<a href="/en/membership/#pricing"[^>]*>\s*That(?:'|&\#x27;)s Premium\.""",
        )

    def test_how_it_works_microcopy(self):
        html = self.client.get("/en/how-it-works/").content.decode()
        self.assertIn("Free account · Events from €0 · Premium optional", html)
        self.assertNotIn("Cancel anytime", html)

    def test_how_it_works_microcopy_translated(self):
        html = self.client.get("/de/how-it-works/").content.decode()
        self.assertIn("Kostenloses Konto · Events ab 0 € · Premium optional", html)
        html = self.client.get("/fr/how-it-works/").content.decode()
        self.assertIn("Compte gratuit · Événements dès 0 € · Premium en option", html)

    def test_footer_links_to_membership(self):
        html = self.client.get("/en/how-it-works/").content.decode()
        self.assertIn('href="/en/membership/"', html)
