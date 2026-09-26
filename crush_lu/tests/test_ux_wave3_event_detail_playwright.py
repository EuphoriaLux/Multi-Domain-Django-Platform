"""Playwright: JS behaviour changed for UX Wave 3 WP6 (event detail).

Covers the two interactive changes: the description "Read more" toggle
(#4-03) and the share button's clipboard fallback + toast (#4-18).

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave3_event_detail_playwright.py -n 0 --create-db
"""

import re
from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}

LONG_DESCRIPTION = (
    "Join us for a wonderful evening of wine tasting paired with speed "
    "dating. We'll explore some of the finest wines the Moselle valley has "
    "to offer, guided by a local sommelier, while you meet new people in a "
    "relaxed, low-pressure setting. Snacks are provided, and the format "
    "leaves plenty of time for longer conversations between rounds. Come "
    "alone or bring a friend — everyone gets matched into the rotation."
)


@pytest.fixture
def upcoming_event(transactional_db):
    from crush_lu.models import MeetupEvent

    return MeetupEvent.objects.create(
        title="Wave 3 WP6 Mixer",
        description=LONG_DESCRIPTION,
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=10),
        location="Luxembourg City",
        address="1 Rue de la Gare, Luxembourg",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(days=8),
        registration_fee=0,
        is_published=True,
        profile_requirement="none",
    )


def test_read_more_expands_the_full_description(page, live_server, upcoming_event):
    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/en/events/{upcoming_event.id}/")

    description = page.locator(".prose")
    toggle = page.get_by_role("button", name="Read more")
    expect(toggle).to_be_visible()
    expect(description).to_have_class(re.compile(r"\bline-clamp-4\b"))

    toggle.click()
    expect(page.get_by_role("button", name="Show less")).to_be_visible()
    expect(description).not_to_have_class(re.compile(r"\bline-clamp-4\b"))


def test_share_button_falls_back_to_clipboard_copy_and_shows_toast(
    page, live_server, upcoming_event
):
    page.set_viewport_size(PHONE)
    # Simulate a browser without the Web Share API (desktop Firefox/Chrome),
    # and stub the clipboard so the test doesn't need OS clipboard access.
    page.add_init_script(
        "delete window.navigator.share;"
        "window.__copiedText = null;"
        "Object.defineProperty(window.navigator, 'clipboard', {"
        "  value: { writeText: (text) => { window.__copiedText = text; "
        "return Promise.resolve(); } },"
        "  configurable: true"
        "});"
    )
    page.goto(f"{live_server.url}/en/events/{upcoming_event.id}/")

    share_btn = page.locator("#shareEventBtn")
    expect(share_btn).to_be_visible()
    share_btn.click()

    expect(page.locator("#toast-container")).to_contain_text("Link copied")
    copied = page.evaluate("window.__copiedText")
    assert copied == page.url
