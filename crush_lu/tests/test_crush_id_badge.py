"""Crush ID step 3: the public coach-verified badge.

Tom, 2026-11-23: the Verified badge is visible to other members and is
validated in Crush ID. The badge is the coach-verified badge Connect cards
already show (``CrushProfile.real_person_badge``): "in_person" when a coach
checked the current photo at an event door, "video"/"voice" from a coach
check. Crush ID shows the member their own badge and how to get or renew it;
the event recap, matches and connection pages now show it too.

The in-person badge is public with the flags either way; video and voice wait
for ``CONNECT_REAL_PERSON_BADGE_VISIBLE`` as before.

Literal ``/en/...`` paths; testserver resolves to crush.lu.
"""

import html
import re

import pytest
from django.core.cache import cache
from django.urls import reverse

from crush_lu.models import CrushProfile
from crush_lu.services import event_swipes
from crush_lu.tests.real_person_check_helpers import make_member, set_photo
from crush_lu.tests.test_crush_connect import _login_eligible
from crush_lu.tests.test_event_lobby import (
    _attend,
    _end_event,
    _login,
    _make_event,
    _make_member,
)
from crush_lu.tests.test_real_person_check_member_ui import LABELS, decorate

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

HUB_URL = "/en/id/"
IN_PERSON = LABELS["en"]["in_person"]
VIDEO = LABELS["en"]["video"]


@pytest.fixture(autouse=True)
def _setup(settings):
    settings.AZURE_ACCOUNT_NAME = ""
    cache.clear()


@pytest.fixture
def flags_on(settings):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True


@pytest.fixture
def flags_off(settings):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False


def _text(resp):
    assert resp.status_code == 200, resp.status_code
    return html.unescape(resp.content.decode())


def _badge_card(body):
    start = body.index("data-crush-id-badge=")
    return body[start : body.index("</section>", start)]


def _state(body):
    return re.search(r'data-crush-id-badge="([a-z_]+)"', body).group(1)


def _hub_for(client, user):
    _login_eligible(client, user)
    return _text(client.get(HUB_URL))


# ---------------------------------------------------------------------------
# Crush ID: "What other members see"
# ---------------------------------------------------------------------------


def test_no_badge_explains_how_to_get_one(client, flags_off):
    body = _hub_for(client, make_member("cidb_none"))

    card = _badge_card(body)
    assert _state(body) == "none"
    assert "No badge yet" in card
    assert 'href="/en/events/"' in card
    assert "data-crush-id-badge-preview" not in card


def test_door_badge_is_previewed_with_the_flags_off(client, flags_off):
    body = _hub_for(client, decorate(make_member("cidb_door"), "in_person"))

    card = _badge_card(body)
    assert _state(body) == "shown"
    assert "data-crush-id-badge-preview" in card
    assert IN_PERSON in card
    assert "A coach checked your photo at an event" in card
    assert "data-crush-id-badge-action" not in card


def test_video_badge_is_previewed_when_badges_are_on(client, flags_on):
    body = _hub_for(client, decorate(make_member("cidb_video"), "video"))

    card = _badge_card(body)
    assert _state(body) == "shown"
    assert VIDEO in card
    assert "video call" in card


def test_granted_call_badge_says_it_is_not_shown_yet(client, flags_off):
    body = _hub_for(client, decorate(make_member("cidb_hidden"), "video"))

    card = _badge_card(body)
    assert _state(body) == "hidden"
    assert VIDEO not in card
    assert "not shown to other members yet" in card
    assert 'href="/en/events/"' in card


def test_new_main_photo_pauses_the_door_badge(client, flags_off):
    user = decorate(make_member("cidb_photo"), "in_person")
    set_photo(user, f"users/{user.pk}/photos/new.jpg")

    body = _hub_for(client, user)

    card = _badge_card(body)
    assert _state(body) == "photo_changed"
    assert "badge is paused" in card
    assert IN_PERSON not in card


@pytest.mark.parametrize(
    ("lang", "expected"),
    (("de", "Was andere Mitglieder sehen"), ("fr", "Ce que voient les autres membres")),
)
def test_badge_card_is_translated(client, flags_off, lang, expected):
    _login_eligible(client, make_member(f"cidb_{lang}"))

    body = _text(client.get(f"/{lang}/id/"))

    assert expected in body
    assert "No badge yet" not in body


# ---------------------------------------------------------------------------
# Event recap, matches and connection pages
# ---------------------------------------------------------------------------


def _with_photo(user):
    set_photo(user, f"users/{user.pk}/photos/{user.username}.jpg")
    return user


@pytest.fixture
def room():
    event = _end_event(_make_event())
    anna = _with_photo(_make_member("anna", gender="F", membership=False, luxid=False))
    carl = _with_photo(_make_member("carl", gender="M", membership=False, luxid=False))
    dan = _with_photo(_make_member("dan", gender="M", membership=False, luxid=False))
    for user in (anna, carl, dan):
        _attend(user, event)
    decorate(carl, "in_person")
    return {"event": event, "anna": anna, "carl": carl, "dan": dan}


def _deck_url(event):
    return reverse("crush_lu:event_attendees", kwargs={"event_id": event.pk})


def _match(room):
    event_swipes.record_swipe(room["anna"], room["event"], room["carl"], liked=True)
    return event_swipes.record_swipe(
        room["carl"], room["event"], room["anna"], liked=True
    )["connection"]


def test_event_recap_card_shows_the_door_badge(client, flags_off, room):
    _login(client, room["anna"])

    body = _text(client.get(_deck_url(room["event"])))

    # Carl has the badge, Dan does not: one pill on the deck.
    assert body.count(IN_PERSON) == 1
    assert "data-public-badge" in body


def test_badge_hides_beside_a_placeholder_photo(client, flags_off, room):
    CrushProfile.objects.filter(user=room["carl"]).update(photo_1="")
    _login(client, room["anna"])

    body = _text(client.get(_deck_url(room["event"])))

    assert IN_PERSON not in body


def test_match_and_connection_pages_show_the_badge(client, flags_off, room):
    conn = _match(room)
    _login(client, room["anna"])

    recap = _text(client.get(_deck_url(room["event"])))
    detail = _text(
        client.get(
            reverse("crush_lu:connection_detail", kwargs={"connection_id": conn.pk})
        )
    )
    connections = _text(client.get(reverse("crush_lu:my_connections")))

    assert IN_PERSON in recap.split("data-swipe-matches", 1)[1]
    assert IN_PERSON in detail
    assert IN_PERSON in connections


def test_recap_cost_does_not_grow_with_badges(client, flags_on, room):
    """The deck joins the badge row: a granted badge costs no extra query."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _login(client, room["anna"])
    client.get(_deck_url(room["event"]))  # warm caches
    with CaptureQueriesContext(connection) as before:
        client.get(_deck_url(room["event"]))
    decorate(room["dan"], "video")
    with CaptureQueriesContext(connection) as after:
        body = _text(client.get(_deck_url(room["event"])))

    assert VIDEO in body
    assert len(after.captured_queries) == len(before.captured_queries)


def test_pending_member_gets_no_second_call_to_action(client, flags_off):
    from crush_lu.tests.test_crush_id import _pending_member

    _pending_member(client, "cidb_p")
    body = _text(client.get(HUB_URL))

    card = _badge_card(body)
    assert _state(body) == "none"
    assert "data-crush-id-badge-action" not in card
