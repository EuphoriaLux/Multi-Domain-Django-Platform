"""Playwright: old /account/settings/#anchor links land on the right sub-section.

UX Wave 4 · WP9b (finding 8-08). A #fragment never reaches the server, so the
301 cannot map it; the browser re-applies it to the drill-down overview and
the ``legacySettingsAnchor`` Alpine component sends it on to ``sub=``.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave4_settings_retire_playwright.py -n 0
"""

from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright")

from crush_lu.tests.test_page_chrome_playwright import (  # noqa: E402
    _member,
    _page,
)

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


def _land(page, url):
    page.goto(url)
    page.wait_for_function("() => window.Alpine && Alpine.store('drawer')")
    page.wait_for_load_state("load")
    parts = urlsplit(page.url)
    return parts.path, parse_qs(parts.query), parts.fragment


@pytest.mark.parametrize(
    "anchor",
    ["email-notifications", "whatsapp-notifications", "push-notifications"],
)
def test_old_anchor_opens_the_notifications_sub_section(browser, live_server, anchor):
    page = _page(browser, live_server, _member())
    page.goto(f"{live_server.url}/en/account/settings/#{anchor}")
    page.wait_for_url("**sub=notifications**")
    parts = urlsplit(page.url)
    path, query, fragment = parts.path, parse_qs(parts.query), parts.fragment
    assert path == "/en/profile/edit/"
    assert query == {"section": ["account"], "sub": ["notifications"]}
    assert fragment == anchor
    assert page.locator(f"#{anchor}").count() >= 1


def test_unknown_anchor_stays_on_the_account_overview(browser, live_server):
    page = _page(browser, live_server, _member())
    path, query, _fragment = _land(
        page, f"{live_server.url}/en/account/settings/#danger-zone"
    )
    assert path == "/en/profile/edit/"
    assert query == {"section": ["account"]}
    assert page.locator("[x-data='legacySettingsAnchor']").count() == 1
    assert urlsplit(page.url).fragment == ""


def test_plain_old_url_opens_the_overview(browser, live_server):
    page = _page(browser, live_server, _member())
    path, query, fragment = _land(page, f"{live_server.url}/en/account/settings/")
    assert (path, query, fragment) == (
        "/en/profile/edit/",
        {"section": ["account"]},
        "",
    )
