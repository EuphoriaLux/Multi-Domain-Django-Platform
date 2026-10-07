"""Exercise the CSP deck, mobile controls and real decision/undo endpoints."""

from io import BytesIO

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import Client
from PIL import Image
from playwright.sync_api import expect

from crush_lu.models import CrushProfile, ProfilePhotoReviewLog
from crush_lu.tests.test_coach_photo_review import _make_candidate, _make_coach

pytestmark = [pytest.mark.playwright, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("language", ["en", "de", "fr"])
def test_mobile_deck_controls_and_undo(
    page, live_server, settings, tmp_path, monkeypatch, language
):
    cache.clear()
    url = live_server.url.replace("localhost", "crush.localhost")
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.CSRF_TRUSTED_ORIGINS = [url]
    storage = FileSystemStorage(location=tmp_path / "media")
    for name in ("photo_1", "photo_2"):
        monkeypatch.setattr(CrushProfile._meta.get_field(name), "storage", storage)
    monkeypatch.setattr(
        "crush_lu.services.photo_review.notify_photo_revision", lambda **kwargs: None
    )
    monkeypatch.setattr(
        "crush_lu.notification_service.NotificationService._send_email",
        lambda *args: False,
    )
    coach, profile = _make_coach(), _make_candidate()
    from crush_lu.models.crush_connect import Interest

    interest = Interest.objects.create(
        slug="mobile-review-interest",
        label="Hiking",
        label_de="Wandern",
        label_fr="Randonnée",
        category="outdoors",
    )
    profile.interests_new.add(interest)
    for field, color in (("photo_1", "purple"), ("photo_2", "pink")):
        image = BytesIO()
        Image.new("RGB", (160, 200), color).save(image, format="PNG")
        key = storage.save(f"{field}.png", ContentFile(image.getvalue()))
        setattr(profile, field, key)
    profile.save(update_fields=["photo_1", "photo_2"])
    client = Client()
    client.force_login(coach.user)
    page.context.add_cookies(
        [
            {
                "name": "sessionid",
                "value": client.cookies["sessionid"].value,
                "url": url,
            },
        ]
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.set_default_timeout(10000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    response = page.goto(f"{url}/{language}/coach/photo-review/")
    assert response.status == 200
    page.locator("#cookie-btn-decline").click()
    deck = page.locator('[x-data="photoSwipeDeck"]')
    deck.focus()
    expect(deck.locator("article")).to_be_visible()
    photo = deck.locator("article img")
    expect(photo).to_have_attribute(
        "src", f"/{language}/media/profile/{profile.user_id}/photo_1/"
    )
    expect(photo).to_have_js_property("naturalWidth", 160)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(deck.locator(r'[\x-text="cardInterests"]')).to_have_text(
        {"en": "Hiking", "de": "Wandern", "fr": "Randonnée"}[language]
    )
    expect(deck.locator(r'[\x-text="cardDobLabel"]')).to_contain_text(
        {"en": "Born", "de": "Geboren", "fr": "Né"}[language]
    )
    page.screenshot(path=str(tmp_path / "deck.png"), full_page=True)

    # The keyboard shortcut advances the displayed photo, without submitting.
    page.keyboard.press("Space")
    expect(photo).to_have_attribute(
        "src", f"/{language}/media/profile/{profile.user_id}/photo_2/"
    )
    assert not ProfilePhotoReviewLog.objects.exists()
    # Secondary images are context only; decisions require the bound primary.
    expect(deck.locator(r'[\@click="approveCurrentCard"]')).to_be_disabled()
    page.keyboard.press("k")
    expect(deck.locator('[role="dialog"]')).to_be_hidden()
    deck.locator(r'[\@click="showPrimaryPhoto"]').click()
    expect(deck).to_be_focused()
    page.keyboard.press("k")
    dialog = page.get_by_role(
        "dialog",
        name={
            "en": "Flag or Request Revision",
            "de": "Markieren oder Änderung anfordern",
            "fr": "Signaler ou demander une modification",
        }[language],
    )
    expect(dialog).to_be_visible()
    expect(dialog.locator('input[value="unclear_face"]')).to_be_checked()
    dialog.locator('input[value="group_photo"]').check()
    dialog.locator("textarea").fill("Please show your face clearly.")
    dialog.locator(r'[\@click="submitFlagDecision"]').click()
    expect(deck.locator("article")).to_have_count(0)
    profile.refresh_from_db()
    assert profile.photo_review_status == "needs_revision"
    assert profile.photo_review_notes == "Please show your face clearly."
    assert ProfilePhotoReviewLog.objects.get().reason == "group_photo"
    expect(deck.locator(r'[\@click="undoLastDecision"]')).to_be_enabled()
    page.keyboard.press("u")
    expect(deck.locator("article")).to_be_visible()
    profile.refresh_from_db()
    assert profile.photo_review_status == "pending"
    assert ProfilePhotoReviewLog.objects.get().undone_at is not None

    # A pointer swipe approves through the actual CSRF-protected endpoint.
    expect(deck.locator(r'[\@click="approveCurrentCard"]')).to_be_enabled()
    photo.scroll_into_view_if_needed()
    box = photo.bounding_box()
    page.mouse.move(box["x"] + 30, box["y"] + 80)
    page.mouse.down()
    page.mouse.move(box["x"] + 160, box["y"] + 80, steps=8)
    page.mouse.up()
    expect(deck.locator("article")).to_have_count(0)
    profile.refresh_from_db()
    assert profile.is_photo_review_approved
    assert not profile.is_photo_verified
    expect(deck.locator(r'[\@click="undoLastDecision"]')).to_be_enabled()
    deck.locator(r'[\@click="undoLastDecision"]').click()
    expect(deck.locator("article")).to_be_visible()
    deck.locator(r'[\@click="openFlagModal"]').click()
    expect(dialog).to_be_visible()
    expect(dialog.locator('input[value="fake_profile"]')).to_be_checked()
    expect(dialog.locator("textarea")).to_have_value("")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / "modal.png"), full_page=True)
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    if language == "en":
        # Skip the entire first batch without deciding; the next profiles must
        # be reachable, and exhausted skips can be revisited explicitly.
        for i in range(31):
            _make_candidate(username=f"pagination_{i}", photo_key=profile.photo_1.name)
        page.goto(f"{url}/{language}/coach/photo-review/")
        initial_cards = page.evaluate(
            "JSON.parse(document.getElementById('photo-review-cards').textContent)"
        )
        assert len(initial_cards) == 30
        logs_before = ProfilePhotoReviewLog.objects.count()
        skip = deck.locator(r'[\@click="skipCard"]')
        for _ in range(30):
            expect(skip).to_be_enabled()
            skip.click()
        expect(deck.locator("article")).to_be_visible()
        visible_name = deck.locator(r'[\x-text="cardName"]').inner_text()
        assert visible_name not in {card["display_name"] for card in initial_cards}
        assert ProfilePhotoReviewLog.objects.count() == logs_before
        for _ in range(2):
            expect(skip).to_be_enabled()
            skip.click()
        expect(deck.locator("article")).to_have_count(0)
        deck.locator(r'[\@click="restartQueue"]').click()
        expect(deck.locator("article")).to_be_visible()
    assert not errors
