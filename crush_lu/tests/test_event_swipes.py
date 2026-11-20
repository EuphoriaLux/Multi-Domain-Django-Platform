"""
Post-event swiping (decision 2026-11-20, replaces "My Crush!" coach leads).

Covers: deck gates and compatibility, final like/pass, mutual like -> shared
``flow='swipe'`` connection with an open chat and notifications, upgrade of
an old crush row, privacy (no pass leaks, blocked pairs), the retired
declaration endpoints, and the read-only coach matches page.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    ConnectionMessage,
    CrushCoach,
    EventConnection,
    EventSwipe,
    UserBlock,
)
from crush_lu.services import event_swipes
from crush_lu.tests.test_event_lobby import (
    _attend,
    _end_event,
    _login,
    _make_event,
    _make_member,
)

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


@pytest.fixture(autouse=True)
def _flags(settings):
    settings.AZURE_ACCOUNT_NAME = ""
    cache.clear()
    yield


def _member(username, gender="F", **kwargs):
    kwargs.setdefault("membership", False)
    kwargs.setdefault("luxid", False)
    return _make_member(username, gender=gender, **kwargs)


def _ended_event():
    return _end_event(_make_event())


def _swipe_url(event, user):
    return reverse(
        "crush_lu:event_swipe", kwargs={"event_id": event.pk, "user_id": user.pk}
    )


def _deck_url(event):
    return reverse("crush_lu:event_attendees", kwargs={"event_id": event.pk})


def _post_swipe(client, event, target, decision):
    return client.post(
        _swipe_url(event, target),
        {"decision": decision},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )


@pytest.fixture
def room():
    """An ended event with two women and two men who all attended."""
    event = _ended_event()
    anna, bea = _member("anna", "F"), _member("bea", "F")
    carl, dan = _member("carl", "M"), _member("dan", "M")
    for user in (anna, bea, carl, dan):
        _attend(user, event)
    return {"event": event, "anna": anna, "bea": bea, "carl": carl, "dan": dan}


# ---------------------------------------------------------------------------
# Compatibility and deck
# ---------------------------------------------------------------------------


class TestDeck:
    def test_default_is_other_gender(self, room):
        deck = event_swipes.swipe_deck(room["anna"], room["event"])
        assert {c["user"] for c in deck} == {room["carl"], room["dan"]}

    def test_preferences_are_respected_both_ways(self, room):
        anna = room["anna"].crushprofile
        anna.preferred_genders = ["F"]
        anna.save(update_fields=["preferred_genders"])
        bea = room["bea"].crushprofile
        bea.preferred_genders = ["F", "M"]
        bea.save(update_fields=["preferred_genders"])

        deck = event_swipes.swipe_deck(room["anna"], room["event"])
        assert [c["user"] for c in deck] == [room["bea"]]
        # Carl (default: women) no longer sees Anna, who wants women only.
        carl_deck = event_swipes.swipe_deck(room["carl"], room["event"])
        assert room["anna"] not in {c["user"] for c in carl_deck}

    def test_swiped_people_leave_the_deck(self, room):
        event_swipes.record_swipe(
            room["anna"], room["event"], room["carl"], liked=False
        )
        deck = event_swipes.swipe_deck(room["anna"], room["event"])
        assert [c["user"] for c in deck] == [room["dan"]]
        assert event_swipes.deck_size(room["anna"], room["event"]) == 2

    def test_unverified_and_absent_people_are_hidden(self, room):
        ghost = _member("ghost", "M", verified=False)
        _attend(ghost, room["event"])
        absent = _member("absent", "M")
        deck = event_swipes.swipe_deck(room["anna"], room["event"])
        users = {c["user"] for c in deck}
        assert ghost not in users and absent not in users

    def test_blocked_pair_hidden(self, room):
        UserBlock.objects.create(blocker=room["carl"], blocked=room["anna"])
        deck = event_swipes.swipe_deck(room["anna"], room["event"])
        assert room["carl"] not in {c["user"] for c in deck}
        with pytest.raises(event_swipes.SwipeNotAllowed):
            event_swipes.record_swipe(
                room["anna"], room["event"], room["carl"], liked=True
            )

    def test_order_is_stable_per_member(self, room):
        a = [c["user"].pk for c in event_swipes.swipe_deck(room["anna"], room["event"])]
        b = [c["user"].pk for c in event_swipes.swipe_deck(room["anna"], room["event"])]
        assert a == b


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


class TestWindow:
    def test_closed_before_end(self):
        event = _make_event(starts_in_minutes=-30, duration=120)
        anna, carl = _member("anna", "F"), _member("carl", "M")
        _attend(anna, event)
        _attend(carl, event)
        with pytest.raises(event_swipes.SwipeNotAllowed):
            event_swipes.record_swipe(anna, event, carl, liked=True)

    def test_closed_after_window(self, room):
        event = room["event"]
        event.date_time = timezone.now() - timedelta(days=5)
        event.save(update_fields=["date_time"])
        with pytest.raises(event_swipes.SwipeNotAllowed):
            event_swipes.record_swipe(room["anna"], event, room["carl"], liked=True)

    def test_deck_page_redirects_after_window(self, client, room):
        event = room["event"]
        event.date_time = timezone.now() - timedelta(days=5)
        event.save(update_fields=["date_time"])
        _login(client, room["anna"])
        response = client.get(_deck_url(event))
        assert response.status_code == 302
        assert response.url.endswith(reverse("crush_lu:my_connections"))


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


class TestMatching:
    def test_one_sided_like_creates_nothing_visible(self, room):
        result = event_swipes.record_swipe(
            room["anna"], room["event"], room["carl"], liked=True
        )
        assert result["match"] is False
        assert not EventConnection.objects.exists()

    def test_mutual_like_creates_shared_swipe_connection(self, room):
        event_swipes.record_swipe(room["anna"], room["event"], room["carl"], liked=True)
        result = event_swipes.record_swipe(
            room["carl"], room["event"], room["anna"], liked=True
        )
        assert result["match"] is True
        conn = result["connection"]
        assert conn.flow == EventConnection.FLOW_SWIPE
        assert conn.status == "shared"
        assert conn.shared_at is not None
        assert conn.requester == room["anna"] and conn.recipient == room["carl"]
        assert conn.requester_consents_to_share and conn.recipient_consents_to_share
        # Email/phone stay private until each side opts in.
        assert not conn.requester_shares_email and not conn.requester_shares_phone

    def test_like_then_pass_is_no_match(self, room):
        event_swipes.record_swipe(room["anna"], room["event"], room["carl"], liked=True)
        result = event_swipes.record_swipe(
            room["carl"], room["event"], room["anna"], liked=False
        )
        assert result["match"] is False
        assert not EventConnection.objects.exists()

    def test_decision_is_final(self, room):
        event_swipes.record_swipe(
            room["anna"], room["event"], room["carl"], liked=False
        )
        result = event_swipes.record_swipe(
            room["anna"], room["event"], room["carl"], liked=True
        )
        assert result["already_swiped"] is True
        assert EventSwipe.objects.get(swiper=room["anna"]).liked is False

    def test_old_crush_lead_is_upgraded(self, room):
        lead = EventConnection.objects.create(
            requester=room["carl"],
            recipient=room["anna"],
            event=room["event"],
            flow=EventConnection.FLOW_CRUSH,
        )
        event_swipes.record_swipe(room["anna"], room["event"], room["carl"], liked=True)
        result = event_swipes.record_swipe(
            room["carl"], room["event"], room["anna"], liked=True
        )
        lead.refresh_from_db()
        assert result["connection"].pk == lead.pk
        assert lead.flow == EventConnection.FLOW_SWIPE
        assert lead.status == "shared"
        assert EventConnection.objects.count() == 1
        assert not EventConnection.objects.open_crush_leads().exists()

    def test_upgrade_keeps_the_crush_note_away_from_the_recipient(self, room):
        EventConnection.objects.create(
            requester=room["carl"],
            recipient=room["anna"],
            event=room["event"],
            flow=EventConnection.FLOW_CRUSH,
            requester_note="Only my coach may read this",
        )
        event_swipes.record_swipe(room["anna"], room["event"], room["carl"], liked=True)
        conn = event_swipes.record_swipe(
            room["carl"], room["event"], room["anna"], liked=True
        )["connection"]
        conn.refresh_from_db()
        assert conn.requester_note == ""
        assert conn.system_actions[-1]["details"]["coach_only_note"] == (
            "Only my coach may read this"
        )

    def test_match_hidden_after_a_block(self, room):
        event_swipes.record_swipe(room["anna"], room["event"], room["carl"], liked=True)
        event_swipes.record_swipe(room["carl"], room["event"], room["anna"], liked=True)
        UserBlock.objects.create(blocker=room["carl"], blocked=room["anna"])
        assert not event_swipes.matches_for_user(room["anna"], room["event"]).exists()
        assert not event_swipes.matches_for_user(room["carl"], room["event"]).exists()

    def test_match_notifies_both_after_commit(
        self, room, monkeypatch, django_capture_on_commit_callbacks
    ):
        notified = []
        monkeypatch.setattr(
            "crush_lu.notification_service.notify_mutual_match",
            lambda **kw: notified.append(kw["user"]),
        )
        event_swipes.record_swipe(room["anna"], room["event"], room["carl"], liked=True)
        with django_capture_on_commit_callbacks(execute=True):
            event_swipes.record_swipe(
                room["carl"], room["event"], room["anna"], liked=True
            )
        assert set(notified) == {room["anna"], room["carl"]}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class TestViews:
    def test_deck_page_lists_compatible_people(self, client, room):
        _login(client, room["anna"])
        response = client.get(_deck_url(room["event"]))
        assert response.status_code == 200
        names = {c["user"] for c in response.context["deck"]}
        assert names == {room["carl"], room["dan"]}
        assert response.context["deck_total"] == 2

    def test_deck_closed_before_event_end(self, client):
        event = _make_event(starts_in_minutes=-30, duration=120)
        anna = _member("anna", "F")
        _attend(anna, event)
        _login(client, anna)
        response = client.get(_deck_url(event))
        assert response.status_code == 302

    def test_json_swipe_and_match(self, client, room):
        _login(client, room["anna"])
        response = _post_swipe(client, room["event"], room["carl"], "like")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "match": False}

        client.logout()
        _login(client, room["carl"])
        response = _post_swipe(client, room["event"], room["anna"], "like")
        data = response.json()
        assert data["match"] is True
        conn = EventConnection.objects.get()
        assert data["chat_url"] == reverse(
            "crush_lu:connection_detail", kwargs={"connection_id": conn.pk}
        )

    def test_form_fallback_redirects_back(self, client, room):
        _login(client, room["anna"])
        response = client.post(
            _swipe_url(room["event"], room["carl"]), {"decision": "pass"}
        )
        assert response.status_code == 302
        assert response.url == _deck_url(room["event"])
        assert EventSwipe.objects.filter(liked=False).count() == 1

    def test_incompatible_target_is_refused(self, client, room):
        _login(client, room["anna"])
        response = _post_swipe(client, room["event"], room["bea"], "like")
        assert response.status_code == 400
        assert not EventSwipe.objects.exists()

    def test_bad_decision_is_refused(self, client, room):
        _login(client, room["anna"])
        response = _post_swipe(client, room["event"], room["carl"], "maybe")
        assert response.status_code == 400

    def test_chat_opens_on_match(self, client, room):
        event_swipes.record_swipe(room["anna"], room["event"], room["carl"], liked=True)
        result = event_swipes.record_swipe(
            room["carl"], room["event"], room["anna"], liked=True
        )
        conn = result["connection"]
        _login(client, room["anna"])
        url = reverse("crush_lu:connection_detail", kwargs={"connection_id": conn.pk})
        assert client.get(url).status_code == 200
        client.post(url, {"message": "Hi Carl!"})
        assert ConnectionMessage.objects.filter(
            connection=conn, sender=room["anna"]
        ).exists()

    def test_retired_declaration_creates_nothing(self, client, room):
        _login(client, room["anna"])
        url = reverse(
            "crush_lu:request_connection",
            kwargs={"event_id": room["event"].pk, "user_id": room["carl"].pk},
        )
        response = client.post(url, {"note": "hi"})
        assert response.status_code == 302
        assert response.url == _deck_url(room["event"])
        assert not EventConnection.objects.exists()

    def test_retired_inline_declaration_htmx_redirects(self, client, room):
        _login(client, room["anna"])
        url = reverse(
            "crush_lu:request_connection_inline",
            kwargs={"event_id": room["event"].pk, "user_id": room["carl"].pk},
        )
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response["HX-Redirect"] == _deck_url(room["event"])
        assert not EventConnection.objects.exists()


# ---------------------------------------------------------------------------
# Coach view
# ---------------------------------------------------------------------------


def _coach(username="coach"):
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x"
    )
    return CrushCoach.objects.create(user=user, bio="Coach", is_active=True)


class TestCoachMatches:
    def _match(self, room):
        event_swipes.record_swipe(room["anna"], room["event"], room["carl"], liked=True)
        return event_swipes.record_swipe(
            room["carl"], room["event"], room["anna"], liked=True
        )["connection"]

    def test_event_coach_sees_match_read_only(self, client, room):
        coach = _coach()
        room["event"].coaches.add(coach)
        conn = self._match(room)
        _login(client, coach.user)
        response = client.get(reverse("crush_lu:coach_event_matches"))
        assert response.status_code == 200
        assert list(response.context["matches"]) == [conn]
        # The action page stays closed for swipe matches.
        review = client.get(
            reverse(
                "crush_lu:coach_connection_review", kwargs={"connection_id": conn.pk}
            )
        )
        assert review.status_code == 404

    def test_other_coach_does_not_see_match(self, client, room):
        coach = _coach("other")
        self._match(room)
        _login(client, coach.user)
        response = client.get(reverse("crush_lu:coach_event_matches"))
        assert list(response.context["matches"]) == []

    def test_stats_only_for_events_the_coach_coaches(self, client, room):
        coach = _coach("outsider")
        self._match(room)
        _login(client, coach.user)
        response = client.get(
            reverse("crush_lu:coach_event_matches") + f"?event={room['event'].pk}"
        )
        assert response.status_code == 200
        assert response.context["event"] is None
        assert response.context["event_stats"] is None

    def test_matches_stay_out_of_the_connection_queue(self, client, room):
        coach = _coach()
        room["event"].coaches.add(coach)
        self._match(room)
        _login(client, coach.user)
        response = client.get(reverse("crush_lu:coach_connections") + "?status=all")
        assert response.status_code == 200
        assert not any(
            c.flow == EventConnection.FLOW_SWIPE
            for c in response.context["page_obj"].object_list
        )


# ---------------------------------------------------------------------------
# Translations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lang,expected",
    [
        ("de", ["Wer ist dir aufgefallen?", "Wische nach rechts", "Sag Hallo"]),
        ("fr", ["Qui a attiré votre attention", "Glissez à droite", "Dites bonjour"]),
    ],
)
def test_deck_page_is_translated(client, room, lang, expected):
    _login(client, room["anna"])
    response = client.get(f"/{lang}/events/{room['event'].pk}/attendees/")
    assert response.status_code == 200
    body = response.content.decode()
    for text in expected:
        assert text in body
