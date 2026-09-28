"""Playwright: UX Wave 4 · WP6 (home story).

The home carousel and its controls were removed from the ``ghostStory``
Alpine component. These check in a real browser that the home page renders
the new strip without Alpine errors, and that the compact story on
/profile-submitted/ (the component's remaining consumer) still auto-plays.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave4_home_story_playwright.py -n 0 --create-db
"""

import json
from datetime import date

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}


def _log_in(page, live_server_url, user):
    from django.test import Client

    client = Client()
    client.force_login(user)
    consent = json.dumps({"essential": True, "analytics": False, "marketing": False})
    page.context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": client.cookies["sessionid"].value,
                "url": live_server_url,
            },
            {"name": "cookie_consent", "value": consent, "url": live_server_url},
        ]
    )


def _collect_errors(page):
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on(
        "console",
        lambda msg: (
            errors.append(msg.text)
            if msg.type == "error" and "Alpine" in msg.text
            else None
        ),
    )
    return errors


def test_home_shows_facts_row_and_evening_strip(page, live_server):
    errors = _collect_errors(page)
    page.set_viewport_size(PHONE)
    page.goto(f"{live_server.url}/en/")

    expect(page.locator(".home-facts-row")).to_be_visible()
    expect(page.locator(".home-facts-row")).to_contain_text(
        "Events in EN · FR · DE · LU"
    )
    strip = page.locator(".evening-strip")
    strip.scroll_into_view_if_needed()
    expect(strip).to_be_visible()
    expect(strip.locator("li")).to_have_count(3)
    expect(strip.locator("li svg")).to_have_count(3)
    expect(page.locator("[x-data='ghostStory']")).to_have_count(0)
    expect(page.locator(".ghost-story-section")).to_have_count(0)
    assert errors == []


def test_compact_ghost_story_still_auto_advances(page, live_server):
    from django.contrib.auth import get_user_model

    from crush_lu.models import CrushProfile, ProfileSubmission
    from crush_lu.models.profiles import UserDataConsent

    user = get_user_model().objects.create_user(
        username="wp6-compact@example.com",
        email="wp6-compact@example.com",
        password="pass123",
        first_name="Lea",
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1993, 5, 1),
        gender="F",
        location="Luxembourg City",
        is_approved=False,
        verification_status="pending",
        is_active=True,
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    ProfileSubmission.objects.create(profile=profile, status="revision")

    errors = _collect_errors(page)
    page.set_viewport_size(PHONE)
    _log_in(page, live_server.url, user)
    page.goto(f"{live_server.url}/en/profile-submitted/")

    story = page.locator(".ghost-story-compact").first
    story.scroll_into_view_if_needed()
    expect(story).to_be_visible()
    # Exactly one scene is shown at a time, and it changes on its own
    # (scenes last 4-6 s) with no controls to drive it.
    active = story.locator(".ghost-story-scene-active")
    expect(active).to_have_count(1)
    expect(active).to_be_visible()
    first = active.get_attribute("class")
    page.wait_for_function(
        "c => document.querySelector('.ghost-story-compact .ghost-story-scene-active')"
        ".className !== c",
        arg=first,
        timeout=8000,
    )
    expect(story.locator(".ghost-story-scene-active")).to_be_visible()
    expect(story.locator("button")).to_have_count(0)
    assert errors == []
