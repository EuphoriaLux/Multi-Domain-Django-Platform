"""
Tests for Door Verification Reject Flow on photo mismatch (Issue #713 & #718).

When an attendee is scanned in at the entrance, their profile is auto-verified.
If the coach notices that the person presenting the ticket does not match the
profile photo, the coach can reject the verification:
- Reverts verification status to 'rejected', is_approved to False, approved_at to None, verification_method to "".
- Updates latest ProfileSubmission with status='rejected' and photo mismatch notes.
- Logs an audit warning.
- Broadcasts the update via WebSocket to keep all coach scanner screens in sync.
- Leaves attendance intact unless coach also explicitly clicks undo check-in.

Run with: pytest crush_lu/tests/test_door_verification_reject.py -v
"""

from datetime import date, timedelta
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.signing import Signer
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    PremiumMembership,
    ProfileSubmission,
    UserDataConsent,
)

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


@pytest.fixture
def event():
    return MeetupEvent.objects.create(
        title="Door Reject Test Event",
        description="Event for testing door reject flow",
        event_type="mixer",
        date_time=timezone.now() + timedelta(hours=1),
        location="Luxembourg",
        address="1 Test Street",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(minutes=30),
        is_published=True,
    )


@pytest.fixture
def coach():
    user = User.objects.create_user(
        username="scannercoach@t.test",
        email="scannercoach@t.test",
        password="pw12345678",
        first_name="Sam",
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return CrushCoach.objects.create(user=user, bio="Coach Sam", is_active=True)


def _attendee(event, name="attendee"):
    email = f"{name}@t.test"
    user = User.objects.create_user(
        username=email, email=email, password="pw12345678", first_name=name.capitalize()
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 10),
        gender="M",
        location="Luxembourg",
        is_active=True,
    )
    profile.photo_1.save(f"{name}.gif", SimpleUploadedFile(f"{name}.gif", _GIF))
    CrushProfile.objects.filter(pk=profile.pk).update(
        verification_status="pending", is_approved=False, phone_verified=True
    )
    submission = ProfileSubmission.objects.create(
        profile=profile,
        status="pending",
    )
    reg = EventRegistration.objects.create(event=event, user=user, status="confirmed")
    return profile, reg, submission


def _scan(client, reg, event, as_coach):
    client.force_login(as_coach.user)
    token = Signer().sign(f"{reg.id}:{event.id}")
    return client.post(f"/api/events/checkin/{reg.id}/{token}/")


def _reject_url(event, reg):
    return reverse(
        "coach_reject_verification",
        kwargs={"event_id": event.id, "registration_id": reg.id},
    )


class TestDoorVerificationReject:
    def test_coach_reject_verification_success(self, client, event, coach):
        profile, reg, submission = _attendee(event, "walkin_mismatch")

        # 1. Scan at the door -> auto-verified
        scan_resp = _scan(client, reg, event, coach)
        assert scan_resp.status_code == 200
        assert scan_resp.json()["auto_verified"] is True

        profile.refresh_from_db()
        submission.refresh_from_db()
        assert profile.verification_status == "verified"
        assert profile.is_approved is True
        assert profile.verification_method == "coach_event"
        assert profile.approved_at is not None
        assert submission.status == "approved"

        # 2. Coach spots photo mismatch -> rejects verification
        client.force_login(coach.user)
        reject_resp = client.post(_reject_url(event, reg))

        assert reject_resp.status_code == 200
        data = reject_resp.json()
        assert data["success"] is True
        assert data["rejected"] is True
        assert data["verification_status"] == "rejected"
        assert data["is_approved"] is False

        # 3. Verify database state
        profile.refresh_from_db()
        submission.refresh_from_db()
        reg.refresh_from_db()

        assert profile.verification_status == "rejected"
        assert profile.is_approved is False
        assert profile.approved_at is None
        assert profile.verification_method == ""

        assert submission.status == "rejected"
        assert "photo mismatch" in submission.coach_notes.lower()
        assert submission.coach == coach

        # Attendance remains intact
        assert reg.status == "attended"

    def test_coach_reject_verification_broadcasts_to_websocket(
        self, client, event, coach
    ):
        profile, reg, _ = _attendee(event, "ws_broadcast_test")
        _scan(client, reg, event, coach)

        with mock.patch("crush_lu.views_checkin._broadcast_checkin") as mock_broadcast:
            client.force_login(coach.user)
            resp = client.post(_reject_url(event, reg))
            assert resp.status_code == 200
            assert mock_broadcast.called
            event_id_arg, data_arg = mock_broadcast.call_args[0]
            assert event_id_arg == event.id
            assert data_arg["rejected"] is True
            assert data_arg["registration_id"] == reg.id
            assert data_arg["is_approved"] is False

    def test_reject_verification_permissions(self, client, event, coach):
        _, reg, _ = _attendee(event, "perm_test")

        # Anonymous user redirected to login
        client.logout()
        anon_resp = client.post(_reject_url(event, reg))
        assert anon_resp.status_code == 302
        assert "/accounts/login/" in anon_resp.url or "login" in anon_resp.url

        # Regular member (non-coach) redirected to dashboard
        regular_user = User.objects.create_user(
            username="regular@t.test", email="regular@t.test", password="pw"
        )
        client.force_login(regular_user)
        user_resp = client.post(_reject_url(event, reg))
        assert user_resp.status_code == 302
        assert "/dashboard/" in user_resp.url

    def test_reject_verification_nonexistent_registration(self, client, event, coach):
        client.force_login(coach.user)
        resp = client.post(
            reverse(
                "coach_reject_verification",
                kwargs={"event_id": event.id, "registration_id": 99999},
            )
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_reject_verification_attendee_no_profile(self, client, event, coach):
        user_no_profile = User.objects.create_user(
            username="noprofile@t.test", email="noprofile@t.test", password="pw"
        )
        reg = EventRegistration.objects.create(
            event=event, user=user_no_profile, status="attended"
        )
        client.force_login(coach.user)
        resp = client.post(_reject_url(event, reg))
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    def test_re_verify_after_rejection(self, client, event, coach):
        """A rejected attendee can still be manually verified later if identity is resolved."""
        profile, reg, _ = _attendee(event, "reverify_test")
        _scan(client, reg, event, coach)

        # Reject
        client.force_login(coach.user)
        reject_resp = client.post(_reject_url(event, reg))
        assert reject_resp.status_code == 200

        profile.refresh_from_db()
        assert profile.verification_status == "rejected"
        assert profile.is_approved is False

        # Coach manually marks verified
        verify_url = reverse(
            "coach_mark_verified",
            kwargs={"event_id": event.id, "registration_id": reg.id},
        )
        verify_resp = client.post(verify_url)
        assert verify_resp.status_code == 200

        profile.refresh_from_db()
        assert profile.verification_status == "verified"
        assert profile.is_approved is True
        assert profile.verification_method == "coach_event"

    def test_expired_submission_is_not_reopened_by_a_door_rejection(
        self, client, event, coach
    ):
        """An expired submission stays terminal through reject and re-verify.

        `latest_for_profile` reads an expired latest row as no submission at
        all — the cleanup closed out that whole coach-review story. If the
        reject endpoint flipped it to "rejected" it would be laundered back
        into a live state, and `_apply_verification`'s claim query (widened to
        include rejected rows so a coach can overturn a door decision) would
        then select and approve the very record the cleanup retired.
        """
        profile, reg, _sig = _attendee(event, "expired_reverify")
        submission = ProfileSubmission.objects.filter(profile=profile).latest(
            "submitted_at"
        )
        submission.status = "expired"
        submission.save(update_fields=["status"])
        _scan(client, reg, event, coach)

        client.force_login(coach.user)
        assert client.post(_reject_url(event, reg)).status_code == 200

        submission.refresh_from_db()
        assert submission.status == "expired", "the door reject clobbered a terminal row"
        # The audit note is still recorded on it — what happened at the door is
        # worth keeping even on a row nothing will reopen.
        assert "photo mismatch" in submission.coach_notes

        verify_resp = client.post(
            reverse(
                "coach_mark_verified",
                kwargs={"event_id": event.id, "registration_id": reg.id},
            )
        )
        assert verify_resp.status_code == 200

        # The profile verifies (the member is self-serve), but the retired
        # submission is not resurrected as an approved coach review.
        profile.refresh_from_db()
        assert profile.verification_status == "verified"
        submission.refresh_from_db()
        assert submission.status == "expired"

    def test_rejected_profile_cannot_use_existing_verified_event_ticket(
        self, client, event, coach
    ):
        """Rejecting identity revokes previously issued verified-only QRs."""
        profile, door_reg, _ = _attendee(event, "future_ticket_rejected")
        CrushProfile.objects.filter(pk=profile.pk).update(
            verification_status="verified",
            is_approved=True,
            verification_method="coach_event",
            approved_at=timezone.now(),
        )
        EventRegistration.objects.filter(pk=door_reg.pk).update(status="attended")

        verified_event = MeetupEvent.objects.create(
            title="Future verified-only event",
            description="Existing ticket must recheck verification",
            event_type="mixer",
            date_time=timezone.now() + timedelta(hours=2),
            location="Luxembourg",
            address="2 Test Street",
            max_participants=30,
            registration_deadline=timezone.now() + timedelta(hours=1),
            is_published=True,
            profile_requirement="approved",
        )
        future_reg = EventRegistration.objects.create(
            event=verified_event,
            user=profile.user,
            status="confirmed",
        )

        client.force_login(coach.user)
        assert client.post(_reject_url(event, door_reg)).status_code == 200

        token = Signer().sign(f"{future_reg.id}:{verified_event.id}")
        response = client.post(f"/api/events/checkin/{future_reg.id}/{token}/")

        assert response.status_code == 403
        assert response.json()["success"] is False
        future_reg.refresh_from_db()
        assert future_reg.status == "confirmed"

    def test_stale_full_save_cannot_resurrect_door_rejection(
        self, client, event, coach
    ):
        """Slow photo/OAuth-style saves preserve a newer rejection decision."""
        profile, reg, _ = _attendee(event, "stale_save_rejected")
        CrushProfile.objects.filter(pk=profile.pk).update(
            verification_status="verified",
            is_approved=True,
            verification_method="coach_event",
            approved_at=timezone.now(),
        )
        EventRegistration.objects.filter(pk=reg.pk).update(status="attended")
        stale_profile = CrushProfile.objects.get(pk=profile.pk)

        client.force_login(coach.user)
        assert client.post(_reject_url(event, reg)).status_code == 200

        stale_profile.location = "Esch-sur-Alzette"
        stale_profile.save()

        profile.refresh_from_db()
        assert profile.location == "Esch-sur-Alzette"
        assert profile.verification_status == "rejected"
        assert profile.is_approved is False
        assert profile.approved_at is None
        assert profile.verification_method == ""

    def test_reject_premium_member_other_coach_403(self, client, event, coach):
        """Authorization parity with coach_mark_verified (premium gate).

        A premium member's verification belongs to their assigned coach. The
        verify endpoint 403s any other coach trying to restore it, so the same
        coach must not be able to destroy it either — reject is destructive
        without a restore path for them.
        """
        profile, reg, submission = _attendee(event, "premium_member")
        CrushProfile.objects.filter(pk=profile.pk).update(
            verification_status="verified",
            is_approved=True,
            verification_method="coach_event",
            approved_at=timezone.now(),
        )

        # Active premium membership with a DIFFERENT assigned coach.
        other_coach_user = User.objects.create_user(
            username="othercoach@t.test",
            email="othercoach@t.test",
            password="pw12345678",
            first_name="Olga",
        )
        other_coach = CrushCoach.objects.create(
            user=other_coach_user, bio="Coach Olga", is_active=True
        )
        PremiumMembership.objects.create(
            user=profile.user, coach=other_coach, status="active"
        )
        CrushProfile.objects.filter(pk=profile.pk).update(assigned_coach=other_coach)

        client.force_login(coach.user)
        resp = client.post(_reject_url(event, reg))

        assert resp.status_code == 403
        data = resp.json()
        assert data["success"] is False
        assert "coach" in data["error"].lower()

        # Profile and submission are untouched.
        profile.refresh_from_db()
        submission.refresh_from_db()
        assert profile.verification_status == "verified"
        assert profile.is_approved is True
        assert profile.verification_method == "coach_event"
        assert profile.approved_at is not None
        assert submission.status == "pending"
        assert submission.coach_notes in ("", None)

    def test_reject_non_seat_holding_registration_400(self, client, event, coach):
        """Authorization parity with coach_mark_verified (seat-holding gate).

        A cancelled registration holds no seat at the door, so it can be
        neither verified nor rejected there.
        """
        profile, reg, submission = _attendee(event, "cancelled_reg")
        CrushProfile.objects.filter(pk=profile.pk).update(
            verification_status="verified",
            is_approved=True,
            verification_method="coach_event",
            approved_at=timezone.now(),
        )
        EventRegistration.objects.filter(pk=reg.pk).update(status="cancelled")

        client.force_login(coach.user)
        resp = client.post(_reject_url(event, reg))

        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False

        # Profile and submission are untouched.
        profile.refresh_from_db()
        submission.refresh_from_db()
        assert profile.verification_status == "verified"
        assert profile.is_approved is True
        assert profile.verification_method == "coach_event"
        assert submission.status == "pending"
        assert submission.coach_notes in ("", None)


@pytest.fixture
def past_event():
    """An event that ended a few hours ago — inside the post-event
    connection window (end_time + 48h by default)."""
    now = timezone.now()
    return MeetupEvent.objects.create(
        title="Ended Event",
        description="Event that has ended, connection window still open",
        event_type="mixer",
        date_time=now - timedelta(hours=5),  # default duration 120min -> ended 3h ago
        location="Luxembourg",
        address="1 Test Street",
        max_participants=30,
        registration_deadline=now - timedelta(hours=6),
        is_published=True,
    )


def _attendees_url(event):
    return f"/en/events/{event.id}/attendees/"


def _connect_url(event, user_id):
    return f"/en/events/{event.id}/connect/{user_id}/"


def _verified_attendee(event, name):
    """An attended + verified member (the post-rejection revocation baseline)."""
    profile, reg, submission = _attendee(event, name)
    EventRegistration.objects.filter(pk=reg.pk).update(status="attended")
    CrushProfile.objects.filter(pk=profile.pk).update(
        verification_status="verified",
        is_approved=True,
        verification_method="coach_event",
        approved_at=timezone.now(),
    )
    reg.refresh_from_db()
    profile.refresh_from_db()
    return profile, reg, submission


class TestRejectionRevokesConnectionAccess:
    """A door-rejected member must lose the post-event connection surfaces
    (named roster, connection requests) — attendance alone is not enough."""

    def test_verified_attendee_keeps_access(self, client, past_event):
        profile, reg, _ = _verified_attendee(past_event, "verified_member")
        _verified_attendee(past_event, "verified_peer")
        assert reg.can_make_connections is True

        client.force_login(profile.user)
        resp = client.get(_attendees_url(past_event))
        assert resp.status_code == 200

        peer = User.objects.get(email="verified_peer@t.test")
        resp = client.get(_connect_url(past_event, peer.id))
        assert resp.status_code == 200

    def test_door_rejected_member_loses_access(self, client, past_event, coach):
        profile, reg, _ = _verified_attendee(past_event, "soon_rejected")
        _verified_attendee(past_event, "other_member")
        peer = User.objects.get(email="other_member@t.test")

        # Baseline: while verified, both surfaces are reachable.
        client.force_login(profile.user)
        assert client.get(_attendees_url(past_event)).status_code == 200
        assert client.get(_connect_url(past_event, peer.id)).status_code == 200

        # Coach door-rejects the member (photo mismatch).
        client.force_login(coach.user)
        reject_resp = client.post(_reject_url(past_event, reg))
        assert reject_resp.status_code == 200
        profile.refresh_from_db()
        reg.refresh_from_db()
        assert profile.verification_status == "rejected"
        assert reg.status == "attended"  # attendance record stays honest
        assert reg.can_make_connections is False

        # Same denial shape those views use for non-attendees: redirect away
        # with an error message, no roster and no request form.
        client.force_login(profile.user)
        attendees_resp = client.get(_attendees_url(past_event))
        assert attendees_resp.status_code == 302
        assert f"/events/{past_event.id}/" in attendees_resp.url

        connect_resp = client.get(_connect_url(past_event, peer.id))
        assert connect_resp.status_code == 302
        assert f"/events/{past_event.id}/" in connect_resp.url

        connect_post = client.post(
            _connect_url(past_event, peer.id), {"note": "hello"}
        )
        assert connect_post.status_code == 302
        assert f"/events/{past_event.id}/" in connect_post.url

    def test_attended_member_without_profile_denied_not_500(self, client, past_event):
        user = User.objects.create_user(
            username="noprofile_conn@t.test",
            email="noprofile_conn@t.test",
            password="pw12345678",
            first_name="Nop",
        )
        UserDataConsent.objects.update_or_create(
            user=user, defaults={"crushlu_consent_given": True}
        )
        reg = EventRegistration.objects.create(
            event=past_event, user=user, status="attended"
        )
        _verified_attendee(past_event, "profile_peer")
        peer = User.objects.get(email="profile_peer@t.test")

        assert reg.can_make_connections is False

        client.force_login(user)
        attendees_resp = client.get(_attendees_url(past_event))
        assert attendees_resp.status_code == 302

        connect_resp = client.get(_connect_url(past_event, peer.id))
        assert connect_resp.status_code == 302
