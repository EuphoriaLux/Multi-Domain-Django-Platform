"""Playwright: the mobile drawer as a modal, and keyboard-only focus rings.

- The drawer keeps Tab inside it, closes on Escape and hands focus back to the
  button that opened it. Before, a keyboard user tabbed straight out of the
  open menu into the page behind it.
- A button shows its focus ring for keyboard focus only, not after a click.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_shell_behaviour_playwright.py -n 0 --create-db
"""

import json

import pytest
from django.conf import settings
from django.test import Client
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
OPEN_MENU = ".top-bar-mobile button[aria-label='Open menu']"
FOCUS_IN_DRAWER = (
    "() => document.getElementById('mobile-drawer-panel')"
    ".contains(document.activeElement)"
)
RING = "el => getComputedStyle(el).boxShadow"


@pytest.fixture
def member_page(page, live_server, transactional_db):
    from crush_lu.tests.test_ux_wave4_polls_timeline import make_member

    client = Client()
    client.force_login(make_member())
    consent = json.dumps({"essential": True, "analytics": False, "marketing": False})
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            },
            {"name": "cookie_consent", "value": consent, "url": live_server.url},
        ]
    )
    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/en/events/")
    page.wait_for_load_state("load")
    return page


def _open_drawer(page):
    page.locator(OPEN_MENU).click()
    panel = page.locator("#mobile-drawer-panel")
    expect(panel).to_be_visible()
    return panel


def test_drawer_is_announced_as_a_modal_dialog(member_page):
    panel = _open_drawer(member_page)

    expect(panel).to_have_attribute("role", "dialog")
    expect(panel).to_have_attribute("aria-modal", "true")


def test_opening_the_drawer_moves_focus_into_it(member_page):
    panel = _open_drawer(member_page)

    expect(panel.get_by_role("button", name="Close menu")).to_be_focused()


def test_tab_and_shift_tab_stay_inside_the_open_drawer(member_page):
    page = member_page
    panel = _open_drawer(page)
    expect(panel.get_by_role("button", name="Close menu")).to_be_focused()

    # Backwards from the first control wraps to the last one, not to the page.
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(FOCUS_IN_DRAWER)
    # And forwards from the last one wraps to the first.
    page.keyboard.press("Tab")
    expect(panel.get_by_role("button", name="Close menu")).to_be_focused()
    for _ in range(40):
        page.keyboard.press("Tab")
        assert page.evaluate(FOCUS_IN_DRAWER)


def test_escape_closes_the_drawer_and_returns_focus_to_the_opener(member_page):
    page = member_page
    panel = _open_drawer(page)

    page.keyboard.press("Escape")

    expect(panel).to_be_hidden()
    expect(page.locator(OPEN_MENU)).to_be_focused()
    assert page.evaluate("document.body.style.overflow") == ""


def test_page_behind_the_open_drawer_does_not_scroll(member_page):
    _open_drawer(member_page)

    assert member_page.evaluate("document.body.style.overflow") == "hidden"


def test_button_ring_shows_for_keyboard_focus_only(member_page):
    page = member_page
    page.set_viewport_size({"width": 1280, "height": 800})
    page.evaluate("""
        () => {
            const button = document.createElement('button');
            button.id = 'probe-button';
            button.type = 'button';
            button.className = 'btn-crush-solid';
            button.textContent = 'Probe';
            button.style.cssText = 'position:fixed;top:160px;left:24px;z-index:99999';
            document.body.append(button);
        }
        """)
    button = page.locator("#probe-button")
    resting = button.evaluate(RING)

    button.click()
    expect(button).to_be_focused()
    page.mouse.move(600, 500)
    assert button.evaluate(RING) == resting

    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")
    expect(button).to_be_focused()
    assert button.evaluate(RING) != resting
