"""Trust signals: press strip on home, imprint in the footer, no stale stats.

UX Wave 2, finding 1-03.
"""

import re
from html.parser import HTMLParser

from django.core.cache import cache
from django.test import TestCase

IMPRINT_ADDRESS = "10 rue des Bons Malades, L-6462 Echternach"


class _AnchorCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []  # [(href, text)]
        self.ids = set()
        self._stack = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a":
            self._stack.append([attrs.get("href", ""), ""])

    def handle_data(self, data):
        for entry in self._stack:
            entry[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._stack:
            href, text = self._stack.pop()
            self.anchors.append((href, " ".join(text.split())))


def _parse(html):
    parser = _AnchorCollector()
    parser.feed(html)
    return parser


def _footer(html):
    match = re.search(r"<footer\b.*?</footer>", html, re.S)
    assert match, "page has no footer"
    return match.group(0)


class WhoRunsThisTests(TestCase):
    def setUp(self):
        cache.clear()

    def _get(self, path):
        response = self.client.get(path, HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_home_press_strip_links_to_about_media_section(self):
        html = self._get("/en/")
        strip = [
            (href, text)
            for href, text in _parse(html).anchors
            if text.startswith("As seen in")
        ]
        self.assertEqual(len(strip), 1, strip)
        href, text = strip[0]
        self.assertEqual(href, "/en/about/#media")
        for outlet in ("Virgule", "LuxTimes", "RTL Today"):
            self.assertIn(outlet, text)

    def test_home_press_strip_is_translated(self):
        html = self._get("/de/")
        hrefs = [href for href, text in _parse(html).anchors if "Bekannt aus" in text]
        self.assertEqual(hrefs, ["/de/about/#media"])

        html = self._get("/fr/")
        hrefs = [href for href, text in _parse(html).anchors if "Vu dans" in text]
        self.assertEqual(hrefs, ["/fr/about/#media"])

    def test_every_strip_outlet_is_cited_on_about(self):
        about = self._get("/en/about/")
        for outlet in ("Virgule", "LuxTimes", "RTL Today"):
            self.assertIn(f'<div class="press-publication">{outlet}', about)

    def test_about_has_media_anchor(self):
        self.assertIn("media", _parse(self._get("/en/about/")).ids)

    def test_footer_shows_imprint(self):
        footer = _footer(self._get("/en/about/"))
        self.assertIn("Crush.lu (en cours de constitution)", footer)
        self.assertIn(IMPRINT_ADDRESS, footer)

    def test_footer_imprint_reuses_privacy_policy_translation(self):
        footer = _footer(self._get("/de/about/"))
        self.assertIn("Crush.lu (in Gründung)", footer)
        self.assertIn(f"{IMPRINT_ADDRESS}, Luxemburg", footer)

        footer = _footer(self._get("/fr/about/"))
        self.assertIn("Crush.lu (en cours de constitution)", footer)
        self.assertIn(f"{IMPRINT_ADDRESS}, Luxembourg", footer)

    def test_about_drops_hardcoded_member_count(self):
        about = self._get("/en/about/")
        self.assertNotIn("100+", about)
        self.assertNotIn("Verified Members", about)
