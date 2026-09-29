"""Playwright: UX Wave 4 WP7a layout behaviour a template test can't prove.

* 1-07 — the How It Works timeline connector ends inside its grid at 1440 px
  (it used to start mid-grid and run a full grid width to the right) and is
  not drawn at phone width; the page never scrolls sideways.
* 1-08 — the legal pages' "Back to contents" link shows on phones, jumps to
  the table of contents, and is hidden on desktop, where the ToC stays close.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave4_static_pages_playwright.py -n 0
"""

import json

import pytest

pytest.importorskip("playwright")

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1440, "height": 900}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})

CONNECTOR_JS = """
() => {
    const grid = document.querySelector(".step-timeline");
    const after = getComputedStyle(grid, "::after");
    const box = grid.getBoundingClientRect();
    const left = parseFloat(after.left), width = parseFloat(after.width);
    return {
        content: after.content,
        gridWidth: box.width,
        left: left,
        right: left + width,
        docWidth: document.documentElement.scrollWidth,
        viewport: window.innerWidth,
    };
}
"""


def _goto(page, live_server, path):
    page.context.add_cookies(
        [{"name": "cookie_consent", "value": DECLINED, "url": live_server.url}]
    )
    page.goto(f"{live_server.url}{path}")
    page.wait_for_load_state("load")


def test_timeline_connector_stays_inside_grid_on_desktop(page, live_server):
    page.set_viewport_size(DESKTOP)
    _goto(page, live_server, "/en/how-it-works/")
    m = page.evaluate(CONNECTOR_JS)
    assert m["content"] not in ("none", "normal"), m
    assert m["left"] > 0, m
    assert m["right"] <= m["gridWidth"] + 0.5, m
    assert m["docWidth"] <= m["viewport"], m


def test_timeline_connector_absent_and_no_sideways_scroll_on_phone(page, live_server):
    page.set_viewport_size(PHONE)
    _goto(page, live_server, "/en/how-it-works/")
    m = page.evaluate(CONNECTOR_JS)
    assert m["content"] in ("none", "normal"), m
    assert m["docWidth"] <= m["viewport"], m


@pytest.mark.parametrize("path", ["/en/privacy-policy/", "/en/terms-of-service/"])
def test_back_to_contents_on_phone_only(page, live_server, path):
    page.set_viewport_size(PHONE)
    _goto(page, live_server, path)
    back = page.locator("a.legal-back-to-contents")
    assert back.is_visible()
    page.mouse.wheel(0, 6000)
    page.wait_for_timeout(300)
    back.click()
    # Smooth scrolling may take a moment; wait until the ToC is in view.
    page.wait_for_function(
        "() => { const t = document.getElementById('legal-contents')"
        ".getBoundingClientRect().top; return t >= 0 && t < 422; }",
        timeout=5000,
    )
    # Bullets are back on the legal lists (preflight strips them).
    style = page.evaluate(
        "() => getComputedStyle(document.querySelector('.legal-prose ul:not(.list-none)')).listStyleType"
    )
    assert style == "disc", style

    page.set_viewport_size(DESKTOP)
    assert not back.is_visible()
