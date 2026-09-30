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
