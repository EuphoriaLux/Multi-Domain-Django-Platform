"""Playwright: what the surface and radius tokens render as.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_surfaces_playwright.py -n 0 --create-db
"""

import json

import pytest

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PROBE = """
(className) => {
    const el = document.createElement('div');
    el.className = className;
    document.body.append(el);
    const style = getComputedStyle(el);
    const seen = {
        display: style.display,
        radius: style.borderTopLeftRadius,
        background: style.backgroundColor,
    };
    el.remove();
    return seen;
}
"""


def _open_home(page, live_server, theme):
    consent = json.dumps({"essential": True, "analytics": False, "marketing": False})
    page.context.add_cookies(
        [{"name": "cookie_consent", "value": consent, "url": live_server.url}]
    )
    page.add_init_script(f"localStorage.setItem('theme', '{theme}')")
    page.goto(f"{live_server.url}/en/")
    page.wait_for_load_state("load")


def test_cards_are_true_white_on_the_lavender_page(page, live_server, transactional_db):
    _open_home(page, live_server, "light")

    assert page.evaluate(PROBE, "bg-white")["background"] == "rgb(255, 255, 255)"
    assert page.evaluate(PROBE, "card")["background"] == "rgb(255, 255, 255)"
    canvas = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert canvas == "rgb(237, 232, 244)"


def test_dark_cards_are_not_white(page, live_server, transactional_db):
    _open_home(page, live_server, "dark")

    assert page.evaluate(PROBE, "card")["background"] != "rgb(255, 255, 255)"


@pytest.mark.parametrize(
    "utility,expected",
    [
        ("rounded-md", "12px"),
        ("rounded-lg", "15px"),
        ("rounded-xl", "20px"),
        ("rounded-2xl", "25px"),
        ("rounded-3xl", "28px"),
    ],
)
def test_radius_utilities_render_the_documented_scale(
    page, live_server, transactional_db, utility, expected
):
    _open_home(page, live_server, "light")

    assert page.evaluate(PROBE, utility)["radius"] == expected


@pytest.mark.parametrize(
    "width,expected",
    [(1280, ["table-cell", "block", "inline-flex"]), (390, ["none", "none", "none"])],
)
def test_hidden_until_a_breakpoint_shows_from_that_breakpoint(
    page, live_server, transactional_db, width, expected
):
    page.set_viewport_size({"width": width, "height": 800})
    _open_home(page, live_server, "light")

    seen = [
        page.evaluate(PROBE, classes)["display"]
        for classes in (
            "hidden sm:table-cell",
            "hidden md:block",
            "hidden sm:inline-flex",
        )
    ]

    assert seen == expected
