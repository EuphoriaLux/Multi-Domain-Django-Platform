"""Playwright: rendered contrast of the Wave 5 · WP10 token floors.

``test_ux_wave5_a11y_contrast.py`` pins the CSS and markup. Only a browser
proves the cascade: the light/dark remaps of ``text-gray-400/500`` and the dark
purple links must win over Tailwind's layered utilities, and the inactive
Connect sub-nav tabs must no longer fall back to the global link colour.

Excluded from the default run (``-m "not playwright"``). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave5_a11y_contrast_playwright.py -n 0
"""

import json

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

from crush_lu.tests.test_contrast_tokens_playwright import (  # noqa: E402
    CONTRAST_JS,
    _axe_source,
)

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})


def _page(browser, live_server, theme, user=None):
    context = browser.new_context(
        viewport=PHONE, color_scheme=theme, reduced_motion="reduce"
    )
    context.route(
        "**://fonts.g*.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    cookies = [{"name": "cookie_consent", "value": DECLINED, "url": live_server.url}]
    if user is not None:
        client = Client()
        client.force_login(user)
        cookies.append(
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        )
    context.add_cookies(cookies)
    context.add_init_script(f"localStorage.setItem('theme', '{theme}');")
    return context.new_page()


def _axe_nodes(page, include):
    page.evaluate(_axe_source())
    return page.evaluate(
        "async (include) => (await axe.run({include}, {runOnly: ['color-contrast']}))"
        ".violations.flatMap(v => v.nodes.map(n => n.target.join(' ')))",
        include,
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_connect_subnav_tabs_reach_aa(browser, live_server, theme):
    from crush_lu.tests.test_profile_edit_connect_card import _make_member

    page = _page(
        browser, live_server, theme, _make_member("wp10@example.com", is_staff=True)
    )
    response = page.goto(f"{live_server.url}/en/crush-connect/home/")
    assert response is not None and response.ok
    tabs = page.locator(".connect-local-nav a")
    assert tabs.count() == 4
    for i in range(tabs.count()):
        ratio = tabs.nth(i).evaluate(CONTRAST_JS)
        assert ratio >= 4.5, (theme, tabs.nth(i).inner_text(), ratio)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_gray_helper_text_and_purple_links_reach_aa(browser, live_server, theme):
    axe = _axe_source()
    if axe is None:
        pytest.skip("axe-core not available (set AXE_CORE_PATH)")
    page = _page(browser, live_server, theme)
    for path in ("/en/signup/", "/en/child-safety-standards/", "/en/login/"):
        response = page.goto(f"{live_server.url}{path}")
        assert response is not None and response.ok
        page.wait_for_timeout(300)
        nodes = _axe_nodes(page, [["main"]])
        assert nodes == [], (path, theme, nodes)


# ---------------------------------------------------------------------------
# The floors must be TEXT ONLY: nothing else (bg/border/ring/gradient/...) may
# change colour because an ancestor, or the element itself, carries a floored
# text utility. Static HTML + the built stylesheet, so no server is needed.
# ---------------------------------------------------------------------------
import re  # noqa: E402
from pathlib import Path  # noqa: E402

_BUILT = (
    Path(__file__).resolve().parents[1] / "static" / "crush_lu" / "css" / "tailwind.css"
).read_text(encoding="utf-8")

_FILL_RE = re.compile(
    r"\.((?:dark\\:)?(?:bg|border(?:-[xytblrse])?|ring|from|to|via|shadow|divide"
    r"|outline|accent|fill|stroke|decoration|caret)-(?:gray-(?:400|500)|green-600)"
    r"(?:\\/\d+)?)[\s,{:]"
)
FILL_CLASSES = sorted(
    {
        m.group(1).replace("\\:", ":").replace("\\/", "/")
        for m in _FILL_RE.finditer(_BUILT)
    }
)
# every trigger of the floor: utilities (and their variants) plus components
FLOORED_PARENTS = [
    "text-gray-400",
    "text-gray-500",
    "text-green-600",
    "dark:text-gray-500",
    "hover:text-gray-500",
    "placeholder-gray-400",
    "placeholder-gray-500",
    "btn-close",
    "form-control",
    "input-crush",
    "form-text",
    "connect-label-hint",
    "journey-icon",
    "journey-icon--idle",
    "text-gray-500 dark:text-gray-400",
]
PROPS = (
    "backgroundColor backgroundImage borderTopColor borderLeftColor outlineColor "
    "boxShadow textDecorationColor accentColor caretColor fill stroke"
).split()
CUSTOM = (
    "--tw-gradient-from --tw-gradient-via --tw-gradient-to --tw-ring-color "
    "--tw-shadow-color --tw-ring-offset-color --tw-inset-ring-color"
).split()

_READ_JS = """
([props, custom]) => {
  const out = {};
  document.querySelectorAll('[data-probe]').forEach(el => {
    const cs = getComputedStyle(el);
    out[el.dataset.probe] = [
      ...props.map(p => cs[p]),
      ...custom.map(p => cs.getPropertyValue(p)),
    ].join('|');
  });
  return out;
}
"""


def _static_page(browser, theme, body):
    page = browser.new_page(color_scheme=theme)
    page.set_content(
        f'<html class="{"dark" if theme == "dark" else ""}"><head>'
        f"<style>{_BUILT}</style></head><body>{body}</body></html>"
    )
    return page


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_floor_never_recolours_non_text_utilities(browser, theme):
    assert FILL_CLASSES, "no bg/border/ring utilities found in the build"
    probes = []
    for i, parent in enumerate(FLOORED_PARENTS):
        for j, child in enumerate(FILL_CLASSES):
            # a direct child and a grandchild, each next to its plain control
            probes.append(
                f'<div class="{parent}"><div><div data-probe="p{i}-{j}" '
                f'class="{child}" style="color: rgb(1, 2, 3)"></div></div></div>'
            )
    for j, child in enumerate(FILL_CLASSES):
        probes.append(
            f'<div><div data-probe="c-{j}" class="{child}" '
            f'style="color: rgb(1, 2, 3)"></div></div>'
        )
    page = _static_page(browser, theme, "".join(probes))
    got = page.evaluate(_READ_JS, [PROPS, CUSTOM])
    page.close()
    leaks = [
        f"{FLOORED_PARENTS[i]} > {FILL_CLASSES[j]}"
        for i in range(len(FLOORED_PARENTS))
        for j in range(len(FILL_CLASSES))
        if got[f"p{i}-{j}"] != got[f"c-{j}"]
    ]
    assert leaks == []


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_floored_text_utility_does_not_recolour_its_own_siblings_fill(browser, theme):
    # the activity-banner case: parent `text-gray-500 dark:text-gray-400`,
    # child `bg-gray-400` dot
    page = _static_page(
        browser,
        theme,
        '<p class="text-gray-500 dark:text-gray-400">'
        '<span data-probe="dot" class="bg-gray-400 inline-block" style="color: #123"></span></p>'
        '<span data-probe="ref" class="bg-gray-400 inline-block" style="color: #123"></span>',
    )
    got = page.evaluate(_READ_JS, [PROPS, CUSTOM])
    page.close()
    assert got["dot"] == got["ref"]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_floors_still_lift_text_and_keep_earlier_fixes(browser, theme):
    body = """
    <p data-probe="g5" class="text-gray-500">a</p>
    <p data-probe="g4" class="text-gray-400">a</p>
    <span data-probe="badge" class="bg-gray-500 text-white">b</span>
    <span data-probe="pass" class="bg-gray-300 dark:hover:bg-gray-500 hover:bg-gray-400 text-gray-700 dark:text-gray-200">p</span>
    <input data-probe="cb" type="checkbox" checked class="text-crush-purple">
    <a data-probe="cta" class="bg-white text-crush-purple">c</a>
    <a data-probe="lnk" href="#" class="dark:text-purple-400 dark:hover:text-purple-300 hover:text-purple-700">l</a>
    <a data-probe="lnk2" href="#" class="dark:text-purple-400 hover:text-purple-700">l2</a>
    <div class="quiz-stage-shell"><p data-probe="stage" class="text-gray-400">s</p></div>
    <p data-probe="ref4" style="color: var(--color-gray-400)">r</p>
    <span data-probe="ref5bg" style="background: var(--color-gray-500)">r</span>
    """
    page = _static_page(browser, theme, body)

    def color(probe, prop="color"):
        return page.eval_on_selector(
            f'[data-probe="{probe}"]', f"e => getComputedStyle(e).{prop}"
        )

    muted = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--text-muted')"
    ).strip()
    probe = page.evaluate(
        "m => { const d = document.createElement('i'); d.style.color = m;"
        "document.body.append(d); return getComputedStyle(d).color }",
        muted,
    )
    # text floors
    # dark: gray-500 text resolves to the stock gray-400 step
    assert color("g5") == (probe if theme == "light" else color("ref4"))
    if theme == "light":
        assert color("g4") == probe
    # background of `bg-gray-500 text-white` keeps the stock step
    assert color("badge", "backgroundColor") == color("ref5bg", "backgroundColor")
    # the always-dark stage keeps the stock gray-400 text
    if theme == "light":
        assert color("stage") == color("ref4")
    # dark: form controls and white CTAs keep the brand purple
    if theme == "dark":
        assert color("cb") == "rgb(155, 89, 182)"
        assert color("cta") == "rgb(155, 89, 182)"
    # dark: purple-200 survives hover of a light-only variant and focus
    if theme == "dark":
        purple_200 = "rgb(213, 173, 225)"
        page.hover('[data-probe="lnk2"]')
        assert color("lnk2") == purple_200
        page.mouse.move(600, 600)
        page.focus('[data-probe="lnk"]')
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.dataset.probe") == "lnk"
        assert color("lnk") == purple_200
        # Pass button: hover background is the stock gray-500, not the floor
        page.hover('[data-probe="pass"]')
        assert color("pass", "backgroundColor") == color("ref5bg", "backgroundColor")
    page.close()
