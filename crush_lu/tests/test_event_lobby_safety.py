"""Report and block inside the Event Lobby, addressed by opaque handle.

Anyone in the room can be reported, because a photo is visible before any
reveal. Only a revealed person can be blocked: the Blocked members page lists
people by first name, so blocking an anonymous tile would unmask them (§13).

Literal paths + HTTP_HOST='crush.lu' per AGENTS.md.
"""

import json

import pytest
from django.core.cache import cache
from django.test import Client, override_settings

from crush_lu.models import UserBlock, UserReport
from crush_lu.services import event_lobby as lobby

from .test_event_lobby import (
    _end_event,
    _handle_of,
    _join,
    _login,
    _make_event,
    _make_member,
)

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


@pytest.fixture(autouse=True)
def _lobby_on(settings):
    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    settings.CRUSH_CONNECT_LAUNCHED = True


@pytest.fixture(autouse=True)
def _clear_ratelimit_cache():
    cache.clear()
    yield


@pytest.fixture
def room():
    event = _make_event()
    alice = _make_member("alice")
    chloe = _make_member("chloe")
    _join(alice, event)
    _join(chloe, event)
    client = Client(HTTP_HOST="crush.lu")
    _login(client, alice)
    return event, alice, chloe, client


def _post(client, event, action, **payload):
    return client.post(
        f"/en/events/{event.pk}/lobby/api/{action}/",
        data=json.dumps(payload),
        content_type="application/json",
    )


def _reveal(a, b, event):
    lobby.send_meet_signal(a, event, _handle_of(b, event))
    lobby.send_meet_signal(b, event, _handle_of(a, event))


class TestReport:
    def test_anonymous_tile_can_be_reported(self, room):
        event, alice, chloe, client = room
        response = _post(
            client,
            event,
            "report",
            handle=_handle_of(chloe, event),
            reason="inappropriate_photos",
            details="  not a real photo  ",
        )
        assert response.status_code == 200
        report = UserReport.objects.get()
        assert report.reporter == alice
        assert report.reported_user == chloe
        assert report.reason == "inappropriate_photos"
        assert report.details == "not a real photo"
        assert report.source == "event_lobby"
        assert report.source_id == event.pk

    def test_reply_never_says_who_the_handle_belongs_to(self, room):
        event, _alice, chloe, client = room
        response = _post(client, event, "report", handle=_handle_of(chloe, event))
        body = response.content.decode()
        assert json.loads(body) == {"ok": True, "result": "reported", "blocked": False}
        assert chloe.first_name not in body
        assert response["Cache-Control"] == "private, no-store"

    def test_unknown_reason_falls_back_to_other(self, room):
        event, _alice, chloe, client = room
        _post(client, event, "report", handle=_handle_of(chloe, event), reason="nope")
        assert UserReport.objects.get().reason == "other"

    def test_also_block_is_ignored_for_an_anonymous_tile(self, room):
        event, alice, chloe, client = room
        response = _post(
            client,
            event,
            "report",
            handle=_handle_of(chloe, event),
            also_block=True,
        )
        assert response.json()["blocked"] is False
        assert UserReport.objects.filter(reported_user=chloe).exists()
        assert not UserBlock.objects.filter(blocker=alice).exists()

    def test_also_block_applies_once_revealed(self, room):
        event, alice, chloe, client = room
        _reveal(alice, chloe, event)
        response = _post(
            client,
            event,
            "report",
            handle=_handle_of(chloe, event),
            also_block=True,
        )
        assert response.json()["blocked"] is True
        assert UserBlock.objects.filter(blocker=alice, blocked=chloe).exists()

    def test_details_are_capped(self, room):
        event, _alice, chloe, client = room
        _post(
            client,
            event,
            "report",
            handle=_handle_of(chloe, event),
            details="x" * 3000,
        )
        assert len(UserReport.objects.get().details) == 2000


class TestBlock:
    def test_anonymous_tile_cannot_be_blocked(self, room):
        """The refusal is what stops the Blocked members page unmasking them."""
        event, alice, chloe, client = room
        response = _post(client, event, "block", handle=_handle_of(chloe, event))
        unknown = _post(client, event, "block", handle="no-such-handle")
        assert response.status_code == 404
        assert response.json() == unknown.json()
        assert not UserBlock.objects.filter(blocker=alice).exists()

    def test_mutual_can_be_blocked_and_leaves_the_room(self, room):
        event, alice, chloe, client = room
        _reveal(alice, chloe, event)
        response = _post(client, event, "block", handle=_handle_of(chloe, event))
        assert response.status_code == 200
        assert UserBlock.objects.filter(blocker=alice, blocked=chloe).exists()

        state = client.get(f"/en/events/{event.pk}/lobby/api/state/").json()
        assert state["roster"] == []
        assert state["mutuals"] == []

    def test_blocked_person_cannot_be_reported_again_through_the_room(self, room):
        event, alice, chloe, client = room
        _reveal(alice, chloe, event)
        handle = _handle_of(chloe, event)
        _post(client, event, "block", handle=handle)
        assert _post(client, event, "report", handle=handle).status_code == 404


class TestAccess:
    @pytest.mark.parametrize("action", ["report", "block"])
    def test_self_and_unknown_are_indistinguishable(self, room, action):
        event, alice, _chloe, client = room
        own = _post(client, event, action, handle=_handle_of(alice, event))
        unknown = _post(client, event, action, handle="no-such-handle")
        assert own.status_code == unknown.status_code == 404
        assert own.json() == unknown.json()

    @pytest.mark.parametrize("action", ["report", "block"])
    def test_a_handle_from_another_event_does_not_resolve(self, room, action):
        event, _alice, _chloe, client = room
        other_event = _make_event()
        stranger = _make_member("stranger")
        _join(stranger, other_event)
        response = _post(
            client, event, action, handle=_handle_of(stranger, other_event)
        )
        assert response.status_code == 404

    @pytest.mark.parametrize("action", ["report", "block"])
    def test_non_participant_is_refused(self, room, action):
        event, _alice, chloe, _client = room
        outsider = _make_member("outsider")
        client = Client(HTTP_HOST="crush.lu")
        _login(client, outsider)
        response = _post(client, event, action, handle=_handle_of(chloe, event))
        assert response.status_code == 403
        assert not UserReport.objects.exists()

    @pytest.mark.parametrize("action", ["report", "block"])
    def test_feature_flag_off_is_refused(self, room, action):
        event, _alice, chloe, client = room
        with override_settings(CRUSH_EVENT_LOBBY_ENABLED=False):
            response = _post(client, event, action, handle=_handle_of(chloe, event))
        assert response.status_code in (403, 404)
        assert not UserReport.objects.exists()

    def test_bad_payload_is_a_400(self, room):
        event, _alice, _chloe, client = room
        for action in ("report", "block"):
            response = client.post(
                f"/en/events/{event.pk}/lobby/api/{action}/",
                data="not json",
                content_type="application/json",
            )
            assert response.status_code == 400

    def test_get_is_not_allowed(self, room):
        event, _alice, _chloe, client = room
        assert client.get(f"/en/events/{event.pk}/lobby/api/report/").status_code == 405

    def test_recap_still_accepts_a_report(self, room):
        event, _alice, chloe, client = room
        _end_event(event)
        response = _post(client, event, "report", handle=_handle_of(chloe, event))
        assert response.status_code == 200
        assert UserReport.objects.filter(reported_user=chloe).exists()

    def test_closed_lobby_is_refused(self, room):
        event, _alice, chloe, client = room
        _end_event(event, hours_ago=72)
        response = _post(client, event, "report", handle=_handle_of(chloe, event))
        assert response.status_code == 403
        assert not UserReport.objects.exists()


class TestPages:
    def test_mutual_row_offers_safety_by_handle_not_user_id(self, room):
        event, alice, chloe, client = room
        _reveal(alice, chloe, event)
        html = client.get(f"/en/events/{event.pk}/lobby/").content.decode()
        assert f'data-safety-handle="{_handle_of(chloe, event)}"' in html
        assert "data-safety-dialog" in html
        assert f"/members/{chloe.pk}/" not in html

    def test_unrevealed_room_has_no_safety_row(self, room):
        event, _alice, _chloe, client = room
        html = client.get(f"/en/events/{event.pk}/lobby/").content.decode()
        assert "data-safety-handle=" not in html
        assert "Report this photo" in html

    def test_reasons_are_listed_on_the_sheet(self, room):
        event, _alice, _chloe, client = room
        html = client.get(f"/en/events/{event.pk}/lobby/").content.decode()
        for value, _label in UserReport.REASON_CHOICES:
            assert f'value="{value}"' in html

    def test_person_page_offers_block_and_report(self, room):
        event, alice, chloe, client = room
        lobby._create_or_get_encounter(alice, chloe, event)
        url = f"/en/crush-connect/people-ive-met/{chloe.pk}/"
        html = client.get(url).content.decode()
        assert "Safety options" in html
        assert 'name="source" value="event_lobby"' in html
        assert 'name="next" value="/en/crush-connect/people-ive-met/"' in html

    def test_blocking_from_the_person_page_lands_on_the_list(self, room):
        event, alice, chloe, client = room
        lobby._create_or_get_encounter(alice, chloe, event)
        response = client.post(
            f"/en/members/{chloe.pk}/block/",
            {"next": "/en/crush-connect/people-ive-met/"},
        )
        assert response["Location"] == "/en/crush-connect/people-ive-met/"
        person = client.get(f"/en/crush-connect/people-ive-met/{chloe.pk}/")
        assert person.status_code == 404
