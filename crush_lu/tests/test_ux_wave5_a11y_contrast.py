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

TAG_RE = re.compile(r"<[a-zA-Z][^<>]*?class=\"[^\"]*\"[^<>]*>", re.DOTALL)


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
            "html.dark.dark\\:text-purple-400:not(input,:is(:hover,:focus-visible)"
            '[class*="dark:hover:text-"])',
            compact,
        )
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
        optional = re.search(
            r'<span class="([^"]*)">\(\{% trans "optional" %\}\)', auth
        )
        self.assertIsNotNone(optional)
        self.assertIn("text-muted-fg", optional.group(1))
        self.assertNotIn("text-gray-400", optional.group(1))

        sent = (TEMPLATES / "account" / "verification_sent_crush.html").read_text(
            encoding="utf-8"
        )
        spam = re.search(r'<p class="([^"]*)">\s*\{% blocktrans %\}\s*If you don', sent)
        self.assertIsNotNone(spam)
        self.assertIn("text-muted-fg", spam.group(1))


class DarkPurpleFloorScopeTests(SimpleTestCase):
    """PR #1136 review: the dark purple floor must not break controls/hovers."""

    @staticmethod
    def _floor_selector(css):
        match = re.search(
            r"html\.dark\s*\.dark\\:text-purple-400:not\([^{]*\{", css, re.DOTALL
        )
        return _compact(match.group(0)) if match else ""

    def test_floor_skips_form_controls(self):
        # Checked checkboxes/radios paint background:currentColor under a white
        # glyph; purple-200 there is 1.9:1 (brand purple is 4.7:1).
        for css in (INPUT_CSS, BUILT_CSS):
            selector = self._floor_selector(css)
            self.assertNotEqual(selector, "")
            self.assertEqual(selector.count(":not(input,"), 2, selector)

    def test_light_only_hover_variants_keep_the_floor(self):
        # Only an explicit dark:hover:text-* may release the hover state.
        for css in (INPUT_CSS, BUILT_CSS):
            selector = self._floor_selector(css)
            self.assertEqual(selector.count('[class*="dark:hover:text-"]'), 2)
            self.assertNotIn(
                '[class*="hover:text-"]', selector.replace("dark:hover", "")
            )


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


def _compact(css):
    return re.sub(r"\s+", "", css)


def _rule_body(css, selector):
    """Declarations of the first top-level rule for ``selector`` (compacted)."""
    match = re.search(re.escape(_compact(selector)) + r"\{([^}]*)\}", _compact(css))
    return match.group(1) if match else ""


class ComponentContrastPinTests(SimpleTestCase):
    """Source pins for the component rules the floors changed."""

    def test_badges_use_contrast_safe_grounds(self):
        for selector, needle in (
            (".badge-success", "bg-green-700"),
            (".badge-info", "bg-blue-700"),
            (".badge-primary", "bg-purple-600"),
        ):
            match = re.search(
                re.escape(selector) + r"[^{;]*\{\s*@apply ([^;]*);", INPUT_CSS
            )
            self.assertIsNotNone(match, selector)
            self.assertIn(needle, match.group(1), selector)
            self.assertNotIn("bg-green-500", match.group(1), selector)
            self.assertNotIn("bg-blue-500", match.group(1), selector)

    def test_badge_crush_soft_text_is_purple_700(self):
        match = re.search(r"\.badge-crush-soft\s*\{\s*@apply ([^;]*);", INPUT_CSS)
        self.assertIsNotNone(match)
        self.assertIn("text-purple-700", match.group(1))
        self.assertNotIn("text-crush-purple-dark", match.group(1))

    def test_btn_link_has_a_dark_step_that_passes_on_dark_cards(self):
        match = re.search(r"\.btn-link\s*\{\s*@apply ([^;]*);", INPUT_CSS)
        self.assertIsNotNone(match)
        self.assertIn("dark:text-purple-200", match.group(1))

    def test_named_page_rules_use_the_muted_token(self):
        # These rules carried #6b7280/#6c757d (4.2:1 on lavender).
        for selector in (
            ".photo-upload-card .photo-help",
            ".coach-stat-label",
        ):
            body = _rule_body(INPUT_CSS, selector)
            self.assertNotEqual(body, "", selector)
            self.assertIn("color:var(--text-muted)", body, selector)
        self.assertIn(
            "color:var(--color-purple-700)", _rule_body(INPUT_CSS, ".press-publication")
        )


class ComponentTemplatePinTests(SimpleTestCase):
    def _read(self, *parts):
        return (TEMPLATES.joinpath(*parts)).read_text(encoding="utf-8")

    def test_consent_confirm_delete_link_is_red_700(self):
        text = self._read("crush_lu", "consent_confirm.html")
        self.assertIn("text-red-700 dark:text-red-400", text)
        self.assertNotIn("text-red-600 dark:text-red-400 hover:underline", text)

    def test_membership_cta_and_badge_are_contrast_safe(self):
        text = self._read("crush_lu", "membership.html")
        self.assertIn("text-crush-purple dark:text-purple-700", text)
        self.assertIn("text-purple-700 dark:text-purple-300", text)
        self.assertNotIn("text-crush-purple-dark dark:text-purple-300", text)

    def test_event_detail_info_banner_links_are_dark_blue_with_a_dark_variant(self):
        text = self._read("crush_lu", "event_detail.html")
        self.assertIn("text-blue-800 hover:text-blue-900", text)
        self.assertIn("dark:text-blue-200 dark:hover:text-blue-100", text)

    def test_login_signup_link_has_a_dark_variant(self):
        text = self._read("account", "login_crush.html")
        self.assertIn(
            "text-purple-600 hover:text-purple-700 dark:text-purple-300", text
        )

    def test_voting_demo_badges_use_700_grounds(self):
        text = self._read("crush_lu", "voting_demo.html")
        self.assertIn("bg-blue-700 text-white", text)
        self.assertIn("bg-pink-700 text-white", text)
        self.assertIn("text-pink-700 dark:text-pink-400", text)
        self.assertNotIn("bg-blue-500 text-white", text)
        self.assertNotIn("bg-crush-pink text-white text-xs", text)


class AlwaysDarkSurfaceTests(SimpleTestCase):
    """The light-mode gray/purple floor must not reach always-dark surfaces."""

    LIGHT_FLOOR_CLASSES = re.compile(
        r"(?<![:\w-])(text-gray-(400|500)|text-green-600|text-crush-purple)\b"
    )
    HARD_DARK_BODY = re.compile(
        r"<body[^>]*class=\"[^\"]*(?<![:\w-])"
        r"(bg-crush-dark|bg-slate-900|bg-gray-900|bg-gray-950|quiz-stage-shell)\b"
    )

    def test_standalone_dark_pages_are_locked_or_scoped(self):
        offenders = []
        for path in sorted(TEMPLATES.rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            html_tag = re.search(r"<html[^>]*>", text)
            if not html_tag or not self.HARD_DARK_BODY.search(text):
                continue
            locked = 'class="dark"' in html_tag.group(0)
            scoped = "quiz-stage-shell" in text
            if not (locked or scoped):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(offenders, [])

    def test_speed_dating_display_is_theme_locked_dark(self):
        text = (TEMPLATES / "crush_lu" / "speed_dating_display.html").read_text(
            encoding="utf-8"
        )
        html_tag = re.search(r"<html[^>]*>", text).group(0)
        self.assertIn('class="dark"', html_tag)
        self.assertIn('data-theme-lock="dark"', html_tag)
        self.assertIn("text-gray-400", text)

    def test_quiz_stage_keeps_stock_grays_and_purple(self):
        body = _rule_body(BUILT_CSS, ".quiz-stage-shell")
        self.assertIn("--color-gray-400:oklch(70.7%.022261.325)", body)
        self.assertIn("--color-gray-500:oklch(55.1%.027264.364)", body)
        compact = _compact(BUILT_CSS)
        self.assertIn(
            "html:not(.dark).text-crush-purple:not(.quiz-stage-shell*,", compact
        )
        quiz_css = (
            REPO_ROOT
            / "crush_lu"
            / "static"
            / "crush_lu"
            / "css"
            / "quiz-experience.css"
        ).read_text(encoding="utf-8")
        muted = re.search(r"--quiz-text-muted:\s*([^;]*);", quiz_css).group(1)
        self.assertIn("oklch(70.7%", muted)
        self.assertNotIn("var(--color-gray", muted)
