"""The quiz host panel's "mark attended" leaves the same trace as a door scan.

It used to flip the row to ``attended`` with no coach provenance: no coach
grant from the host, no photo attestation, no prior status. A verified member
checked in that way was never counted as "seen at an event" (Crush ID level 3),
and a member without a coach was credited to the event's first coach instead of
the coach who checked them in.

Run with: pytest crush_lu/tests/test_checkin_coach_provenance.py -v
"""

import json
from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    QuizEvent,
    UserDataConsent,
)

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def _user(name):
    email = f"{name}@t.test"
    user = User.objects.create_user(username=email, email=email, password="pw12345678")
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return user


def _coach(name):
    return CrushCoach.objects.create(user=_user(name), bio="c", is_active=True)


@pytest.fixture
def quiz():
    event = MeetupEvent.objects.create(
        title="Mixer with numbers",
        description="x",
        event_type="quiz_night",
        date_time=timezone.now() + timedelta(hours=1),
        location="Luxembourg",
        address="1 Test Street",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(minutes=30),
        is_published=True,
    )
    return QuizEvent.objects.create(event=event, created_by=_user("creator"))


def _member(event, name, *, status="verified", with_photo=True):
    user = _user(name)
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
        verification_status=status,
        is_approved=status == "verified",
        verification_method="legacy" if status == "verified" else "",
        phone_verified=True,
    )
    reg = EventRegistration.objects.create(event=event, user=user, status="confirmed")
    return profile, reg


def _mark(quiz, reg, as_user):
    client = Client()
    client.force_login(as_user)
    return client.post(
        f"/api/quiz/{quiz.id}/mark-attended/",
        data=json.dumps({"registration_id": reg.id}),
        content_type="application/json",
    )


def test_host_coach_check_in_counts_as_seen_at_event(quiz):
    """The Claudine case: verified member whose own coach is not at the event."""
    own_coach, host = _coach("owncoach"), _coach("host")
    quiz.event.coaches.add(host)
    profile, reg = _member(quiz.event, "verifiedmember")
    CrushProfile.objects.filter(pk=profile.pk).update(assigned_coach=own_coach)

    assert _mark(quiz, reg, host.user).status_code == 200

    reg.refresh_from_db()
    profile.refresh_from_db()
    assert reg.status == "attended"
    assert reg.checkin_prior_status == "confirmed"
    assert reg.checkin_attested_photo_key == profile.photo_1.name
    assert profile.is_photo_verified is True
    assert profile.has_attended_event is True
    assert profile.assigned_coach_id == own_coach.id


def test_host_coach_becomes_coach_of_member_without_one(quiz):
    first, host = _coach("firstcoach"), _coach("host2")
    quiz.event.coaches.add(first, host)
    profile, reg = _member(quiz.event, "nocoach", with_photo=False)

    assert _mark(quiz, reg, host.user).status_code == 200

    reg.refresh_from_db()
    profile.refresh_from_db()
    assert profile.assigned_coach_id == host.id
    assert reg.checkin_granted_coach_id == host.id
    assert profile.has_attended_event is True


def test_pending_walk_in_is_verified_like_a_door_scan(quiz):
    host = _coach("host3")
    quiz.event.coaches.add(host)
    profile, reg = _member(quiz.event, "pendingwalkin", status="pending")

    assert _mark(quiz, reg, host.user).status_code == 200

    profile.refresh_from_db()
    reg.refresh_from_db()
    assert profile.verification_status == "verified"
    assert profile.verification_method == "coach_event"
    assert reg.checkin_auto_verified is True


def test_non_coach_quiz_creator_attests_nothing(quiz):
    """The quiz creator may mark attendance, but without a coach session nobody
    looked at the member, so no coach grant and no photo attestation."""
    profile, reg = _member(quiz.event, "creatormarked")

    assert _mark(quiz, reg, quiz.created_by).status_code == 200

    reg.refresh_from_db()
    profile.refresh_from_db()
    assert reg.status == "attended"
    assert reg.checkin_granted_coach_id is None
    assert reg.checkin_attested_photo_key == ""
    assert profile.has_attended_event is False


# ---------------------------------------------------------------------------
# checkin_scanned_by_coach: the plain record of which coach checked a member in
# ---------------------------------------------------------------------------


def _token(reg):
    from django.core.signing import Signer

    return Signer().sign(f"{reg.id}:{reg.event_id}")


def _door_scan(reg, as_coach):
    client = Client()
    client.force_login(as_coach.user)
    return client.post(f"/api/events/checkin/{reg.id}/{_token(reg)}/")


@pytest.fixture
def door_event():
    return MeetupEvent.objects.create(
        title="Mixer",
        description="x",
        event_type="mixer",
        date_time=timezone.now() + timedelta(hours=1),
        location="Luxembourg",
        address="1 Test Street",
        max_participants=30,
        registration_deadline=timezone.now() + timedelta(minutes=30),
        is_published=True,
    )


def test_door_scan_of_photoless_member_with_absent_coach_counts(door_event):
    """The Claudine case: verified, no photo, own coach not at the event. The
    scan grants nothing and attests nothing, but records the scanning coach."""
    own_coach, scanner = _coach("own"), _coach("scanner")
    door_event.coaches.add(scanner)
    profile, reg = _member(door_event, "photoless", with_photo=False)
    CrushProfile.objects.filter(pk=profile.pk).update(assigned_coach=own_coach)

    assert _door_scan(reg, scanner).status_code == 200

    reg.refresh_from_db()
    profile.refresh_from_db()
    assert reg.checkin_granted_coach_id is None
    assert reg.checkin_attested_photo_key == ""
    assert reg.checkin_scanned_by_coach_id == scanner.id
    assert profile.has_attended_event is True

    from crush_lu.services.crush_connect import filter_connect_identity_verified

    assert filter_connect_identity_verified(
        User.objects.filter(pk=profile.user_id)
    ).exists()


def test_member_posting_their_own_check_in_is_refused(door_event):
    profile, reg = _member(door_event, "selfpost", with_photo=False)

    member_client = Client()
    member_client.force_login(profile.user)
    resp = member_client.post(f"/api/events/checkin/{reg.id}/{_token(reg)}/")

    assert resp.status_code == 403
    reg.refresh_from_db()
    assert reg.status == "confirmed"
    assert reg.checkin_scanned_by_coach_id is None


def test_coach_does_not_vouch_for_their_own_member_account(door_event):
    me = _coach("coachmember")
    door_event.coaches.add(me)
    profile = CrushProfile.objects.create(
        user=me.user,
        date_of_birth=date(1990, 1, 1),
        gender="F",
        location="Luxembourg",
        is_active=True,
    )
    CrushProfile.objects.filter(pk=profile.pk).update(
        verification_status="verified",
        is_approved=True,
        verification_method="legacy",
        assigned_coach=me,
    )
    reg = EventRegistration.objects.create(
        event=door_event, user=me.user, status="confirmed"
    )

    assert _door_scan(reg, me).status_code == 200

    reg.refresh_from_db()
    assert reg.checkin_scanned_by_coach_id is None


def test_coach_rescan_fills_a_missing_scanning_coach(door_event):
    own_coach, scanner = _coach("own3"), _coach("scanner3")
    door_event.coaches.add(scanner)
    profile, reg = _member(door_event, "oldrow", with_photo=False)
    CrushProfile.objects.filter(pk=profile.pk).update(assigned_coach=own_coach)
    EventRegistration.objects.filter(pk=reg.pk).update(
        status="attended", checked_in_at=timezone.now()
    )
    reg.refresh_from_db()

    assert _door_scan(reg, scanner).status_code == 200

    reg.refresh_from_db()
    profile.refresh_from_db()
    assert reg.checkin_scanned_by_coach_id == scanner.id
    assert profile.has_attended_event is True


def test_undo_clears_scanning_coach(door_event):
    from django.urls import reverse

    own_coach, scanner = _coach("own4"), _coach("scanner4")
    door_event.coaches.add(scanner)
    profile, reg = _member(door_event, "undone", with_photo=False)
    CrushProfile.objects.filter(pk=profile.pk).update(assigned_coach=own_coach)
    assert _door_scan(reg, scanner).status_code == 200

    client = Client()
    client.force_login(scanner.user)
    resp = client.post(
        reverse(
            "coach_undo_checkin",
            kwargs={"event_id": door_event.pk, "registration_id": reg.pk},
        )
    )
    assert resp.status_code in (200, 302), resp.content

    reg.refresh_from_db()
    profile.refresh_from_db()
    assert reg.status == "confirmed"
    assert reg.checkin_scanned_by_coach_id is None
    assert profile.has_attended_event is False


def test_host_mark_outside_checkin_window_records_no_coach_evidence(quiz):
    """A host fixing attendance long after the evening is not a coach seeing
    the member: attendance is recorded, verification and evidence are not."""
    host = _coach("latehost")
    quiz.event.coaches.add(host)
    MeetupEvent.objects.filter(pk=quiz.event_id).update(
        date_time=timezone.now() - timedelta(days=3)
    )
    profile, reg = _member(quiz.event, "latepending", status="pending")

    assert _mark(quiz, reg, host.user).status_code == 200

    reg.refresh_from_db()
    profile.refresh_from_db()
    assert reg.status == "attended"
    assert reg.checkin_scanned_by_coach_id is None
    assert reg.checkin_granted_coach_id is None
    assert reg.checkin_attested_photo_key == ""
    assert profile.verification_status == "pending"
