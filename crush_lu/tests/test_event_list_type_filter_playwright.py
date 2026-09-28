"""
Playwright: the event-list type-filter chips actually show/hide cards.

crush_lu/tests/test_ux_wave3_event_list.py only asserts on server-rendered
markup (chip HTML, role=tablist/tab attributes) — nothing exercises a real
click on a type-filter chip and checks that the matching cards actually
show/hide via the shared Alpine.store("eventTypeFilter") wired up in
js/alpine/core.js (eventTypeFilterChip / eventTypeFilterCard). That
store/attribute wiring (data-filter-type / data-event-type) passes every
unit test yet could silently do nothing in a real browser (finding WP7-2).

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_event_list_type_filter_playwright.py -n 0 --create-db
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def mixer_event(transactional_db):
    from crush_lu.models import MeetupEvent

    return MeetupEvent.objects.create(
        title="Filter Test Mixer",
        description="A mixer for the type-filter test",
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=10),
        location="Luxembourg City",
        address="1 Rue de la Gare, Luxembourg",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(days=8),
        registration_fee=Decimal("0"),
        is_published=True,
        profile_requirement="none",
    )


@pytest.fixture
def speed_dating_event(transactional_db):
    from crush_lu.models import MeetupEvent

    return MeetupEvent.objects.create(
        title="Filter Test Speed Dating",
        description="A speed dating night for the type-filter test",
        event_type="speed_dating",
        date_time=timezone.now() + timedelta(days=11),
        location="Luxembourg City",
        address="1 Rue de la Gare, Luxembourg",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(days=9),
        registration_fee=Decimal("0"),
        is_published=True,
        profile_requirement="none",
    )


def test_type_filter_chip_click_shows_and_hides_matching_cards(
    page, live_server, mixer_event, speed_dating_event
):
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server.url}/en/events/")

    # event_card.html's own root div also carries a data-event-type
    # attribute (for CSS/other tooling), so scope to the eventTypeFilterCard
    # wrapper specifically to avoid matching both the wrapper and the card.
    mixer_card = page.locator(
        f'[x-data="eventTypeFilterCard"][data-event-type="mixer"]:has-text("{mixer_event.title}")'
    )
    speed_dating_card = page.locator(
        f'[x-data="eventTypeFilterCard"][data-event-type="speed_dating"]:has-text("{speed_dating_event.title}")'
    )
    expect(mixer_card).to_be_visible()
    expect(speed_dating_card).to_be_visible()

    mixer_chip = page.locator('[data-filter-type="mixer"]')
    expect(mixer_chip).to_be_visible()
    mixer_chip.click()

    expect(mixer_card).to_be_visible()
    expect(speed_dating_card).to_be_hidden()

    all_chip = page.locator('[data-filter-type="all"]')
    all_chip.click()

    expect(mixer_card).to_be_visible()
    expect(speed_dating_card).to_be_visible()



def test_time_tabs_use_roving_focus_with_csp_safe_bindings(page, live_server):
    page.goto(f"{live_server.url}/en/events/")
    upcoming = page.locator("#events-tab-upcoming")
    past = page.locator("#events-tab-past")

    expect(upcoming).to_have_attribute("tabindex", "0")
    expect(past).to_have_attribute("tabindex", "-1")
    upcoming.focus()
    upcoming.press("ArrowRight")
    expect(past).to_be_focused()
    expect(upcoming).to_have_attribute("tabindex", "-1")
    expect(past).to_have_attribute("tabindex", "0")

    past.press("Home")
    expect(upcoming).to_be_focused()
    expect(upcoming).to_have_attribute("tabindex", "0")
    expect(past).to_have_attribute("tabindex", "-1")
