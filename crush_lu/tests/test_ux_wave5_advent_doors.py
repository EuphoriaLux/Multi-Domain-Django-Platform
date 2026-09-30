"""Wave 5 WP2: advent door templates use tokens, no Bootstrap badge, motion guards."""

import re
from pathlib import Path

from django.test import SimpleTestCase

ADVENT = Path(__file__).resolve().parents[1] / "templates" / "crush_lu" / "advent"
TW_INPUT = (
    Path(__file__).resolve().parents[2] / "tailwind-src/crush_lu/tailwind-input.css"
)


def _read(name):
    return (ADVENT / name).read_text(encoding="utf-8")


class AdventDoorTemplateTests(SimpleTestCase):
    def test_no_bootstrap_badge_on_door_pages(self):
        for name in ("door_photo.html", "door_gift.html", "door_poem.html"):
            self.assertNotIn("badge bg-danger", _read(name), name)
            self.assertIn("bg-advent-red", _read(name), name)

    def test_no_hardcoded_advent_hexes(self):
        for name in ("door_gift.html", "door_poem.html", "qr_scanner.html"):
            src = _read(name).lower()
            self.assertNotIn("#c41e3a", src, name)
            self.assertNotIn("#ff6b6b", src, name)

    def test_door_default_has_no_inline_style(self):
        self.assertNotIn("<style", _read("door_default.html"))
        css = TW_INPUT.read_text(encoding="utf-8")
        self.assertIn(".bonus-hint", css)
        self.assertIn(".door-message", css)

    def test_infinite_animations_have_reduced_motion_guard(self):
        for name, selector in (
            ("door_gift.html", ".gift-animation"),
            ("calendar_locked.html", ".locked-icon"),
            ("qr_scanner.html", ".scanner-frame"),
        ):
            guard = re.search(
                r"prefers-reduced-motion:\s*reduce\)\s*\{[^}]*"
                + re.escape(selector)
                + r"\s*\{\s*animation:\s*none",
                _read(name),
            )
            self.assertIsNotNone(guard, name)

    def test_door_nav_row_wraps(self):
        for name in (
            "door_default.html",
            "door_photo.html",
            "door_gift.html",
            "door_poem.html",
        ):
            self.assertIn("door-navigation flex flex-wrap", _read(name), name)

    def test_door_card_padding_shrinks_on_phones(self):
        self.assertRegex(
            _read("advent_base.html"),
            r"max-width:\s*480px\)\s*\{\s*\.door-content-card\s*\{\s*padding:\s*1\.25rem",
        )
