"""`profile_requirement` registration gates — the full option x state matrix.

Spec: docs/superpowers/specs/2026-07-27-profile-requirement-audit.md

These defects survived because nothing pinned *who* each option admits — the
labels described intent and the gates implemented something different, in both
directions:

- ``profile_exists`` ("Profile must exist") rejected approved profiles, making
  it a duplicate of ``unverified`` and silently locking out every verified
  member. No option expressed "any profile" at all.
- ``unverified`` / ``profile_exists`` gated on ``not is_approved``, and a
  *rejected* profile also has ``is_approved=False`` — so both admitted rejected
  members. ``completed`` already guarded against exactly this.
- ``coach_assigned`` never looked at verification state, and nothing clears
  ``assigned_coach`` on rejection, so a rejected member who kept a coach still
  registered.

The matrix below is the regression guard. Run with:
    pytest crush_lu/tests/test_profile_requirement_gates.py -v
"""

from datetime import date, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
    UserDataConsent,
)

User = get_user_model()

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

# state id -> (verification_status, is_approved, phone_verified, has_coach)
STATES = {
    "S1_incomplete": ("incomplete", False, False, False),
    "S2_pending_nophone": ("pending", False, False, False),
    "S3_pending_phone": ("pending", False, True, False),
    "S4_verified": ("verified", True, True, False),
    "S6_verified_coach": ("verified", True, True, True),
    "S7_rejected": ("rejected", False, True, False),
    "S7_rejected_coach": ("rejected", False, True, True),
}

# option -> set of state ids that MUST be able to register.
EXPECTED = {
    "completed": {"S3_pending_phone", "S4_verified", "S6_verified_coach"},
    "approved": {"S4_verified", "S6_verified_coach"},
    "coach_assigned": {"S6_verified_coach"},
    "unverified": {"S1_incomplete", "S2_pending_nophone", "S3_pending_phone"},
    "profile_exists": {
        "S1_incomplete",
        "S2_pending_nophone",
        "S3_pending_phone",
        "S4_verified",
        "S6_verified_coach",
    },
    "none": set(STATES),  # every state with a profile; S0 covered separately
}


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    """`event_register` is `@ratelimit(key="user", rate="5/h")`.

    Each test rolls the DB back, so user pks restart at 1 and the cached
    per-user counter collides across tests — the matrix would start seeing 429s
    partway through and report them as rejections. Same `cache.clear()`
    convention the existing event tests use.
    """
    cache.clear()
    yield
    cache.clear()


def _event(requirement):
    return MeetupEvent.objects.create(
        title=f"Gate event {requirement}",
        description="x",
        event_type="mixer",
        date_time=timezone.now() + timedelta(days=3),
        location="Luxembourg",
        address="1 Test Street",
        max_participants=50,
        registration_deadline=timezone.now() + timedelta(days=2),
        is_published=True,
        profile_requirement=requirement,
        # Deliberately unrestricted: the age and language gates run AFTER
        # profile_requirement and also reject a missing profile, which would
        # mask what this matrix is measuring.
        min_age=18,
        max_age=99,
    )


def _user(name, state=None):
    user = User.objects.create_user(
        username=f"{name}@gates.test", email=f"{name}@gates.test", password="pw12345678"
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    if state is None:
        return user  # S0 — no CrushProfile at all
    status, approved, phone, has_coach = STATES[state]
    coach = None
    if has_coach:
        cu = User.objects.create_user(
            username=f"coach-{name}@gates.test",
            email=f"coach-{name}@gates.test",
            password="pw12345678",
        )
        coach = CrushCoach.objects.create(user=cu, bio="c", is_active=True)
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="F",
        location="Luxembourg",
        is_active=True,
        assigned_coach=coach,
    )
    # Set the verification fields directly: CrushProfile.save() syncs
    # is_approved -> verification_status, which would overwrite "rejected".
    CrushProfile.objects.filter(pk=profile.pk).update(
        verification_status=status, is_approved=approved, phone_verified=phone
    )
    return user


def _try_register(user, event):
    """True when registration succeeded (a row exists afterwards)."""
    client = Client()
    client.force_login(user)
    client.post(
        reverse("crush_lu:event_register", args=[event.id]),
        # `age_confirmation` is required only for a profile-less user on an
        # unrestricted event; `gender` only when the event uses per-gender caps.
        # Both are harmless when not required, and sending them keeps the helper
        # measuring the profile_requirement gate rather than form validation.
        {"age_confirmation": "on", "gender": "F"},
        follow=True,
    )
    return EventRegistration.objects.filter(event=event, user=user).exists()


@pytest.mark.parametrize("requirement", sorted(EXPECTED))
@pytest.mark.parametrize("state", sorted(STATES))
def test_gate_matrix(requirement, state):
    """Each option admits exactly the states the audit says it should."""
    event = _event(requirement)
    user = _user(f"{requirement}-{state}".lower(), state)
    allowed = _try_register(user, event)
    assert allowed is (state in EXPECTED[requirement]), (
        f"{requirement} x {state}: expected "
        f"{'ADMIT' if state in EXPECTED[requirement] else 'REJECT'}, got "
        f"{'ADMIT' if allowed else 'REJECT'}"
    )


@pytest.mark.parametrize("requirement", sorted(EXPECTED))
def test_no_profile_only_admitted_by_none(requirement):
    """S0 (account, no CrushProfile) registers only under `none`."""
    event = _event(requirement)
    user = _user(f"{requirement}-s0".lower(), None)
    assert _try_register(user, event) is (requirement == "none")


def test_profile_exists_admits_verified_members():
    """The headline regression: "Profile must exist" must not exclude them."""
    event = _event("profile_exists")
    user = _user("pe-verified", "S4_verified")
    assert _try_register(user, event), (
        "profile_exists rejected a verified member — it is a duplicate of "
        "`unverified` again, and its label promises the opposite"
    )


@pytest.mark.parametrize(
    "requirement", ["unverified", "profile_exists", "coach_assigned"]
)
def test_rejected_profiles_never_admitted(requirement):
    """All three once admitted rejected profiles, by two different routes."""
    state = "S7_rejected_coach" if requirement == "coach_assigned" else "S7_rejected"
    event = _event(requirement)
    user = _user(f"rej-{requirement}", state)
    assert not _try_register(user, event)


def test_completed_admits_verified_without_phone_verified():
    """`completed` applies the phone check only to `pending` profiles."""
    event = _event("completed")
    user = User.objects.create_user(
        username="cv@gates.test", email="cv@gates.test", password="pw12345678"
    )
    UserDataConsent.objects.update_or_create(
        user=user, defaults={"crushlu_consent_given": True}
    )
    profile = CrushProfile.objects.create(
        user=user,
        date_of_birth=date(1995, 5, 15),
        gender="F",
        location="Luxembourg",
        is_active=True,
    )
    CrushProfile.objects.filter(pk=profile.pk).update(
        verification_status="verified", is_approved=True, phone_verified=False
    )
    assert _try_register(user, event)
