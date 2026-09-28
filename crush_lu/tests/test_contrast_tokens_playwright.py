"""Playwright: contrast tokens and the always-dark journey (UX Wave 2 · WP9).

``test_contrast_tokens.py`` pins the markup and CSS. Only a browser proves:

* 7-03 — theme-manager.js keeps ``.dark`` on journey / gift pages even when
  the saved preference is light, never overwrites that preference, and the
  global chrome (tab bar) takes its dark variant; the toggle is disabled.
* 5-05 — the tab-bar labels and ``.btn-crush-solid`` reach 4.5:1 in the
  rendered page (light and dark).
* An axe ``color-contrast`` pass over the touched pages, when axe-core is
  available (``AXE_CORE_PATH`` or ``node_modules/axe-core/axe.min.js``).

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_contrast_tokens_playwright.py -n 0
"""

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})
REPO_ROOT = Path(__file__).resolve().parents[2]
LOCKED_LABEL = "This experience is always in night mode"

# WCAG 2.x contrast of an element's text against the nearest opaque background.
CONTRAST_JS = """
(el) => {
    const parse = (c) => c.match(/[\\d.]+/g).map(Number);
    const lum = ([r, g, b]) => {
        const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    let node = el, bg = null;
    while (node) {
        const c = getComputedStyle(node).backgroundColor;
        const v = parse(c);
        if (v.length === 3 || v[3] === 1) { bg = v; break; }
        node = node.parentElement;
    }
    const fg = lum(parse(getComputedStyle(el).color));
    const b = lum(bg || [255, 255, 255]);
    return (Math.max(fg, b) + 0.05) / (Math.min(fg, b) + 0.05);
}
"""


def _axe_source():
    for candidate in (
        os.environ.get("AXE_CORE_PATH"),
        REPO_ROOT / "node_modules" / "axe-core" / "axe.min.js",
    ):
        if candidate and Path(candidate).is_file():
            return Path(candidate).read_text(encoding="utf-8")
    return None


def _member(*, is_staff=False):
    # Gift sender pages are staff/coach-only (UX Wave 4 decision C).
    from crush_lu.tests.test_profile_edit_connect_card import _make_member

    return _make_member("contrast@example.com", is_staff=is_staff)


def _page(browser, live_server, user, theme):
    context = browser.new_context(viewport=PHONE, color_scheme=theme)
    context.route(
        "**://fonts.g*.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    client = Client()
    client.force_login(user)
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            },
            {"name": "cookie_consent", "value": DECLINED, "url": live_server.url},
        ]
    )
    context.add_init_script(
        f"if (!sessionStorage.getItem('seeded')) {{"
        f" sessionStorage.setItem('seeded', '1');"
        f" localStorage.setItem('theme', '{theme}'); }}"
    )
    return context.new_page()


def _open(page, url):
    response = page.goto(url)
    assert response is not None and response.ok, response and response.status
    page.wait_for_function("() => window.Alpine && Alpine.store('prompts')")


def test_gift_page_stays_dark_under_a_light_preference(browser, live_server):
    page = _page(browser, live_server, _member(is_staff=True), "light")
    _open(page, f"{live_server.url}/en/journey/gift/create/")

    assert page.evaluate("() => document.documentElement.classList.contains('dark')")
    # The saved preference is untouched, so other pages stay light.
    assert page.evaluate("() => localStorage.getItem('theme')") == "light"
    # Global chrome takes its dark variant (7-03: lavender bar under navy page).
    nav_bg = page.evaluate(
        "() => getComputedStyle(document.querySelector('nav.bottom-nav')).backgroundColor"
    )
    assert nav_bg == "rgb(15, 23, 42)"

    toggle = page.locator("[x-data='themeToggle'] button").first
    # aria-disabled, not disabled: it stays focusable so the reason is read.
    assert toggle.get_attribute("aria-disabled") == "true"
    assert toggle.evaluate("el => !el.disabled && el.tabIndex === 0")
    # A real activation is still a no-op.
    toggle.dispatch_event("click")
    assert page.evaluate("() => document.documentElement.classList.contains('dark')")
    assert toggle.get_attribute("title") == LOCKED_LABEL
    assert toggle.get_attribute("aria-label") == LOCKED_LABEL

    # Even a scripted toggle cannot switch the page to light.
    page.evaluate("() => window.themeManager.toggleTheme()")
    assert page.evaluate("() => document.documentElement.classList.contains('dark')")
    assert page.evaluate("() => localStorage.getItem('theme')") == "light"

    _open(page, f"{live_server.url}/en/dashboard/")
    assert not page.evaluate(
        "() => document.documentElement.classList.contains('dark')"
    )
    toggle = page.locator("[x-data='themeToggle'] button").first
    assert toggle.get_attribute("aria-disabled") is None
    assert toggle.get_attribute("title") is None


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_tab_bar_and_solid_button_reach_aa(browser, live_server, theme):
    page = _page(browser, live_server, _member(), theme)
    _open(page, f"{live_server.url}/en/dashboard/")

    labels = page.locator(
        "nav.bottom-nav .bottom-nav-item > span:not(.bottom-nav-badge)"
    )
    assert labels.count() >= 4
    for i in range(labels.count()):
        label = labels.nth(i)
        assert label.evaluate("(el) => getComputedStyle(el).fontSize") == "11px"
        ratio = label.evaluate(CONTRAST_JS)
        assert ratio >= 4.5, (theme, label.inner_text(), ratio)

    button = page.locator(".btn-crush-solid").first
    ratio = button.evaluate(CONTRAST_JS)
    assert ratio >= 4.5, (theme, ratio)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(
    "path", ["/en/connections/", "/en/journey/gift/create/", "/en/journey/gifts/"]
)
def test_axe_color_contrast_is_clean(browser, live_server, theme, path):
    axe = _axe_source()
    if axe is None:
        pytest.skip("axe-core not available (set AXE_CORE_PATH)")
    page = _page(browser, live_server, _member(is_staff="/gift" in path), theme)
    _open(page, f"{live_server.url}{path}")
    page.wait_for_timeout(300)
    page.evaluate(axe)
    nodes = page.evaluate(
        "async () => (await axe.run(document, {runOnly: ['color-contrast']}))"
        ".violations.flatMap(v => v.nodes.map(n => n.target.join(' ')))"
    )
    assert nodes == [], nodes


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_axe_dashboard_touched_elements_are_clean(browser, live_server, theme):
    axe = _axe_source()
    if axe is None:
        pytest.skip("axe-core not available (set AXE_CORE_PATH)")
    page = _page(browser, live_server, _member(), theme)
    _open(page, f"{live_server.url}/en/dashboard/")
    page.wait_for_timeout(300)
    page.evaluate(axe)
    nodes = page.evaluate(
        "async () => (await axe.run({include: [['nav.bottom-nav'], ['.btn-crush-solid'],"
        " ['.text-muted-fg']]}, {runOnly: ['color-contrast']}))"
        ".violations.flatMap(v => v.nodes.map(n => n.target.join(' ')))"
    )
    assert nodes == [], nodes
