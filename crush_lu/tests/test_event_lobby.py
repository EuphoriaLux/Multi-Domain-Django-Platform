"""
Tests for the Crush Connect Event Lobby prototype slice.

Spec: docs/superpowers/specs/2026-07-17-crush-connect-event-lobby-design.md
Covers the §18 test-plan rows that fall inside the slice: every eligibility
condition independently denies access; non-Connect check-in succeeds without
participation; onboarding before/after end; exact three-signal quota with
idempotent duplicates; immutable signals; anonymous counters excluding
blocked/ineligible senders; reciprocal signal reveals only the exact pair;
exact phase boundaries; HTTP authorization (guest / non-participant /
cross-event handles / pre-mutual identity leaks); and consumer authorization.
"""

import json
from datetime import date, timedelta

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.signing import Signer
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    ConfirmedEncounter,
    CrushConnectMembership,
    CrushProfile,
    EventLobbyParticipation,
    EventMeetSignal,
    EventRegistration,
    MeetupEvent,
    UserBlock,
    UserDataConsent,
)
from crush_lu.services import event_lobby as lobby

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


# ---------------------------------------------------------------------------
# Fixtures & builders
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def lobby_flags(settings):
    """Rollout flag + Connect launch phase on; force local photo serving."""
    settings.CRUSH_EVENT_LOBBY_ENABLED = True
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.AZURE_ACCOUNT_NAME = ""


@pytest.fixture(autouse=True)
def _clear_ratelimit_cache():
    cache.clear()
    yield


def _make_member(
    username,
    *,
    gender="F",
    membership=True,
    onboarded=True,
    excluded=False,
    verified=True,
    luxid=True,
    photo_consent=True,
    photo=True,
):
    """A user with every §5.1 gate knob individually controllable. Defaults to
    a fully eligible Crush Connect member (no premium, no coach — the gate
    must not require either)."""
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="testpass123",
        first_name=username.title(),
    )
    User.objects.filter(pk=user.pk).update(last_login=timezone.now())
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender=gender,
        location="Luxembourg City",
        is_approved=verified,
        verification_status="verified" if verified else "incomplete",
        is_active=True,
    )
    if photo:
        profile.photo_1 = f"users/{user.pk}/photos/test.jpg"
        profile.save(update_fields=["photo_1"])
    if luxid:
        SocialAccount.objects.create(
            user=user, provider="luxid", uid=f"luxid-test-{user.pk}"
        )
    if membership:
        CrushConnectMembership.objects.create(
            user=user,
            onboarded_at=timezone.now() if onboarded else None,
            excluded_by_coach=excluded,
            photo_share_consent=photo_consent,
        )
    return User.objects.select_related("crushprofile", "crush_connect_membership").get(
        pk=user.pk
    )


def _make_event(starts_in_minutes=-30, duration=120, published=True, cancelled=False):
    now = timezone.now()
    return MeetupEvent.objects.create(
        title="Lobby Test Event",
        description="x",
        event_type="mixer",
        location="Lux",
        address="1 Test St",
        canton="Luxembourg",
        date_time=now + timedelta(minutes=starts_in_minutes),
        duration_minutes=duration,
        max_participants=30,
        registration_deadline=now - timedelta(hours=1),
        is_published=published,
        is_cancelled=cancelled,
    )


def _attend(user, event):
    return EventRegistration.objects.create(
        event=event, user=user, status="attended", checked_in_at=timezone.now()
    )


def _join(user, event):
    """Attend + run the real check-in integration path."""
    registration = _attend(user, event)
    participation, _created = lobby.handle_checkin(registration)
    return participation


def _login(client, user):
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    client.force_login(user)


def _end_event(event, hours_ago=1):
    """Rewind the event so its exact scheduled end lies ``hours_ago`` in the
    past (participations created while live stay in place)."""
    event.date_time = (
        timezone.now()
        - timedelta(hours=hours_ago)
        - timedelta(minutes=event.duration_minutes)
    )
    event.save(update_fields=["date_time"])
    return event


# ---------------------------------------------------------------------------
# Phase model (§6)
# ---------------------------------------------------------------------------


class TestPhase:
    def test_live_until_exact_end(self):
        event = _make_event(starts_in_minutes=-30, duration=120)
        end = event.end_time
        assert lobby.event_lobby_phase(event, end - timedelta(seconds=1)) == "live"
        # §7.6: the exact scheduled end is already recap, no grace period.
        assert lobby.event_lobby_phase(event, end) == "recap"

    def test_recap_closes_exactly_48h_after_end(self):
        event = _make_event()
        end = event.end_time
        assert (
            lobby.event_lobby_phase(event, end + timedelta(hours=48, seconds=-1))
            == "recap"
        )
        assert lobby.event_lobby_phase(event, end + timedelta(hours=48)) == "closed"

    def test_cancelled_or_unpublished_is_closed(self):
        cancelled = _make_event(cancelled=True)
        unpublished = _make_event(published=False)
        assert lobby.event_lobby_phase(cancelled) == "closed"
        assert lobby.event_lobby_phase(unpublished) == "closed"


# ---------------------------------------------------------------------------
# Eligibility & participation creation (§5)
# ---------------------------------------------------------------------------


class TestEligibility:
    def test_eligible_member_joins_on_checkin(self):
        event = _make_event()
        member = _make_member("alice")
        participation = _join(member, event)
        assert participation is not None
        assert participation.eligibility_source == "checkin"
        assert participation.event_registration.user_id == member.pk
        assert participation.event_id == event.pk

    def test_participation_is_idempotent(self):
        event = _make_event()
        member = _make_member("alice")
        registration = _attend(member, event)
        first, created_first = lobby.handle_checkin(registration)
        second, created_second = lobby.handle_checkin(registration)
        assert created_first is True
        assert created_second is False
        assert first.pk == second.pk
        assert EventLobbyParticipation.objects.count() == 1

    @pytest.mark.parametrize(
        "knobs",
        [
            {"membership": False},
            {"onboarded": False},
            {"excluded": True},
            {"verified": False},
            {"luxid": False},
            {"photo_consent": False},
            {"photo": False},
        ],
        ids=[
            "no_membership",
            "not_onboarded",
            "coach_excluded",
            "not_verified",
            "no_luxid",
            "no_photo_consent",
            "no_photo",
        ],
    )
    def test_each_gate_condition_independently_denies(self, knobs):
        """§18: every eligibility condition independently denies access."""
        event = _make_event()
        member = _make_member("denied", **knobs)
        assert _join(member, event) is None
        assert EventLobbyParticipation.objects.count() == 0

    def test_confirmed_but_not_attended_denies(self):
        event = _make_event()
        member = _make_member("alice")
        registration = EventRegistration.objects.create(
            event=event, user=member, status="confirmed"
        )
        participation, created = lobby.handle_checkin(registration)
        assert participation is None and created is False

    def test_unpublished_or_cancelled_event_denies(self):
        member = _make_member("alice")
        for event in (_make_event(published=False), _make_event(cancelled=True)):
            assert _join(member, event) is None
            EventRegistration.objects.all().delete()

    def test_rollout_flag_off_denies(self, settings):
        settings.CRUSH_EVENT_LOBBY_ENABLED = False
        event = _make_event()
        member = _make_member("alice")
        assert _join(member, event) is None

    def test_connect_phase_closed_denies(self, settings):
        settings.CRUSH_CONNECT_LAUNCHED = False
        settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
        event = _make_event()
        member = _make_member("alice")
        assert _join(member, event) is None

    def test_checkin_after_event_end_denies(self):
        event = _make_event(starts_in_minutes=-200, duration=120)  # ended 80m ago
        member = _make_member("alice")
        assert _join(member, event) is None

    def test_premium_and_preferences_do_not_matter(self):
        """§5.1: Premium and dating preferences are never part of this gate —
        an eligible member with restrictive preferences still sees everyone."""
        event = _make_event()
        alice = _make_member("alice", gender="F")
        ben = _make_member("ben", gender="M")
        alice.crush_connect_membership.preferred_genders = ["F"]
        alice.crush_connect_membership.save(update_fields=["preferred_genders"])
        _join(alice, event)
        _join(ben, event)
        handles = [e["handle"] for e in lobby.get_roster(alice, event)]
        assert len(handles) == 1  # ben visible despite preferences

    def test_onboarding_before_end_joins_after_end_does_not(self):
        event = _make_event()
        guest = _make_member("guest", membership=False)
        _attend(guest, event)
        # Mid-event: guest completes Connect onboarding.
        CrushConnectMembership.objects.create(
            user=guest, onboarded_at=timezone.now(), photo_share_consent=True
        )
        guest = User.objects.select_related(
            "crushprofile", "crush_connect_membership"
        ).get(pk=guest.pk)
        created = lobby.handle_onboarding_completed(guest)
        assert len(created) == 1
        assert created[0].eligibility_source == "onboarding_completed"

        # A second member finishing only after the exact end joins nothing (§5.3).
        late = _make_member("late", membership=False)
        _attend(late, event)
        CrushConnectMembership.objects.create(
            user=late, onboarded_at=timezone.now(), photo_share_consent=True
        )
        late = User.objects.select_related(
            "crushprofile", "crush_connect_membership"
        ).get(pk=late.pk)
        after_end = event.end_time + timedelta(seconds=1)
        assert lobby.handle_onboarding_completed(late, now=after_end) == []


class TestCheckinNeverDependsOnLobby:
    """§19: a normal event check-in never depends on the lobby succeeding."""

    def _checkin(self, client, registration):
        token = Signer().sign(f"{registration.pk}:{registration.event_id}")
        url = reverse(
            "event_checkin_api",
            kwargs={"registration_id": registration.pk, "token": token},
        )
        return client.post(url)

    def test_non_connect_guest_checks_in_normally_without_participation(self, client):
        event = _make_event(starts_in_minutes=30)
        guest = _make_member("guest", membership=False)
        registration = EventRegistration.objects.create(
            event=event, user=guest, status="confirmed"
        )
        response = self._checkin(client, registration)
        assert response.status_code == 200
        assert response.json()["success"] is True
        registration.refresh_from_db()
        assert registration.status == "attended"
        assert EventLobbyParticipation.objects.count() == 0

    def test_eligible_member_checkin_creates_participation_and_broadcasts(
        self, client, mocker
    ):
        broadcast = mocker.patch(
            "crush_lu.views_event_lobby.broadcast_participant_joined"
        )
        event = _make_event(starts_in_minutes=30)
        member = _make_member("alice")
        registration = EventRegistration.objects.create(
            event=event, user=member, status="confirmed"
        )
        response = self._checkin(client, registration)
        assert response.status_code == 200
        participation = EventLobbyParticipation.objects.get()
        assert participation.user_id == member.pk
        broadcast.assert_called_once_with(event.pk)

    def test_duplicate_scan_stays_idempotent(self, client):
        """§16: re-scanned QR — no new participation, no signal reset."""
        event = _make_event(starts_in_minutes=30)
        member = _make_member("alice")
        registration = EventRegistration.objects.create(
            event=event, user=member, status="confirmed"
        )
        assert self._checkin(client, registration).status_code == 200
        response = self._checkin(client, registration)
        assert response.status_code == 200
        assert response.json()["already_checked_in"] is True
        assert EventLobbyParticipation.objects.count() == 1

    def test_lobby_failure_never_fails_checkin(self, client, mocker):
        mocker.patch(
            "crush_lu.services.event_lobby.handle_checkin",
            side_effect=RuntimeError("lobby exploded"),
        )
        event = _make_event(starts_in_minutes=30)
        member = _make_member("alice")
        registration = EventRegistration.objects.create(
            event=event, user=member, status="confirmed"
        )
        response = self._checkin(client, registration)
        assert response.status_code == 200
        assert response.json()["success"] is True
        registration.refresh_from_db()
        assert registration.status == "attended"


class TestManualMarkAttended:
    """§10.1: the host's manual mark-attended backstop (api_quiz.mark_attended,
    used for unreadable QR / no-phone cases) mirrors the QR path's lobby side
    effect, so a Connect member checked in by hand still gets their recap
    participation frozen (Codex review on PR #637)."""

    def _quiz_for(self, event, host):
        from crush_lu.models import QuizEvent

        return QuizEvent.objects.create(event=event, status="draft", created_by=host)

    def _mark(self, client, quiz, registration):
        url = reverse("api_quiz_mark_attended", kwargs={"quiz_id": quiz.pk})
        return client.post(
            url,
            data=json.dumps({"registration_id": registration.pk}),
            content_type="application/json",
        )

    def test_manual_mark_attended_creates_participation(self, client, mocker):
        broadcast = mocker.patch(
            "crush_lu.views_event_lobby.broadcast_participant_joined"
        )
        event = _make_event(starts_in_minutes=30)
        host = User.objects.create_user(
            username="quizhost", email="quizhost@example.com", password="x"
        )
        quiz = self._quiz_for(event, host)
        member = _make_member("alice")
        registration = EventRegistration.objects.create(
            event=event, user=member, status="confirmed"
        )
        _login(client, host)
        response = self._mark(client, quiz, registration)
        assert response.status_code == 200
        assert response.json()["success"] is True
        participation = EventLobbyParticipation.objects.get()
        assert participation.user_id == member.pk
        assert participation.eligibility_source == "checkin"
        broadcast.assert_called_once_with(event.pk)

    def test_manual_mark_attended_non_connect_no_participation(self, client):
        event = _make_event(starts_in_minutes=30)
        host = User.objects.create_user(
            username="quizhost", email="quizhost@example.com", password="x"
        )
        quiz = self._quiz_for(event, host)
        guest = _make_member("guest", membership=False)
        registration = EventRegistration.objects.create(
            event=event, user=guest, status="confirmed"
        )
        _login(client, host)
        response = self._mark(client, quiz, registration)
        assert response.status_code == 200
        registration.refresh_from_db()
        assert registration.status == "attended"
        assert EventLobbyParticipation.objects.count() == 0

    def test_lobby_failure_never_fails_manual_mark_attended(self, client, mocker):
        mocker.patch(
            "crush_lu.services.event_lobby.handle_checkin",
            side_effect=RuntimeError("lobby exploded"),
        )
        event = _make_event(starts_in_minutes=30)
        host = User.objects.create_user(
            username="quizhost", email="quizhost@example.com", password="x"
        )
        quiz = self._quiz_for(event, host)
        member = _make_member("alice")
        registration = EventRegistration.objects.create(
            event=event, user=member, status="confirmed"
        )
        _login(client, host)
        response = self._mark(client, quiz, registration)
        assert response.status_code == 200
        assert response.json()["success"] is True
        registration.refresh_from_db()
        assert registration.status == "attended"


# ---------------------------------------------------------------------------
# Roster shaping & read-time eligibility (§5.2, §7.2, §13)
# ---------------------------------------------------------------------------


class TestRoster:
    def test_roster_shows_all_eligible_except_self_photo_only(self):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        chloe = _make_member("chloe")
        for member in (alice, ben, chloe):
            _join(member, event)
        roster = lobby.get_roster(alice, event)
        assert len(roster) == 2
        for entry in roster:
            # §13: opaque handle + authorized photo URL only before a reveal.
            assert set(entry.keys()) == {
                "handle",
                "photo_url",
                "is_mutual",
                "already_met",
                "signalled",
            }
            assert entry["is_mutual"] is False
            assert entry["already_met"] is False
            # The photo route is handle-addressed, never the durable user-id
            # route used by serve_profile_photo.
            assert entry["photo_url"] == reverse(
                "crush_lu:event_lobby_photo",
                kwargs={"event_id": event.pk, "handle": entry["handle"]},
            )

    def test_roster_newest_first(self):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        first = _join(_make_member("ben", gender="M"), event)
        later = _join(_make_member("chloe"), event)
        EventLobbyParticipation.objects.filter(pk=later.pk).update(
            joined_at=timezone.now() + timedelta(minutes=5)
        )
        roster = lobby.get_roster(alice, event)
        assert [e["handle"] for e in roster] == [later.handle, first.handle]

    def test_blocked_pair_mutually_invisible(self):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        UserBlock.objects.create(blocker=alice, blocked=ben)
        assert lobby.get_roster(alice, event) == []
        assert lobby.get_roster(ben, event) == []

    def test_eligibility_loss_hides_at_read_time(self):
        """§5.2: a stale participation row must not preserve roster access."""
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        assert len(lobby.get_roster(alice, event)) == 1
        membership = ben.crush_connect_membership
        membership.excluded_by_coach = True
        membership.save(update_fields=["excluded_by_coach"])
        assert lobby.get_roster(alice, event) == []
        # And the excluded member loses their own access too.
        ben = User.objects.select_related(
            "crushprofile", "crush_connect_membership"
        ).get(pk=ben.pk)
        assert lobby.viewer_participation(ben, event) is None

    def test_inactive_profile_loses_access_and_disappears(self):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)

        profile = ben.crushprofile
        profile.is_active = False
        profile.save(update_fields=["is_active"])
        ben = User.objects.select_related(
            "crushprofile", "crush_connect_membership"
        ).get(pk=ben.pk)

        assert lobby.participant_gate(ben)[0] is False
        assert lobby.viewer_participation(ben, event) is None
        assert lobby.get_roster(alice, event) == []

    def test_corrected_attendance_revokes_api_authorization_and_roster(self):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        ben_handle = _handle_of(ben, event)

        EventRegistration.objects.filter(event=event, user=alice).update(
            status="confirmed"
        )
        assert lobby.viewer_participation(alice, event) is None
        assert (
            lobby.send_meet_signal(alice, event, ben_handle)["result"]
            == "not_participant"
        )

        EventRegistration.objects.filter(event=event, user=ben).update(
            status="confirmed"
        )
        assert lobby.get_roster(alice, event) == []


# ---------------------------------------------------------------------------
# Meet signals: quota, idempotency, mutual reveal (§7.3–7.4, §9.2)
# ---------------------------------------------------------------------------


def _handle_of(user, event):
    return EventLobbyParticipation.objects.get(event=event, user=user).handle


class TestMeetSignals:
    def _lobby_with(self, *usernames):
        event = _make_event()
        genders = ["F", "M"] * 3
        members = [
            _make_member(name, gender=genders[i]) for i, name in enumerate(usernames)
        ]
        for member in members:
            _join(member, event)
        return event, members

    def test_signal_neutral_then_mutual_reveals_exact_pair(self):
        event, (alice, ben, chloe) = self._lobby_with("alice", "ben", "chloe")

        result = lobby.send_meet_signal(alice, event, _handle_of(ben, event))
        assert result["result"] == "sent"
        assert result["signals_remaining"] == 2
        # Recipient sees only the exact anonymous count.
        assert lobby.incoming_signal_count(ben, event) == 1
        assert lobby.get_mutuals(ben, event) == []
        assert lobby.get_mutuals(alice, event) == []

        back = lobby.send_meet_signal(ben, event, _handle_of(alice, event))
        assert back["result"] == "mutual"
        assert back["first_name"] == "Alice"
        # Both directions stamped, mutuals visible to both, count drops.
        assert (
            EventMeetSignal.objects.filter(
                event=event, mutual_revealed_at__isnull=False
            ).count()
            == 2
        )
        assert [m["first_name"] for m in lobby.get_mutuals(alice, event)] == ["Ben"]
        assert [m["first_name"] for m in lobby.get_mutuals(ben, event)] == ["Alice"]
        assert lobby.incoming_signal_count(ben, event) == 0
        # §18: the reveal is for the exact pair only — chloe sees nothing.
        assert lobby.get_mutuals(chloe, event) == []
        roster_chloe = lobby.get_roster(chloe, event)
        assert all(entry["is_mutual"] is False for entry in roster_chloe)

    def test_exact_three_signal_quota(self):
        event, (alice, b, c, d, e) = self._lobby_with("alice", "b", "c", "d", "e")
        for target in (b, c, d):
            assert (
                lobby.send_meet_signal(alice, event, _handle_of(target, event))[
                    "result"
                ]
                == "sent"
            )
        fourth = lobby.send_meet_signal(alice, event, _handle_of(e, event))
        assert fourth["result"] == "quota_exhausted"
        assert EventMeetSignal.objects.filter(event=event, sender=alice).count() == 3
        # Incoming signals are unlimited: everyone can still signal alice.
        for sender in (b, c, d, e):
            assert lobby.send_meet_signal(sender, event, _handle_of(alice, event))[
                "result"
            ] in ("sent", "mutual")
        assert lobby.signals_remaining(alice, event) == 0

    def test_duplicate_signal_idempotent_consumes_nothing(self):
        event, (alice, ben) = self._lobby_with("alice", "ben")
        handle = _handle_of(ben, event)
        assert lobby.send_meet_signal(alice, event, handle)["result"] == "sent"
        duplicate = lobby.send_meet_signal(alice, event, handle)
        assert duplicate["result"] == "duplicate"
        assert duplicate["signals_remaining"] == 2
        assert EventMeetSignal.objects.filter(event=event, sender=alice).count() == 1
        # Duplicate onto an existing mutual re-reports the reveal idempotently.
        lobby.send_meet_signal(ben, event, _handle_of(alice, event))
        again = lobby.send_meet_signal(alice, event, handle)
        assert again["result"] == "mutual"
        assert again["already"] is True

    def test_signal_rejected_at_exact_end(self):
        """§7.6/§13 privacy-adjacent invariant: writes stop at the exact end."""
        event, (alice, ben) = self._lobby_with("alice", "ben")
        handle = _handle_of(ben, event)
        _end_event(event, hours_ago=0)  # end == now (already recap)
        result = lobby.send_meet_signal(alice, event, handle)
        assert result["result"] == "phase_closed"
        assert EventMeetSignal.objects.count() == 0

    def test_signal_to_unknown_blocked_or_cross_event_handle(self):
        event, (alice, ben) = self._lobby_with("alice", "ben")
        other_event = _make_event()
        chloe = _make_member("chloe")
        _join(chloe, other_event)

        # Unknown handle.
        assert (
            lobby.send_meet_signal(alice, event, "deadbeef" * 4)["result"]
            == "unknown_participant"
        )
        # §13: a handle can never be replayed across events.
        cross = lobby.send_meet_signal(alice, event, _handle_of(chloe, other_event))
        assert cross["result"] == "unknown_participant"
        # Blocked pair is indistinguishable from unknown (§8.2).
        UserBlock.objects.create(blocker=ben, blocked=alice)
        blocked = lobby.send_meet_signal(alice, event, _handle_of(ben, event))
        assert blocked["result"] == "unknown_participant"
        assert EventMeetSignal.objects.count() == 0

    def test_counter_excludes_blocked_and_ineligible_senders(self):
        """§18: anonymous counters exclude blocked/ineligible senders."""
        event, (alice, ben, chloe) = self._lobby_with("alice", "ben", "chloe")
        lobby.send_meet_signal(ben, event, _handle_of(alice, event))
        lobby.send_meet_signal(chloe, event, _handle_of(alice, event))
        assert lobby.incoming_signal_count(alice, event) == 2
        UserBlock.objects.create(blocker=alice, blocked=ben)
        assert lobby.incoming_signal_count(alice, event) == 1
        membership = chloe.crush_connect_membership
        membership.excluded_by_coach = True
        membership.save(update_fields=["excluded_by_coach"])
        assert lobby.incoming_signal_count(alice, event) == 0

    def test_signals_survive_deactivation_and_never_reset(self):
        """§16: temporary Connect deactivation never resets the quota."""
        event, (alice, ben, chloe, dan) = self._lobby_with(
            "alice", "ben", "chloe", "dan"
        )
        for target in (ben, chloe, dan):
            lobby.send_meet_signal(alice, event, _handle_of(target, event))
        membership = alice.crush_connect_membership
        membership.excluded_by_coach = True
        membership.save(update_fields=["excluded_by_coach"])
        membership.excluded_by_coach = False
        membership.save(update_fields=["excluded_by_coach"])
        alice = User.objects.select_related(
            "crushprofile", "crush_connect_membership"
        ).get(pk=alice.pk)
        assert lobby.signals_remaining(alice, event) == 0

    def test_signal_rows_are_immutable_at_the_database(self):
        """§9.2 constraints: unique per (event, sender, recipient), no self."""
        event, (alice, ben) = self._lobby_with("alice", "ben")
        EventMeetSignal.objects.create(event=event, sender=alice, recipient=ben)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                EventMeetSignal.objects.create(event=event, sender=alice, recipient=ben)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                EventMeetSignal.objects.create(
                    event=event, sender=alice, recipient=alice
                )


# ---------------------------------------------------------------------------
# HTTP surface (§11, §13)
# ---------------------------------------------------------------------------


def _lobby_url(event):
    return reverse("crush_lu:event_lobby", args=[event.pk])


def _state_url(event):
    return reverse("crush_lu:event_lobby_state_api", args=[event.pk])


def _signal_url(event):
    return reverse("crush_lu:event_lobby_signal_api", args=[event.pk])


class TestLobbyPage:
    def test_anonymous_redirects_to_login(self, client):
        event = _make_event()
        response = client.get(_lobby_url(event))
        assert response.status_code in (301, 302)

    def test_non_attendee_gets_404(self, client):
        event = _make_event()
        outsider = _make_member("outsider")
        _login(client, outsider)
        assert client.get(_lobby_url(event)).status_code == 404

    def test_feature_flag_off_gets_404_even_for_participant(self, client, settings):
        event = _make_event()
        member = _make_member("alice")
        _join(member, event)
        _login(client, member)
        settings.CRUSH_EVENT_LOBBY_ENABLED = False
        assert client.get(_lobby_url(event)).status_code == 404

    def test_checked_in_not_onboarded_sees_cta_never_roster(self, client):
        """§5.3: onboarding CTA instead of the lobby; no roster, no counts."""
        event = _make_event()
        ben = _make_member("ben", gender="M")
        _join(ben, event)
        guest = _make_member("guest", onboarded=False)
        _attend(guest, event)
        _login(client, guest)
        response = client.get(_lobby_url(event))
        assert response.status_code == 200
        html = response.content.decode()
        assert "Finish Crush Connect" in html
        assert "lobby-grid" not in html
        assert _handle_of(ben, event) not in html

    def test_participant_sees_photo_grid_without_pre_mutual_names(self, client):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _login(client, alice)
        response = client.get(_lobby_url(event))
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        html = response.content.decode()
        assert _handle_of(ben, event) in html
        # §13: no first name in HTML, alt text, or data attributes pre-reveal.
        assert "Ben" not in html

    def test_live_template_uses_csp_safe_static_signal_dots(self, client):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        _login(client, alice)

        response = client.get(_lobby_url(event))

        assert response.status_code == 200
        html = response.content.decode()
        assert "x-for" not in html
        assert 'x-show="firstSignalUsed"' in html
        assert 'x-show="thirdSignalAvailable"' in html

    def test_live_component_loads_before_alpine(self, client):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        _login(client, alice)

        html = client.get(_lobby_url(event)).content.decode()

        assert html.index("event-lobby.js") < html.index("js/alpine-components.js")
        assert html.index("js/alpine-components.js") < html.index("@alpinejs/csp")

    def test_after_end_renders_recap_grid_not_live_grid(self, client):
        """After the exact end the page flips from the live grid to the recap
        grid (§7.6/§7.7). Recap tiles are still photo-only for non-mutuals, so
        no first name leaks in the markup."""
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _end_event(event)
        _login(client, alice)
        response = client.get(_lobby_url(event))
        assert response.status_code == 200
        html = response.content.decode()
        assert "lobby-grid" not in html  # the live grid is gone
        assert "recap-grid" in html  # replaced by the recap grid
        assert "Ben" not in html  # non-mutual → photo only, no name (§13)


class TestStateApi:
    def test_non_participant_forbidden(self, client):
        event = _make_event()
        outsider = _make_member("outsider")
        _login(client, outsider)
        response = client.get(_state_url(event))
        assert response.status_code == 403

    def test_corrected_attendance_is_forbidden(self, client):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        _login(client, alice)
        EventRegistration.objects.filter(event=event, user=alice).update(
            status="confirmed"
        )

        response = client.get(_state_url(event))

        assert response.status_code == 403

    def test_state_payload_has_no_pre_mutual_identity(self, client):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _login(client, alice)
        response = client.get(_state_url(event))
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        payload = response.json()
        assert payload["ok"] is True
        assert payload["state"]["phase"] == "live"
        assert payload["state"]["signals_remaining"] == 3
        assert len(payload["roster"]) == 1
        raw = json.dumps(payload)
        assert "Ben" not in raw
        assert f'"{ben.pk}"' not in raw and "user_id" not in raw

    def test_mutual_pair_gets_first_names_in_state(self, client):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        lobby.send_meet_signal(alice, event, _handle_of(ben, event))
        lobby.send_meet_signal(ben, event, _handle_of(alice, event))
        _login(client, alice)
        payload = client.get(_state_url(event)).json()
        assert [m["first_name"] for m in payload["mutuals"]] == ["Ben"]
        assert payload["roster"][0]["is_mutual"] is True

    def test_after_end_state_flips_to_recap_grid(self, client):
        """At the exact end the live roster is replaced by the recap grid
        (§7.6) — recap state carries the confirmation counter, not a live
        countdown, and the recap roster is populated."""
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _end_event(event)
        _login(client, alice)
        payload = client.get(_state_url(event)).json()
        assert payload["state"]["phase"] == "recap"
        assert "incoming_confirmations" in payload["state"]
        assert "seconds_to_end" not in payload["state"]
        assert len(payload["roster"]) == 1

    def test_closed_phase_returns_phase_only(self, client):
        """§13/§18: once the recap closes the API answers with the phase and
        nothing else — expired quotas and the anonymous incoming counter must
        not stay visible to ex-participants."""
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        # Give alice a non-zero incoming counter while the lobby is live so
        # the closed-phase payload provably drops it.
        lobby.send_meet_signal(ben, event, _handle_of(alice, event))
        _end_event(event, hours_ago=49)
        _login(client, alice)
        response = client.get(_state_url(event))
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["state"] == {"phase": "closed"}
        assert payload["roster"] == []
        assert payload["mutuals"] == []


class TestSignalApi:
    def _post(self, client, event, body):
        return client.post(
            _signal_url(event), data=json.dumps(body), content_type="application/json"
        )

    def test_happy_path_sent_without_recipient_leak(self, client):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _login(client, alice)
        response = self._post(client, event, {"handle": _handle_of(ben, event)})
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["result"] == "sent"
        assert payload["signals_remaining"] == 2
        # §13: the recipient's user id must never reach the sender's client.
        assert "recipient_user_id" not in payload
        assert "Ben" not in json.dumps(payload)

    def test_mutual_response_reveals_first_name_and_pushes_privately(
        self, client, mocker
    ):
        notify = mocker.patch("crush_lu.views_event_lobby._notify_lobby_user")
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        lobby.send_meet_signal(ben, event, _handle_of(alice, event))
        _login(client, alice)
        response = self._post(client, event, {"handle": _handle_of(ben, event)})
        payload = response.json()
        assert payload["result"] == "mutual"
        assert payload["first_name"] == "Ben"
        assert "recipient_user_id" not in payload
        # Private per-user hint to the counterpart, sanitized (no identity).
        notify.assert_called_once_with(event.pk, ben.pk, "lobby.mutual", {})

    def test_bad_payload_and_unknown_handle(self, client):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        _login(client, alice)
        assert self._post(client, event, {"nope": 1}).status_code == 400
        response = self._post(client, event, {"handle": "f" * 32})
        assert response.status_code == 404
        assert response.json()["error"] == "unknown_participant"

    def test_non_participant_forbidden(self, client):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(ben, event)
        _login(client, alice)  # attended nothing
        response = self._post(client, event, {"handle": _handle_of(ben, event)})
        assert response.status_code == 403

    def test_signal_after_end_reports_phase_closed(self, client):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        handle = _handle_of(ben, event)
        _end_event(event)
        _login(client, alice)
        payload = self._post(client, event, {"handle": handle}).json()
        assert payload["result"] == "phase_closed"
        assert EventMeetSignal.objects.count() == 0


class TestLobbyPhoto:
    def _photo_url(self, event, handle):
        return reverse(
            "crush_lu:event_lobby_photo",
            kwargs={"event_id": event.pk, "handle": handle},
        )

    def _give_real_photo(self, user, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path)
        profile = user.crushprofile
        profile.photo_1.save("lobby.jpg", ContentFile(b"jpegbytes"), save=True)
        return profile

    def test_participant_fetches_photo_by_handle(self, client, settings, tmp_path):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        self._give_real_photo(ben, settings, tmp_path)
        _join(alice, event)
        _join(ben, event)
        _login(client, alice)
        response = client.get(self._photo_url(event, _handle_of(ben, event)))
        assert response.status_code == 200
        assert response["Content-Type"] == "image/jpeg"
        assert response["Cache-Control"] == "private, max-age=300"

    def test_photo_is_proxied_never_a_sas_redirect(self, client, settings, tmp_path):
        """§13 revocation: even with Azure storage configured the endpoint
        must stream the image itself — a 302 to a SAS URL would keep working
        after a block/exclusion until the token expires (Codex review)."""
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        self._give_real_photo(ben, settings, tmp_path)
        _join(alice, event)
        _join(ben, event)
        _login(client, alice)
        settings.AZURE_ACCOUNT_NAME = "teststorageaccount"
        response = client.get(self._photo_url(event, _handle_of(ben, event)))
        assert response.status_code == 200  # not a 302 redirect
        assert response["Content-Type"] == "image/jpeg"
        assert response.content == b"jpegbytes"

    def test_non_participant_viewer_denied(self, client, settings, tmp_path):
        event = _make_event()
        ben = _make_member("ben", gender="M")
        self._give_real_photo(ben, settings, tmp_path)
        _join(ben, event)
        outsider = _make_member("outsider")
        _login(client, outsider)
        response = client.get(self._photo_url(event, _handle_of(ben, event)))
        assert response.status_code == 404

    def test_corrected_attendance_viewer_denied(self, client):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        _join(alice, event)
        _join(ben, event)
        _login(client, alice)
        EventRegistration.objects.filter(event=event, user=alice).update(
            status="confirmed"
        )

        response = client.get(self._photo_url(event, _handle_of(ben, event)))

        assert response.status_code == 404

    def test_handle_not_replayable_on_other_event(self, client, settings, tmp_path):
        event_a = _make_event()
        event_b = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        self._give_real_photo(ben, settings, tmp_path)
        _join(alice, event_b)
        _join(ben, event_a)
        _login(client, alice)
        # alice is a participant of B, but ben's handle belongs to A.
        response = client.get(self._photo_url(event_b, _handle_of(ben, event_a)))
        assert response.status_code == 404

    def test_blocked_pair_photo_denied(self, client, settings, tmp_path):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        self._give_real_photo(ben, settings, tmp_path)
        _join(alice, event)
        _join(ben, event)
        UserBlock.objects.create(blocker=ben, blocked=alice)
        _login(client, alice)
        response = client.get(self._photo_url(event, _handle_of(ben, event)))
        assert response.status_code == 404

    @pytest.mark.parametrize("status", ["removal_pending", "removed"])
    def test_hidden_encounter_pair_photo_denied(
        self, client, settings, tmp_path, status
    ):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        low, high = ConfirmedEncounter.canonical_pair(alice, ben)
        ConfirmedEncounter.objects.create(
            user_low=low,
            user_high=high,
            status=status,
        )
        self._give_real_photo(ben, settings, tmp_path)
        _join(alice, event)
        _join(ben, event)
        handle = _handle_of(ben, event)
        _login(client, alice)

        response = client.get(self._photo_url(event, handle))

        assert response.status_code == 404

    def test_closed_phase_denies_photos(self, client, settings, tmp_path):
        event = _make_event()
        alice = _make_member("alice")
        ben = _make_member("ben", gender="M")
        self._give_real_photo(ben, settings, tmp_path)
        _join(alice, event)
        _join(ben, event)
        handle = _handle_of(ben, event)
        _end_event(event, hours_ago=49)  # recap window over
        _login(client, alice)
        assert client.get(self._photo_url(event, handle)).status_code == 404


class TestHubCard:
    def test_live_lobby_card_shown_to_participant(self, client):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        _login(client, alice)
        response = client.get(reverse("crush_lu:crush_connect_hub"))
        assert response.status_code == 200
        assert response.context["active_lobby"].event_id == event.pk
        assert "Event Lobby is live" in response.content.decode()

    def test_live_lobby_card_repairs_missing_participation(self, client):
        event = _make_event()
        alice = _make_member("alice")
        registration = _attend(alice, event)
        _login(client, alice)

        response = client.get(reverse("crush_lu:crush_connect_hub"))

        assert response.status_code == 200
        participation = EventLobbyParticipation.objects.get(
            event_registration=registration
        )
        assert response.context["active_lobby"] == participation
        assert "Event Lobby is live" in response.content.decode()

    def test_recap_card_keeps_confirmation_grid_reachable(self, client):
        event = _make_event()
        alice = _make_member("alice")
        participation = _join(alice, event)
        _end_event(event)
        _login(client, alice)

        response = client.get(reverse("crush_lu:crush_connect_hub"))

        assert response.status_code == 200
        assert response.context["active_lobby"] == participation
        assert response.context["active_lobby"].lobby_phase == "recap"
        html = response.content.decode()
        assert "Event recap is ready" in html
        assert reverse("crush_lu:event_lobby", args=[event.pk]) in html

    def test_no_card_after_recap_closes(self, client):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        _end_event(event, hours_ago=49)
        _login(client, alice)
        response = client.get(reverse("crush_lu:crush_connect_hub"))
        assert response.status_code == 200
        assert response.context["active_lobby"] is None
        assert "Event Lobby is live" not in response.content.decode()


# ---------------------------------------------------------------------------
# Consumer authorization (§11.1)
# ---------------------------------------------------------------------------


class TestEventLobbyConsumer:
    @pytest.fixture(autouse=True)
    def _no_close_old_connections(self, monkeypatch):
        # database_sync_to_async closes connections around each hop, which
        # kills the test transaction — same idiom as test_quiz.py.
        monkeypatch.setattr("channels.db.close_old_connections", lambda *a, **kw: None)

    def _can_join(self, event, user):
        from asgiref.sync import async_to_sync

        from crush_lu.consumers_event_lobby import EventLobbyConsumer

        consumer = EventLobbyConsumer()
        consumer.event_id = event.pk
        return async_to_sync(consumer._can_join)(user.pk)

    def test_participant_allowed_while_live(self):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        assert self._can_join(event, alice) is True

    def test_non_participant_and_ineligible_rejected(self):
        event = _make_event()
        alice = _make_member("alice")
        assert self._can_join(event, alice) is False
        _join(alice, event)
        membership = alice.crush_connect_membership
        membership.excluded_by_coach = True
        membership.save(update_fields=["excluded_by_coach"])
        assert self._can_join(event, alice) is False

    def test_corrected_attendance_rejected(self):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        EventRegistration.objects.filter(event=event, user=alice).update(
            status="confirmed"
        )

        assert self._can_join(event, alice) is False

    def test_rejected_after_event_end(self):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        _end_event(event)
        assert self._can_join(event, alice) is False

    def test_rejected_when_flag_off(self, settings):
        event = _make_event()
        alice = _make_member("alice")
        _join(alice, event)
        settings.CRUSH_EVENT_LOBBY_ENABLED = False
        assert self._can_join(event, alice) is False


# ---------------------------------------------------------------------------
# seed_event_lobby_demo guard
# ---------------------------------------------------------------------------


class TestSeedCommandGuard:
    def test_refuses_to_run_on_azure(self, monkeypatch):
        """The demo seeder creates verified accounts with a shared default
        password — it must refuse to run anywhere WEBSITE_HOSTNAME is set
        (every Azure App Service slot, including Kudu SSH shells)."""
        from django.core.management import call_command
        from django.core.management.base import CommandError

        monkeypatch.setenv("WEBSITE_HOSTNAME", "crush-lu-app.azurewebsites.net")
        with pytest.raises(CommandError, match="refuses to run"):
            call_command("seed_event_lobby_demo")
