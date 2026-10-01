"""Playwright: drawer preferences, drill-down top bar, status-bar colour (WP10).

``test_page_chrome.py`` pins the markup and CSS. Only a browser proves:

* 8-10 — the drawer's Light/Dark/System control drives theme-manager.js
  (System drops the saved choice), keeps the navbar toggle in step, and is
  aria-disabled with the locked label on always-dark pages; the drawer's
  language select switches the page language.
* 8-12 — drill-down pages show Back + title in the top bar and no visible
  duplicate h1.
* 8-16 — the top bar keeps 48px of content (its height is 48px + inset), and
  the ``theme-color`` metas follow a manual pick or a theme lock.
* No color-contrast violations inside the open drawer (axe, when available:
  ``AXE_CORE_PATH`` or ``node_modules/axe-core/axe.min.js``).

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_page_chrome_playwright.py -n 0
"""

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})
REPO_ROOT = Path(__file__).resolve().parents[2]
LOCKED_LABEL = "This experience is always in night mode"
# WCAG 2.x contrast of an element's text against the nearest opaque background.
CONTRAST_JS = """
(el) => {
    // Tailwind v4 colours compute as oklch(): let a canvas convert to sRGB.
    const ctx = document.createElement("canvas").getContext("2d", {willReadFrequently: true});
    const parse = (c) => {
        ctx.clearRect(0, 0, 1, 1);
        ctx.fillStyle = c;
        ctx.fillRect(0, 0, 1, 1);
        const d = ctx.getImageData(0, 0, 1, 1).data;
        return [d[0], d[1], d[2], d[3] / 255];
    };
    const lum = ([r, g, b]) => {
        const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    let node = el, bg = null;
    while (node) {
        const v = parse(getComputedStyle(node).backgroundColor);
        if (v[3] === 1) { bg = v; break; }
        node = node.parentElement;
    }
    const fg = lum(parse(getComputedStyle(el).color));
    const b = lum(bg || [255, 255, 255]);
    return (Math.max(fg, b) + 0.05) / (Math.min(fg, b) + 0.05);
}
"""
THEME_COLORS_JS = (
    "() => [...document.querySelectorAll('meta[name=\"theme-color\"]')]"
    ".map(m => m.getAttribute('content'))"
)


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

    return _make_member("chrome-pw@example.com", is_staff=is_staff)


def _page(browser, live_server, user, *, scheme="light", saved=None):
    context = browser.new_context(viewport=PHONE, color_scheme=scheme)
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
    if saved:
        context.add_init_script(
            "if (!sessionStorage.getItem('seeded')) {"
            " sessionStorage.setItem('seeded', '1');"
            f" localStorage.setItem('theme', '{saved}'); }}"
        )
    return context.new_page()


def _open(page, url):
    response = page.goto(url)
    assert response is not None and response.ok, response and response.status
    page.wait_for_function("() => window.Alpine && Alpine.store('drawer')")


def _open_drawer(page):
    page.locator(".top-bar-mobile button[aria-label='Open menu']").click()
    drawer = page.locator("[x-data='themeChoice']")
    expect(drawer).to_be_visible()
    return drawer


def _is_dark(page):
    return page.evaluate("() => document.documentElement.classList.contains('dark')")


def test_drawer_theme_choice_drives_theme_and_status_bar(browser, live_server):
    page = _page(browser, live_server, _member(), scheme="light")
    _open(page, f"{live_server.url}/en/notifications/")

    # No manual choice: loading a page must not save one ("System").
    assert page.evaluate("() => localStorage.getItem('theme')") is None
    assert page.evaluate(THEME_COLORS_JS) == ["#0f172a", "#9B59B6"]

    choice = _open_drawer(page)
    system = choice.get_by_role("button", name="System")
    dark = choice.get_by_role("button", name="Dark")
    light = choice.get_by_role("button", name="Light")
    expect(system).to_have_attribute("aria-pressed", "true")
    expect(dark).to_have_attribute("aria-pressed", "false")

    dark.click()
    assert _is_dark(page)
    assert page.evaluate("() => localStorage.getItem('theme')") == "dark"
    expect(dark).to_have_attribute("aria-pressed", "true")
    expect(system).to_have_attribute("aria-pressed", "false")
    # A manual pick overrides the OS scheme, so both metas follow it.
    assert page.evaluate(THEME_COLORS_JS) == ["#0f172a", "#0f172a"]
    # The navbar toggle (another themeToggle instance) stays in step.
    assert (
        page.evaluate(
            "() => Alpine.$data(document.querySelector("
            "'nav.crush-navbar [x-data=\"themeToggle\"]')).currentTheme"
        )
        == "dark"
    )

    light.click()
    assert not _is_dark(page)
    assert page.evaluate(THEME_COLORS_JS) == ["#9B59B6", "#9B59B6"]

    system.click()
    assert page.evaluate("() => localStorage.getItem('theme')") is None
    assert not _is_dark(page)  # the emulated OS scheme is light
    assert page.evaluate(THEME_COLORS_JS) == ["#0f172a", "#9B59B6"]
    expect(system).to_have_attribute("aria-pressed", "true")

    # "System" survives a reload and still follows the OS.
    _open(page, f"{live_server.url}/en/notifications/")
    assert page.evaluate("() => localStorage.getItem('theme')") is None


def test_system_follows_a_dark_os(browser, live_server):
    page = _page(browser, live_server, _member(), scheme="dark", saved="light")
    _open(page, f"{live_server.url}/en/notifications/")
    assert not _is_dark(page)
    _open_drawer(page).get_by_role("button", name="System").click()
    assert _is_dark(page)
    assert page.evaluate("() => localStorage.getItem('theme')") is None


def test_theme_choice_is_free_on_journey_pages(browser, live_server):
    """UX Wave 5 · WP14: gift/journey pages follow the theme (advent stays locked)."""
    page = _page(
        browser, live_server, _member(is_staff=True), scheme="light", saved="light"
    )
    _open(page, f"{live_server.url}/en/journey/gift/create/")
    assert not _is_dark(page)

    choice = _open_drawer(page)
    buttons = choice.get_by_role("button")
    assert buttons.count() == 3
    for i in range(3):
        expect(buttons.nth(i)).not_to_have_attribute("aria-disabled", "true")
    choice.get_by_role("button", name="Dark").click()
    assert _is_dark(page)


def test_other_theme_labels_follow_a_drawer_pick(browser, live_server):
    # OS light + System: picking Light keeps the resolved theme light, so only
    # the reactive preference (not localStorage) can refresh the other labels.
    page = _page(browser, live_server, _member(), scheme="light")
    _open(page, f"{live_server.url}/en/profile/edit/?section=account")
    card = page.locator(
        ".section-edit-card[x-data='themeToggle'] [x-text='themeLabel']"
    )
    expect(card).to_have_text("System (Light)")
    _open_drawer(page).get_by_role("button", name="Light").click()
    expect(card).to_have_text("Light")
    page.locator("[x-data='themeChoice']").get_by_role("button", name="System").click()
    expect(card).to_have_text("System (Light)")


def test_drawer_language_select_switches_language(browser, live_server):
    page = _page(browser, live_server, _member())
    _open(page, f"{live_server.url}/en/notifications/")
    _open_drawer(page)
    with page.expect_navigation():
        page.locator("#language-drawer").select_option("de")
    assert page.url.endswith("/de/notifications/"), page.url
    expect(page.locator(".top-bar-mobile-title")).to_have_text("Benachrichtigungen")


@pytest.mark.parametrize(
    "path,title",
    [
        ("/en/notifications/", "Notifications"),
        ("/en/settings/blocked/", "Blocked members"),
        ("/en/account/gdpr/", "Data Management"),
        ("/en/account/delete-profile/", "Delete Crush.lu Profile"),
    ],
)
def test_drill_down_top_bar_shows_back_and_title(browser, live_server, path, title):
    page = _page(browser, live_server, _member())
    _open(page, f"{live_server.url}{path}")
    bar = page.locator(".top-bar-mobile")
    expect(bar.locator(".top-bar-mobile-back")).to_be_visible()
    expect(bar.locator(".top-bar-mobile-title")).to_have_text(title)
    expect(bar.locator(".top-bar-mobile-logo")).to_be_hidden()
    # One h1, read by screen readers but not repeated on screen.
    h1 = page.locator("main h1")
    assert h1.count() == 1
    assert h1.evaluate("el => el.getBoundingClientRect().height") <= 1
    # 48px of content (no inset in a desktop browser) and no content under it.
    box = bar.evaluate(
        "el => { const s = getComputedStyle(el); return [el.offsetHeight,"
        " parseFloat(s.paddingTop)]; }"
    )
    assert box == [48, 0], box


def test_top_bar_back_works_from_the_keyboard(browser, live_server):
    page = _page(browser, live_server, _member())
    _open(page, f"{live_server.url}/en/dashboard/")
    _open(page, f"{live_server.url}/en/notifications/")
    back = page.locator(".top-bar-mobile-back")
    expect(back).to_be_visible()
    expect(back).to_have_attribute("type", "button")
    back.focus()
    assert page.evaluate(
        "() => document.activeElement.classList.contains('top-bar-mobile-back')"
    )
    with page.expect_navigation():
        page.keyboard.press("Enter")
    assert page.url.endswith("/en/dashboard/"), page.url


def test_account_settings_has_no_mobile_back_pill(browser, live_server):
    page = _page(browser, live_server, _member())
    _open(page, f"{live_server.url}/en/profile/edit/?section=account")
    expect(page.locator(".top-bar-mobile-back")).to_be_visible()
    expect(page.get_by_role("link", name="Back to Dashboard")).to_be_hidden()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_axe_color_contrast_in_the_open_drawer(browser, live_server, theme):
    axe = _axe_source()
    if axe is None:
        pytest.skip("axe-core not available (set AXE_CORE_PATH)")
    page = _page(browser, live_server, _member(), scheme=theme, saved=theme)
    _open(page, f"{live_server.url}/en/notifications/")
    _open_drawer(page)
    page.wait_for_timeout(400)  # slide-in transition
    page.evaluate(axe)
    body = page.locator("[x-data='mobileDrawer'] .overflow-y-auto")
    # Top of the drawer, then scrolled to the new groups and Logout.
    for scroll in (0, 99999):
        body.evaluate(f"el => {{ el.scrollTop = {scroll}; }}")
        page.wait_for_timeout(150)
        nodes = page.evaluate(
            "async () => (await axe.run({include: [['[x-data=\"mobileDrawer\"]']]},"
            " {runOnly: ['color-contrast']}))"
            ".violations.flatMap(v => v.nodes.map(n => n.target.join(' ')))"
        )
        assert nodes == [], (scroll, nodes)
    logout = page.locator("[x-data='mobileDrawer'] a[href$='/logout/'] span")
    assert logout.evaluate(CONTRAST_JS) >= 4.5
