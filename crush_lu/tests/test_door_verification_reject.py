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
