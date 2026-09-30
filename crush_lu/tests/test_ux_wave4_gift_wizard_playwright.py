"""Playwright: gift wizard behaviour (UX Wave 4 · WP3, finding 7-06/7-07).

``test_ux_wave4_gift_wizard.py`` pins the markup. Only a browser proves:

* picking a letter-music file shows its name and size (the listener is
  bound to ``id_chapter5_letter_music``), and a non-audio file is refused;
* a server re-render with a media error opens on step 2, moves focus to the
  error summary, and the stepper's ``aria-current`` follows Next/Back.

Excluded from the default run (``-m "not playwright"`` in pytest.ini). Run:
    pytest -m playwright crush_lu/tests/test_ux_wave4_gift_wizard_playwright.py -n 0
"""

import base64
import json

import pytest

pytest.importorskip("playwright")

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402
from playwright.sync_api import expect  # noqa: E402

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]

PHONE = {"width": 390, "height": 844}
DECLINED = json.dumps({"essential": True, "analytics": False, "marketing": False})


def _page(browser, live_server):
    user = get_user_model().objects.create_user(
        username="gift-pw@example.com",
        email="gift-pw@example.com",
        password="testpass123",
        is_staff=True,
    )
    from crush_lu.models import UserDataConsent

    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    context = browser.new_context(viewport=PHONE)
    context.route(
        "**://fonts.g*.com/**",
        lambda route: route.fulfill(status=200, content_type="text/css", body=""),
    )
    client = Client()
    client.force_login(user)
    context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            },
            {"name": "cookie_consent", "value": DECLINED, "url": live_server.url},
        ]
    )
    page = context.new_page()
    page.goto(f"{live_server.url}/en/journey/gift/create/")
    page.wait_for_function(
        "() => window.Alpine && document.querySelector('.step-content.active')"
    )
    return page


def _fill_story(page):
    page.fill("#id_recipient_name", "Marie")
    page.fill("#id_date_first_met", "2024-02-14")
    page.fill("#id_location_first_met", "Luxembourg City")


def test_letter_music_shows_file_info_and_refuses_non_audio(browser, live_server):
    page = _page(browser, live_server)
    steps = page.locator("ol.step-nav > li")
    expect(steps.nth(0)).to_have_attribute("aria-current", "step")

    _fill_story(page)
    page.get_by_role("button", name="Next: Add Media").click()
    expect(steps.nth(1)).to_have_attribute("aria-current", "step")
    expect(steps.nth(0)).not_to_have_attribute("aria-current", "step")

    audio = page.locator("#id_chapter5_letter_music")
    audio.set_input_files(
        {"name": "song.mp3", "mimeType": "audio/mpeg", "buffer": b"ID3" + b"0" * 2045}
    )
    section = page.locator(".media-section").nth(3)
    expect(section.locator(".file-name")).to_have_text("song.mp3")
    expect(section.get_by_text("2 KB", exact=True)).to_be_visible()

    audio.set_input_files(
        {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"}
    )
    expect(section.get_by_text("Invalid audio format.", exact=False)).to_be_visible()

    page.get_by_role("button", name="Back").click()
    expect(steps.nth(0)).to_have_attribute("aria-current", "step")


def test_media_error_reopens_step_two_with_focused_summary(browser, live_server):
    page = _page(browser, live_server)
    _fill_story(page)
    page.get_by_role("button", name="Next: Add Media").click()
    page.locator("#id_chapter1_image").set_input_files(
        {"name": "puzzle.jpg", "mimeType": "image/jpeg", "buffer": b"not an image"}
    )
    page.get_by_role("button", name="Create Gift").click()
    page.wait_for_load_state("load")
    page.wait_for_function(
        "() => window.Alpine && document.querySelector('.step-content.active')"
    )

    summary = page.get_by_role("alert").filter(has_text="Please fix the following")
    expect(summary).to_be_visible()
    expect(summary).to_be_focused()
    expect(page.locator("ol.step-nav > li").nth(1)).to_have_attribute(
        "aria-current", "step"
    )
    # Step 1 is hidden; the recipient name the user typed is kept.
    expect(page.locator("#id_recipient_name")).to_be_hidden()
    expect(page.locator("#id_recipient_name")).to_have_value("Marie")


PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_next_with_missing_story_fields_focuses_the_first_invalid_one(
    browser, live_server
):
    page = _page(browser, live_server)
    page.fill("#id_location_first_met", "Luxembourg City")
    page.get_by_role("button", name="Next: Add Media").click()
    expect(page.locator("#id_recipient_name")).to_be_focused()
    expect(page.locator("#id_recipient_name")).to_have_attribute("aria-invalid", "true")
    expect(page.locator("ol.step-nav > li").nth(0)).to_have_attribute(
        "aria-current", "step"
    )


def test_slideshow_pick_shows_a_thumbnail_and_back_refocuses_step_one(
    browser, live_server
):
    page = _page(browser, live_server)
    _fill_story(page)
    page.get_by_role("button", name="Next: Add Media").click()
    expect(page.locator(".media-info")).to_be_focused()

    tile = page.locator(".slideshow-item").nth(1)
    expect(tile.locator(".file-upload-preview")).to_be_hidden()
    tile.locator("input[type=file]").set_input_files(
        {"name": "p.png", "mimeType": "image/png", "buffer": PIXEL_PNG}
    )
    expect(tile.locator(".file-upload-preview img")).to_be_visible()

    page.locator("#id_chapter5_letter_music").set_input_files(
        {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"}
    )
    expect(page.get_by_text("Invalid audio format.", exact=False)).to_be_visible()

    page.get_by_role("button", name="Back").click()
    expect(page.locator("#id_recipient_name")).to_be_focused()
