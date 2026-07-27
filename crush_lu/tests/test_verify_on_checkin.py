"""Attending the event verifies the profile — with two deliberate exceptions.

Scanning an attendee in at the door now sets `verified` / `coach_event` for the
ordinary walk-in, so a coach does not tap a second button. Held back for:

* **premium members** — "only their own coach verifies" is intentional there;
* **profiles with no photo** — since the fast-track change a member can complete
  their profile without one, so a scan cannot confirm an identity against
  anything.

And the auto-verify only fires for a request carrying an **active coach's
session**: `event_checkin_api` authenticates on the signed token alone, and the
attendee holds their own QR, so without that check a member could POST their own
check-in URL and self-verify.

Run with: pytest crush_lu/tests/test_verify_on_checkin.py -v
"""

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.signing import Signer
from django.test import Client
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    PremiumMembership,
    UserDataConsent,
)

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

# A 1x1 GIF — enough for `photo_1` to be truthy.
_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


@pytest.fixture
def event():
    ev = MeetupEvent.objects.create(
        title="Door event",
        description="x",
        event_type="mixer",
        # Inside the +/-12h check-in window.
        date_time=timezone.now() + timedelta(hours=1),
        location="Luxembourg",
        address="1 Test Street",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(minutes=30),
        is_published=True,
    )
    return ev


@pytest.fixture
def coach():
    user = User.objects.create_user(
        username="doorcoach@t.test", email="doorcoach@t.test", password="pw12345678"
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return CrushCoach.objects.create(user=user, bio="c", is_active=True)


def _premium_coach():
    """PremiumMembership requires a coach FK, so give it its own."""
    user = User.objects.create_user(
        username="premcoach@t.test", email="premcoach@t.test", password="pw12345678"
    )
    return CrushCoach.objects.create(user=user, bio="p", is_active=True)


def _attendee(event, name, with_photo=True, premium=False):
    email = f"{name}@t.test"
    user = User.objects.create_user(username=email, email=email, password="pw12345678")
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1994, 3, 2),
        gender="F",
        location="Luxembourg",
        is_active=True,
    )
    if with_photo:
        profile.photo_1.save(f"{name}.gif", SimpleUploadedFile(f"{name}.gif", _GIF))
    CrushProfile.objects.filter(pk=profile.pk).update(
        verification_status="pending", is_approved=False, phone_verified=True
    )
    if premium:
        PremiumMembership.objects.create(
            user=user, coach=_premium_coach(), status="active"
        )
    reg = EventRegistration.objects.create(event=event, user=user, status="confirmed")
    return profile, reg


def _scan(reg, event, as_coach=None):
    """POST the signed check-in URL, optionally with a coach's session."""
    client = Client()
    if as_coach is not None:
        client.force_login(as_coach.user)
    token = Signer().sign(f"{reg.id}:{event.id}")
    return client.post(f"/api/events/checkin/{reg.id}/{token}/")


def test_scan_by_a_coach_verifies_an_ordinary_attendee(event, coach):
    profile, reg = _attendee(event, "walkin")
    response = _scan(reg, event, as_coach=coach)

    assert response.status_code == 200
    assert response.json()["auto_verified"] is True
    reg.refresh_from_db()
    profile.refresh_from_db()
    assert reg.status == "attended"
    assert profile.verification_status == "verified"
    assert profile.is_approved is True
    assert profile.verification_method == "coach_event"


def test_toast_reports_the_attendee_as_verified(event, coach):
    """The scanner must not warn "Unverified Profile" about someone it just verified."""
    _profile, reg = _attendee(event, "toast")
    payload = _scan(reg, event, as_coach=coach).json()
    assert payload["profile"]["is_approved"] is True


def test_premium_member_is_left_to_their_own_coach(event, coach):
    profile, reg = _attendee(event, "premium", premium=True)
    response = _scan(reg, event, as_coach=coach)

    assert response.status_code == 200
    assert response.json()["auto_verified"] is False
    reg.refresh_from_db()
    profile.refresh_from_db()
    assert reg.status == "attended"  # still checked in
    assert profile.verification_status == "pending"  # but not verified


def test_profile_without_a_photo_is_not_auto_verified(event, coach):
    """Nothing on screen to compare the person against, so nobody checked."""
    profile, reg = _attendee(event, "nophoto", with_photo=False)
    response = _scan(reg, event, as_coach=coach)

    assert response.status_code == 200
    assert response.json()["auto_verified"] is False
    reg.refresh_from_db()
    profile.refresh_from_db()
    assert reg.status == "attended"
    assert profile.verification_status == "pending"


def test_self_scan_without_a_coach_session_checks_in_but_does_not_verify(event):
    """The attendee holds their own QR — it must not be a self-verify."""
    profile, reg = _attendee(event, "selfscan")
    response = _scan(reg, event, as_coach=None)

    assert response.status_code == 200
    assert response.json()["auto_verified"] is False
    reg.refresh_from_db()
    profile.refresh_from_db()
    assert reg.status == "attended"  # unchanged behaviour
    assert profile.verification_status == "pending"
    assert profile.is_approved is False


def test_inactive_coach_session_does_not_verify(event, coach):
    profile, reg = _attendee(event, "inactive")
    CrushCoach.objects.filter(pk=coach.pk).update(is_active=False)
    response = _scan(reg, event, as_coach=coach)

    assert response.json()["auto_verified"] is False
    profile.refresh_from_db()
    assert profile.verification_status == "pending"


def test_already_verified_profile_is_untouched(event, coach):
    profile, reg = _attendee(event, "already")
    CrushProfile.objects.filter(pk=profile.pk).update(
        verification_status="verified", is_approved=True, verification_method="luxid"
    )
    _scan(reg, event, as_coach=coach)

    profile.refresh_from_db()
    # LuxID verification must not be overwritten with coach_event.
    assert profile.verification_method == "luxid"


def test_rejected_profile_is_not_verified_by_attending(event, coach):
    profile, reg = _attendee(event, "rejected")
    CrushProfile.objects.filter(pk=profile.pk).update(verification_status="rejected")
    response = _scan(reg, event, as_coach=coach)

    assert response.json()["auto_verified"] is False
    profile.refresh_from_db()
    assert profile.verification_status == "rejected"
