"""Playwright: UX Wave 4 WP7b footer and FAQ behaviour in a real browser.

* Decision H (#1055) — a logged-in member on a phone sees the compact footer
  (help/legal links + imprint), fully above the fixed bottom tab bar once
  scrolled to the end; on desktop the compact block is hidden and the full
  footer shows.
* 1-10 — the Support FAQ items open on tap.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave4_support_footer_playwright.py -n 0
"""

import json

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.test import Client  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})


def _member_page(browser, live_server, viewport):
    from crush_lu.tests.test_profile_edit_connect_card import _make_member

    user = _make_member("wp7b-pw@example.com")
    client = Client()
    client.force_login(user)
    context = browser.new_context(viewport=viewport)
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
    page = context.new_page()
    page.goto(f"{live_server.url}/en/support/")
    page.wait_for_load_state("load")
    return page


def test_member_phone_footer_sits_above_tab_bar(browser, live_server):
    page = _member_page(browser, live_server, PHONE)
    compact = page.locator("footer nav[aria-label]").first
    page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(400)
    assert compact.is_visible()
    links = compact.locator("a")
    assert links.count() == 4
    nav_top = page.evaluate(
        "() => document.querySelector('.bottom-nav').getBoundingClientRect().top"
    )
    last_line = page.evaluate(
        "() => document.querySelector('footer nav[aria-label] p')"
        ".getBoundingClientRect().bottom"
    )
    assert last_line <= nav_top, (last_line, nav_top)
    # The full four-column footer stays desktop-only for members.
    assert not page.locator("footer .grid").first.is_visible()

    # FAQ items open on tap.
    first = page.locator("details").first
    assert first.get_attribute("open") is None
    first.locator("summary").click()
    assert first.get_attribute("open") is not None


def test_member_desktop_footer_is_the_full_footer(browser, live_server):
    page = _member_page(browser, live_server, DESKTOP)
    assert not page.locator("footer nav[aria-label]").first.is_visible()
    assert page.locator("footer .grid").first.is_visible()
