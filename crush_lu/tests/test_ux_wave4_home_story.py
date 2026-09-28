"""Home tells what an evening is like; the ghost-story carousel is gone.

UX Wave 4 · WP6, finding 1-09 and issue #1055 (About page age range).

  - A facts row directly under the hero states the event languages only.
  - A three-step "What an evening looks like" strip, illustrated with the
    existing ghost SVGs, replaces the six-slide "A Lonely Ghost" carousel.
  - The carousel's section partial, its controls in the ``ghostStory`` Alpine
    component and their CSS are removed. The compact story on
    /profile-submitted/ keeps its auto-play (same component).
  - About no longer shows a hardcoded "19-67" age range.
"""

from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import TestCase

BASE = Path(settings.BASE_DIR)
ALPINE_JS = BASE / "crush_lu/static/crush_lu/js/alpine/core.js"
TAILWIND_SRC = BASE / "tailwind-src/crush_lu/tailwind-input.css"


class _ClassScope(HTMLParser):
    """Collects text, <li> count and <svg> count inside the first element
    carrying ``css_class`` (void elements are not on the stack)."""

    VOID = {"br", "img", "input", "meta", "link", "hr", "path", "circle"}

    def __init__(self, css_class):
        super().__init__()
        self.css_class = css_class
        self.depth = 0  # >0 while inside the scope
        self.done = False
        self.found = False
        self.text = []
        self.hidden_text = []
        self.items = 0
        self.svgs = 0
        self.headings = []
        self._hidden_depth = 0
        self._heading = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.done or tag in self.VOID:
            return
        if not self.depth:
            if self.css_class in (attrs.get("class") or "").split():
                self.found = True
                self.depth = 1
            return
        self.depth += 1
        if self._hidden_depth:
            self._hidden_depth += 1
        elif attrs.get("aria-hidden") == "true":
            self._hidden_depth = 1
        if tag == "li":
            self.items += 1
        if tag == "svg":
            self.svgs += 1
        if tag in ("h2", "h3"):
            self._heading = [tag, ""]

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if not self.depth or tag in self.VOID:
            return
        if self._heading and tag == self._heading[0]:
            self.headings.append((tag, " ".join(self._heading[1].split())))
            self._heading = None
        if self._hidden_depth:
            self._hidden_depth -= 1
        self.depth -= 1
        if not self.depth:
            self.done = True

    def handle_data(self, data):
        if not self.depth:
            return
        (self.hidden_text if self._hidden_depth else self.text).append(data)
        if self._heading:
            self._heading[1] += data


def _scope(html, css_class):
    parser = _ClassScope(css_class)
    parser.feed(html)
    assert parser.found, f"no element with class {css_class!r}"
    return parser


def _text(parts):
    return " ".join(" ".join(parts).split())


class HomeStoryTests(TestCase):
    def setUp(self):
        cache.clear()

    def _get(self, path):
        response = self.client.get(path, HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # -- Facts row -----------------------------------------------------------

    def test_facts_row_states_event_languages_only(self):
        html = self._get("/en/")
        row = _scope(html, "home-facts-row")
        self.assertEqual(_text(row.hidden_text), "Events in EN · FR · DE · LU")
        self.assertEqual(
            _text(row.text), "Events in English, French, German and Luxembourgish"
        )
        # Languages only: no cities, ages, prices or counts.
        visible = _text(row.hidden_text + row.text)
        self.assertFalse(any(ch.isdigit() for ch in visible), visible)
        self.assertNotIn("€", visible)

    def test_facts_row_sits_directly_under_the_hero(self):
        html = self._get("/en/")
        hero_end = html.index("</section>", html.index('class="hero-section"'))
        facts = html.index("home-facts-row")
        features = html.index('class="features-section')
        self.assertLess(hero_end, facts)
        self.assertLess(facts, features)

    def test_facts_row_is_translated(self):
        de = _scope(self._get("/de/"), "home-facts-row")
        self.assertEqual(_text(de.hidden_text), "Events auf EN · FR · DE · LU")
        fr = _scope(self._get("/fr/"), "home-facts-row")
        self.assertEqual(_text(fr.hidden_text), "Événements en EN · FR · DE · LU")
        self.assertEqual(
            _text(fr.text),
            "Événements en anglais, français, allemand et luxembourgeois",
        )

    # -- Evening strip -------------------------------------------------------

    def test_evening_strip_has_three_illustrated_steps(self):
        strip = _scope(self._get("/en/"), "evening-strip")
        self.assertEqual(strip.items, 3)
        self.assertEqual(strip.svgs, 3)
        self.assertEqual(
            strip.headings,
            [
                ("h2", "What an evening looks like"),
                ("h3", "A coach welcomes you"),
                ("h3", "You meet face-to-face"),
                ("h3", "You choose who to see again"),
            ],
        )
        captions = _text(strip.text)
        self.assertIn(
            "Our coaches host every event and welcome you in person.", captions
        )
        self.assertIn("Safe, organized spaces for real conversations", captions)
        self.assertIn("After the event, connect with people you clicked with", captions)
        # No invented numbers or quotes.
        self.assertFalse(any(ch.isdigit() for ch in captions), captions)
        self.assertNotIn("“", captions)

    def test_evening_strip_is_translated(self):
        de = _scope(self._get("/de/"), "evening-strip")
        self.assertEqual(de.headings[0], ("h2", "So sieht ein Abend aus"))
        self.assertIn(
            "Unsere Coaches organisieren jedes Event und begrüßen dich persönlich.",
            _text(de.text),
        )
        fr = _scope(self._get("/fr/"), "evening-strip")
        self.assertEqual(fr.headings[0], ("h2", "À quoi ressemble une soirée"))
        self.assertIn(("h3", "Un coach vous accueille"), fr.headings)

    def test_evening_strip_follows_how_it_works(self):
        html = self._get("/en/")
        self.assertLess(
            html.index('id="how-it-works-heading"'), html.index('id="evening-heading"')
        )
        # How It Works keeps its own four steps.
        self.assertEqual(html.count('class="step-item"'), 4)

    # -- Carousel removed ----------------------------------------------------

    def test_home_has_no_ghost_story_carousel(self):
        html = self._get("/en/")
        self.assertNotIn("ghost-story-section", html)
        self.assertNotIn('x-data="ghostStory"', html)
        self.assertNotIn("A Lonely Ghost", html)
        self.assertNotIn("ghost-story-dot", html)

    def test_carousel_partial_is_deleted(self):
        with self.assertRaises(TemplateDoesNotExist):
            get_template("crush_lu/includes/ghost-story-section.html")
        # The compact story on /profile-submitted/ stays.
        get_template("crush_lu/includes/ghost-story-compact.html")

    def test_carousel_controls_removed_from_component_and_css(self):
        js = ALPINE_JS.read_text(encoding="utf-8")
        start = js.index('Alpine.data("ghostStory"')
        component = js[start : js.index("Alpine.data(", start + 1)]
        for member in ("goToScene0", "togglePause", "previousScene", "dot0Class"):
            self.assertNotIn(member, component)
        # Auto-play the compact story needs stays.
        for member in ("scene0Class", "startAutoAdvance", "nextScene"):
            self.assertIn(member, component)
        css = TAILWIND_SRC.read_text(encoding="utf-8")
        for selector in (
            ".ghost-story-section",
            ".ghost-story-nav-btn",
            ".ghost-story-dot",
            ".ghost-story-cta-btn",
        ):
            self.assertNotIn(selector, css)
        self.assertIn(".ghost-story-compact", css)

    # -- About (#1055) ---------------------------------------------------------

    def test_about_drops_hardcoded_age_range(self):
        html = self._get("/en/about/")
        self.assertNotIn("19-67", html)
        stats = _scope(html, "about-stats-section")
        self.assertIn("Ways to Verify", _text(stats.text))
        self.assertIn("Made in Luxembourg", _text(stats.text))
        self.assertNotIn("Age Range", _text(stats.text))

    def test_about_stat_grid_has_no_empty_column(self):
        html = self._get("/en/about/")
        self.assertEqual(html.count('class="about-stat-card"'), 2)
        css = TAILWIND_SRC.read_text(encoding="utf-8")
        rule = css.index(".about-stats-section {")
        self.assertNotIn(
            "repeat(3, 1fr)",
            css[rule : css.index("}", rule)],
        )
        # No wider-screen override re-introduces a third column.
        tail = css[rule:]
        while ".about-stats-section {" in tail:
            i = tail.index(".about-stats-section {")
            self.assertNotIn("repeat(3", tail[i : tail.index("}", i)])
            tail = tail[i + 1 :]
