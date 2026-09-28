"""Playwright: UX Wave 4 · WP13b "followups-a11y-contrast" (#1077, #1088).

* On a theme-locked (always-dark) page no Light/Dark/System option reports
  ``aria-pressed="true"`` (the saved preference is not what the page shows).
* themeToggle's status text follows the page language.
* The pages #1088 / #1077 list have no axe color-contrast nodes, light and
  dark (when axe-core is available: ``AXE_CORE_PATH`` or
  ``node_modules/axe-core/axe.min.js``).

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave4_followups_a11y_playwright.py -n 0
"""

import pytest

pytest.importorskip("playwright")

from django.utils import translation  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

from crush_lu.tests.test_page_chrome_playwright import (  # noqa: E402
    _axe_source,
    _is_dark,
    _member,
    _open,
    _open_drawer,
    _page,
)

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("saved", ["light", "dark", None])
def test_locked_page_has_no_pressed_theme_option(browser, live_server, saved):
    page = _page(
        browser, live_server, _member(is_staff=True), scheme="light", saved=saved
    )
    _open(page, f"{live_server.url}/en/journey/gift/create/")
    assert _is_dark(page)
    buttons = _open_drawer(page).get_by_role("button")
    assert buttons.count() == 3
    pressed = [buttons.nth(i).get_attribute("aria-pressed") for i in range(3)]
    assert pressed == ["false", "false", "false"], (saved, pressed)


def test_theme_toggle_status_is_translated(browser, live_server):
    page = _page(browser, live_server, _member(), scheme="light")
    _open(page, f"{live_server.url}/de/profile/edit/?section=account")
    card = page.locator(".section-edit-card[x-data='themeToggle']")
    expect(card.locator("[x-text='themeLabel']")).to_have_text("System (Hell)")
    expect(card.locator("[x-text='themeButtonLabel']")).to_have_text(
        "Zu Dunkel wechseln"
    )
    with translation.override("de"):
        open_menu = translation.gettext("Open menu")
    page.locator(f".top-bar-mobile button[aria-label='{open_menu}']").click()
    drawer = page.locator("[x-data='themeChoice']")
    drawer.get_by_role("button", name="Dunkel").click()
    expect(card.locator("[x-text='themeLabel']")).to_have_text("Dunkel")
    expect(card.locator("[x-text='themeButtonLabel']")).to_have_text("Zu Hell wechseln")


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(
    "path",
    [
        "/en/account/gdpr/",
        "/en/account/delete-profile/",
        "/en/account/settings/",
        "/en/data-deletion/",
        "/en/notifications/",
        "/en/settings/blocked/",
        "/en/dashboard/",
    ],
)
def test_axe_color_contrast_is_clean(browser, live_server, theme, path):
    axe = _axe_source()
    if axe is None:
        pytest.skip("axe-core not available (set AXE_CORE_PATH)")
    page = _page(browser, live_server, _member(), scheme=theme, saved=theme)
    _open(page, f"{live_server.url}{path}")
    page.wait_for_timeout(300)
    page.evaluate(axe)
    nodes = page.evaluate(
        "async () => (await axe.run(document, {runOnly: ['color-contrast']}))"
        ".violations.flatMap(v => v.nodes.map(n => n.target.join(' ') + ' :: '"
        " + ((n.any[0] || {}).message || '')))"
    )
    assert nodes == [], nodes
