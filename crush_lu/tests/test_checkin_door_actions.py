"""
Door corrections on the coach check-in page: undo a mis-scan, and check in a
waitlisted walk-up.

Both exist because attendance stopped being a plain status flip. It now
auto-verifies the profile (referral credit + welcome email) and grants a
*permanent* coach, and a re-scan repairs neither — the already-attended branch
never re-saves the row, so ``assign_coach_on_first_attendance`` never fires
again. Before this the only fix for a wrong badge was Django admin.
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.signing import Signer
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    PremiumMembership,
    UserDataConsent,
)
from crush_lu.views_checkin import CHECKIN_UNDO_WINDOW_MINUTES

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]


@pytest.fixture(autouse=True)
def _clear_ratelimit_cache():
    cache.clear()
    yield


def _grant_consent(user):
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )


def _make_coach(username="coach@example.com", first_name="Cam"):
    user = User.objects.create_user(
        username=username, email=username, password="pass12345", first_name=first_name
    )
    _grant_consent(user)
    return CrushCoach.objects.create(user=user, is_active=True)


def _make_attendee(username="ada@example.com"):
    user = User.objects.create_user(
        username=username, email=username, password="pass12345", first_name="Ada"
    )
    _grant_consent(user)
    CrushProfile.objects.create(
        user=user,
        date_of_birth=timezone.now().date() - timedelta(days=365 * 30),
        gender="F",
        location="Luxembourg City",
        is_active=True,
    )
    return user


def _make_event(starts_in_minutes=-30, duration=120):
    now = timezone.now()
    return MeetupEvent.objects.create(
        title="Door Test Event",
        description="x",
        event_type="mixer",
        location="Lux",
        address="1 Test St",
        canton="Luxembourg",
        date_time=now + timedelta(minutes=starts_in_minutes),
        duration_minutes=duration,
        max_participants=30,
        registration_deadline=now + timedelta(minutes=30),
        is_published=True,
    )


# These live in azureproject/urls_crush.py, outside the crush_lu namespace —
# they are language-neutral API routes, like event_checkin_api next to them.
def _undo_url(event, registration):
    return reverse(
        "coach_undo_checkin",
        kwargs={"event_id": event.pk, "registration_id": registration.pk},
    )


def _promote_url(event, registration):
    return reverse(
        "coach_promote_from_waitlist",
        kwargs={"event_id": event.pk, "registration_id": registration.pk},
    )


def _scan(client, event, registration):
    """Drive the real door scan (`event_checkin_api`) on `client`'s session.

    The undo now reverses what the check-in *recorded*, so a hand-built
    ``status="attended"`` row is no longer a stand-in for a scan — it carries
    no provenance and every undo assertion about it would pass or fail for
    reasons unrelated to the door.
    """
    token = Signer().sign(f"{registration.pk}:{event.pk}")
    return client.post(f"/api/events/checkin/{registration.pk}/{token}/")


class TestUndoCheckin:
    def test_undo_reverts_attendance(self, client):
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=timezone.now()
        )
        client.force_login(coach.user)

        response = client.post(_undo_url(event, registration))

        assert response.status_code == 200
        assert response.json()["success"] is True
        registration.refresh_from_db()
        assert registration.status == "confirmed"
        assert registration.checked_in_at is None

    def test_undo_clears_a_coach_this_attendance_granted(self, client):
        """The whole point: a mis-scan makes the scanner someone's permanent
        coach, and nothing else can take it back.

        Driven through the real scan so the grant is the one
        `assign_coach_on_first_attendance` actually wrote — that is the record
        the undo now reads, in place of comparing timestamps.
        """
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)
        assert _scan(client, event, registration).status_code == 200

        profile = attendee.crushprofile
        profile.refresh_from_db()
        assert profile.assigned_coach_id == coach.pk  # the scan granted it
        registration.refresh_from_db()
        assert registration.checkin_granted_coach_id == coach.pk
        assert registration.checkin_granted_coach_at == profile.assigned_coach_at

        response = client.post(_undo_url(event, registration))

        assert response.json()["coach_cleared"] is True
        profile.refresh_from_db()
        assert profile.assigned_coach_id is None
        assert profile.assigned_coach_at is None
        # Provenance describes an attendance the row no longer has.
        registration.refresh_from_db()
        assert registration.checkin_granted_coach_id is None
        assert registration.checkin_granted_coach_at is None
        assert registration.checkin_prior_status == ""

    def test_undo_keeps_a_coach_the_member_already_had(self, client):
        """A member who arrived with a coach must keep it — no other flow can
        restore an assignment this endpoint wrongly stripped."""
        scanning_coach = _make_coach()
        earlier_coach = _make_coach(username="earlier@example.com", first_name="Robin")
        attendee = _make_attendee()
        event = _make_event()
        profile = attendee.crushprofile
        profile.assigned_coach = earlier_coach
        profile.assigned_coach_at = timezone.now() - timedelta(days=30)
        profile.save(update_fields=["assigned_coach", "assigned_coach_at"])
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(scanning_coach.user)
        _scan(client, event, registration)

        registration.refresh_from_db()
        assert registration.checkin_granted_coach_id is None  # granted nothing

        response = client.post(_undo_url(event, registration))

        assert response.json()["coach_cleared"] is False
        profile.refresh_from_db()
        assert profile.assigned_coach_id == earlier_coach.pk

    def test_a_premium_confirmation_inside_the_window_survives_an_undo(self, client):
        """The failure the timestamp comparison caused, in full.

        Scanned at the door, then a pending premium membership is confirmed
        four minutes later — `PremiumMembership.confirm()` writes the same two
        profile fields, so `assigned_coach_at >= checked_in_at` held and the
        undo stripped a coach the member had *paid* for. Nothing restores it.
        """
        door_coach = _make_coach()
        paid_coach = _make_coach(username="paid@example.com", first_name="Robin")
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(door_coach.user)
        _scan(client, event, registration)

        membership = PremiumMembership.objects.create(
            user=attendee, coach=paid_coach, status="pending"
        )
        membership.confirm()

        response = client.post(_undo_url(event, registration))

        assert response.status_code == 200
        assert response.json()["coach_cleared"] is False
        profile = attendee.crushprofile
        profile.refresh_from_db()
        assert profile.assigned_coach_id == paid_coach.pk
        assert profile.assigned_coach_at is not None

    def test_an_admin_reassignment_inside_the_window_survives_an_undo(self, client):
        """Admin edits `assigned_coach` and `assigned_coach_at` as two separate
        form fields, so a reassignment can move the coach and leave the
        timestamp alone — or the reverse. Either way the profile no longer
        holds the pair this check-in wrote, and the undo must not touch it."""
        door_coach = _make_coach()
        new_coach = _make_coach(username="reassigned@example.com", first_name="Robin")
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(door_coach.user)
        _scan(client, event, registration)

        profile = attendee.crushprofile
        profile.refresh_from_db()
        granted_at = profile.assigned_coach_at
        # Coach moved, timestamp deliberately untouched — the harder half.
        CrushProfile.objects.filter(pk=profile.pk).update(assigned_coach=new_coach)

        response = client.post(_undo_url(event, registration))

        assert response.json()["coach_cleared"] is False
        profile.refresh_from_db()
        assert profile.assigned_coach_id == new_coach.pk
        assert profile.assigned_coach_at == granted_at

    def test_undo_restores_confirmed_for_a_scanned_attendee(self, client):
        """The ordinary case still lands where it always did."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)
        _scan(client, event, registration)

        registration.refresh_from_db()
        assert registration.checkin_prior_status == "confirmed"

        payload = client.post(_undo_url(event, registration)).json()

        assert payload["restored_status"] == "confirmed"
        registration.refresh_from_db()
        assert registration.status == "confirmed"

    def test_undo_of_a_promotion_returns_the_walk_up_to_the_waitlist(self, client):
        """Both endpoints, no hand-built fixture.

        A promotion is `waitlist -> attended`. Undoing it used to write
        `confirmed` unconditionally, so a mistaken promotion left the walk-up
        holding a seat nobody gave them — and event capacity plus registration
        priority are counted from exactly that status.
        """
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)

        promote = client.post(_promote_url(event, registration))
        assert promote.status_code == 200
        registration.refresh_from_db()
        assert registration.status == "attended"
        assert registration.checkin_prior_status == "waitlist"

        undo = client.post(_undo_url(event, registration))

        assert undo.status_code == 200
        assert undo.json()["restored_status"] == "waitlist"
        registration.refresh_from_db()
        assert registration.status == "waitlist"
        assert registration.checked_in_at is None
        assert registration.checkin_prior_status == ""

    def test_undo_of_a_promotion_still_clears_the_coach_it_granted(self, client):
        """A promotion grants a permanent coach exactly like a scan does, so
        undoing one has to take it back the same way."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)
        client.post(_promote_url(event, registration))

        profile = attendee.crushprofile
        profile.refresh_from_db()
        assert profile.assigned_coach_id == coach.pk

        payload = client.post(_undo_url(event, registration)).json()

        assert payload["coach_cleared"] is True
        assert payload["restored_status"] == "waitlist"
        profile.refresh_from_db()
        assert profile.assigned_coach_id is None

    def test_undo_of_a_legacy_attendance_restores_confirmed_and_keeps_the_coach(
        self, client
    ):
        """A row checked in before provenance existed carries none of it.

        Both guesses fall the safe way: `confirmed` is what the undo always
        restored, and a coach with no recorded grant is left alone rather than
        stripped on a hunch.
        """
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        checked_in_at = timezone.now()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=checked_in_at
        )
        profile = attendee.crushprofile
        profile.assigned_coach = coach
        profile.assigned_coach_at = checked_in_at + timedelta(seconds=1)
        profile.save(update_fields=["assigned_coach", "assigned_coach_at"])
        client.force_login(coach.user)

        payload = client.post(_undo_url(event, registration)).json()

        assert payload["restored_status"] == "confirmed"
        assert payload["coach_cleared"] is False
        registration.refresh_from_db()
        assert registration.status == "confirmed"
        profile.refresh_from_db()
        assert profile.assigned_coach_id == coach.pk

    def test_a_re_scan_after_an_undo_records_fresh_provenance(self, client):
        """The corrected scan is the one that matters. Its own grant has to be
        undoable, and it must not inherit the cleared one."""
        first_coach = _make_coach()
        second_coach = _make_coach(username="second@example.com", first_name="Robin")
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(first_coach.user)
        _scan(client, event, registration)
        client.post(_undo_url(event, registration))

        client.force_login(second_coach.user)
        _scan(client, event, registration)

        registration.refresh_from_db()
        assert registration.status == "attended"
        assert registration.checkin_granted_coach_id == second_coach.pk

        payload = client.post(_undo_url(event, registration)).json()

        assert payload["coach_cleared"] is True
        profile = attendee.crushprofile
        profile.refresh_from_db()
        assert profile.assigned_coach_id is None

    def test_undo_does_not_revert_verification(self, client):
        """The welcome email and referral credit are already out. Un-verifying
        a member standing in the room is worse than an over-verified walk-in."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=timezone.now()
        )
        profile = attendee.crushprofile
        profile.verification_status = "verified"
        profile.is_approved = True
        profile.save(update_fields=["verification_status", "is_approved"])
        client.force_login(coach.user)

        client.post(_undo_url(event, registration))

        profile.refresh_from_db()
        assert profile.verification_status == "verified"

    def test_undo_returns_the_gender_bucket(self, client):
        """The client decrements the live "In the room" split with this — the
        tile is rendered once and a door shift never reloads."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=timezone.now()
        )
        client.force_login(coach.user)

        payload = client.post(_undo_url(event, registration)).json()

        assert payload["gender"] == "F"

    def test_undo_touches_updated_at(self, client):
        """auto_now only writes when the field is named in update_fields."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=timezone.now()
        )
        EventRegistration.objects.filter(pk=registration.pk).update(
            updated_at=timezone.now() - timedelta(days=1)
        )
        stale = EventRegistration.objects.get(pk=registration.pk).updated_at
        client.force_login(coach.user)

        client.post(_undo_url(event, registration))

        registration.refresh_from_db()
        assert registration.updated_at > stale

    def test_undo_refused_outside_the_window(self, client):
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event,
            user=attendee,
            status="attended",
            checked_in_at=timezone.now()
            - timedelta(minutes=CHECKIN_UNDO_WINDOW_MINUTES + 1),
        )
        client.force_login(coach.user)

        response = client.post(_undo_url(event, registration))

        assert response.status_code == 409
        registration.refresh_from_db()
        assert registration.status == "attended"

    def test_undo_refused_for_undated_attendance(self, client):
        """checked_in_at is nullable and an attended row can legitimately have
        none — attendance entered administratively or by an older flow. Skipping
        the age check there turns a 15-minute correction into an editor for
        attendance of any age, because every attended row offers Undo."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=None
        )
        client.force_login(coach.user)

        response = client.post(_undo_url(event, registration))

        assert response.status_code == 409
        registration.refresh_from_db()
        assert registration.status == "attended"

    def test_undo_releases_the_quiz_seat(self, client):
        """A status flip does not free the chair. The membership and the
        rotation rows are separate objects, so without this the mistakenly
        scanned person still occupies a seat their table is short of."""
        from crush_lu.models.quiz import (
            QuizEvent,
            QuizRotationSchedule,
            QuizTableMembership,
        )

        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        event.event_type = "quiz_night"
        event.save(update_fields=["event_type"])
        QuizEvent.objects.create(
            event=event, status="draft", created_by=coach.user, num_tables=2
        )
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)

        # Drive both endpoints rather than hand-building the seat: the point is
        # that the pair is symmetric, which a fixture cannot demonstrate.
        assert client.post(_promote_url(event, registration)).status_code == 200
        assert QuizTableMembership.objects.filter(user=attendee).exists()
        assert QuizRotationSchedule.objects.filter(user=attendee).exists()

        assert client.post(_undo_url(event, registration)).status_code == 200

        assert not QuizTableMembership.objects.filter(user=attendee).exists()
        assert not QuizRotationSchedule.objects.filter(user=attendee).exists()

    def test_the_freed_seat_is_named_under_its_own_key(self, client):
        """`released_table_numbers`, never `table_number`.

        The acting coach's page has to put its own table-fill grid back, and
        the only channel for that is this response. But `handleRemoteCheckin`
        still reads an undo broadcast as an arrival (#710), and it keys on
        `table_number` — so naming it that way would make every *other* coach's
        page count the freed seat up. Two keys is what keeps one page correcting
        itself from becoming every page double-counting.
        """
        from crush_lu.models.quiz import QuizEvent

        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        event.event_type = "quiz_night"
        event.save(update_fields=["event_type"])
        QuizEvent.objects.create(
            event=event, status="draft", created_by=coach.user, num_tables=2
        )
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)
        _scan(client, event, registration)

        payload = client.post(_undo_url(event, registration)).json()

        assert payload["released_table_numbers"] in ([1], [2])
        assert "table_number" not in payload

    def test_the_door_grid_is_given_back_the_table_it_counts(self, client):
        """A rotator undone after moving. The door page's table-fill grid is
        built from QuizTableMembership, which stays on the round-0 table, so
        the response has to name that one — decrementing the table they had
        rotated to would leave it a seat short *and* strand a seat on the tile
        they actually vacated. The live broadcast still gets the current one."""
        from crush_lu.models.quiz import (
            QuizEvent,
            QuizRotationSchedule,
            QuizRound,
            QuizTableMembership,
        )

        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        event.event_type = "quiz_night"
        event.save(update_fields=["event_type"])
        quiz = QuizEvent.objects.create(
            event=event, status="draft", created_by=coach.user, num_tables=2
        )
        quiz.ensure_tables()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)
        _scan(client, event, registration)

        # Rotate them off their check-in table: membership stays at 1, the
        # current round seats them at 2.
        seated_at = QuizTableMembership.objects.get(
            table__quiz=quiz, user=attendee
        ).table
        other = quiz.tables.exclude(pk=seated_at.pk).first()
        QuizRound.objects.create(quiz=quiz, title="R1", sort_order=0)
        second = QuizRound.objects.create(quiz=quiz, title="R2", sort_order=1)
        quiz.current_round = second
        quiz.save(update_fields=["current_round"])
        QuizRotationSchedule.objects.create(
            quiz=quiz, round_number=1, table=other, user=attendee, role="rotator"
        )
        quiz = QuizEvent.objects.get(pk=quiz.pk)
        assert quiz.get_round_number() == 1

        with mock.patch(
            "crush_lu.views_checkin._broadcast_quiz_table_update"
        ) as broadcast:
            payload = client.post(_undo_url(event, registration)).json()

        assert payload["released_table_numbers"] == [seated_at.table_number]
        assert broadcast.call_args.args[1]["table_number"] == other.table_number

    def test_a_non_quiz_undo_names_no_table(self, client):
        """The key is absent, not null — the client tests it for truthiness and
        an ordinary mixer must not reach the table-fill branch at all."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)
        _scan(client, event, registration)

        payload = client.post(_undo_url(event, registration)).json()

        assert payload["success"] is True
        assert "released_table_numbers" not in payload

    def test_undo_reactivates_the_wallet_ticket(
        self, client, settings, django_capture_on_commit_callbacks
    ):
        """The scan marked the pass completed. Leaving it there hands the member
        a used ticket while their registration is valid and they are still at
        the door.

        The PATCH is deferred to commit, so the callbacks have to be executed
        explicitly — pytest-django's transaction never commits, and without
        this the assertion below would pass for the wrong reason.
        """
        settings.WALLET_GOOGLE_EVENT_TICKET_ENABLED = True
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event,
            user=attendee,
            status="attended",
            checked_in_at=timezone.now(),
            google_wallet_ticket_object_id="issuer.ticket-1",
        )
        client.force_login(coach.user)

        with mock.patch(
            "crush_lu.wallet.google_event_ticket_api.activate_event_ticket",
            return_value={"success": True, "message": "ok"},
        ) as activate:
            with django_capture_on_commit_callbacks(execute=True):
                assert client.post(_undo_url(event, registration)).status_code == 200

        assert activate.call_count == 1
        assert activate.call_args[0][0].pk == registration.pk

    def test_a_deferred_wallet_patch_sends_what_the_row_says_now(
        self, settings, django_capture_on_commit_callbacks
    ):
        """Committing releases the row lock, so two overlapping door actions
        can have PATCHes in flight at once and the last one decides what Google
        stores. The state is re-derived at send time, so a scan whose PATCH
        lands after an undo sends `active` — matching the row — instead of the
        `completed` it decided on up to 30 seconds earlier."""
        settings.WALLET_GOOGLE_EVENT_TICKET_ENABLED = True
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event,
            user=attendee,
            status="confirmed",
            google_wallet_ticket_object_id="issuer.ticket-1",
        )

        with (
            mock.patch(
                "crush_lu.wallet.google_event_ticket_api.complete_event_ticket",
                return_value={"success": True, "message": "ok"},
            ) as complete,
            mock.patch(
                "crush_lu.wallet.google_event_ticket_api.activate_event_ticket",
                return_value={"success": True, "message": "ok"},
            ) as activate,
        ):
            with django_capture_on_commit_callbacks(execute=True):
                registration.status = "attended"
                registration.save(update_fields=["status"])
                # Another coach undoes it before the deferred PATCH runs.
                EventRegistration.objects.filter(pk=registration.pk).update(
                    status="confirmed"
                )

        assert complete.call_count == 0, "sent a decision the row had moved past"
        assert activate.call_count == 1

    def test_an_unrelated_save_does_not_reactivate_the_ticket(
        self, settings, django_capture_on_commit_callbacks
    ):
        """Reactivation is flagged by the undo path, not derived from the
        status. _ensure_ticket_object_id and _generate_checkin_token both save
        non-status fields on a confirmed row, and the first of them runs
        *before* the JWT has created the Wallet object — a status-only branch
        would PATCH a 404 and still allow the request its 30 seconds, in front
        of ticket generation."""
        settings.WALLET_GOOGLE_EVENT_TICKET_ENABLED = True
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event,
            user=attendee,
            status="confirmed",
            google_wallet_ticket_object_id="issuer.ticket-1",
        )

        with mock.patch(
            "crush_lu.wallet.google_event_ticket_api.activate_event_ticket",
            return_value={"success": True, "message": "ok"},
        ) as activate:
            with django_capture_on_commit_callbacks(execute=True):
                registration.checkin_token = "tok"
                registration.save(update_fields=["checkin_token"])

        assert activate.call_count == 0

    def test_undo_refused_when_not_checked_in(self, client):
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)

        assert client.post(_undo_url(event, registration)).status_code == 409

    def test_undo_requires_a_coach(self, client):
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=timezone.now()
        )
        client.force_login(attendee)

        response = client.post(_undo_url(event, registration))

        assert response.status_code in (302, 403)
        registration.refresh_from_db()
        assert registration.status == "attended"

    def test_undo_rejects_get(self, client):
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=timezone.now()
        )
        client.force_login(coach.user)

        assert client.get(_undo_url(event, registration)).status_code == 405

    def test_undo_drops_the_member_from_the_lobby_roster(self, client, settings):
        """No participation cleanup is needed — eligible_participations
        re-checks the registration status at read time (§5.2). Pinned so a
        future change to that query cannot silently leave a non-attendee on
        the live roster."""
        settings.CRUSH_EVENT_LOBBY_ENABLED = True
        settings.CRUSH_CONNECT_LAUNCHED = True
        from crush_lu.models import EventLobbyParticipation
        from crush_lu.services.event_lobby import eligible_participations

        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=timezone.now()
        )
        EventLobbyParticipation.objects.create(
            event=event, user=attendee, event_registration=registration
        )
        client.force_login(coach.user)

        client.post(_undo_url(event, registration))

        assert not eligible_participations(event).filter(user=attendee).exists()


class TestPromoteFromWaitlist:
    def test_promote_checks_the_walk_up_in(self, client):
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)

        response = client.post(_promote_url(event, registration))

        assert response.status_code == 200
        assert response.json()["promoted"] is True
        registration.refresh_from_db()
        assert registration.status == "attended"
        assert registration.checked_in_at is not None

    def test_promoting_coach_becomes_the_permanent_coach(self, client):
        """Must not differ from a normal scan: PR #698 made the person at the
        door the coach, and a promotion is a check-in at the door."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)

        client.post(_promote_url(event, registration))

        attendee.crushprofile.refresh_from_db()
        assert attendee.crushprofile.assigned_coach_id == coach.pk

    def test_promote_refused_for_a_confirmed_registration(self, client):
        """Confirmed attendees have their own Check In button; routing them
        through here would skip the QR token path entirely."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)

        assert client.post(_promote_url(event, registration)).status_code == 409

    def test_promote_is_idempotent_against_a_double_tap(self, client):
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)

        first = client.post(_promote_url(event, registration))
        second = client.post(_promote_url(event, registration))

        assert first.status_code == 200
        assert second.status_code == 409
        assert EventRegistration.objects.filter(status="attended").count() == 1

    def test_promote_enforces_the_check_in_window(self, client):
        """A promotion grants a seat, a verification and a permanent coach.
        None of that should be reachable from a stale event page weeks later
        just because this route skipped the check the scanner enforces."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event(starts_in_minutes=-60 * 24 * 30)  # a month ago
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)

        response = client.post(_promote_url(event, registration))

        assert response.status_code == 400
        registration.refresh_from_db()
        assert registration.status == "waitlist"

    def test_promote_refused_for_a_cancelled_event(self, client):
        """Cancelling an event only flips is_cancelled — the waitlist rows keep
        their status, so an already-open scanner tab could still grant
        attendance, verification and a permanent coach for an event that is
        not happening."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        MeetupEvent.objects.filter(pk=event.pk).update(is_cancelled=True)
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)

        response = client.post(_promote_url(event, registration))

        assert response.status_code == 409
        registration.refresh_from_db()
        assert registration.status == "waitlist"

    def test_promote_reports_itself_as_a_promotion(self, client):
        """The client keys the counter maths off this: a promoted row was
        never in the expected set, so unlike a scan it must not decrement
        "not arrived"."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)

        payload = client.post(_promote_url(event, registration)).json()

        assert payload["promoted"] is True
        assert payload["profile"]["gender"] == "F"

    def test_promote_requires_a_coach(self, client):
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(attendee)

        response = client.post(_promote_url(event, registration))

        assert response.status_code in (302, 403)
        registration.refresh_from_db()
        assert registration.status == "waitlist"


class TestQuizSeatRelease:
    """release_table_on_undo is the inverse of assign_table_on_checkin, and
    has to undo everything the seat accumulated — not just the chair."""

    def _seated_quiz(self, rotated_to=None):
        from crush_lu.models.quiz import (
            QuizEvent,
            QuizRotationSchedule,
            QuizRound,
            QuizTableMembership,
        )

        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        quiz = QuizEvent.objects.create(
            event=event, status="draft", created_by=coach.user, num_tables=2
        )
        tables = quiz.ensure_tables()
        QuizRound.objects.create(quiz=quiz, title="Round 1", sort_order=0)
        second = QuizRound.objects.create(quiz=quiz, title="Round 2", sort_order=1)

        QuizTableMembership.objects.create(table=tables[1], user=attendee)
        QuizRotationSchedule.objects.create(
            quiz=quiz, round_number=0, table=tables[1], user=attendee, role="rotator"
        )
        if rotated_to is not None:
            quiz.current_round = second
            quiz.save(update_fields=["current_round"])
            QuizRotationSchedule.objects.create(
                quiz=quiz,
                round_number=1,
                table=tables[rotated_to],
                user=attendee,
                role="rotator",
            )
        return quiz, attendee

    def test_it_notifies_the_table_the_player_is_at_now(self):
        """A rotator's membership row stays on their round-0 table while their
        live subscription follows the current schedule row. Broadcasting the
        membership table mid-quiz refreshes a table they already left, so the
        people they are actually sitting with keep seeing them."""
        from crush_lu.services.quiz_rotation import release_table_on_undo

        quiz, attendee = self._seated_quiz(rotated_to=2)
        assert quiz.get_round_number() == 1

        released = release_table_on_undo(quiz, attendee)

        assert released["table_number"] == 2
        # …and the door page needs the other one. Its table-fill grid is
        # rendered from QuizTableMembership, which never moved off table 1, so
        # decrementing the current table would leave T2 a seat short and T1
        # still holding one nobody occupies. Same release, two audiences.
        assert released["membership_table_numbers"] == [1]

    def test_it_reads_the_round_through_the_lock_not_the_callers_instance(self):
        """An undo that waited behind a round advance holds a pre-advance
        instance. Deriving the table from it would announce the departure at
        the table they had already left, leaving the new one stale."""
        from crush_lu.models.quiz import QuizEvent
        from crush_lu.services.quiz_rotation import release_table_on_undo

        quiz, attendee = self._seated_quiz(rotated_to=2)

        # The caller's copy still points at round 0; the committed row is on
        # round 1, which is where the player actually is.
        stale = QuizEvent.objects.get(pk=quiz.pk)
        stale.current_round = quiz.rounds.order_by("sort_order").first()
        assert stale.get_round_number() == 0

        assert release_table_on_undo(stale, attendee)["table_number"] == 2

    def test_it_falls_back_to_the_membership_table_before_any_rotation(self):
        from crush_lu.services.quiz_rotation import release_table_on_undo

        quiz, attendee = self._seated_quiz()

        assert release_table_on_undo(quiz, attendee) == {
            "table_number": 1,
            "membership_table_numbers": [1],
            "scores_removed": False,
        }

    def test_it_removes_the_scores_the_undone_attendee_collected(self):
        """A table scored inside the 15-minute undo window creates an
        IndividualScore for every scheduled member. Leaving them behind keeps
        a person the undo declares was never present on the leaderboard."""
        from crush_lu.models.quiz import IndividualScore, QuizQuestion
        from crush_lu.services.quiz_rotation import release_table_on_undo

        quiz, attendee = self._seated_quiz()
        question = QuizQuestion.objects.create(
            round=quiz.rounds.first(), text="Capital of Luxembourg?"
        )
        IndividualScore.objects.create(
            quiz=quiz, user=attendee, question=question, points_earned=10
        )

        release_table_on_undo(quiz, attendee)

        assert not IndividualScore.objects.filter(quiz=quiz, user=attendee).exists()

    def test_it_is_a_no_op_for_someone_who_never_sat_down(self):
        from crush_lu.services.quiz_rotation import release_table_on_undo

        quiz, _attendee = self._seated_quiz()
        stranger = _make_attendee("stranger@example.com")

        assert release_table_on_undo(quiz, stranger) is None

    def _broadcast_groups(self, quiz, table_assignment):
        from crush_lu.views_checkin import _broadcast_quiz_table_update

        sent = []
        with (
            mock.patch(
                "crush_lu.views_checkin.get_channel_layer",
                return_value=mock.MagicMock(),
            ),
            mock.patch(
                "crush_lu.views_checkin.async_to_sync",
                lambda fn: lambda group, payload: sent.append(group),
            ),
        ):
            _broadcast_quiz_table_update(quiz.event, table_assignment)
        return sent

    def test_the_projector_and_host_refresh_even_when_no_seat_was_freed(self):
        """A cleanup that frees no current-round seat carries no table number,
        and both the projector and the host overview show the whole room —
        attendance, the individual leaderboard, every table's roster. All of
        that just changed. Returning early would leave the removed attendee on
        screen until some other quiz event happened to fire."""
        quiz, _attendee = self._seated_quiz()

        sent = self._broadcast_groups(quiz, {"table_number": None})

        assert sent == [f"quiz_{quiz.id}_display", f"quiz_{quiz.id}_host"]

    def test_a_named_table_reaches_the_players_the_projector_and_the_host(self):
        """Three audiences, three groups — and deliberately not `quiz_<id>`,
        which the host shares with every player. A player's own
        `quiz.table_update` branch refetches their assignment, so putting a
        seat change on the shared group would have the entire room refetch on
        every door scan, and would reach the projector twice over (a display
        connection joins `quiz_<id>` as well as `quiz_<id>_display`)."""
        quiz, _attendee = self._seated_quiz()
        table = quiz.tables.get(table_number=1)

        sent = self._broadcast_groups(quiz, {"table_number": 1})

        assert sent == [
            f"quiz_{quiz.id}_table_{table.id}",
            f"quiz_{quiz.id}_display",
            f"quiz_{quiz.id}_host",
        ]
        assert f"quiz_{quiz.id}" not in sent

    def test_it_clears_schedule_rows_left_without_a_membership(self):
        """Someone checked in before num_tables was configured never gets a
        membership — assign_table_on_checkin returns early — but a later
        generate_rotation_rounds can still schedule them off their attendance.
        Keying the cleanup on membership would leave a confirmed non-attendee
        seated for the rest of the quiz."""
        from crush_lu.models.quiz import (
            IndividualScore,
            QuizQuestion,
            QuizRotationSchedule,
        )
        from crush_lu.services.quiz_rotation import release_table_on_undo

        quiz, attendee = self._seated_quiz()
        orphan = _make_attendee("orphan@example.com")
        table = quiz.tables.get(table_number=2)
        QuizRotationSchedule.objects.create(
            quiz=quiz, round_number=0, table=table, user=orphan, role="anchor"
        )
        question = QuizQuestion.objects.create(
            round=quiz.rounds.first(), text="Capital of Luxembourg?"
        )
        IndividualScore.objects.create(
            quiz=quiz, user=orphan, question=question, points_earned=10
        )
        assert not orphan.quiz_tables.exists(), "fixture should have no membership"

        released = release_table_on_undo(quiz, orphan)

        # No membership row, so nothing for the door grid to give back — it
        # counts memberships, and this one never had one.
        assert released == {
            "table_number": 2,
            "membership_table_numbers": [],
            "scores_removed": True,
        }
        assert not QuizRotationSchedule.objects.filter(quiz=quiz, user=orphan).exists()
        assert not IndividualScore.objects.filter(quiz=quiz, user=orphan).exists()
        # The properly-seated attendee is untouched.
        assert QuizRotationSchedule.objects.filter(quiz=quiz, user=attendee).exists()


class TestDoorPageContext:
    def _page(self, client, event):
        return client.get(reverse("crush_lu:coach_event_checkin", args=[event.pk]))

    def test_waitlisted_rows_reach_the_page(self, client):
        """They never did before, which is why a walk-up meant leaving the
        scanner for the event management page."""
        coach = _make_coach()
        waiting = _make_attendee("wait@example.com")
        event = _make_event()
        EventRegistration.objects.create(event=event, user=waiting, status="waitlist")
        client.force_login(coach.user)

        response = self._page(client, event)

        assert response.status_code == 200
        assert response.context["waitlist_count"] == 1
        assert b"waitlist-reg-" in response.content

    def test_waitlisted_rows_do_not_get_check_in_tokens(self, client):
        """Promotion is addressed by registration id, so a waitlisted row
        never needs a signed token — and minting one is not free.
        _generate_checkin_token saves the registration, which fires the Wallet
        pass refresh signal (the Google call allows 30s). Doing that per
        waitlisted row would stall the one page that must load instantly."""
        coach = _make_coach()
        waiting = _make_attendee("wait@example.com")
        confirmed = _make_attendee("conf@example.com")
        event = _make_event()
        wl = EventRegistration.objects.create(
            event=event, user=waiting, status="waitlist"
        )
        cf = EventRegistration.objects.create(
            event=event, user=confirmed, status="confirmed"
        )
        client.force_login(coach.user)

        self._page(client, event)

        wl.refresh_from_db()
        cf.refresh_from_db()
        assert not wl.checkin_token, "waitlisted row was given a token"
        assert cf.checkin_token, "confirmed row lost its token"

    def test_undated_attendance_gets_no_undo_button(self, client):
        """The endpoint refuses a row with no checked_in_at, so rendering the
        button would only ever offer a correction that answers 409."""
        coach = _make_coach()
        dated = _make_attendee("dated@example.com")
        undated = _make_attendee("undated@example.com")
        event = _make_event()
        EventRegistration.objects.create(
            event=event, user=dated, status="attended", checked_in_at=timezone.now()
        )
        EventRegistration.objects.create(
            event=event, user=undated, status="attended", checked_in_at=None
        )
        client.force_login(coach.user)

        content = self._page(client, event).content.decode()

        assert content.count("manual-undo-btn") == 1

    def test_counts_split_arrived_from_outstanding(self, client):
        coach = _make_coach()
        here = _make_attendee("here@example.com")
        missing = _make_attendee("missing@example.com")
        event = _make_event()
        EventRegistration.objects.create(
            event=event, user=here, status="attended", checked_in_at=timezone.now()
        )
        EventRegistration.objects.create(event=event, user=missing, status="confirmed")
        client.force_login(coach.user)

        response = self._page(client, event)

        assert response.context["attended_count"] == 1
        assert response.context["outstanding_count"] == 1
        assert response.context["confirmed_count"] == 2

    def test_gender_split_counts_only_who_arrived(self, client):
        coach = _make_coach()
        here = _make_attendee("she@example.com")
        missing = _make_attendee("he@example.com")
        missing.crushprofile.gender = "M"
        missing.crushprofile.save(update_fields=["gender"])
        event = _make_event()
        EventRegistration.objects.create(
            event=event, user=here, status="attended", checked_in_at=timezone.now()
        )
        EventRegistration.objects.create(event=event, user=missing, status="confirmed")
        client.force_login(coach.user)

        response = self._page(client, event)

        assert response.context["gender_checked_in"]["F"] == 1
        assert response.context["gender_checked_in"]["M"] == 0
        assert response.context["gender_expected"]["M"] == 1

    def test_row_shows_the_assigned_coach_not_the_reviewer(self, client):
        """Attendance is what grants the permanent coach, so this row must
        describe that relationship and no other."""
        from crush_lu.models import ProfileSubmission

        coach = _make_coach()
        reviewer = _make_coach(username="reviewer@example.com", first_name="Robin")
        attendee = _make_attendee()
        event = _make_event()
        EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=timezone.now()
        )
        profile = attendee.crushprofile
        profile.assigned_coach = coach
        profile.assigned_coach_at = timezone.now()
        profile.save(update_fields=["assigned_coach", "assigned_coach_at"])
        ProfileSubmission.objects.create(profile=profile, coach=reviewer)
        client.force_login(coach.user)

        html = self._page(client, event).content.decode()

        assert "Cam" in html
        assert "Robin" not in html

    def test_attended_without_a_coach_is_called_out(self, client):
        """Kimi's failure mode: an event with no coaches attached leaves every
        first-time attendee coachless, nothing breaks on the night, and a
        re-scan cannot repair it."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=timezone.now()
        )
        client.force_login(coach.user)

        html = self._page(client, event).content.decode()

        assert "No coach yet" in html


class TestSeatLockOrder:
    """Every seat operation must take the quiz row before the tables.

    `release_table_on_undo` used to lock the tables first and then the quiz,
    while `dissolve_table` and `manual_assign_table` lock the quiz and then a
    table — a straight ABBA deadlock when a host dissolves or hand-assigns a
    table in the same moment as an undo. It is worse than a plain deadlock
    error: `coach_undo_checkin` swallows a failure from the release so the
    correction still lands, so the undo loses the tie and commits having
    silently left the seat and the scores in place.

    `assign_table_on_checkin` had the same inversion by a longer route — it
    locked only the tables, then reached the quiz row through the
    `generate_rotation_rounds` call, which runs inside the caller's
    transaction with those table locks still held.

    Asserted by inspection: two connections deadlocking is not something
    SQLite can be made to express.
    """

    def _lock_order(self, fn):
        import inspect
        import re

        src = inspect.getsource(inspect.unwrap(fn))
        return [
            m.group(1)
            for m in re.finditer(
                r"(QuizEvent|QuizTable)\.objects[\s\S]{0,120}?"
                r"select_for_update\(\)",
                src,
            )
        ]

    def test_the_release_takes_the_quiz_row_before_the_tables(self):
        from crush_lu.services.quiz_rotation import release_table_on_undo

        order = self._lock_order(release_table_on_undo)

        assert order[:2] == ["QuizEvent", "QuizTable"], order

    def test_the_assign_takes_the_quiz_row_before_the_tables(self):
        from crush_lu.services.quiz_rotation import assign_table_on_checkin

        order = self._lock_order(assign_table_on_checkin)

        assert order[:2] == ["QuizEvent", "QuizTable"], order

    def test_the_host_side_operations_agree(self):
        """The order the other two were already using, pinned so a future
        change to either side has to move both."""
        from crush_lu.services.quiz_rotation import dissolve_table, manual_assign_table

        for fn in (dissolve_table, manual_assign_table):
            assert self._lock_order(fn)[:2] == ["QuizEvent", "QuizTable"], fn.__name__


class TestSeatReleaseEdges:
    """Cases the second Codex round surfaced."""

    def _quiz_night(self, num_tables=3):
        from crush_lu.models.quiz import QuizEvent

        coach = _make_coach()
        event = _make_event()
        event.event_type = "quiz_night"
        event.save(update_fields=["event_type"])
        quiz = QuizEvent.objects.create(
            event=event, status="draft", created_by=coach.user, num_tables=num_tables
        )
        quiz.ensure_tables()
        return coach, event, quiz

    def test_every_chair_is_released_not_just_the_first(self, client):
        """`unique_together` is (table, user), so one person can legally hold
        chairs at two tables of the same quiz — the admin's
        QuizTableMembershipInline creates exactly that. Releasing only the
        first left the other occupied, and the door grid kept counting it."""
        from crush_lu.models.quiz import QuizTableMembership
        from crush_lu.services.quiz_rotation import release_table_on_undo

        coach, event, quiz = self._quiz_night()
        attendee = _make_attendee()
        for number in (1, 3):
            QuizTableMembership.objects.create(
                table=quiz.tables.get(table_number=number), user=attendee
            )

        released = release_table_on_undo(quiz, attendee)

        assert released["membership_table_numbers"] == [1, 3]
        assert not QuizTableMembership.objects.filter(
            table__quiz=quiz, user=attendee
        ).exists()

    def test_the_door_response_names_both_tables(self, client):
        from crush_lu.models.quiz import QuizTableMembership

        coach, event, quiz = self._quiz_night()
        attendee = _make_attendee()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)
        _scan(client, event, registration)
        # A second chair, as only the admin can create.
        spare = quiz.tables.exclude(
            pk=QuizTableMembership.objects.get(table__quiz=quiz, user=attendee).table_id
        ).first()
        QuizTableMembership.objects.create(table=spare, user=attendee)

        payload = client.post(_undo_url(event, registration)).json()

        assert len(payload["released_table_numbers"]) == 2
        assert spare.table_number in payload["released_table_numbers"]

    def test_the_room_is_told_when_scores_disappear(self, client):
        """Deleting IndividualScore rows changes the standings everyone can
        see, not just the people at that table — and the table broadcast only
        reaches the latter, whose handler refetches an assignment, not a
        leaderboard."""
        from crush_lu.models.quiz import IndividualScore, QuizQuestion, QuizRound

        coach, event, quiz = self._quiz_night()
        attendee = _make_attendee()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)
        _scan(client, event, registration)
        round1 = QuizRound.objects.create(quiz=quiz, title="R1", sort_order=0)
        question = QuizQuestion.objects.create(round=round1, text="Q?", points=10)
        IndividualScore.objects.create(
            quiz=quiz, user=attendee, question=question, points_earned=10
        )

        with mock.patch(
            "crush_lu.views_checkin._broadcast_quiz_leaderboard"
        ) as leaderboard:
            client.post(_undo_url(event, registration))

        assert leaderboard.call_count == 1

    def test_no_leaderboard_broadcast_when_nothing_was_scored(self, client):
        """The ordinary door undo. Standings did not change, so the whole room
        does not need waking up for it."""
        coach, event, quiz = self._quiz_night()
        attendee = _make_attendee()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)
        _scan(client, event, registration)

        with mock.patch(
            "crush_lu.views_checkin._broadcast_quiz_leaderboard"
        ) as leaderboard:
            client.post(_undo_url(event, registration))

        assert leaderboard.call_count == 0

    def test_the_leaderboard_goes_to_the_shared_group(self, client):
        """`quiz_<id>`, deliberately — the standings are the one thing on that
        page that really is the same for the whole room, and `quiz.leaderboard`
        triggers no assignment refetch."""
        from crush_lu.views_checkin import _broadcast_quiz_leaderboard

        coach, event, quiz = self._quiz_night()
        sent = []

        with (
            mock.patch(
                "crush_lu.views_checkin.get_channel_layer",
                return_value=mock.MagicMock(),
            ),
            mock.patch(
                "crush_lu.views_checkin.async_to_sync",
                lambda fn: lambda group, payload: sent.append((group, payload["type"])),
            ),
        ):
            _broadcast_quiz_leaderboard(event)

        assert sent == [(f"quiz_{quiz.id}", "quiz.leaderboard")]


def _summary_url(event):
    event_id = getattr(event, "pk", event)
    return reverse("event_checkin_summary", kwargs={"event_id": event_id})


class TestCheckinSummary:
    """`event_checkin_summary` — the read-only counters the door page refetches
    after every door action, local or broadcast (#710).

    The page used to bump these tiles by hand from whichever handler knew
    about them; the endpoint makes the server the single source, so a path
    that forgets a tile can only be late, never wrong."""

    def test_anonymous_requests_are_sent_to_login(self, client):
        response = client.get(_summary_url(_make_event()))
        assert response.status_code == 302
        assert "login" in response["Location"]

    def test_non_coaches_do_not_get_counters(self, client):
        plain = _make_attendee("plain@example.com")
        client.force_login(plain)
        response = client.get(_summary_url(_make_event()))
        assert response.status_code == 302
        assert response["Location"] == reverse("crush_lu:dashboard")

    def test_unknown_event_is_a_404(self, client):
        coach = _make_coach()
        client.force_login(coach.user)
        assert client.get(_summary_url(999999)).status_code == 404

    def test_counts_split_arrived_outstanding_and_waitlist(self, client):
        coach = _make_coach()
        event = _make_event()
        EventRegistration.objects.create(
            event=event,
            user=_make_attendee("here@example.com"),
            status="attended",
            checked_in_at=timezone.now(),
        )
        EventRegistration.objects.create(
            event=event,
            user=_make_attendee("missing@example.com"),
            status="confirmed",
        )
        EventRegistration.objects.create(
            event=event, user=_make_attendee("wait@example.com"), status="waitlist"
        )
        client.force_login(coach.user)

        summary = client.get(_summary_url(event)).json()

        assert summary["success"] is True
        assert summary["attended_count"] == 1
        assert summary["expected_count"] == 2
        assert summary["outstanding_count"] == 1
        assert summary["waitlist_count"] == 1

    def test_cancelled_rows_count_nowhere_and_pending_seats_count_as_expected(
        self, client
    ):
        """Mirrors the page's arithmetic exactly: a Pending Payment seat is
        scannable at the door, so it is expected; a cancelled row is nothing
        the door cares about."""
        coach = _make_coach()
        event = _make_event()
        EventRegistration.objects.create(
            event=event, user=_make_attendee("unpaid@example.com"), status="pending"
        )
        EventRegistration.objects.create(
            event=event, user=_make_attendee("gone@example.com"), status="cancelled"
        )
        client.force_login(coach.user)

        summary = client.get(_summary_url(event)).json()

        assert summary["expected_count"] == 1
        assert summary["outstanding_count"] == 1
        assert summary["attended_count"] == 0
        assert summary["waitlist_count"] == 0

    def test_all_three_gender_buckets_are_always_present(self, client):
        """The client writes numerators AND denominators for all three tiles,
        so the endpoint may never omit a bucket — the render-time
        `{% if gender_expected.other %}` guard is what let a promotion show
        `6 / 5` (#710 finding 5)."""
        coach = _make_coach()
        event = _make_event()
        EventRegistration.objects.create(
            event=event,
            user=_make_attendee("she@example.com"),
            status="attended",
            checked_in_at=timezone.now(),
        )
        client.force_login(coach.user)

        summary = client.get(_summary_url(event)).json()

        assert summary["gender_checked_in"] == {"F": 1, "M": 0, "other": 0}
        assert summary["gender_expected"] == {"F": 1, "M": 0, "other": 0}

    def test_the_gender_split_counts_only_seat_holders_who_arrived(self, client):
        coach = _make_coach()
        event = _make_event()
        here = _make_attendee("she@example.com")
        waiting = _make_attendee("waiting@example.com")
        waiting.crushprofile.gender = "M"
        waiting.crushprofile.save(update_fields=["gender"])
        EventRegistration.objects.create(
            event=event, user=here, status="attended", checked_in_at=timezone.now()
        )
        EventRegistration.objects.create(event=event, user=waiting, status="waitlist")
        client.force_login(coach.user)

        summary = client.get(_summary_url(event)).json()

        assert summary["gender_checked_in"]["F"] == 1
        # A waitlisted walk-up is not in the room yet.
        assert summary["gender_expected"]["M"] == 0

    def test_quiz_nights_carry_the_table_fill_grid(self, client):
        from crush_lu.models.quiz import QuizEvent, QuizTableMembership

        coach = _make_coach()
        event = _make_event()
        event.event_type = "quiz_night"
        event.save(update_fields=["event_type"])
        quiz = QuizEvent.objects.create(
            event=event, status="draft", created_by=coach.user, num_tables=2
        )
        quiz.ensure_tables()
        attendee = _make_attendee()
        EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=timezone.now()
        )
        QuizTableMembership.objects.create(
            table=quiz.tables.get(table_number=2), user=attendee
        )
        client.force_login(coach.user)

        summary = client.get(_summary_url(event)).json()

        assert summary["table_fill"] == [
            {"number": 1, "count": 0},
            {"number": 2, "count": 1},
        ]

    def test_ordinary_events_send_no_table_fill(self, client):
        coach = _make_coach()
        event = _make_event()
        EventRegistration.objects.create(
            event=event, user=_make_attendee(), status="confirmed"
        )
        client.force_login(coach.user)

        summary = client.get(_summary_url(event)).json()

        assert summary["table_fill"] == []

    def test_post_is_refused(self, client):
        coach = _make_coach()
        client.force_login(coach.user)
        response = client.post(_summary_url(_make_event()))
        assert response.status_code == 405


class TestRowStatePayload:
    """Every door action now carries `row` — the input to the page's single
    row-state applier, on the local response AND the broadcast of it (#710).
    No handler may patch its own subset of the DOM any more."""

    def test_a_scan_row_reports_the_coach_this_scan_granted(self, client):
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)

        row = _scan(client, event, registration).json()["row"]

        assert row["registration_id"] == registration.pk
        assert row["status"] == "attended"
        assert row["checked_in_at"] is not None
        # The grant happens in a signal during the save; the row must report
        # it, not the pre-scan cache — this is the line finding 9 fixed.
        assert row["coach_name"] == "Cam"
        assert row["has_profile"] is True

    def test_a_scan_row_carries_no_legal_name_or_email(self, client):
        """`event_checkin_api` needs no coach session — the signed QR is the
        credential and a member may POST their own check-in URL — so its
        response must not include the search haystack. Only the coach-only
        promotion payload carries it, because only that row is rebuilt
        client-side."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)

        row = _scan(client, event, registration).json()["row"]

        assert "search" not in row

    def test_a_rescan_row_still_travels(self, client):
        """The already-attended branch is the one a remote coach's page most
        often sees (a re-scan that verifies) — it must carry the row too."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)
        _scan(client, event, registration)

        payload = _scan(client, event, registration).json()

        assert payload["already_checked_in"] is True
        assert payload["row"]["status"] == "attended"

    def test_an_attendee_with_no_profile_is_flagged(self, client):
        coach = _make_coach()
        bare = User.objects.create_user(
            username="bare@example.com", email="bare@example.com", password="pass12345"
        )
        _grant_consent(bare)
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=bare, status="confirmed"
        )
        client.force_login(coach.user)

        row = _scan(client, event, registration).json()["row"]

        assert row["has_profile"] is False
        assert row["is_approved"] is False
        assert row["coach_name"] is None

    def test_a_promotion_row_carries_what_the_confirmed_list_never_had(self, client):
        """A promoted walk-up was never in the confirmed list, so the applier
        builds their row from the payload — identity fields included (#710
        finding 3)."""
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)

        row = client.post(_promote_url(event, registration)).json()["row"]

        assert row["status"] == "attended"
        assert row["checked_in_at"] is not None
        assert row["coach_name"] == "Cam"
        assert row["gender"] == "F"
        assert row["age_display"]
        assert "ada@example.com" in row["search"]

    def test_an_undone_promotion_row_goes_back_to_the_waitlist(self, client):
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="waitlist"
        )
        client.force_login(coach.user)
        client.post(_promote_url(event, registration))

        payload = client.post(_undo_url(event, registration)).json()

        assert payload["restored_status"] == "waitlist"
        assert payload["row"]["status"] == "waitlist"
        assert payload["row"]["checked_in_at"] is None
        # The undo cleared the coach this promotion granted; the row the
        # waitlist rebuild renders from must not still name them.
        assert payload["row"]["coach_name"] is None

    def test_an_undone_scan_row_returns_to_confirmed(self, client):
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)
        _scan(client, event, registration)

        payload = client.post(_undo_url(event, registration)).json()

        assert payload["row"]["status"] == "confirmed"
        assert payload["row"]["checked_in_at"] is None
        assert payload["row"]["coach_name"] is None

    def test_a_verify_row_swaps_the_pill(self, client):
        coach = _make_coach()
        attendee = _make_attendee()
        event = _make_event()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="confirmed"
        )
        client.force_login(coach.user)

        url = reverse(
            "coach_mark_verified",
            kwargs={"event_id": event.pk, "registration_id": registration.pk},
        )
        row = client.post(url).json()["row"]

        assert row["is_approved"] is True
