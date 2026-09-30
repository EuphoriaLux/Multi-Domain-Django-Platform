"""UX review Wave 5 · WP10: colour-contrast floors (finding R6, #1117).

Axe reported ~370 ``color-contrast`` nodes, almost all from a handful of
causes: hard-coded ``text-gray-400/500`` helper text on the tinted lavender
surface, purple links in dark mode, inactive Connect sub-nav tabs that took the
global link colour, and hover variants with no dark-mode counterpart. The
fixes are token-level, so these tests pin the tokens and the markup; the
rendered ratios are covered by ``test_ux_wave5_a11y_contrast_playwright.py``.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "crush_lu" / "templates"
INPUT_CSS = (REPO_ROOT / "tailwind-src" / "crush_lu" / "tailwind-input.css").read_text(
    encoding="utf-8"
)
BUILT_CSS = (
    REPO_ROOT / "crush_lu" / "static" / "crush_lu" / "css" / "tailwind.css"
).read_text(encoding="utf-8")
CONNECT_CSS = (
    REPO_ROOT / "crush_lu" / "static" / "crush_lu" / "css" / "connect-mobile.css"
).read_text(encoding="utf-8")

TAG_RE = re.compile(r"<[a-zA-Z][^<>]*?class=\"[^\"]*\"[^<>]*>", re.S)


class ContrastFloorTokenTests(SimpleTestCase):
    def test_light_mode_gray_helper_text_resolves_to_the_muted_token(self):
        # gray-500 is ~4.2:1 on #f0ecf5 and gray-400 ~2.4:1; both must read as
        # --text-muted (gray-600) in light mode, in source and in the build.
        for css in (INPUT_CSS, BUILT_CSS.replace(" ", "")):
            compact = css.replace(" ", "").replace("\n", "")
            self.assertIn("html:not(.dark){--color-gray-400:var(--text-muted)", compact)
            self.assertIn("--color-gray-500:var(--text-muted)", compact)

    def test_dark_mode_gray_500_is_lifted_to_gray_400(self):
        compact = BUILT_CSS.replace(" ", "").replace("\n", "")
        self.assertIn("html.dark{--color-gray-500:var(--color-gray-400)", compact)

    def test_dark_purple_text_uses_a_lighter_step_without_touching_brand_hexes(self):
        compact = BUILT_CSS.replace(" ", "").replace("\n", "")
        self.assertIn(
            "html.dark.dark\\:text-purple-400:not(:is(:hover,:focus-visible)"
            "[class*=hover\\:text-])",
            compact,
        )
        # A hovered link keeps its own hover colour; only un-styled hover stays
        # on the floor colour.
        # The brand scale itself is unchanged (four-place brand sync).
        self.assertIn("--color-purple-400: #ab5cc3;", INPUT_CSS)
        self.assertIn("--color-crush-purple: #9b59b6;", INPUT_CSS)

    def test_inactive_connect_tabs_get_a_colour_and_dark_active_tab_is_darker(self):
        self.assertRegex(
            CONNECT_CSS,
            r"\.connect-local-nav a \{ color: var\(--text-muted\); \}",
        )
        self.assertIn(
            'html.dark .connect-local-nav a[aria-current="page"] '
            "{ background: var(--color-purple-700); }",
            CONNECT_CSS,
        )

    def test_named_helper_text_uses_the_muted_utility(self):
        auth = (TEMPLATES / "crush_lu" / "auth.html").read_text(encoding="utf-8")
        optional = re.search(r'<span class="([^"]*)">\(\{% trans "optional" %\}\)', auth)
        self.assertIsNotNone(optional)
        self.assertIn("text-muted-fg", optional.group(1))
        self.assertNotIn("text-gray-400", optional.group(1))

        sent = (TEMPLATES / "account" / "verification_sent_crush.html").read_text(
            encoding="utf-8"
        )
        spam = re.search(r'<p class="([^"]*)">\s*\{% blocktrans %\}\s*If you don', sent)
        self.assertIsNotNone(spam)
        self.assertIn("text-muted-fg", spam.group(1))


class DarkHoverVariantTests(SimpleTestCase):
    """#1117: dark-on-dark hover on purple-800 / red-800 links."""

    def test_every_dark_hover_of_purple_or_red_800_has_a_dark_counterpart(self):
        offenders = []
        for path in sorted(TEMPLATES.rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            for tag in TAG_RE.findall(text):
                if re.search(r"(?<![:\w-])hover:text-(purple|red)-800\b", tag) and (
                    "dark:hover:text-" not in tag
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {tag[:90]}")
        self.assertEqual(offenders, [])
