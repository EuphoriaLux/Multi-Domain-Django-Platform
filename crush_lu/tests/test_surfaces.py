"""Surface and radius tokens (STYLE.md "Surfaces" and "Border radius").

Source-level guards for three foundations that had drifted silently:

- ``bg-white`` was re-pointed to a lavender tint, leaving cards lavender on a
  lavender page.
- An unlayered ``:root`` overrode Tailwind's radius scale, so the rendered
  values were not the documented ones and ``rounded-2xl`` was rounder than
  ``rounded-3xl``.
- ``.hidden{display:none}`` in base.html's unlayered critical CSS beat every
  layered ``sm:flex`` / ``md:table-cell``, so "hidden on mobile, shown on
  desktop" elements stayed hidden at all widths.

The rendered values are covered by test_surfaces_playwright.py.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TAILWIND_INPUT = REPO_ROOT / "tailwind-src" / "crush_lu" / "tailwind-input.css"
BASE_HTML = REPO_ROOT / "crush_lu" / "templates" / "crush_lu" / "base.html"

RADIUS_SCALE = {"sm": 8, "md": 12, "lg": 15, "xl": 20, "2xl": 25, "3xl": 28}


def _css():
    return TAILWIND_INPUT.read_text(encoding="utf-8")


def _theme_block(css):
    start = css.index("@theme {")
    return css[start : css.index("\n}", start)]


def _critical_css():
    html = BASE_HTML.read_text(encoding="utf-8")
    start = html.index("<!-- Critical CSS")
    block = html[start : html.index("</style>", start)]
    return re.sub(r"/\*.*?\*/", "", block, flags=re.S)


def test_white_is_not_repointed_to_a_tint():
    css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)

    assert not re.search(r"--color-white\s*:", css)
    assert "#f0ecf5" not in _theme_block(css).lower()


def test_surface_card_is_a_theme_colour_so_it_has_a_utility():
    assert re.search(r"--color-surface-card:\s*#ffffff;", _theme_block(_css()))


def test_radius_scale_lives_in_the_theme_and_only_there():
    css = _css()
    theme = _theme_block(css)

    for step, pixels in RADIUS_SCALE.items():
        assert f"--radius-{step}: {pixels}px;" in theme, step
        assert css.count(f"--radius-{step}:") == 1, step
    values = list(RADIUS_SCALE.values())
    assert values == sorted(set(values)), "each step must be rounder than the last"


def test_critical_css_has_no_unlayered_hidden_rule():
    assert not re.search(r"\.hidden\s*\{", _critical_css())


def test_body_font_does_not_name_a_font_that_is_never_loaded():
    assert "Inter" not in _critical_css()
    theme = _theme_block(_css())
    sans = theme[theme.index("--font-sans:") :]
    assert "Inter" not in sans[: sans.index(";")]
