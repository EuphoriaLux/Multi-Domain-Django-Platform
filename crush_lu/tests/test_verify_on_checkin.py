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
    assert profile.is_photo_verified
    assert profile.photo_verification_key == profile.photo_1.name


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
    profile.photo_1 = "users/1/photos/uploaded-after-checkin.jpg"
    profile.save(update_fields=["photo_1"])
    assert not profile.is_photo_verified


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
    assert not profile.is_photo_verified


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
    assert profile.is_photo_verified


def test_rejected_profile_is_not_verified_by_attending(event, coach):
    profile, reg = _attendee(event, "rejected")
    CrushProfile.objects.filter(pk=profile.pk).update(verification_status="rejected")
    response = _scan(reg, event, as_coach=coach)

    assert response.json()["auto_verified"] is False
    profile.refresh_from_db()
    assert profile.verification_status == "rejected"


def test_referral_reward_and_welcome_email_fire(
    event, coach, monkeypatch, django_capture_on_commit_callbacks
):
    """The auto-verify must run the same side effects as the Verify button.

    Event verification is the primary path for new members, so skipping these
    silently stops paying referrers and stops welcoming people.

    The side effects are deferred with `transaction.on_commit` so an email is
    never sent for a rolled-back check-in — which also means the test must
    capture and run those callbacks, since pytest rolls its transaction back.
    """
    calls = {"reward": 0, "notify": 0}
    import crush_lu.referrals as referrals
    import crush_lu.notification_service as notifications

    monkeypatch.setattr(
        referrals,
        "check_and_apply_profile_approved_reward",
        lambda profile: calls.__setitem__("reward", calls["reward"] + 1),
    )
    monkeypatch.setattr(
        notifications,
        "notify_profile_approved",
        lambda **kw: calls.__setitem__("notify", calls["notify"] + 1),
    )

    _profile, reg = _attendee(event, "sideeffects")
    with django_capture_on_commit_callbacks(execute=True):
        assert _scan(reg, event, as_coach=coach).json()["auto_verified"] is True
    assert calls == {"reward": 1, "notify": 1}


def test_pending_submission_is_approved_by_the_auto_verify(event, coach):
    """Not just the profile fields — the submission must close too."""
    from crush_lu.models import ProfileSubmission

    profile, reg = _attendee(event, "submission")
    sub = ProfileSubmission.objects.create(profile=profile, status="pending")

    assert _scan(reg, event, as_coach=coach).json()["auto_verified"] is True
    sub.refresh_from_db()
    assert sub.status == "approved"
    assert sub.reviewed_at is not None
    assert sub.coach_id == coach.id


def test_rescan_by_a_coach_verifies_a_self_scanned_attendee(event, coach):
    """A first scan without a coach session must not strand them unverified."""
    profile, reg = _attendee(event, "rescan")

    first = _scan(reg, event, as_coach=None).json()
    assert first["auto_verified"] is False
    profile.refresh_from_db()
    assert profile.verification_status == "pending"

    second = _scan(reg, event, as_coach=coach).json()
    assert second["already_checked_in"] is True
    assert second["auto_verified"] is True
    profile.refresh_from_db()
    assert profile.verification_status == "verified"
    assert profile.verification_method == "coach_event"


def test_rescan_of_an_already_verified_attendee_reports_no_new_verification(
    event, coach
):
    profile, reg = _attendee(event, "rescan2")
    _scan(reg, event, as_coach=coach)

    again = _scan(reg, event, as_coach=coach).json()
    assert again["already_checked_in"] is True
    assert again["auto_verified"] is False


def test_repeated_rescans_fire_the_side_effects_only_once(
    event, coach, monkeypatch, django_capture_on_commit_callbacks
):
    """The welcome email has no double-send guard of its own.

    `check_and_apply_profile_approved_reward` self-guards, but
    `notify_profile_approved` does not — so the verification must happen exactly
    once. The `select_for_update` on the re-scan path is what serialises genuine
    concurrent scans; this pins the observable outcome.
    """
    calls = {"notify": 0}
    import crush_lu.notification_service as notifications

    monkeypatch.setattr(
        notifications,
        "notify_profile_approved",
        lambda **kw: calls.__setitem__("notify", calls["notify"] + 1),
    )

    profile, reg = _attendee(event, "doublescan")
    # First scan has no coach session, so it only checks in.
    _scan(reg, event, as_coach=None)

    with django_capture_on_commit_callbacks(execute=True):
        first = _scan(reg, event, as_coach=coach).json()
    with django_capture_on_commit_callbacks(execute=True):
        second = _scan(reg, event, as_coach=coach).json()

    assert first["auto_verified"] is True
    assert second["auto_verified"] is False
    assert calls["notify"] == 1
    profile.refresh_from_db()
    assert profile.verification_status == "verified"


def test_rescan_outside_the_checkin_window_does_not_verify(event, coach):
    """Tickets do not expire — a months-old QR must not grant verification."""
    profile, reg = _attendee(event, "oldticket")
    reg.status = "attended"
    reg.save(update_fields=["status"])
    # Move the event far outside the +/-12h window.
    MeetupEvent.objects.filter(pk=event.pk).update(
        date_time=timezone.now() - timedelta(days=60)
    )

    payload = _scan(reg, event, as_coach=coach).json()

    assert payload["already_checked_in"] is True
    assert payload["auto_verified"] is False
    profile.refresh_from_db()
    assert profile.verification_status == "pending"


def test_a_concurrent_verification_wins_only_once(event, coach):
    """The pending -> verified claim is conditional, so one path wins.

    Simulates the LuxID callback landing first: the row is already `verified`
    when the scan's claim UPDATE runs, so it matches nothing and the scan must
    not overwrite the method or re-run the side effects.
    """
    profile, reg = _attendee(event, "concurrent")
    CrushProfile.objects.filter(pk=profile.pk).update(
        verification_status="verified", is_approved=True, verification_method="luxid"
    )

    payload = _scan(reg, event, as_coach=coach).json()

    assert payload["auto_verified"] is False
    profile.refresh_from_db()
    assert profile.verification_method == "luxid"


def test_manual_verify_still_works_for_an_incomplete_profile(event, coach, client):
    """The Verify button must keep working for non-`pending` profiles.

    The scanner offers it for every unapproved profile, and the endpoint has
    always guarded only on "already verified". Narrowing the shared claim to
    `pending` broke this and — worse — reported `already_verified`, which makes
    the client drop the button while the row stays unapproved.
    """
    profile, reg = _attendee(event, "incompleteprof")
    CrushProfile.objects.filter(pk=profile.pk).update(
        verification_status="incomplete", is_approved=False
    )

    client.force_login(coach.user)
    response = client.post(f"/api/events/{event.id}/verify/{reg.id}/")

    assert response.status_code == 200
    assert response.json().get("already_verified") is not True
    profile.refresh_from_db()
    assert profile.verification_status == "verified"
    assert profile.is_approved is True


def test_luxid_does_not_overwrite_a_scan_that_verified_first(event, coach):
    """LuxID must respect the same pending -> verified claim.

    Otherwise a callback that read `pending` resumes after a door scan has
    committed and overwrites `verification_method`, and both paths emit the
    approval notification and referral work.
    """
    from crush_lu.signals import _execute_luxid_direct_verify

    profile, reg = _attendee(event, "luxidrace")
    # The door scan wins first.
    assert _scan(reg, event, as_coach=coach).json()["auto_verified"] is True

    # LuxID resumes holding its stale `pending` instance.
    stale = CrushProfile.objects.get(pk=profile.pk)
    stale.verification_status = "pending"
    _execute_luxid_direct_verify(stale.user, stale, None, None)

    profile.refresh_from_db()
    assert profile.verification_method == "coach_event"


# ---------------------------------------------------------------------------
# Coach assignment: the scanner, not the coach-list order, decides who you get
# ---------------------------------------------------------------------------


def _second_coach():
    """A later-created (higher-pk) coach, so `event.coaches.first()` is NOT it."""
    user = User.objects.create_user(
        username="secondcoach@t.test", email="secondcoach@t.test", password="pw12345678"
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    return CrushCoach.objects.create(user=user, bio="s", is_active=True)


def test_scanning_coach_becomes_the_assigned_coach(event, coach):
    """The member's permanent coach is whoever actually met them at the door.

    `assign_coach_on_first_attendance` used to take `event.coaches.first()`,
    linking the member to whichever coach happened to sort first — not the one
    who checked them in.
    """
    scanner = _second_coach()
    event.coaches.add(coach, scanner)
    profile, reg = _attendee(event, "assignscan")

    response = _scan(reg, event, as_coach=scanner)

    assert response.status_code == 200
    profile.refresh_from_db()
    assert event.coaches.first() == coach  # the scanner is NOT first in the list
    assert profile.assigned_coach_id == scanner.id
    assert profile.assigned_coach_at is not None


def test_anonymous_scan_still_assigns_the_events_first_coach(event, coach):
    """No coach session on the request -> the legacy fallback stands."""
    scanner = _second_coach()
    event.coaches.add(coach, scanner)
    profile, reg = _attendee(event, "assignanon")

    _scan(reg, event, as_coach=None)

    profile.refresh_from_db()
    assert profile.assigned_coach_id == coach.id


def test_existing_coach_is_never_reassigned_by_a_scan(event, coach):
    scanner = _second_coach()
    event.coaches.add(coach, scanner)
    profile, reg = _attendee(event, "assignkeep")
    CrushProfile.objects.filter(pk=profile.pk).update(assigned_coach=coach)

    _scan(reg, event, as_coach=scanner)

    profile.refresh_from_db()
    assert profile.assigned_coach_id == coach.id
