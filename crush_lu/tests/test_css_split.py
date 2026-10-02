"""WP11a css-weight: page-only CSS ships in per-feature stylesheets.

``tailwind.css`` is loaded on every page, so the journey/gift/reward rules and
the public marketing-page rules live in ``journey.css`` / ``marketing.css`` and
are linked only by the pages that use them.
"""

import datetime
import re
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from crush_lu.models import AdventDoor, AdventDoorContent, JourneyGift
from crush_lu.tests.test_ux_wave4_advent import NOW, make_advent_user

HOST = {"HTTP_HOST": "crush.lu"}
CSS_DIR = Path(settings.BASE_DIR) / "crush_lu" / "static" / "crush_lu" / "css"
JOURNEY_LINK = "crush_lu/css/journey.css"
MARKETING_LINK = "crush_lu/css/marketing.css"


class FeatureStylesheetFilesTests(TestCase):
    def test_built_files_exist_and_split_the_rules(self):
        base = (CSS_DIR / "tailwind.css").read_text(encoding="utf-8")
        journey = (CSS_DIR / "journey.css").read_text(encoding="utf-8")
        marketing = (CSS_DIR / "marketing.css").read_text(encoding="utf-8")
        # Journey-only rules moved out of the global bundle.
        self.assertIn(".journey-container-content", journey)
        self.assertNotIn(".journey-container-content", base)
        self.assertIn(".gift-container", journey)
        self.assertNotIn(".gift-container", base)
        # Marketing-page rules moved out too.
        self.assertIn(".how-it-works-hero", marketing)
        self.assertNotIn(".how-it-works-hero", base)
        # The global bundle keeps genuinely shared component rules.
        self.assertIn(".btn-primary", base)

    def test_runtime_library_classes_stay_in_the_global_bundle(self):
        # HTMX adds these classes itself, so no template or script mentions
        # them; they looked like dead selectors but every hx-* swap uses them.
        base = (CSS_DIR / "tailwind.css").read_text(encoding="utf-8")
        self.assertIn(".htmx-swapping", base)
        self.assertIn(".htmx-settling", base)
        self.assertIn(".htmx-request", base)

    def test_feature_sources_reference_the_main_input(self):
        src = Path(settings.BASE_DIR) / "tailwind-src" / "crush_lu" / "features"
        for name in ("journey", "marketing"):
            text = (src / f"{name}.css").read_text(encoding="utf-8")
            self.assertIn('@reference "../tailwind-input.css";', text)


class FeatureStylesheetLinkTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_marketing_pages_link_marketing_css_only(self):
        for path in ("/en/about/", "/en/how-it-works/", "/en/crush-coach/"):
            html = self.client.get(path, **HOST).content.decode()
            self.assertIn(MARKETING_LINK, html, path)
            self.assertNotIn(JOURNEY_LINK, html, path)

    def test_home_links_neither_feature_stylesheet(self):
        html = self.client.get("/en/", **HOST).content.decode()
        self.assertNotIn(MARKETING_LINK, html)
        self.assertNotIn(JOURNEY_LINK, html)

    def test_gift_landing_links_journey_css(self):
        sender = get_user_model().objects.create_user(
            username="css-split-sender", email="s@example.com", password="x"
        )
        gift = JourneyGift.objects.create(
            sender=sender,
            recipient_name="Lea",
            sender_message="Hi",
            date_first_met=datetime.date(2025, 5, 5),
            location_first_met="Luxembourg",
        )
        html = self.client.get(
            f"/en/journey/gift/{gift.gift_code}/", **HOST
        ).content.decode()
        self.assertIn(JOURNEY_LINK, html)
        self.assertNotIn(MARKETING_LINK, html)

    def test_crush_connect_links_marketing_css_only(self):
        html = self.client.get("/en/crush-connect/", **HOST).content.decode()
        self.assertIn(MARKETING_LINK, html)
        self.assertNotIn(JOURNEY_LINK, html)

    def _advent_door_html(self, content_type, template):
        make_advent_user(self)
        door = AdventDoor.objects.get(calendar=self.calendar, door_number=2)
        door.content_type = content_type
        door.save()
        AdventDoorContent.objects.create(door=door, title="A small parcel")
        with patch(
            NOW,
            return_value=datetime.datetime(
                2024, 12, 5, 12, tzinfo=datetime.timezone.utc
            ),
        ):
            response = self.client.get("/en/advent/door/2/", **HOST)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, template)
        return response.content.decode()

    def test_advent_gift_door_links_journey_css(self):
        html = self._advent_door_html("gift_teaser", "crush_lu/advent/door_gift.html")
        self.assertIn(JOURNEY_LINK, html)

    def test_advent_poem_door_links_journey_css(self):
        html = self._advent_door_html("poem", "crush_lu/advent/door_poem.html")
        self.assertIn(JOURNEY_LINK, html)


class FeatureStylesheetTemplateTests(TestCase):
    """Static guard for pages that are costly to render in a test."""

    TEMPLATES = Path(settings.BASE_DIR) / "crush_lu" / "templates" / "crush_lu"

    def _read(self, rel):
        return (self.TEMPLATES / rel).read_text(encoding="utf-8")

    def test_templates_link_their_feature_stylesheet(self):
        expected = {
            "journey/journey_base.html": "journey",
            "journey/gift_base.html": "journey",
            "journey/journey_selector.html": "journey",
            "advent/door_poem.html": "journey",
            "advent/door_gift.html": "journey",
            "crush_connect.html": "marketing",
        }
        missing = [
            rel
            for rel, name in expected.items()
            if f"{{% static 'crush_lu/css/{name}.css' %}}" not in self._read(rel)
        ]
        self.assertEqual(missing, [])

    def test_journey_children_keep_the_inherited_stylesheet(self):
        # journey_base.html links journey.css in extra_css (and offers
        # extra_journey_css); a child overriding extra_css must call
        # block.super or it silently loses the stylesheet.
        offenders = []
        for rel in ("journey", "advent"):
            for path in (self.TEMPLATES / rel).rglob("*.html"):
                text = path.read_text(encoding="utf-8")
                for m in re.finditer(
                    r"{% block extra_css %}(.*?){% endblock %}", text, re.S
                ):
                    body = m.group(1)
                    if path.name in (
                        "journey_base.html",
                        "gift_base.html",
                        "advent_base.html",
                    ):
                        continue
                    if "block.super" not in body and "css/journey.css" not in body:
                        offenders.append(str(path.relative_to(self.TEMPLATES)))
        self.assertEqual(offenders, [])


class RuntimeLibraryClassTests(TestCase):
    """WP6 css-slice-2 (#1149): classes that a library adds at runtime.

    No template mentions these classes literally (HTMX, Alpine, SortableJS and
    intl-tel-input put them on the DOM themselves), so a dead-selector sweep
    sees them as unused. Every page that loads the library must also load a
    stylesheet that still has a rule for each of them.
    """

    TEMPLATES = Path(settings.BASE_DIR) / "crush_lu" / "templates"
    STATIC = Path(settings.BASE_DIR) / "crush_lu" / "static"

    # (library, script marker in a template, runtime selectors)
    LIBRARIES = (
        (
            "htmx",
            "js/vendor/htmx-",
            (".htmx-request", ".htmx-swapping", ".htmx-settling", ".htmx-indicator"),
        ),
        ("alpine", "alpinejs-csp-", ("[x-cloak]",)),
        (
            "sortable",
            "js/vendor/sortable-",
            (".sortable-ghost", ".sortable-chosen", ".sortable-drag"),
        ),
        (
            "intl-tel-input",
            "intlTelInput.min.js",
            (".iti", ".iti__selected-country", ".iti__country", ".iti__dial-code"),
        ),
    )

    @staticmethod
    def _has_rule(css, selector):
        # Not followed by a name character, so ".iti" is not satisfied by
        # ".iti__country" alone.
        return re.search(re.escape(selector) + r"(?![\w-])", css) is not None

    def _linked_css(self, name):
        """Concatenated local stylesheets linked by a template or its parents."""
        css = []
        seen = set()
        while name and name not in seen:
            seen.add(name)
            path = self.TEMPLATES / name
            if not path.exists():
                break
            text = path.read_text(encoding="utf-8")
            for rel in re.findall(r"{% static '(crush_lu/css/[^']+\.css)' %}", text):
                css.append((self.STATIC / rel).read_text(encoding="utf-8"))
            m = re.search(r"{%\s*extends\s+[\"']([^\"']+)[\"']", text)
            name = m.group(1) if m else None
        return "\n".join(css)

    def test_pages_loading_a_library_keep_its_runtime_rules(self):
        missing = []
        checked = set()
        for path in sorted((self.TEMPLATES / "crush_lu").rglob("*.html")):
            name = str(path.relative_to(self.TEMPLATES)).replace("\\", "/")
            text = path.read_text(encoding="utf-8")
            for library, marker, selectors in self.LIBRARIES:
                if marker not in text:
                    continue
                checked.add(library)
                css = self._linked_css(name)
                for selector in selectors:
                    if not self._has_rule(css, selector):
                        missing.append(f"{name} ({library}): {selector}")
        # Every library is found on at least one page, so the scan is live.
        self.assertEqual(checked, {lib for lib, _m, _s in self.LIBRARIES})
        self.assertEqual(missing, [])
