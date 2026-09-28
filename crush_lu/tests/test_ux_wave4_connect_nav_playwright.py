"""Browser check for the Connect subnav on the narrowest phones (UX Wave 4 ·
WP10, PR #1091 review): below ~340px the four FR tabs plus counts overflow
the strip, which scrolls sideways. A freshly loaded Chats page must start
with its current tab fully visible, not clipped at ``scrollLeft = 0``.

Run with:
    pytest crush_lu/tests/test_ux_wave4_connect_nav_playwright.py -m playwright
"""

import pytest

pytest.importorskip("playwright")

from crush_lu.tests.test_connect_review_choice_playwright import (  # noqa: E402
    _log_in,
)
from crush_lu.tests.test_crush_connect import _grant_consent, _make_user  # noqa: E402
from crush_lu.tests.test_ux_wave4_connect_nav import _connect_activity  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


def test_current_tab_is_scrolled_into_view_at_320px_fr(page, live_server, settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    me = _make_user(username="nav_pw_320")
    _connect_activity(me)
    _grant_consent(me)
    _log_in(page, live_server, me)
    page.set_viewport_size({"width": 320, "height": 640})

    response = page.goto(f"{live_server.url}/fr/crush-connect/week/chats/")
    assert response is not None and response.ok
    page.wait_for_load_state("load")

    nav = page.locator("nav.connect-local-nav")
    tab = nav.locator('a[aria-current="page"]')
    assert tab.inner_text().startswith("Chats")
    strip, current = nav.bounding_box(), tab.bounding_box()
    overflow = nav.evaluate("n => n.scrollWidth - n.clientWidth")

    assert overflow > 0, "the strip should overflow at 320px FR for this check"
    assert current["x"] >= strip["x"] - 0.5
    assert current["x"] + current["width"] <= strip["x"] + strip["width"] + 0.5
