"""
Tests for the Crush Connect Event Lobby recap & People I've Met slice (Phase C).

Spec: docs/superpowers/specs/2026-07-17-crush-connect-event-lobby-design.md
Covers the §18 rows for this phase: recap membership frozen at scheduled end;
unlimited immutable confirmations; anonymous recap counter excluding
blocked/ineligible; reciprocal confirmations create exactly one permanent
encounter; repeated events never reorder/update it; live mutuals sort first
and stay labeled in recap; deactivation hides & reactivation restores; plus
HTTP authorization for the confirm endpoint and People I've Met.

Reuses the builders from test_event_lobby via import to stay DRY.
"""

import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    ConfirmedEncounter,
    EventMeetingConfirmation,
    EventMeetSignal,
    UserBlock,
)
from crush_lu.services import event_lobby as lobby
from crush_lu.tests.test_event_lobby import (  # reuse builders + autouse fixtures
    _attend,
    _end_event,
    _handle_of,
    _join,
    _login,
    _make_event,
    _make_member,
    lobby_flags,  # noqa: F401 (autouse)
    _clear_ratelimit_cache,  # noqa: F401 (autouse)
)

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


def _recap_event():
    """A live event, joined, then rewound so it is in the 48h recap window."""
    return _make_event(starts_in_minutes=-30, duration=120)


def _to_recap(event, hours_ago=1):
    return _end_event(event, hours_ago=hours_ago)


# ---------------------------------------------------------------------------
# Confirmations & permanent encounters (§7.7, §9.3–9.4)
# ---------------------------------------------------------------------------


class TestConfirmations:
    def _recap_with(self, *usernames):
        event = _recap_event()
        genders = ["F", "M"] * 4
        members = [
            _make_member(name, gender=genders[i]) for i, name in enumerate(usernames)
        ]
        for member in members:
            _join(member, event)  # participation created while the event is live
        handles = {m.username: _handle_of(m, event) for m in members}
        _to_recap(event)  # rewind so the event is now in the 48h recap window
        return event, members, handles

    def test_reciprocal_confirmations_create_one_encounter(self):
        event, (alice, ben, chloe), h = self._recap_with("alice", "ben", "chloe")

        first = lobby.confirm_meeting(alice, event, h["ben"])
        assert first["result"] == "confirmed"
        # Anonymous until reciprocal: ben sees only a count, no encounter.
        assert lobby.incoming_confirmation_count(ben, event) == 1
        assert lobby.get_people_ive_met(alice) == []
        assert lobby.get_people_ive_met(ben) == []

        back = lobby.confirm_meeting(ben, event, h["alice"])
        assert back["result"] == "encounter"
        assert back["first_name"] == "Alice"
        # Exactly one permanent encounter, visible to both, newest-first.
        assert ConfirmedEncounter.objects.count() == 1
        alice_people = lobby.get_people_ive_met(alice)
        ben_people = lobby.get_people_ive_met(ben)
        assert [p["first_name"] for p in alice_people] == ["Ben"]
        assert [p["first_name"] for p in ben_people] == ["Alice"]
        # Reciprocal confirmation leaves the anonymous count (now revealed).
        assert lobby.incoming_confirmation_count(ben, event) == 0
        # chloe uninvolved.
        assert lobby.get_people_ive_met(chloe) == []

    def test_confirmations_are_unlimited(self):
        event, members, h = self._recap_with("alice", "b", "c", "d", "e")
        alice = members[0]
        for name in ("b", "c", "d", "e"):
            assert lobby.confirm_meeting(alice, event, h[name])["result"] == "confirmed"
        assert (
            EventMeetingConfirmation.objects.filter(event=event, confirmer=alice).count()
            == 4
        )

    def test_duplicate_confirmation_is_idempotent(self):
        event, (alice, ben), h = self._recap_with("alice", "ben")
        assert lobby.confirm_meeting(alice, event, h["ben"])["result"] == "confirmed"
        assert lobby.confirm_meeting(alice, event, h["ben"])["result"] == "duplicate"
        assert (
            EventMeetingConfirmation.objects.filter(event=event, confirmer=alice).count()
            == 1
        )

    def test_confirmation_rejected_outside_recap_window(self):
        event, (alice, ben), h = self._recap_with("alice", "ben")
        # Push past the 48h recap close.
        _to_recap(event, hours_ago=49)
        result = lobby.confirm_meeting(alice, event, h["ben"])
        assert result["result"] == "phase_closed"
        assert EventMeetingConfirmation.objects.count() == 0

    def test_confirmation_rejected_during_live_phase(self):
        event = _recap_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        # Still live (not rewound).
        result = lobby.confirm_meeting(alice, event, _handle_of(ben, event))
        assert result["result"] == "phase_closed"

    def test_already_met_pair_is_non_actionable(self):
        event, (alice, ben), h = self._recap_with("alice", "ben")
        lobby.confirm_meeting(alice, event, h["ben"])
        lobby.confirm_meeting(ben, event, h["alice"])  # → encounter
        # A later confirmation attempt on the same pair is a no-op.
        again = lobby.confirm_meeting(alice, event, h["ben"])
        assert again["result"] == "already_met"
        assert ConfirmedEncounter.objects.count() == 1

    def test_blocked_or_unknown_handle_indistinguishable(self):
        event, (alice, ben), h = self._recap_with("alice", "ben")
        UserBlock.objects.create(blocker=ben, blocked=alice)
        assert (
            lobby.confirm_meeting(alice, event, h["ben"])["result"]
            == "unknown_participant"
        )
        assert (
            lobby.confirm_meeting(alice, event, "f" * 32)["result"]
            == "unknown_participant"
        )

    def test_recap_counter_excludes_blocked_and_ineligible(self):
        event, (alice, ben, chloe), h = self._recap_with("alice", "ben", "chloe")
        lobby.confirm_meeting(ben, event, h["alice"])
        lobby.confirm_meeting(chloe, event, h["alice"])
        assert lobby.incoming_confirmation_count(alice, event) == 2
        UserBlock.objects.create(blocker=alice, blocked=ben)
        assert lobby.incoming_confirmation_count(alice, event) == 1
        membership = chloe.crush_connect_membership
        membership.excluded_by_coach = True
        membership.save(update_fields=["excluded_by_coach"])
        assert lobby.incoming_confirmation_count(alice, event) == 0

    def test_confirmation_rows_are_immutable_at_db(self):
        from django.db import IntegrityError, transaction

        event, (alice, ben), h = self._recap_with("alice", "ben")
        EventMeetingConfirmation.objects.create(
            event=event, confirmer=alice, other_user=ben
        )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                EventMeetingConfirmation.objects.create(
                    event=event, confirmer=alice, other_user=ben
                )
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                EventMeetingConfirmation.objects.create(
                    event=event, confirmer=alice, other_user=alice
                )


# ---------------------------------------------------------------------------
# Recap roster shaping (§7.7)
# ---------------------------------------------------------------------------


class TestRecapRoster:
    def test_live_mutuals_sort_first_and_keep_first_name(self):
        event = _recap_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        chloe = _make_member("chloe")
        for m in (alice, ben, chloe):
            _join(m, event)
        # Alice & Ben are a live mutual; Chloe is not.
        lobby.send_meet_signal(alice, event, _handle_of(ben, event))
        lobby.send_meet_signal(ben, event, _handle_of(alice, event))
        _to_recap(event)

        roster = lobby.get_recap_roster(alice, event)
        assert roster[0]["is_live_mutual"] is True
        assert roster[0]["first_name"] == "Ben"
        # Non-mutual participant is photo-only (no first name leaked, §13).
        chloe_entry = [e for e in roster if not e["is_live_mutual"]][0]
        assert "first_name" not in chloe_entry

    def test_confirmed_flag_reflects_own_confirmations(self):
        event = _recap_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _to_recap(event)
        lobby.confirm_meeting(alice, event, _handle_of(ben, event))
        roster = lobby.get_recap_roster(alice, event)
        assert roster[0]["confirmed"] is True

    def test_already_met_pair_marked_non_actionable(self):
        event = _recap_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _to_recap(event)
        lobby.confirm_meeting(alice, event, _handle_of(ben, event))
        lobby.confirm_meeting(ben, event, _handle_of(alice, event))
        roster = lobby.get_recap_roster(alice, event)
        assert roster[0]["already_met"] is True
        assert roster[0]["first_name"] == "Ben"

    def test_recap_roster_excludes_self_and_blocked(self):
        event = _recap_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _to_recap(event)
        UserBlock.objects.create(blocker=alice, blocked=ben)
        assert lobby.get_recap_roster(alice, event) == []

    def test_recap_membership_frozen_at_scheduled_end(self):
        """§6/§9.1: recap includes everyone who joined before the exact end —
        including people who may already have left."""
        event = _recap_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _to_recap(event)
        # A late-onboarding member after end never joins the recap (§5.3).
        assert len(lobby.get_recap_roster(alice, event)) == 1


# ---------------------------------------------------------------------------
# People I've Met (§7.8)
# ---------------------------------------------------------------------------


def _make_encounter(user_a, user_b, event=None):
    low, high = ConfirmedEncounter.canonical_pair(user_a, user_b)
    return ConfirmedEncounter.objects.create(
        user_low=low, user_high=high, created_from_event=event, status="active"
    )


class TestPeopleIveMet:
    def test_one_entry_per_pair_photo_and_name_only(self):
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _make_encounter(alice, ben)
        people = lobby.get_people_ive_met(alice)
        assert len(people) == 1
        entry = people[0]
        assert entry["first_name"] == "Ben"
        assert set(entry.keys()) == {
            "user_id",
            "first_name",
            "photo_url",
            "profile_url",
            "created_at",
        }

    def test_newest_first_and_repeated_events_do_not_reorder(self):
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        chloe = _make_member("chloe")
        enc_ben = _make_encounter(alice, ben)
        enc_chloe = _make_encounter(alice, chloe)
        # Make ben's the older encounter.
        ConfirmedEncounter.objects.filter(pk=enc_ben.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        names = [p["first_name"] for p in lobby.get_people_ive_met(alice)]
        assert names == ["Chloe", "Ben"]
        # A later shared event re-confirming Ben must NOT reorder or update.
        original_created = ConfirmedEncounter.objects.get(pk=enc_ben.pk).created_at
        again, created = lobby._create_or_get_encounter(alice, ben, None)
        assert created is False
        assert again.created_at == original_created
        names_after = [p["first_name"] for p in lobby.get_people_ive_met(alice)]
        assert names_after == ["Chloe", "Ben"]

    def test_entry_hidden_when_counterpart_excluded_restored_on_reactivation(self):
        """§7.8: entries disappear while either side is inactive/excluded;
        ordinary reactivation restores them."""
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _make_encounter(alice, ben)
        assert len(lobby.get_people_ive_met(alice)) == 1
        membership = ben.crush_connect_membership
        membership.excluded_by_coach = True
        membership.save(update_fields=["excluded_by_coach"])
        assert lobby.get_people_ive_met(alice) == []
        membership.excluded_by_coach = False
        membership.save(update_fields=["excluded_by_coach"])
        assert len(lobby.get_people_ive_met(alice)) == 1

    def test_entry_hidden_when_pair_blocked(self):
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _make_encounter(alice, ben)
        UserBlock.objects.create(blocker=ben, blocked=alice)
        assert lobby.get_people_ive_met(alice) == []
        assert lobby.get_people_ive_met(ben) == []

    def test_deactivated_viewer_sees_empty_collection(self):
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _make_encounter(alice, ben)
        membership = alice.crush_connect_membership
        membership.excluded_by_coach = True
        membership.save(update_fields=["excluded_by_coach"])
        alice.refresh_from_db()
        assert lobby.get_people_ive_met(alice) == []

    def test_removed_encounter_hidden_and_not_resurrected(self):
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        encounter = _make_encounter(alice, ben)
        encounter.status = "removed"
        encounter.save(update_fields=["status"])
        assert lobby.get_people_ive_met(alice) == []
        # A later reciprocal confirmation must not resurrect a removed pair.
        again, created = lobby._create_or_get_encounter(alice, ben, None)
        assert created is False
        assert again.status == "removed"


# ---------------------------------------------------------------------------
# HTTP surface (§11, §13)
# ---------------------------------------------------------------------------


class TestConfirmApi:
    def _url(self, event):
        return reverse("crush_lu:event_lobby_confirm_api", args=[event.pk])

    def _post(self, client, event, body):
        return client.post(
            self._url(event), data=json.dumps(body), content_type="application/json"
        )

    def test_confirm_happy_path_without_recipient_leak(self, client):
        event = _recap_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        handle = _handle_of(ben, event)
        _to_recap(event)
        _login(client, alice)
        response = self._post(client, event, {"handle": handle})
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["result"] == "confirmed"
        assert "recipient_user_id" not in payload
        assert "Ben" not in json.dumps(payload)

    def test_encounter_reveals_name_and_pushes_privately(self, client, mocker):
        notify = mocker.patch("crush_lu.views_event_lobby._notify_lobby_user")
        event = _recap_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        handle = _handle_of(ben, event)
        _to_recap(event)
        lobby.confirm_meeting(ben, event, _handle_of(alice, event))
        _login(client, alice)
        payload = self._post(client, event, {"handle": handle}).json()
        assert payload["result"] == "encounter"
        assert payload["first_name"] == "Ben"
        assert "recipient_user_id" not in payload
        notify.assert_called_once_with(event.pk, ben.pk, "lobby.encounter", {})

    def test_non_participant_forbidden(self, client):
        event = _recap_event()
        ben = _make_member("ben", gender="M")
        _join(ben, event)
        handle = _handle_of(ben, event)
        _to_recap(event)
        alice = _make_member("alice")  # never attended
        _login(client, alice)
        assert self._post(client, event, {"handle": handle}).status_code == 403

    def test_bad_payload(self, client):
        event = _recap_event()
        alice = _make_member("alice")
        _join(alice, event)
        _to_recap(event)
        _login(client, alice)
        assert self._post(client, event, {"nope": 1}).status_code == 400

    def test_state_api_returns_recap_roster(self, client):
        event = _recap_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _to_recap(event)
        _login(client, alice)
        url = reverse("crush_lu:event_lobby_state_api", args=[event.pk])
        payload = client.get(url).json()
        assert payload["state"]["phase"] == "recap"
        assert "incoming_confirmations" in payload["state"]
        assert len(payload["roster"]) == 1
        assert "Ben" not in json.dumps(payload)  # non-mutual → no name


class TestPeopleIveMetPages:
    def test_collection_lists_encounters(self, client):
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _make_encounter(alice, ben)
        _login(client, alice)
        response = client.get(reverse("crush_lu:event_lobby_people"))
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        assert "Ben" in response.content.decode()

    def test_collection_empty_for_member_without_encounters(self, client):
        alice = _make_member("alice")
        _login(client, alice)
        response = client.get(reverse("crush_lu:event_lobby_people"))
        assert response.status_code == 200
        assert "No one here yet" in response.content.decode()

    def test_person_profile_requires_active_encounter(self, client):
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        stranger = _make_member("stranger", gender="M")
        _make_encounter(alice, ben)
        _login(client, alice)
        # With an encounter → 200 and the counterpart's profile.
        ok = client.get(reverse("crush_lu:event_lobby_person", args=[ben.pk]))
        assert ok.status_code == 200
        assert "Ben" in ok.content.decode()
        # No encounter with the stranger → 404 (not an unguessable id, §13).
        denied = client.get(
            reverse("crush_lu:event_lobby_person", args=[stranger.pk])
        )
        assert denied.status_code == 404

    def test_person_profile_denied_after_block(self, client):
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _make_encounter(alice, ben)
        UserBlock.objects.create(blocker=ben, blocked=alice)
        _login(client, alice)
        response = client.get(reverse("crush_lu:event_lobby_person", args=[ben.pk]))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Live-grid "already met" integration (§7.3 step 2 / §2)
# ---------------------------------------------------------------------------


class TestAlreadyMetInLiveGrid:
    def test_existing_encounter_shows_in_live_grid_but_cannot_signal(self):
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _make_encounter(alice, ben)  # from a previous event
        event = _recap_event()  # a NEW live event both attend
        _join(alice, event)
        _join(ben, event)
        # Ben still visible in alice's live roster, marked already-met.
        roster = lobby.get_roster(alice, event)
        assert len(roster) == 1
        assert roster[0]["already_met"] is True
        assert roster[0]["first_name"] == "Ben"
        # Signalling is a non-actionable no-op that consumes nothing.
        result = lobby.send_meet_signal(alice, event, _handle_of(ben, event))
        assert result["result"] == "already_met"
        assert result["signals_remaining"] == 3
        assert EventMeetSignal.objects.count() == 0
