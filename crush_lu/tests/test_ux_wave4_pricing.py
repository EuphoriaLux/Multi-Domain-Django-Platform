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
from html.parser import HTMLParser

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from crush_lu.models import CrushProfile
from crush_lu.models.profiles import UserDataConsent


def _premium_card(html):
    match = re.search(r'id="premium-plan".*?</section>', html, re.S)
    assert match, "Premium plan card missing"
    return match.group(0)


class _Links(HTMLParser):
    """Collects each <a>'s class, href and text, in document order."""

    def __init__(self):
        super().__init__()
        self.links = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            attrs = dict(attrs)
            self._current = {
                "href": attrs.get("href", ""),
                "class": (attrs.get("class") or "").split(),
                "text": "",
            }

    def handle_endtag(self, tag):
        if tag == "a" and self._current is not None:
            self._current["text"] = " ".join(self._current["text"].split())
            self.links.append(self._current)
            self._current = None

    def handle_data(self, data):
        if self._current is not None:
            self._current["text"] += data


def _links(html):
    parser = _Links()
    parser.feed(html)
    return parser.links


class _HeadMeta(HTMLParser):
    """Collect <title> text and <meta> content by name/property."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.meta = {}
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = attrs.get("name") or attrs.get("property")
            if key and key not in self.meta:
                self.meta[key] = attrs.get("content", "")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _head(html):
    parser = _HeadMeta()
    parser.feed(html)
    parser.title = parser.title.strip()
    return parser


class PricingMetaTests(TestCase):
    """Title and meta describe the pricing role, not the old points programme."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")

    def test_english_title_and_description(self):
        head = _head(self.client.get("/en/membership/").content.decode())
        description = (
            "Free account, events from €0. "
            "Premium adds a personal coach and reserved seats."
        )
        self.assertEqual(head.title, "Pricing – Crush.lu")
        self.assertEqual(head.meta["og:title"], "Pricing – Crush.lu")
        self.assertEqual(head.meta["description"], description)
        self.assertEqual(head.meta["og:description"], description)
        self.assertNotIn("Earn points", head.meta["description"])

    def test_german_title_and_description(self):
        head = _head(self.client.get("/de/membership/").content.decode())
        self.assertEqual(head.title, "Preise – Crush.lu")
        self.assertEqual(
            head.meta["description"],
            "Kostenloses Konto, Events ab 0 €. "
            "Premium bietet dir einen persönlichen Coach und reservierte Plätze.",
        )

    def test_french_title_and_description(self):
        head = _head(self.client.get("/fr/membership/").content.decode())
        self.assertEqual(head.title, "Tarifs – Crush.lu")
        self.assertEqual(
            head.meta["description"],
            "Compte gratuit, événements dès 0 €. "
            "Premium vous offre un coach personnel et des places réservées.",
        )


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

    def _pending_membership(self):
        from crush_lu.models import CrushCoach, PremiumMembership

        coach_user = User.objects.create_user(
            username="pc@example.com", email="pc@example.com", password="x"
        )
        coach = CrushCoach.objects.create(user=coach_user, is_active=True)
        return PremiumMembership.objects.create(
            user=self.user, coach=coach, status="pending"
        )

    def _waitlist(self, *, selected):
        from crush_lu.models import CrushConnectWaitlist

        return CrushConnectWaitlist.objects.create(
            user=self.user, selected_as_tester=selected
        )

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_pending_member_in_beta_can_complete_signup(self):
        # premium_choose_coach lets a pending member past the beta funnel, so
        # the pricing page must link there too, not to the waitlist. Only a
        # selected tester may actually pay (_premium_purchase_refused).
        self._waitlist(selected=True)
        self._pending_membership()
        card = _premium_card(self.client.get("/en/membership/").content.decode())
        self.assertIn('href="/en/premium/coaches/"', card)
        self.assertIn("Complete your Premium signup", card)
        self.assertNotIn("invite-only", card)
        self.assertNotIn("/crush-connect/", card)
        response = self.client.get("/en/premium/coaches/")
        self.assertEqual(response.status_code, 200)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_pending_member_cta_translated(self):
        self._waitlist(selected=True)
        self._pending_membership()
        card = _premium_card(self.client.get("/de/membership/").content.decode())
        self.assertIn("Schließ deine Premium-Anmeldung ab", card)
        card = _premium_card(self.client.get("/fr/membership/").content.decode())
        self.assertIn("Finalisez votre inscription Premium", card)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_selected_tester_with_pending_request_sees_complete(self):
        self._waitlist(selected=True)
        self._pending_membership()
        links = _links(
            _premium_card(self.client.get("/en/membership/").content.decode())
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["href"], "/en/premium/coaches/")
        self.assertEqual(links[0]["text"], "Complete your Premium signup")
        self.assertIn("btn-crush-solid", links[0]["class"])

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_deselected_tester_pending_request_is_not_promised_completion(self):
        # A deselected tester's pending request is refused at checkout (403,
        # views_payments._premium_purchase_refused), so the pricing page must
        # not promise completion -- only a way to manage (cancel) the request.
        self._waitlist(selected=False)
        self._pending_membership()
        card = _premium_card(self.client.get("/en/membership/").content.decode())
        links = _links(card)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["href"], "/en/premium/coaches/")
        self.assertEqual(links[0]["text"], "Manage your Premium request")
        self.assertIn("btn-crush-outline", links[0]["class"])
        self.assertNotIn("Complete your Premium signup", card)
        self.assertIn("Premium is invite-only during the beta.", card)
        # The link lands on a page that still renders the cancel path.
        response = self.client.get("/en/premium/coaches/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'action="/en/premium/cancel/"')

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_pending_member_never_on_waitlist_is_not_promised_completion(self):
        self._pending_membership()
        card = _premium_card(self.client.get("/en/membership/").content.decode())
        self.assertIn("Manage your Premium request", card)
        self.assertNotIn("Complete your Premium signup", card)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_manage_request_cta_translated(self):
        self._waitlist(selected=False)
        self._pending_membership()
        card = _premium_card(self.client.get("/de/membership/").content.decode())
        self.assertIn("Verwalte deine Premium-Anfrage", card)
        card = _premium_card(self.client.get("/fr/membership/").content.decode())
        self.assertIn("Gérez votre demande Premium", card)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=False)
    def test_pending_member_without_beta_sees_complete(self):
        self._pending_membership()
        card = _premium_card(self.client.get("/en/membership/").content.decode())
        self.assertIn("Complete your Premium signup", card)
        self.assertNotIn("Manage your Premium request", card)

    def _paid_transaction(self, membership):
        from decimal import Decimal

        from crush_lu.models import PaymentTransaction

        return PaymentTransaction.objects.create(
            transaction_reference=f"CRUSH-PREM-{membership.pk}-paid01",
            provider=PaymentTransaction.Provider.SUMUP,
            amount=Decimal("10.00"),
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            user=self.user,
            premium_membership=membership,
        )

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_already_paid_pending_member_is_sent_to_support(self):
        # Payment captured but Premium never granted: checkout answers 409
        # (views_payments.create_sumup_premium_checkout) until staff reconcile,
        # so the pricing page must not offer completion -- only support.
        self._waitlist(selected=True)
        self._paid_transaction(self._pending_membership())
        card = _premium_card(self.client.get("/en/membership/").content.decode())
        links = _links(card)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["href"], "/en/support/")
        self.assertEqual(links[0]["text"], "Contact support about your Premium payment")
        self.assertNotIn("Complete your Premium signup", card)
        self.assertNotIn("Manage your Premium request", card)
        self.assertNotIn("/premium/coaches/", card)
        self.assertEqual(self.client.get("/en/support/").status_code, 200)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_already_paid_support_link_translated(self):
        self._waitlist(selected=True)
        self._paid_transaction(self._pending_membership())
        card = _premium_card(self.client.get("/de/membership/").content.decode())
        self.assertIn("Kontaktiere den Support zu deiner Premium-Zahlung", card)
        self.assertNotIn("Schließ deine Premium-Anmeldung ab", card)
        card = _premium_card(self.client.get("/fr/membership/").content.decode())
        self.assertIn("Contactez le support au sujet de votre paiement Premium", card)
        self.assertNotIn("Finalisez votre inscription Premium", card)

    @override_settings(PREMIUM_REDIRECTS_TO_BETA=True)
    def test_selected_beta_tester_without_request_sees_waitlist(self):
        from crush_lu.models import CrushConnectWaitlist

        CrushConnectWaitlist.objects.create(user=self.user, selected_as_tester=True)
        card = _premium_card(self.client.get("/en/membership/").content.decode())
        self.assertIn("Premium is invite-only during the beta.", card)
        self.assertIn('href="/en/crush-connect/"', card)
        self.assertNotIn("Complete your Premium signup", card)

    @override_settings(
        PREMIUM_REDIRECTS_TO_BETA=True,
        IOS_NATIVE_COMMERCE_ENABLED=False,
    )
    def test_native_app_hides_pending_cta(self):
        self._pending_membership()
        html = self.client.get(
            "/en/membership/", HTTP_X_CRUSH_CLIENT="ios-app"
        ).content.decode()
        card = _premium_card(html)
        self.assertNotIn("/premium/coaches/", card)
        self.assertNotIn("Complete your Premium signup", card)

    @override_settings(
        PREMIUM_REDIRECTS_TO_BETA=True,
        IOS_NATIVE_COMMERCE_ENABLED=False,
        ANDROID_NATIVE_COMMERCE_ENABLED=False,
    )
    def test_native_app_paid_member_still_sees_support_link(self):
        # #1099: a captured payment is not commerce. A native-shell member
        # whose Premium was paid but never granted must still reach support,
        # not the "Available outside the mobile app" dead end.
        self._waitlist(selected=True)
        self._paid_transaction(self._pending_membership())
        for header in ("ios-app", "android-app"):
            html = self.client.get(
                "/en/membership/", HTTP_X_CRUSH_CLIENT=header
            ).content.decode()
            card = _premium_card(html)
            links = _links(card)
            self.assertEqual(len(links), 1, header)
            self.assertEqual(links[0]["href"], "/en/support/")
            self.assertNotIn('data-testid="premium-outside-app"', card)
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
