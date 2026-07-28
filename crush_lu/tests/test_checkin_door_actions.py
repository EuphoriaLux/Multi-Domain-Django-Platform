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
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
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
        coach, and nothing else can take it back."""
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

        response = client.post(_undo_url(event, registration))

        assert response.json()["coach_cleared"] is True
        profile.refresh_from_db()
        assert profile.assigned_coach_id is None
        assert profile.assigned_coach_at is None

    def test_undo_keeps_a_coach_the_member_already_had(self, client):
        """A member who arrived with a coach must keep it — no other flow can
        restore an assignment this endpoint wrongly stripped."""
        scanning_coach = _make_coach()
        earlier_coach = _make_coach(username="earlier@example.com", first_name="Robin")
        attendee = _make_attendee()
        event = _make_event()
        checked_in_at = timezone.now()
        registration = EventRegistration.objects.create(
            event=event, user=attendee, status="attended", checked_in_at=checked_in_at
        )
        profile = attendee.crushprofile
        profile.assigned_coach = earlier_coach
        profile.assigned_coach_at = checked_in_at - timedelta(days=30)
        profile.save(update_fields=["assigned_coach", "assigned_coach_at"])
        client.force_login(scanning_coach.user)

        response = client.post(_undo_url(event, registration))

        assert response.json()["coach_cleared"] is False
        profile.refresh_from_db()
        assert profile.assigned_coach_id == earlier_coach.pk

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
