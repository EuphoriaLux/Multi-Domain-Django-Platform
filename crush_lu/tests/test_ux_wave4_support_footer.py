"""UX Wave 4 · WP7b: Support page (1-10) and the members' compact footer
(decision H, #1055).

Covers:
- Support: card headings use the card-title scale under the h1; an FAQ
  accordion whose answers reuse published site copy; a "Report a safety
  concern" card (#safety) routing to support@crush.lu with no response-time
  promise; the WhatsApp card from the same SiteConfig switch as the footer.
- Footer: "Support" and "Child Safety" in the Legal list; logged-in members
  get a compact imprint + help/legal block that is not hidden on phones.
"""

from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase

from crush_lu import context_processors
from crush_lu.models import CrushSiteConfig
from crush_lu.tests.test_profile_edit_connect_card import _make_member

HOST = {"HTTP_HOST": "crush.lu"}
TAILWIND_INPUT = (
    Path(settings.BASE_DIR) / "tailwind-src" / "crush_lu" / "tailwind-input.css"
)


class _Page(HTMLParser):
    """Start tags with attributes, plus footer / compact-nav / #safety scope."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.footer_attrs = None
        self._in_footer = False
        self._in_compact = False
        self._safety_depth = 0
        self.safety_text = []
        self.compact_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "footer":
            self._in_footer = True
            self.footer_attrs = attrs
        if self._in_footer and tag == "nav" and "lg:hidden" in attrs.get("class", ""):
            self._in_compact = True
        if tag == "section" and attrs.get("id") == "safety":
            self._safety_depth = 1
        elif self._safety_depth and tag == "section":
            self._safety_depth += 1
        self.tags.append((tag, attrs, self._in_footer, self._in_compact))

    def handle_endtag(self, tag):
        if tag == "footer":
            self._in_footer = False
        if tag == "nav" and self._in_compact:
            self._in_compact = False
        if tag == "section" and self._safety_depth:
            self._safety_depth -= 1

    def handle_data(self, data):
        if self._safety_depth:
            self.safety_text.append(data)
        if self._in_compact:
            self.compact_text.append(data)

    def of(self, tag):
        return [a for t, a, _, _ in self.tags if t == tag]


def _parse(html):
    page = _Page()
    page.feed(html)
    return page


def _reset_site_config():
    context_processors._site_config_cache["config"] = None
    context_processors._site_config_cache["expires"] = 0


class SupportPageTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_site_config()

    def _get(self, path="/en/support/"):
        response = self.client.get(path, **HOST)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_card_headings_sit_below_the_h1(self):
        page = _parse(self._get())
        self.assertEqual(len(page.of("h1")), 1)
        h2s = page.of("h2")
        self.assertGreaterEqual(len(h2s), 5)
        for attrs in h2s:
            self.assertIn("card-title", attrs.get("class", ""))

    def test_faq_accordion_reuses_published_answers(self):
        html = self._get()
        page = _parse(html)
        self.assertEqual(len(page.of("details")), 5)
        self.assertEqual(len(page.of("summary")), 5)
        for question in (
            "How does Crush.lu work?",
            "How do I get verified?",
            "Is Crush.lu free to join?",
            "What happens at an event?",
            "How do I delete my account?",
        ):
            self.assertIn(question, html)
        # Answers are the existing site copy (how_it_works / home / data_deletion).
        self.assertIn("connect your LuxID — instant, no waiting.", html)
        self.assertIn("Creating a profile is free.", html)
        self.assertIn("Account deletion is permanent and cannot be undone.", html)

    def test_faq_is_translated(self):
        html = self._get("/de/support/")
        self.assertIn("Häufig gestellte Fragen", html)
        self.assertIn("Wie werde ich verifiziert?", html)
        self.assertIn("Sicherheitsproblem melden", html)
        html = self._get("/fr/support/")
        self.assertIn("Comment me faire vérifier ?", html)
        self.assertIn("Signaler un problème de sécurité", html)

    def test_safety_card_routes_to_support_without_response_time(self):
        page = _parse(self._get())
        sections = [a for a in page.of("section") if a.get("id") == "safety"]
        self.assertEqual(len(sections), 1)
        text = " ".join(page.safety_text)
        self.assertIn("Report a safety concern", text)
        for promise in ("hour", "within", "day", "respond", "reply"):
            self.assertNotIn(promise, text.lower())
        hrefs = [a.get("href") for a in page.of("a")]
        self.assertIn("mailto:support@crush.lu", hrefs)
        self.assertIn("/en/child-safety-standards/", hrefs)

    def test_whatsapp_card_follows_site_config(self):
        hrefs = [a.get("href", "") for a in _parse(self._get()).of("a")]
        self.assertFalse(any(h.startswith("https://wa.me/") for h in hrefs))

        CrushSiteConfig.objects.update_or_create(
            pk=1, defaults={"whatsapp_number": "352621000000", "whatsapp_enabled": True}
        )
        _reset_site_config()
        page = _parse(self._get())
        wa = [a for a in page.of("a") if a.get("href") == "https://wa.me/352621000000"]
        # The support card and the footer link (the floating button adds a
        # ?text= prefill, so its href differs).
        self.assertEqual(len(wa), 2)
        self.assertIn("Contact us on WhatsApp", self._get())
        self.assertTrue(all(a.get("rel") == "noopener noreferrer" for a in wa))


class FooterTests(TestCase):
    def setUp(self):
        cache.clear()
        _reset_site_config()

    def _footer_hrefs(self, page, compact=None):
        return [
            a.get("href")
            for t, a, in_footer, in_compact in page.tags
            if t == "a" and in_footer and (compact is None or in_compact == compact)
        ]

    def test_legal_list_has_support_and_child_safety(self):
        page = _parse(self.client.get("/en/about/", **HOST).content.decode())
        hrefs = self._footer_hrefs(page)
        self.assertIn("/en/support/", hrefs)
        self.assertIn("/en/child-safety-standards/", hrefs)
        # Guests have no compact block: the full footer shows on every width.
        self.assertEqual(self._footer_hrefs(page, compact=True), [])
        self.assertNotIn("site-footer-member", page.footer_attrs.get("class", ""))

    def test_members_get_compact_footer_on_phones(self):
        self.client.force_login(_make_member("wp7b-footer@example.com"))
        response = self.client.get("/en/support/", **HOST)
        self.assertEqual(response.status_code, 200)
        page = _parse(response.content.decode())
        footer_class = page.footer_attrs.get("class", "")
        # No longer hidden wholesale below lg (it was `max-lg:hidden`).
        self.assertNotIn("max-lg:hidden", footer_class)
        self.assertIn("site-footer-member", footer_class)
        self.assertEqual(
            self._footer_hrefs(page, compact=True),
            [
                "/en/support/",
                "/en/privacy-policy/",
                "/en/terms-of-service/",
                "/en/child-safety-standards/",
            ],
        )
        self.assertIn("10 rue des Bons Malades", " ".join(page.compact_text))

    def test_member_footer_clears_the_tab_bar(self):
        css = TAILWIND_INPUT.read_text(encoding="utf-8")
        start = css.index("body:has(.bottom-nav) .site-footer-member")
        rule = css[start : css.index("}", start)]
        self.assertIn("display: block", rule)
        self.assertIn("var(--bottom-nav-height)", rule)
        self.assertIn("safe-area-inset-bottom", rule)
