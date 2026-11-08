"""Connect card photos: quiet coach-reviewed markers and the full-screen viewer.

On the weekly review's half-width cards, a "Coach-reviewed photo" caption on
each secondary photo wrapped to four lines (DE "Vom Coach geprüftes Foto")
and covered the face. The marker is now a corner icon whose label is kept for
screen readers, and every card photo opens full screen
(crush_connect/_photo_viewer.html + connect-photo-viewer.js).

Requests use literal ``/en/`` paths (AGENTS.md: ``reverse()`` resolves the
default urlconf, not the one the host selects).
"""

import html as html_lib

import pytest
from django.core.cache import cache
from django.template import Context, Template
from django.template.loader import render_to_string

from crush_lu.models import PublishedProfilePhoto
from crush_lu.tests.photo_storage_helpers import stub_photo_deletes
from crush_lu.tests.real_person_check_helpers import make_member, profile_of
from crush_lu.tests.test_connect_week_experience import (
    WEEK_HOME_URL,
    WEEK_INBOX_URL,
    WEEK_REVIEW_URL,
)
from crush_lu.tests.test_real_person_check_member_ui import (
    COACH_PICK_URL,
    chat_world,
    home_world,
    inbox_world,
    logged_in,
    pick_world,
    review_world,
)

pytestmark = pytest.mark.django_db

VIEWER_DIALOG = '<dialog class="crush-photo-viewer" data-photo-viewer'
VIEWER_SCRIPT = "crush_lu/js/connect-photo-viewer.js"
PHOTO_BUTTON = "data-photo-viewer-open"
# The caption the secondary photos carried before: a dark pill along the
# bottom of each thumbnail.
OLD_CAPTION_PILL = "absolute inset-x-1 bottom-1"


@pytest.fixture(autouse=True)
def _setup(settings, monkeypatch):
    # Every test's viewer shares one ratelimit counter (AGENTS.md trap), and
    # adding a photo must not touch storage.
    cache.clear()
    stub_photo_deletes(monkeypatch, lambda name: None)
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True


def text_of(response):
    assert response.status_code == 200
    return html_lib.unescape(response.content.decode())


def publish_secondary_photos(user, fields=("photo_2", "photo_3")):
    """Coach-approved secondary photos, as other members see them."""
    profile = profile_of(user)
    for field in fields:
        key = f"users/{user.pk}/photos/{user.username}-{field}.jpg"
        setattr(profile, field, key)
        profile.save(update_fields=[field])
        PublishedProfilePhoto.objects.create(
            profile=profile, photo_field=field, photo_key=key
        )
    return profile


# ---------------------------------------------------------------------------
# Weekly review card (the half-width grid)
# ---------------------------------------------------------------------------


def test_review_secondary_photos_carry_an_icon_not_a_caption(client):
    viewer, session = review_world([None])
    publish_secondary_photos(session.cards.get().target_user)

    body = text_of(logged_in(client, viewer).get(WEEK_REVIEW_URL))

    assert OLD_CAPTION_PILL not in body
    # The label stays, for screen readers only, once per secondary photo.
    assert (
        body.count('<figcaption class="sr-only">Coach-reviewed photo</figcaption>') == 2
    )
    # The main photo's marker is the same icon, not the text pill.
    assert "inline-flex rounded-full bg-black/70 px-2 py-0.5" not in body


def test_review_explains_the_icon_and_the_tap_once(client):
    viewer, _session = review_world([None, None])

    body = text_of(logged_in(client, viewer).get(WEEK_REVIEW_URL))

    assert body.count("Tap a photo to view it full screen.") == 1


def test_review_explanation_is_translated(client):
    viewer, _session = review_world([None])
    logged_in(client, viewer)

    de = text_of(client.get("/de/crush-connect/week/review/"))
    fr = text_of(client.get("/fr/crush-connect/week/review/"))

    assert "Tippe auf ein Foto, um es im Vollbild zu sehen." in de
    assert "Vom Coach geprüftes Foto" in de
    assert "Appuyez sur une photo pour l’afficher en plein écran." in fr


def test_every_review_photo_opens_full_screen_as_one_gallery(client):
    viewer, session = review_world([None])
    publish_secondary_photos(session.cards.get().target_user)

    body = text_of(logged_in(client, viewer).get(WEEK_REVIEW_URL))

    # Main photo plus two secondary photos, grouped by the card.
    assert body.count(PHOTO_BUTTON) == 3
    assert body.count("data-photo-viewer-group") == 1
    assert body.count(VIEWER_DIALOG) == 1
    assert body.count(VIEWER_SCRIPT) == 1


# ---------------------------------------------------------------------------
# Every page with a zoomable photo carries the viewer
# ---------------------------------------------------------------------------


def _week_home():
    viewer, _targets = home_world([None])
    return viewer, WEEK_HOME_URL


def _week_review():
    viewer, _session = review_world([None])
    return viewer, WEEK_REVIEW_URL


def _inbox():
    recipient, _requesters = inbox_world([None])
    return recipient, WEEK_INBOX_URL


def _coach_pick():
    member, _candidate = pick_world(None)
    return member, COACH_PICK_URL


def _chat():
    me, _partner, _chat, url = chat_world(None)
    return me, url


@pytest.mark.parametrize(
    "world",
    [_week_home, _week_review, _inbox, _coach_pick, _chat],
    ids=["week_home", "week_review", "inbox", "coach_pick", "chat"],
)
def test_a_page_with_zoomable_photos_includes_the_viewer(client, world):
    user, url = world()

    body = text_of(logged_in(client, user).get(url))

    assert PHOTO_BUTTON in body
    assert body.count(VIEWER_DIALOG) == 1
    assert body.count(VIEWER_SCRIPT) == 1


def test_the_viewer_labels_are_translated(client):
    viewer, _session = review_world([None])
    logged_in(client, viewer)

    de = text_of(client.get("/de/crush-connect/week/review/"))

    assert 'aria-label="Foto im Vollbild"' in de
    assert "Foto im Vollbild ansehen" in de


# ---------------------------------------------------------------------------
# Where a photo must not become a button
# ---------------------------------------------------------------------------


def _candidate_card(user, **extra):
    profile = profile_of(user)
    return render_to_string(
        "crush_lu/crush_connect/_candidate_card.html",
        {
            "target": user,
            "target_profile": profile,
            "target_membership": user.crush_connect_membership,
            **extra,
        },
    )


def test_the_showcase_card_does_not_open_photos():
    """The guest swipes the Showcase card, so a tap must not open the viewer."""
    member = make_member("viewer_showcase", gender="F")
    publish_secondary_photos(member)

    showcase = _candidate_card(member, showcase=True)
    elsewhere = _candidate_card(member)

    assert PHOTO_BUTTON not in showcase
    assert "?view=public" in showcase
    assert elsewhere.count(PHOTO_BUTTON) == 3
    assert "?view=public" not in elsewhere


def test_a_placeholder_is_never_a_photo_button():
    member = make_member("viewer_placeholder", gender="F")
    template = Template(
        "{% load crush_media %}{% profile_photo profile field zoomable=True %}"
    )

    with_photo = template.render(
        Context({"profile": profile_of(member), "field": "photo_1"})
    )
    without = template.render(
        Context({"profile": profile_of(member), "field": "photo_2"})
    )

    assert PHOTO_BUTTON in with_photo and "<img" in with_photo
    assert PHOTO_BUTTON not in without and "<img" not in without


def test_photos_are_not_buttons_unless_asked():
    member = make_member("viewer_default", gender="F")
    template = Template("{% load crush_media %}{% profile_photo profile 'photo_1' %}")

    assert PHOTO_BUTTON not in template.render(Context({"profile": profile_of(member)}))
