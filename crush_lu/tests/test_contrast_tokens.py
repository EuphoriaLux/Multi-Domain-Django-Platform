"""UX review Wave 2 · WP9: contrast tokens, one progress counter, dark journeys.

* 5-05 — secondary copy on the lavender surfaces uses the ``--text-muted``
  token (``text-muted-fg``) instead of ``text-gray-500``; the tab-bar labels
  are 11px and darker; ``.btn-crush-solid`` sits on crush-purple-dark.
* 3-03 — the navbar "Complete Profile" badge counts the same onboarding steps
  as the journey stepper (N/5), is hidden on pages that render the stepper,
  and its badge is no longer white on pink.
* 7-03 — advent pages render ``<html class="dark" data-theme-lock="dark">`` so
  the global chrome takes its dark variant, and the theme toggles explain why
  they are disabled. Journey and gift pages follow the member's theme again
  (UX Wave 5 · WP14).

The runtime (theme-manager.js, axe contrast) is covered by
``test_contrast_tokens_playwright.py``.
"""

import re
from datetime import date
from pathlib import Path

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from crush_lu.context_processors import crush_user_context
from crush_lu.models import CrushProfile, UserDataConsent
from crush_lu.tests.test_profile_edit_connect_card import _make_member

REPO_ROOT = Path(__file__).resolve().parents[2]
TAILWIND_INPUT = REPO_ROOT / "tailwind-src" / "crush_lu" / "tailwind-input.css"
HTML_TAG_RE = re.compile(r"<html\b[^>]*>")
LOCKED_LABEL = "This experience is always in night mode"


def _onboarding_user(email="onboard@example.com", **profile_fields):
    """A user at onboarding step 4 (build profile)."""
    user = get_user_model().objects.create_user(
        username=email, email=email, password="testpass123", first_name="Onb"
    )
    fields = dict(
        date_of_birth=date(1995, 5, 15),
        gender="M",
        location="canton-luxembourg",
        is_active=True,
        verification_status="incomplete",
        welcome_seen_at=timezone.now(),
        phone_verified=True,
        coach_intro_seen_at=timezone.now(),
    )
    fields.update(profile_fields)
    CrushProfile.objects.create(user=user, **fields)
    consent, _ = UserDataConsent.objects.get_or_create(user=user)
    consent.crushlu_consent_given = True
    consent.save()
    EmailAddress.objects.update_or_create(
        user=user, email=email, defaults={"verified": True, "primary": True}
    )
    return user


def _css_rule(css, selector):
    start = css.index("\n    " + selector + " {")
    return css[start : css.index("}", start)]


class NavProgressContextTests(TestCase):
    """3-03: the navbar counter is the onboarding step, not a 1-3 status map."""

    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def _context(self, user):
        request = self.factory.get("/en/events/")
        request.user = user
        return crush_user_context(request)

    def test_build_profile_step_matches_the_stepper(self):
        context = self._context(_onboarding_user())
        self.assertEqual(context["profile_completion_step"], 4)
        self.assertEqual(context["profile_completion_total"], 5)
        self.assertEqual(str(context["profile_step_label"]), "Build profile")

    def test_pending_profile_is_on_the_last_step(self):
        user = _onboarding_user(verification_status="pending")
        context = self._context(user)
        self.assertEqual(context["profile_completion_step"], 5)
        self.assertEqual(str(context["profile_step_label"]), "Get verified")

    def test_early_journey_steps_are_counted(self):
        # The old status map reported every incomplete profile as step 1.
        for step, fields in (
            (2, {"phone_verified": False}),
            (3, {"coach_intro_seen_at": None}),
        ):
            with self.subTest(step=step):
                user = _onboarding_user(f"step{step}@example.com", **fields)
                context = self._context(user)
                self.assertEqual(context["profile_completion_step"], step)
                # The bar fills with completed steps, like the stepper.
                self.assertEqual(
                    context["profile_completion_pct"], (step - 1) * 100 // 5
                )

    def test_bar_is_empty_before_anything_is_done(self):
        user = _onboarding_user(welcome_seen_at=None, phone_verified=False)
        context = self._context(user)
        self.assertEqual(context["profile_completion_step"], 1)
        self.assertEqual(context["profile_completion_pct"], 0)


class NavProgressMarkupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(_onboarding_user())

    def _get(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _dropdown(self, html):
        start = html.index('x-data="profileProgress"')
        return html[start : html.index("</button>", start)]

    def test_nav_badge_shows_the_journey_step_out_of_five(self):
        badge = self._dropdown(self._get("/en/events/"))
        self.assertRegex(badge, r">\s*4/5\s*</span>")
        # White on pink measured 2.67:1; purple-dark on white passes AA.
        self.assertNotIn("bg-crush-pink text-white", badge)
        self.assertIn("text-crush-purple-dark", badge)

    def test_continue_setup_resumes_the_current_step(self):
        html = self._get("/en/events/")
        start = html.index('x-data="profileProgress"')
        dropdown = html[start : html.index("Continue Setup", start)]
        self.assertIn('href="/en/onboarding/"', dropdown)
        self.assertIn("width: 60%", dropdown)

    def test_nav_badge_is_hidden_where_the_stepper_is_shown(self):
        html = self._get("/en/create-profile/")
        self.assertIn('aria-label="Onboarding progress"', html)
        self.assertNotIn('x-data="profileProgress"', html)

    def test_mobile_menu_progress_is_hidden_where_the_stepper_is_shown(self):
        # The hamburger menu's "Profile Progress" card is a second counter;
        # on stepper pages it can disagree with the stepper (welcome_view
        # saves welcome_seen_at before the context processor runs).
        self.assertIn("Profile Progress", self._get("/en/events/"))
        html = self._get("/en/create-profile/")
        self.assertIn('aria-label="Onboarding progress"', html)
        self.assertNotIn("Profile Progress", html)


class ThemeLockMarkupTests(TestCase):
    """7-03: only advent stays an explicit dark surface; journeys are themed."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        # Gift sender pages are staff/coach-only (UX Wave 4 decision C).
        self.client.force_login(_make_member("gifter@example.com", is_staff=True))

    def _html_tag(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        return HTML_TAG_RE.search(html).group(0), html

    def test_gift_pages_follow_the_user_theme(self):
        tags = [
            self._html_tag(path)[0]
            for path in ("/en/journey/gift/create/", "/en/journey/gifts/")
        ]
        for tag in tags:
            self.assertNotIn("data-theme-lock", tag)
            self.assertNotIn('class="dark"', tag)

    def test_regular_pages_follow_the_user_theme(self):
        tag, html = self._html_tag("/en/dashboard/")
        self.assertNotIn("data-theme-lock", tag)
        self.assertNotIn('class="dark"', tag)
        # The toggles (navbar x2, drawer theme choice) carry the explanation
        # for locked pages.
        self.assertEqual(html.count(f'data-locked-label="{LOCKED_LABEL}"'), 3)

    def test_locked_label_is_translated(self):
        for lang, label in (
            ("de", "Dieses Erlebnis ist immer im Nachtmodus"),
            ("fr", "Cette expérience est toujours en mode nuit"),
        ):
            with self.subTest(lang=lang):
                _, html = self._html_tag(f"/{lang}/dashboard/")
                self.assertIn(f'data-locked-label="{label}"', html)

    def test_journey_templates_do_not_lock_the_theme(self):
        base = REPO_ROOT / "crush_lu" / "templates" / "crush_lu" / "journey"
        for name in ("journey_base.html", "gift_base.html", "journey_selector.html"):
            with self.subTest(template=name):
                source = (base / name).read_text(encoding="utf-8")
                self.assertNotIn("theme_lock", source)
                self.assertIn("journey-bg", source)

    def test_journey_light_theme_is_light(self):
        css = TAILWIND_INPUT.read_text(encoding="utf-8") + "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted((TAILWIND_INPUT.parent / "features").glob("*.css"))
        )
        # Dark keeps the navy gradient; light gets its own page background.
        self.assertIn("body.journey-bg {", css)
        self.assertIn("html:not(.dark) body.journey-bg {", css)
        light = css.split("html:not(.dark) body.journey-bg {", 1)[1].split("}", 1)[0]
        self.assertNotIn("--color-journey-dark", light)


class MutedTextMarkupTests(TestCase):
    """5-05: secondary copy on the dashboard / connections uses the token."""

    def setUp(self):
        cache.clear()
        self.client = Client(HTTP_HOST="crush.lu")
        self.client.force_login(_make_member("muted@example.com"))

    def test_dashboard_and_connections_have_no_gray_500_copy(self):
        for path in ("/en/dashboard/", "/en/connections/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                main = response.content.decode()
                main = main[main.index('id="main-content"') :]
                main = main[: main.index("<footer")]
                self.assertIn("text-muted-fg", main)
                self.assertNotIn("text-gray-500 dark:text-gray-400", main)


class ContrastTokenCssTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.css = TAILWIND_INPUT.read_text(encoding="utf-8")

    def test_text_muted_token_is_gray_600_light_gray_400_dark(self):
        self.assertRegex(self.css, r":root \{[^}]*--text-muted: #4b5563;")
        self.assertRegex(self.css, r"html\.dark \{\s*--text-muted: #9ca3af;\s*\}")
        self.assertIn("--color-muted-fg: var(--text-muted);", self.css)

    def test_bottom_nav_labels_are_larger_and_darker(self):
        item = _css_rule(self.css, ".bottom-nav-item")
        self.assertIn("font-size: 11px;", item)
        self.assertIn("color: var(--text-muted);", item)
        self.assertNotIn("html.dark .bottom-nav-item {", self.css)
        active = _css_rule(self.css, ".bottom-nav-item-active")
        self.assertIn("color: var(--color-crush-purple-dark);", active)

    def test_btn_crush_solid_sits_on_purple_dark(self):
        rule = _css_rule(self.css, ".btn-crush-solid")
        self.assertIn("@apply bg-crush-purple-dark ", rule)
        self.assertNotIn(" bg-crush-purple ", rule)
