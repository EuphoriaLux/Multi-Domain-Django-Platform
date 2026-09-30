"""UX Wave 4 · WP1 — advent calendar polish (findings 7-11, 7-12, 7-13, 7-15).

Server-side checks on the rendered advent pages and the shared stylesheet:
the door-open keyframes live in CSS (not in the <script>), QR-locked doors
read as locked and point at the scanner, every door state has an accessible
name, the teaser is readable, the QR FAB clears the tab bar and has a name,
decorative motion honours prefers-reduced-motion, door_default drops its
Bootstrap classes for Tailwind + the advent-red token, and advent pages are
theme-locked dark. JS behaviour is covered by
``test_ux_wave4_advent_playwright.py``.
"""

import re
from datetime import date, datetime, timezone as dt_timezone
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from crush_lu.models import (
    AdventCalendar,
    AdventDoor,
    AdventDoorContent,
    AdventProgress,
    CrushSiteConfig,
    JourneyConfiguration,
    SpecialUserExperience,
)
from crush_lu.models.profiles import UserDataConsent


class _TagTextCollector(HTMLParser):
    """Collect the raw text inside every <script> or <style> element."""

    def __init__(self, tag):
        super().__init__(convert_charrefs=False)
        self.tag = tag
        self.bodies = []
        self._inside = False

    def handle_starttag(self, tag, attrs):
        if tag == self.tag:
            self._inside = True
            self.bodies.append("")

    def handle_endtag(self, tag):
        if tag == self.tag:
            self._inside = False

    def handle_data(self, data):
        if self._inside:
            self.bodies[-1] += data


def _tag_bodies(html, tag):
    parser = _TagTextCollector(tag)
    parser.feed(html)
    parser.close()
    return parser.bodies


class _AccessibleName(HTMLParser):
    """Accessible name of the first <a> with `link_class`, per the ARIA rule
    that matters here: aria-label wins, else the text content minus
    aria-hidden subtrees."""

    VOID = {"br", "img", "input", "meta", "link", "hr", "source"}

    def __init__(self, link_class):
        super().__init__()
        self.link_class = link_class
        self.label = None
        self.text = ""
        self.found = False
        self._depth = 0  # element depth inside the link
        self._hidden_at = None  # depth where an aria-hidden subtree began

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self.found:
            classes = (attrs.get("class") or "").split()
            if tag == "a" and self.link_class in classes:
                self.found = True
                self._depth = 1
                self.label = attrs.get("aria-label")
            return
        if self._depth == 0 or tag in self.VOID:
            return
        self._depth += 1
        if self._hidden_at is None and attrs.get("aria-hidden") == "true":
            self._hidden_at = self._depth

    def handle_endtag(self, tag):
        if self._depth == 0 or tag in self.VOID:
            return
        if self._hidden_at == self._depth:
            self._hidden_at = None
        self._depth -= 1

    def handle_data(self, data):
        if self._depth and self._hidden_at is None:
            self.text += data

    def name(self):
        if self.label is not None:
            return self.label
        return " ".join(self.text.split())


def accessible_name(html, link_class):
    parser = _AccessibleName(link_class)
    parser.feed(html)
    parser.close()
    assert parser.found, f"no <a class={link_class}>"
    return parser.name()


class _ElementAttrs(HTMLParser):
    """Attributes (plus its tag name as ``_tag``) of every element carrying
    `css_class`."""

    def __init__(self, css_class):
        super().__init__()
        self.css_class = css_class
        self.matches = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.css_class in (attrs.get("class") or "").split():
            attrs["_tag"] = tag
            self.matches.append(attrs)


def elements_with_class(html, css_class):
    parser = _ElementAttrs(css_class)
    parser.feed(html)
    parser.close()
    return parser.matches


def enable_whatsapp():
    """Configure the global WhatsApp FAB and drop the 5-minute config cache."""
    from crush_lu.context_processors import _site_config_cache

    CrushSiteConfig.objects.update_or_create(
        pk=1, defaults={"whatsapp_number": "352621000000", "whatsapp_enabled": True}
    )
    _site_config_cache["config"] = None
    _site_config_cache["expires"] = 0


def reset_site_config_cache():
    from crush_lu.context_processors import _site_config_cache

    _site_config_cache["config"] = None
    _site_config_cache["expires"] = 0


User = get_user_model()

NOW = "crush_lu.models.advent.timezone.now"
DEC_5 = datetime(2024, 12, 5, 12, 0, tzinfo=dt_timezone.utc)
TAILWIND_INPUT = Path(settings.BASE_DIR) / "tailwind-src/crush_lu/tailwind-input.css"
# Journey rules live in their own stylesheet (WP11a css-weight), loaded by the
# journey pages and the two advent door pages that reuse them.
JOURNEY_CSS_SRC = Path(settings.BASE_DIR) / "tailwind-src/crush_lu/features/journey.css"


def make_advent_user(test):
    """Linked advent user with doors 1-7 (door 4 needs a QR scan).

    On 5 December: 1 is opened, 2/3/5 are available, 4 is QR-locked,
    6/7 are in the future.
    """
    test.user = User.objects.create_user(
        username="advent-wave4@example.com",
        email="advent-wave4@example.com",
        password="testpass123",
        first_name="Marie",
        last_name="Dupont",
    )
    UserDataConsent.objects.update_or_create(
        user=test.user, defaults={"crushlu_consent_given": True}
    )
    EmailAddress.objects.create(
        user=test.user, email=test.user.email, verified=True, primary=True
    )
    experience = SpecialUserExperience.objects.create(
        first_name="Marie",
        last_name="Dupont",
        linked_user=test.user,
        is_active=True,
    )
    journey = JourneyConfiguration.objects.create(
        special_experience=experience,
        journey_type="advent_calendar",
        is_active=True,
        journey_name="Marie's Advent Calendar",
    )
    test.calendar = AdventCalendar.objects.create(
        journey=journey,
        year=2024,
        start_date=date(2024, 12, 1),
        end_date=date(2024, 12, 24),
    )
    for number in range(1, 8):
        AdventDoor.objects.create(
            calendar=test.calendar,
            door_number=number,
            content_type="memory",
            qr_mode="required" if number == 4 else "none",
            teaser_text="A little something sweet" if number == 2 else "",
        )
    AdventProgress.objects.create(
        user=test.user, calendar=test.calendar, doors_opened=[1]
    )
    test.client.force_login(test.user)


def door_cell(html, number):
    """The markup of one grid door, up to the next door (or the FAB)."""
    grid = html.split("<!-- QR Scanner Button -->", 1)[0]
    for segment in grid.split('<div class="advent-door ')[1:]:
        if re.match(r'[^"]*"\s+data-door="%d"' % number, segment):
            return segment
    raise AssertionError(f"door {number} not rendered")


def style_rule(css, selector):
    """Body of the first `selector { ... }` rule in css."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"no rule for {selector}"
    return match.group(1)


class AdventCalendarPageTests(TestCase):
    def setUp(self):
        cache.clear()
        make_advent_user(self)

    def get_calendar(self, lang="en"):
        with patch(NOW, return_value=DEC_5):
            response = self.client.get(f"/{lang}/advent/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/advent/calendar.html")
        return response.content.decode()

    def test_keyframes_are_css_not_script(self):
        html = self.get_calendar()
        scripts = _tag_bodies(html, "script")
        self.assertTrue(scripts)
        for body in scripts:
            self.assertNotIn("@keyframes", body)
        styles = "".join(_tag_bodies(html, "style"))
        self.assertIn("@keyframes doorOpen", styles)

    def test_qr_locked_door_reads_as_locked_and_links_to_scanner(self):
        html = self.get_calendar()
        styles = "".join(_tag_bodies(html, "style"))
        rule = style_rule(styles, ".door-link.qr-required")
        self.assertNotIn("animation", rule)
        self.assertNotIn("cursor: pointer", rule)

        cell = door_cell(html, 4)
        self.assertIn("qr-locked", cell)
        self.assertIn('aria-label="Door 4, needs a QR scan to open"', cell)
        self.assertIn('aria-describedby="advent-qr-hint"', cell)

        hint = re.search(
            r'<p class="advent-qr-hint" id="advent-qr-hint">(.*?)</p>', html, re.S
        )
        self.assertIsNotNone(hint)
        self.assertIn("scan the QR code on your physical gift", hint.group(1))
        self.assertIn('href="/en/advent/qr-scanner/"', hint.group(1))

    def test_hint_hidden_without_qr_locked_doors(self):
        AdventDoor.objects.filter(door_number=4).update(qr_mode="none")
        html = self.get_calendar()
        self.assertNotIn("advent-qr-hint", html.split("<style")[0])

    def test_every_door_state_has_an_aria_label(self):
        html = self.get_calendar()
        expected = {
            1: "Door 1, opened",
            3: "Door 3, ready to open",
            4: "Door 4, needs a QR scan to open",
            6: "Door 6, opens December 6",
            7: "Door 7, opens December 7",
        }
        for number, label in expected.items():
            self.assertIn(f'aria-label="{label}"', door_cell(html, number))

    def test_available_door_teaser_is_part_of_its_accessible_name(self):
        # An aria-label would override the visible teaser for screen readers.
        html = self.get_calendar()
        name = accessible_name(door_cell(html, 2), "door-link")
        self.assertIn("Door 2, ready to open", name)
        self.assertIn("A little something sweet", name)
        # The bare door number / emoji are not repeated in the name.
        self.assertFalse(name.startswith("2"), name)
        self.assertEqual(
            accessible_name(door_cell(html, 3), "door-link"), "Door 3, ready to open"
        )

    def test_available_door_teaser_name_is_translated(self):
        html = self.get_calendar("fr")
        name = accessible_name(door_cell(html, 2), "door-link")
        self.assertIn("Porte 2", name)
        self.assertIn("A little something sweet", name)

    def test_missed_door_without_catch_up_is_not_announced_as_future(self):
        self.calendar.allow_catch_up = False
        self.calendar.save()
        html = self.get_calendar()
        self.assertIn('aria-label="Door 3, locked"', door_cell(html, 3))
        self.assertIn('aria-label="Door 6, opens December 6"', door_cell(html, 6))

    def test_aria_labels_are_translated(self):
        html = self.get_calendar("de")
        self.assertIn('aria-label="Türchen 6, öffnet sich am 6. Dezember"', html)
        self.assertIn('aria-label="Türchen 4, zum Öffnen QR-Code scannen"', html)
        html = self.get_calendar("fr")
        self.assertIn('aria-label="Porte 1, ouverte"', html)
        self.assertIn("Ouvrir le scanner QR", html)

    def test_teaser_is_readable(self):
        html = self.get_calendar()
        styles = "".join(_tag_bodies(html, "style"))
        size = re.search(
            r"font-size:\s*([\d.]+)rem", style_rule(styles, ".door-teaser")
        )
        self.assertIsNotNone(size)
        self.assertGreaterEqual(float(size.group(1)), 0.75)
        self.assertIn("A little something sweet", door_cell(html, 2))

    def test_qr_fab_has_a_name_and_clears_the_tab_bar(self):
        html = self.get_calendar()
        fab = re.search(r'<a [^>]*class="qr-scanner-btn"[^>]*>', html)
        self.assertIsNotNone(fab)
        self.assertIn('aria-label="Scan QR Code"', fab.group(0))
        self.assertRegex(
            html,
            r"\.qr-scanner-btn\s*\{\s*bottom:\s*calc\(var\(--bottom-nav-height\)"
            r"[^;]*env\(safe-area-inset-bottom",
        )

    def test_qr_fab_stacks_above_the_whatsapp_fab(self):
        reset_site_config_cache()
        self.addCleanup(reset_site_config_cache)
        enable_whatsapp()
        html = self.get_calendar()
        self.assertTrue(elements_with_class(html, "crush-whatsapp-btn"))
        styles = "".join(_tag_bodies(html, "style"))
        selector = "body:has(.crush-whatsapp-btn) .qr-scanner-btn"
        rules = re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", styles)
        # Desktop and below-lg offsets both clear the 56px WhatsApp FAB.
        self.assertEqual(len(rules), 2, rules)
        self.assertIn("1.5rem + 56px", rules[0])
        self.assertIn("4.5rem + 56px", rules[1])
        for rule in rules:
            self.assertIn("safe-area-inset-bottom", rule)

    def test_decorative_motion_honours_reduced_motion(self):
        html = self.get_calendar()
        styles = "".join(_tag_bodies(html, "style"))
        sway = re.search(r"@keyframes sway\s*\{(.*?)\n    \}", styles, re.S)
        self.assertIsNotNone(sway)
        self.assertNotIn("margin-left", sway.group(1))
        self.assertIn("translate:", sway.group(1))
        reduced = re.findall(
            r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n    \}", styles, re.S
        )
        joined = "".join(reduced)
        self.assertIn(".snowflakes", joined)
        self.assertIn(".door-glow", joined)
        scripts = "".join(_tag_bodies(html, "script"))
        self.assertIn("matchMedia('(prefers-reduced-motion: reduce)')", scripts)

    def test_advent_pages_are_theme_locked_dark(self):
        html = self.get_calendar()
        html_tag = re.search(r"<html[^>]*>", html).group(0)
        self.assertIn('class="dark"', html_tag)
        self.assertIn('data-theme-lock="dark"', html_tag)

    def test_advent_red_is_a_token_not_a_hex(self):
        html = self.get_calendar()
        styles = "".join(_tag_bodies(html, "style"))
        self.assertNotIn("#c41e3a", styles.lower())
        self.assertNotIn("196, 30, 58", styles)
        self.assertIn("var(--color-advent-red)", styles)


class AdventDoorPageTests(TestCase):
    def setUp(self):
        cache.clear()
        make_advent_user(self)

    def get_door(self, number=2):
        with patch(NOW, return_value=DEC_5):
            response = self.client.get(
                f"/en/advent/door/{number}/", HTTP_HOST="crush.lu"
            )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/advent/door_default.html")
        return response.content.decode()

    def test_door_default_uses_tailwind_not_bootstrap(self):
        html = self.get_door()
        body = html.split('class="door-content-card"', 1)[1].split("<style", 1)[0]
        for legacy in (
            "bg-danger",
            "fs-4",
            "img-fluid",
            "text-success",
            "w-100",
            'text-muted"',
            "py-5",
        ):
            self.assertNotIn(legacy, body)
        self.assertIn("bg-advent-red", body)
        self.assertIn("text-muted-fg", body)

    def test_bonus_hint_pulse_is_guarded(self):
        AdventDoor.objects.filter(door_number=2).update(qr_mode="bonus")
        html = self.get_door()
        self.assertIn("bonus-hint", html)
        self.assertRegex(
            html,
            r"@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.bonus-hint\s*\{"
            r"\s*animation:\s*none",
        )


class AdventGiftDoorPageTests(TestCase):
    def setUp(self):
        cache.clear()
        make_advent_user(self)
        door = AdventDoor.objects.get(calendar=self.calendar, door_number=2)
        door.content_type = "gift_teaser"
        door.qr_mode = "bonus"
        door.save()
        AdventDoorContent.objects.create(
            door=door, title="A small parcel", bonus_title="You found it"
        )

    def test_bonus_hint_has_a_dark_treatment(self):
        with patch(NOW, return_value=DEC_5):
            response = self.client.get("/en/advent/door/2/", HTTP_HOST="crush.lu")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crush_lu/advent/door_gift.html")
        html = response.content.decode()
        hints = elements_with_class(html, "bonus-hint")
        self.assertEqual(len(hints), 1)
        hint = hints[0]
        # No pale hardcoded background that the forced dark theme can't reach.
        self.assertNotIn("#fff3cd", (hint.get("style") or "").lower())
        classes = hint["class"].split()
        self.assertTrue(any(c.startswith("dark:bg-") for c in classes), classes)
        # Bootstrap's text-muted stayed mid-grey on the dark surface; the hint
        # copy and heading now carry explicit dark-mode text colours.
        self.assertFalse(elements_with_class(html, "text-muted"))
        heading = elements_with_class(html, "dark:text-yellow-200")
        copy = elements_with_class(html, "dark:text-gray-200")
        self.assertIn("h5", [a["_tag"] for a in heading])
        self.assertIn("p", [a["_tag"] for a in copy])


class SharedStylesheetTests(TestCase):
    def test_advent_red_token_defined_in_theme(self):
        css = TAILWIND_INPUT.read_text(encoding="utf-8")
        theme = re.search(r"@theme \{(.*?)\n\}", css, re.S).group(1)
        self.assertIn("--color-advent-red: #c41e3a;", theme)

    def test_journey_hearts_honour_reduced_motion_and_small_screens(self):
        css = JOURNEY_CSS_SRC.read_text(encoding="utf-8")
        blocks = re.findall(
            r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\s*\}", css, re.S
        )
        self.assertTrue(any(".journey-hearts .heart-bg" in b for b in blocks))
        self.assertRegex(
            css,
            r"@media \(max-width: 480px\) \{\s*\.journey-hearts \.heart-bg:nth-child",
        )
