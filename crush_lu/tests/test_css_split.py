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


def _has_rule(css, selector):
    """True when ``selector`` ends the subject of some rule in ``css``.

    After the selector only pseudo-classes or pseudo-elements may follow
    before the ``{`` or ``,`` that closes the selector. So
    ``.htmx-request .spinner`` is not a rule for ``.htmx-request``, and
    ``.timeline-item.sortable-ghost`` is not one for ``.timeline-item``
    (it is one for ``.sortable-ghost``, which is the class that rule styles).
    """
    pattern = re.escape(selector) + r"(?::{1,2}[\w-]+(?:\([^(){}]*\))?)*\s*[{,]"
    return re.search(pattern, css) is not None


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

    def test_journey_gift_upload_and_certificate_rules_left_the_global_bundle(self):
        # WP6 css-slice-2 (#1149): the rest of the journey, gift wizard,
        # gift landing, file upload and certificate rules.
        base = (CSS_DIR / "tailwind.css").read_text(encoding="utf-8")
        journey = (CSS_DIR / "journey.css").read_text(encoding="utf-8")
        moved = (
            ".journey-btn-primary",
            ".journey-selector-card",
            ".journey-mc-option-card",
            ".timeline-item",
            ".reveal-puzzle-piece",
            ".gift-card",
            ".btn-gift",
            ".gift-input",
            ".step-indicator",
            ".gift-landing .hero-recipient",
            ".file-upload-wrapper",
            ".file-upload-preview",
            ".certificate",
            ".certificate-confetti",
            ".nav-btn",
            "@keyframes giftFadeInUp",
        )
        # Any mention counts here (not only a rule whose subject it is), so a
        # leftover descendant or compound rule is caught too.
        in_base = [
            sel for sel in moved if re.search(re.escape(sel) + r"(?![\w-])", base)
        ]
        not_in_journey = [sel for sel in moved if not _has_rule(journey, sel)]
        self.assertEqual(in_base, [])
        self.assertEqual(not_in_journey, [])

    def test_shared_and_order_guarded_rules_stay_in_the_global_bundle(self):
        base = (CSS_DIR / "tailwind.css").read_text(encoding="utf-8")
        kept = (
            # Shared by the profile editors, not gift-only.
            ".photo-upload-card",
            # Global nav link and the global contrast floor rules.
            ".nav-link.special-journey",
            ".journey-icon",
            # Kept because a later global rule targets the same class
            # (.feature-card / .cta-section); moving them would flip the
            # cascade order.
            ".gift-landing .feature-card",
            ".gift-landing .cta-section",
            # Used by the coach progress page too.
            "html.dark .stat-label",
        )
        self.assertEqual([sel for sel in kept if not _has_rule(base, sel)], [])

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

    JOURNEY_ONLY_PREFIXES = (
        "journey-",
        "gift-",
        "btn-gift",
        "certificate",
        "file-upload-",
        "reveal-",
        "timeline-",
        "step-indicator",
        "share-btn",
        "claim-form",
        "nav-btn",
    )

    def _journey_only_classes(self):
        base = (CSS_DIR / "tailwind.css").read_text(encoding="utf-8")
        journey = (CSS_DIR / "journey.css").read_text(encoding="utf-8")
        names = re.compile(r"\.(-?[A-Za-z_][\w-]*)")
        return {
            c
            for c in set(names.findall(journey)) - set(names.findall(base))
            if c.startswith(self.JOURNEY_ONLY_PREFIXES)
        }

    def _template_graph(self):
        """Template texts by name, plus a function telling if one links journey.css."""
        texts, parent = {}, {}
        for path in self.TEMPLATES.rglob("*.html"):
            name = "crush_lu/" + str(path.relative_to(self.TEMPLATES)).replace(
                "\\", "/"
            )
            texts[name] = path.read_text(encoding="utf-8")
            m = re.search(r"{%\s*extends\s+[\"']([^\"']+)[\"']", texts[name])
            parent[name] = m.group(1) if m else None

        def links(name, seen=()):
            # The page itself, a parent, or (for a partial) every includer.
            chain = name
            while chain:
                if "css/journey.css" in texts.get(chain, ""):
                    return True
                chain = parent.get(chain)
            if parent.get(name) is None:
                includers = [
                    n
                    for n, t in texts.items()
                    if re.search(r"{%\s*include\s+[\"']" + re.escape(name), t)
                ]
                return bool(includers) and all(
                    n not in seen and links(n, seen + (name,)) for n in includers
                )
            return False

        return texts, links

    def test_pages_using_journey_only_classes_link_journey_css(self):
        """A class whose only rule is in journey.css needs journey.css.

        Scans literal ``class="..."`` attributes and the quoted class names in
        Alpine ``:class`` / ``x-bind:class`` bindings. Classes that Python form
        widgets and journey.js add are covered by the two tests below.
        """
        journey_only = self._journey_only_classes()
        self.assertIn("certificate", journey_only)
        self.assertIn("file-upload-wrapper", journey_only)
        texts, links = self._template_graph()

        offenders = []
        for name, text in texts.items():
            used = set()
            for m in re.finditer(r'(?<![\w:-])class="([^"]*)"', text):
                used |= set(re.sub(r"{[{%].*?[%}]}", " ", m.group(1)).split())
            for m in re.finditer(r'(?:x-bind)?:class="([^"]*)"', text):
                for quoted in re.findall(r"'([^']*)'", m.group(1)):
                    used |= set(quoted.split())
            hits = sorted(used & journey_only)
            if hits and not links(name):
                offenders.append(f"{name}: {hits[:3]}")
        self.assertEqual(offenders, [])

    def test_pages_loading_the_journey_bundle_link_journey_css(self):
        """journey.js adds journey-only classes at runtime (toasts, counters,
        slideshow dots), so every page that loads it needs journey.css."""
        texts, links = self._template_graph()
        bundle = re.compile(r"alpine_bundle\.html[\"']\s+with\s+bundle=[\"']journey")
        loaders = [name for name, text in texts.items() if bundle.search(text)]
        self.assertTrue(loaders)
        self.assertEqual([name for name in loaders if not links(name)], [])

    def test_forms_with_journey_only_widget_classes_render_on_journey_pages(self):
        """Form widgets set classes such as gift-input in Python, so the
        templates that render those forms must link journey.css."""
        journey_only = self._journey_only_classes()
        texts, links = self._template_graph()
        root = Path(settings.BASE_DIR) / "crush_lu"
        forms = set()
        for path in root.glob("forms*.py"):
            text = path.read_text(encoding="utf-8")
            for block in re.split(r"\n(?=class \w+\()", text):
                m = re.match(r"class (\w+)\(", block)
                widget_classes = {
                    c
                    for value in re.findall(r"[\"']class[\"']:\s*[\"']([^\"']*)", block)
                    for c in value.split()
                }
                if m and widget_classes & journey_only:
                    forms.add(m.group(1))
        self.assertIn("JourneyGiftForm", forms)

        offenders = []
        for path in root.glob("views*.py"):
            text = path.read_text(encoding="utf-8")
            if not any(re.search(rf"\b{f}\b", text) for f in forms):
                continue
            for name in re.findall(r"[\"'](crush_lu/[\w/]+\.html)[\"']", text):
                if name in texts and not links(name):
                    offenders.append(f"{path.name}: {name}")
        self.assertEqual(offenders, [])

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
                    if not _has_rule(css, selector):
                        missing.append(f"{name} ({library}): {selector}")
        # Every library is found on at least one page, so the scan is live.
        self.assertEqual(checked, {lib for lib, _m, _s in self.LIBRARIES})
        self.assertEqual(missing, [])


def _layers_by_selector(css):
    """Map each selector (outside @media/@supports) to the @layer(s) holding it.

    A small brace-depth walk over the minified build: ``""`` means unlayered.
    Escapes and quoted strings are skipped so ``content:"{"`` or Tailwind's
    escaped class names cannot unbalance the depth count.
    """
    found = {}
    stack = []
    head = ""
    i = 0
    while i < len(css):
        ch = css[i]
        if ch == "\\":
            head += css[i : i + 2]
            i += 2
            continue
        if ch in "\"'":
            end = i + 1
            while css[end] != ch:
                end += 2 if css[end] == "\\" else 1
            head += css[i : end + 1]
            i = end + 1
            continue
        if ch == "{":
            prelude = head.strip()
            if not prelude.startswith("@") and not any(
                not p.startswith("@layer") for p in stack
            ):
                layer = "/".join(p.split(None, 1)[1] for p in stack if " " in p)
                depth = 0
                part = ""
                for c in prelude + ",":
                    depth += c in "([" and 1 or (c in ")]" and -1 or 0)
                    if c == "," and depth == 0:
                        found.setdefault(part.strip(), set()).add(layer)
                        part = ""
                    else:
                        part += c
            stack.append(prelude)
            head = ""
        elif ch == "}":
            if stack:
                stack.pop()
            head = ""
        elif ch == ";":
            head = ""
        else:
            head += ch
        i += 1
    return found


class MovedRulesKeepTheirCascadeLayerTests(TestCase):
    """WP6 css-slice-2 (#1149): moving a rule must not change its @layer.

    Cascade layers decide precedence before specificity, so a rule that moves
    between files must sit in the same layer it had in ``tailwind.css``. The
    journey component rules came from ``@layer components``; the gift wizard,
    gift landing, upload and certificate rules were unlayered on main and must
    stay unlayered. ``.gift-landing .hero-title`` is the sharpest case:
    base.html inlines an unlayered critical ``.hero-title`` rule (Fraunces,
    clamp(2.75rem, 6.5vw, 5rem), weight 600, line-height 1.05). Only an
    unlayered ``.gift-landing .hero-title`` beats it; put it in any layer and
    the gift heading silently takes the marketing hero's typography.
    """

    COMPONENTS = (
        ".journey-btn-primary",
        ".journey-title-xl",
        ".journey-input",
        ".journey-selector-card",
        ".journey-mc-option-card",
        ".journey-wyr-option-card",
        ".journey-chapter-icon",
        ".timeline-move",
        ".reveal-puzzle-piece",
        ".reveal-complete-message",
        "body.journey-theme-starlit_sky",
    )
    UNLAYERED = (
        ".gift-landing .hero-title",
        ".gift-landing .hero-recipient",
        ".gift-card",
        ".btn-gift",
        ".gift-input",
        ".step-indicator",
        ".file-upload-wrapper",
        ".file-upload-preview",
        ".certificate",
        ".certificate-confetti",
        ".nav-btn",
    )
    # Defined twice on main: once in @layer components, once unlayered.
    BOTH = (".char-counter", ".timeline-item")

    def setUp(self):
        self.journey = _layers_by_selector(
            (CSS_DIR / "journey.css").read_text(encoding="utf-8")
        )

    def test_journey_component_rules_stay_in_the_components_layer(self):
        wrong = {
            sel: self.journey.get(sel)
            for sel in self.COMPONENTS
            if self.journey.get(sel) != {"components"}
        }
        self.assertEqual(wrong, {})

    def test_rules_defined_in_both_places_keep_both_layers(self):
        wrong = {
            sel: self.journey.get(sel)
            for sel in self.BOTH
            if self.journey.get(sel) != {"", "components"}
        }
        self.assertEqual(wrong, {})

    def test_gift_upload_and_certificate_rules_stay_unlayered(self):
        wrong = {
            sel: self.journey.get(sel)
            for sel in self.UNLAYERED
            if self.journey.get(sel) != {""}
        }
        self.assertEqual(wrong, {})

    def test_feature_bundle_uses_no_layer_the_moved_rules_never_had(self):
        # Nothing moved out of @layer base or @layer utilities, so a rule in
        # either layer of journey.css would be a layer change.
        layers = set().union(*self.journey.values())
        self.assertLessEqual(layers, {"", "components"})

    def test_gift_hero_title_still_beats_the_critical_inline_rule(self):
        base_html = (
            Path(settings.BASE_DIR)
            / "crush_lu"
            / "templates"
            / "crush_lu"
            / "base.html"
        ).read_text(encoding="utf-8")
        # The critical rule sits unlayered in an inline <style>...
        style = re.search(r"<style>(.*?)</style>", base_html, re.S).group(1)
        style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
        self.assertIn("\n        .hero-title{", style)
        self.assertIsNone(re.search(r"@layer[^;{]*\{", style))
        # ...so the gift override must be unlayered too.
        self.assertEqual(self.journey.get(".gift-landing .hero-title"), {""})
