"""Playwright: JS/CSS behaviour changed for UX Wave 4 WP1 (advent).

Covers the calendar script parsing (the stray @keyframes used to be a
SyntaxError), the snowflakes (none under reduced motion, 20 on phones, sway
on `translate` instead of `margin-left`), the QR FAB clearing the mobile tab
bar, and the journey-map floating hearts (hidden under reduced motion, 6 of
15 on phones).

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave4_advent_playwright.py -n 0 --create-db
"""

from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.conf import settings
from django.test import Client

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}
NOW = "crush_lu.models.advent.timezone.now"
DEC_5 = datetime(2024, 12, 5, 12, 0, tzinfo=dt_timezone.utc)


class _Holder:
    """make_advent_user() stores attributes on a test-case-like object."""

    def __init__(self):
        self.client = Client()


@pytest.fixture
def advent(transactional_db):
    from crush_lu.tests.test_ux_wave4_advent import make_advent_user

    holder = _Holder()
    make_advent_user(holder)
    return holder


@pytest.fixture
def december():
    with patch(NOW, return_value=DEC_5):
        yield


def _open_calendar(page, live_server, advent):
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": advent.client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{live_server.url}/en/advent/")
    page.wait_for_load_state("load")
    assert page.locator(".advent-grid").count() == 1, page.url
    return errors


def test_calendar_script_parses_and_phone_gets_fewer_flakes(
    page, live_server, advent, december
):
    page.set_viewport_size(PHONE)
    errors = _open_calendar(page, live_server, advent)
    # The stray @keyframes used to make the whole calendar script unparsable.
    assert not [e for e in errors if "unexpected" in e.lower()], errors
    assert page.locator("#snowflakes .snowflake").count() == 20

    # Sway runs on `translate`, leaving layout (margin) alone and the fall
    # `transform` intact.
    page.wait_for_timeout(700)
    styles = page.evaluate(
        """() => [...document.querySelectorAll('#snowflakes .snowflake')].map(el => {
            const cs = getComputedStyle(el);
            return {margin: cs.marginLeft, translate: cs.translate, transform: cs.transform};
        })"""
    )
    assert all(s["margin"] == "0px" for s in styles)
    assert any(s["translate"] not in ("none", "0px") for s in styles)
    assert all(s["transform"] != "none" for s in styles)


def test_desktop_keeps_the_full_snowfall(page, live_server, advent, december):
    page.set_viewport_size(DESKTOP)
    _open_calendar(page, live_server, advent)
    assert page.locator("#snowflakes .snowflake").count() == 60


def test_reduced_motion_skips_the_snowflakes(page, live_server, advent, december):
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size(DESKTOP)
    _open_calendar(page, live_server, advent)
    assert page.locator("#snowflakes .snowflake").count() == 0
    glow = page.locator(".door-link.available .door-glow").first
    assert glow.evaluate("el => getComputedStyle(el).animationName") == "none"


def test_qr_fab_clears_the_mobile_tab_bar(page, live_server, advent, december):
    page.set_viewport_size(PHONE)
    _open_calendar(page, live_server, advent)
    nav = page.locator("nav.bottom-nav")
    fab = page.locator("a.qr-scanner-btn")
    assert nav.is_visible()
    nav_box = nav.bounding_box()
    fab_box = fab.bounding_box()
    assert fab_box["y"] + fab_box["height"] <= nav_box["y"], (fab_box, nav_box)
    assert fab.get_attribute("aria-label") == "Scan QR Code"


HEARTS = "".join('<div class="heart-bg">&#128149;</div>' for _ in range(15))


def _hearts_page(page, live_server):
    page.goto(f"{live_server.url}/static/crush_lu/css/tailwind.css")
    page.set_content(
        f'<html><head><link rel="stylesheet" href="{live_server.url}'
        f'/static/crush_lu/css/tailwind.css"></head><body>'
        f'<div class="journey-hearts">{HEARTS}</div></body></html>'
    )
    page.wait_for_load_state("load")
    return page.locator(".journey-hearts .heart-bg:visible").count()


def test_journey_hearts_are_fewer_on_phones(page, live_server):
    page.set_viewport_size(DESKTOP)
    assert _hearts_page(page, live_server) == 15
    page.set_viewport_size(PHONE)
    assert page.locator(".journey-hearts .heart-bg:visible").count() == 6


def test_journey_hearts_hidden_under_reduced_motion(page, live_server):
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size(DESKTOP)
    assert _hearts_page(page, live_server) == 0
