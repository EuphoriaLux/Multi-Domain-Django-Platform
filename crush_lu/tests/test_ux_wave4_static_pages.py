"""UX Wave 4 · WP7a: static pages (How It Works, legal pages, 404/500) and
decision G legal copy.

Covers:
- 1-07: the How It Works timeline connector stays inside its grid.
- 1-08: the legal pages share the scoped ``.legal-prose`` style, keep a
  h3 -> h4 outline, expose keyboard-focusable table regions and offer a
  "Back to contents" link.
- 1-16: the standalone 500 page carries EN/DE/FR lines, a support contact and
  a dark palette; the 404 page has no nested <main> and links to support.
- Decision G: no photo-blur wording, "Crush.lu account" instead of "PowerUp
  Account", the cancellation email follows the credit settings, and the meta
  descriptions say "coach-hosted" instead of "coach-curated".
"""

import re
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.utils import timezone

from crush_lu.email_helpers import send_event_cancellation_confirmation
from crush_lu.models import CrushProfile, MeetupEvent

HOST = {"HTTP_HOST": "crush.lu"}
TAILWIND_INPUT = (
    Path(settings.BASE_DIR) / "tailwind-src" / "crush_lu" / "tailwind-input.css"
)
FEATURE_SRC = Path(settings.BASE_DIR) / "tailwind-src" / "crush_lu" / "features"


def _css_src():
    """Main input plus the per-feature sheets split out of it (WP11a)."""
    parts = [TAILWIND_INPUT.read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in sorted(FEATURE_SRC.glob("*.css"))]
    return "\n".join(parts)


class _Collector(HTMLParser):
    """Collects start tags with their attributes, plus the <head> text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def of(self, tag):
        return [attrs for name, attrs in self.tags if name == tag]


def _parse(html):
    parser = _Collector()
    parser.feed(html)
    return parser


def _meta_description(html):
    for attrs in _parse(html).of("meta"):
        if attrs.get("name") == "description":
            return attrs.get("content", "")
    return ""


class HowItWorksTimelineTests(TestCase):
    """1-07: the connector is sized from the grid, never 100% past its start."""

    def test_timeline_connector_is_bounded_to_the_grid(self):
        css = _css_src()
        start = css.index(".step-timeline::after")
        rule = css[start : css.index("}", start)]
        self.assertNotIn("width: 100%", rule)
        # Half a column: (100% - 3 gaps) / 8, gap = md:gap-6 (1.5rem).
        self.assertIn("left: calc((100% - 3 * 1.5rem) / 8)", rule)
        self.assertIn("right: calc((100% - 3 * 1.5rem) / 8)", rule)
        # Vertically anchored to the icon centre: card padding + sm:p-6 +
        # half the 80px icon (WP14).
        self.assertIn("top: calc(var(--space-8) + 1.5rem + 40px)", rule)
        # Only the single-row desktop grid (lg: 4 columns) gets the line; at
        # md the grid is 2x2 and a horizontal line would cut between rows.
        media = css.rfind("@media", 0, start)
        self.assertIn("min-width: 1024px", css[media : media + 40])


class LegalPagesStructureTests(TestCase):
    """1-08: shared legal-prose rhythm, outline, regions, back link."""

    def setUp(self):
        cache.clear()

    def _get(self, path):
        response = self.client.get(path, **HOST)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_privacy_and_terms_share_legal_prose_and_outline(self):
        for path in ("/en/privacy-policy/", "/en/terms-of-service/"):
            html = self._get(path)
            parsed = _parse(html)
            classes = " ".join(a.get("class", "") for _, a in parsed.tags)
            self.assertIn("legal-prose", classes, path)
            # Subsections are h4 under the h3 sections, never an h3 -> h5 jump.
            self.assertEqual(parsed.of("h5"), [], path)
            self.assertGreater(len(parsed.of("h4")), 10, path)
            # The table of contents is an anchor target, and the page offers a
            # way back to it.
            navs = [a for a in parsed.of("nav") if a.get("id") == "legal-contents"]
            self.assertEqual(len(navs), 1, path)
            back = [
                a
                for a in parsed.of("a")
                if a.get("href") == "#legal-contents"
                and "legal-back-to-contents" in a.get("class", "")
            ]
            self.assertEqual(len(back), 1, path)
            self.assertContains(
                self.client.get(path, **HOST), "Back to contents", count=1
            )

    def test_privacy_tables_are_focusable_labelled_regions(self):
        parsed = _parse(self._get("/en/privacy-policy/"))
        wrappers = [
            a for a in parsed.of("div") if "overflow-x-auto" in a.get("class", "")
        ]
        self.assertEqual(len(wrappers), len(parsed.of("table")))
        self.assertEqual(len(wrappers), 4)
        for attrs in wrappers:
            self.assertEqual(attrs.get("role"), "region")
            self.assertEqual(attrs.get("tabindex"), "0")
            self.assertTrue(attrs.get("aria-label"))

    def test_legal_prose_css_restores_bullets_and_underlines_links(self):
        css = _css_src()
        start = css.index("LEGAL PROSE")
        block = css[start : start + 3000]
        self.assertIn(".legal-prose ul:not(.list-none)", block)
        self.assertIn("list-disc", block)
        self.assertIn(".legal-prose :is(p, li, td) a", block)
        self.assertIn("underline", block)
        self.assertIn("dark:", block)


class LegalCopyDecisionGTests(TestCase):
    """Decision G (#1054/#1058): wording the product no longer supports."""

    def setUp(self):
        cache.clear()

    def test_no_blur_or_powerup_account_in_any_language(self):
        for lang in ("en", "de", "fr"):
            for page in ("privacy-policy", "terms-of-service"):
                path = f"/{lang}/{page}/"
                response = self.client.get(path, **HOST)
                self.assertEqual(response.status_code, 200, path)
                text = response.content.decode().lower()
                self.assertNotIn("powerup", text, path)
                # Photo-blur wording (the "blur" CSS utilities are fine).
                for phrase in (
                    "blur photos",
                    "photo blur",
                    "blur feature",
                    "weichzeich",
                    "flouter",
                    "flou de photo",
                    "fonctionnalité de flou",
                ):
                    self.assertNotIn(phrase, text, path)

    def test_crush_lu_account_wording(self):
        expected = {
            "en": "Crush.lu account",
            "de": "Crush.lu-Konto",
            "fr": "compte Crush.lu",
        }
        for lang, phrase in expected.items():
            for page in ("privacy-policy", "terms-of-service"):
                path = f"/{lang}/{page}/"
                self.assertContains(self.client.get(path, **HOST), phrase)

    def test_meta_descriptions_and_json_ld_say_coach_hosted(self):
        # /en/support/ has no meta_description block: base.html's default.
        for path in ("/en/events/", "/en/support/"):
            response = self.client.get(path, **HOST)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(
                "coach-hosted",
                _meta_description(response.content.decode()).lower(),
                path,
            )
        # home.html / how_it_works.html / about.html carry it in JSON-LD or
        # og:description.
        for path in (
            "/en/",
            "/en/about/",
            "/en/events/",
            "/en/how-it-works/",
            "/en/support/",
        ):
            response = self.client.get(path, **HOST)
            html = response.content.decode().lower()
            self.assertNotIn("coach-curated", html, path)
            self.assertIn("coach-hosted", html, path)

    def test_rtl_today_press_quote_is_kept_verbatim(self):
        response = self.client.get("/en/about/", **HOST)
        self.assertContains(response, "coach-verified profiles")


class CancellationEmailSettingsTests(TestCase):
    """#1052: the cancellation email reads the credit settings."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="wp7-cancel@example.com",
            email="wp7-cancel@example.com",
            password="pw",
            first_name="Ana",
        )
        start = timezone.now() + timedelta(hours=20)
        self.event = MeetupEvent.objects.create(
            title="WP7 Mixer",
            description="x",
            event_type="mixer",
            date_time=start,
            duration_minutes=120,
            location="Luxembourg",
            address="1 Test Street",
            max_participants=10,
            registration_deadline=start - timedelta(hours=1),
            is_published=True,
        )

    def _send(self):
        mail.outbox = []
        send_event_cancellation_confirmation(
            self.user, self.event, None, awaiting_resale=True
        )
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        html = message.alternatives[0][0] if message.alternatives else message.body
        return re.sub(r"\s+", " ", html)

    @override_settings(
        CRUSH_CREDIT_LATE_CANCELLATION_HOURS=24,
        CRUSH_CREDIT_RESALE_SHARE_PERCENT=40,
    )
    def test_email_uses_configured_window_and_share(self):
        html = self._send()
        self.assertIn("inside 24 hours", html)
        self.assertIn("add 40% of what you paid", html)
        self.assertNotIn("48 hours", html)

    @override_settings(
        CRUSH_CREDIT_LATE_CANCELLATION_HOURS=24,
        CRUSH_CREDIT_RESALE_SHARE_PERCENT=40,
    )
    def test_email_translation_uses_configured_values(self):
        CrushProfile.objects.create(
            user=self.user, date_of_birth="1995-01-01", preferred_language="de"
        )
        html = self._send()
        self.assertIn("innerhalb von 24 Stunden", html)
        self.assertIn("40 %", html)


class ErrorPagesTests(TestCase):
    """1-16: 404 inside base.html's <main>; standalone multilingual 500."""

    def setUp(self):
        cache.clear()

    def test_404_has_a_single_main_and_support_link(self):
        response = self.client.get("/en/this-page-does-not-exist-wp7/", **HOST)
        self.assertEqual(response.status_code, 404)
        parsed = _parse(response.content.decode())
        self.assertEqual(len(parsed.of("main")), 1)
        hrefs = [a.get("href") for a in parsed.of("a")]
        self.assertIn("/en/support/", hrefs)
        self.assertContains(response, "Contact support", status_code=404)
        buttons = [
            a.get("href")
            for a in parsed.of("a")
            if "btn-crush-primary" in a.get("class", "")
        ]
        self.assertEqual(buttons, ["/en/"])

    def test_500_is_standalone_multilingual_with_support_and_dark_mode(self):
        html = render_to_string("crush_lu/500.html")
        parsed = _parse(html)
        langs = {a.get("lang") for _, a in parsed.tags if a.get("lang")}
        self.assertTrue({"en", "de", "fr"} <= langs, langs)
        self.assertIn(
            "mailto:support@crush.lu", [a.get("href") for a in parsed.of("a")]
        )
        self.assertIn("Still stuck?", html)
        self.assertIn("prefers-color-scheme: dark", html)
        # Dependency-free: no stylesheet or script is loaded.
        self.assertEqual(parsed.of("link"), [])
        self.assertEqual(parsed.of("script"), [])


class LegalVersionBumpTests(TestCase):
    """Decision G is a clarification: bump the date and minor version."""

    def setUp(self):
        cache.clear()

    def test_privacy_and_terms_show_version_2_1(self):
        for path in ("/en/privacy-policy/", "/en/terms-of-service/"):
            response = self.client.get(path, **HOST)
            self.assertEqual(response.status_code, 200, path)
            self.assertContains(response, "Version 2.1")
            self.assertContains(response, "Last Updated: September 28, 2026")
            self.assertNotContains(response, "Version 2.0 ")

    def test_terms_consent_sentence_names_the_current_version(self):
        # Codex P1 on #1103: the Acceptance section still said 2.0.
        cases = {
            "en": "This version is 2.1.",
            "de": "Diese Version ist 2.1.",
            "fr": "Cette version est la 2.1.",
        }
        bodies = {}
        for lang in cases:
            response = self.client.get(f"/{lang}/terms-of-service/", **HOST)
            self.assertEqual(response.status_code, 200, lang)
            bodies[lang] = response.content.decode()
        for lang, sentence in cases.items():
            self.assertIn(sentence, bodies[lang], lang)
            self.assertNotIn(sentence.replace("2.1", "2.0"), bodies[lang], lang)
