"""Playwright: the motion system (STYLE.md "Motion").

- Buttons and bare ``transition`` utilities run at the default 200ms on the
  house curve, and buttons no longer transition every property.
- Members who ask for reduced motion get the SVG (SMIL) artwork frozen: the
  stylesheet's reduced-motion rule cannot reach ``<animate>`` elements, and the
  ghost in the navbar animates that way on every page.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_motion_playwright.py -n 0 --create-db
"""

import json

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

ANIMATED_SVGS = """
() => [...document.querySelectorAll('svg')]
    .filter(svg => !svg.ownerSVGElement && svg.querySelector('animate, animateTransform'))
    .map(svg => svg.animationsPaused())
"""

BUTTON_TRANSITION = """
(variant) => {
    const button = document.createElement('a');
    button.className = variant;
    document.body.append(button);
    const style = getComputedStyle(button);
    return {
        property: style.transitionProperty,
        duration: style.transitionDuration,
        easing: style.transitionTimingFunction,
    };
}
"""


def _open_home(page, live_server):
    consent = json.dumps({"essential": True, "analytics": False, "marketing": False})
    page.context.add_cookies(
        [{"name": "cookie_consent", "value": consent, "url": live_server.url}]
    )
    page.goto(f"{live_server.url}/en/")
    page.wait_for_load_state("load")


@pytest.mark.parametrize(
    "variant",
    ["btn-crush-primary", "btn-crush-solid", "btn-crush-outline", "btn-danger"],
)
def test_buttons_use_the_default_duration_and_curve(
    page, live_server, transactional_db, variant
):
    _open_home(page, live_server)

    transition = page.evaluate(BUTTON_TRANSITION, variant)

    assert "all" not in transition["property"].split(", ")
    assert set(transition["duration"].split(", ")) == {"0.2s"}
    # Every listed property eases on the house curve and nothing else.
    curve = "cubic-bezier(0.2, 0, 0, 1)"
    assert transition["easing"].replace(curve, "").strip(", ") == ""


def test_svg_artwork_keeps_moving_by_default(page, live_server, transactional_db):
    _open_home(page, live_server)

    paused = page.evaluate(ANIMATED_SVGS)

    assert paused, "expected animated SVG artwork on the home page"
    assert not any(paused)


def test_reduced_motion_freezes_svg_artwork(browser, live_server, transactional_db):
    context = browser.new_context(reduced_motion="reduce")
    try:
        page = context.new_page()
        _open_home(page, live_server)

        paused = page.evaluate(ANIMATED_SVGS)

        assert paused, "expected animated SVG artwork on the home page"
        assert all(paused)
    finally:
        context.close()


def test_notifications_dropdown_fades_out_on_close(page, live_server, transactional_db):
    """It used to vanish in one frame: every other dropdown had a leave step."""
    from django.conf import settings
    from django.test import Client

    from crush_lu.tests.test_ux_wave4_polls_timeline import make_member

    client = Client()
    client.force_login(make_member())
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    page.set_viewport_size({"width": 1280, "height": 800})
    _open_home(page, live_server)
    bell = page.locator(".crush-navbar").get_by_role("button", name="Notifications")
    panel = page.locator(".crush-navbar [x-show='isOpen']").filter(
        has_text="Notifications"
    )

    bell.click()
    expect(panel).to_be_visible()
    # Watch the close: record every opacity the panel passes through.
    page.evaluate("""
        () => {
            const panel = [...document.querySelectorAll(".crush-navbar [x-show='isOpen']")]
                .find(el => el.textContent.includes('Notifications'));
            window.__opacities = [];
            const sample = () => {
                window.__opacities.push(Number(getComputedStyle(panel).opacity));
                if (getComputedStyle(panel).display !== 'none') requestAnimationFrame(sample);
            };
            requestAnimationFrame(sample);
        }
        """)
    bell.click()

    expect(panel).to_be_hidden()
    opacities = page.evaluate("window.__opacities")
    assert any(0 < value < 1 for value in opacities), opacities
