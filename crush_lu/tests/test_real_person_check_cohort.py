"""Cohort of the Connect real-person check: catalogue members no coach has seen.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

from datetime import timedelta
from io import StringIO

import pytest
from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from crush_lu.models import CrushCoach, CrushProfile, EventRegistration, MeetupEvent
from crush_lu.services.crush_connect import (
    filter_catalogue_eligible,
    filter_connect_identity_verified,
)
from crush_lu.services.real_person_check import real_person_check_cohort
from crush_lu.tests.test_crush_connect import _make_user

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def _member(username, **kwargs):
    kwargs.setdefault("premium", False)
    return _make_user(username=username, **kwargs)


def _in_cohort(user):
    return real_person_check_cohort(User.objects.filter(pk=user.pk)).exists()


def _past_event(title="Past mixer"):
    return MeetupEvent.objects.create(
        title=title,
        description="x",
        event_type="mixer",
        date_time=timezone.now() - timedelta(days=10),
        location="Luxembourg",
        address="1 Test Street",
        max_participants=30,
        registration_deadline=timezone.now() - timedelta(days=11),
        is_published=True,
    )


def _coach(name):
    user = User.objects.create_user(
        username=f"{name}@t.test", email=f"{name}@t.test", password="pw12345678"
    )
    return CrushCoach.objects.create(user=user, bio="c", is_active=True)


def _attend(user, event, **fields):
    reg = EventRegistration.objects.create(user=user, event=event, status="confirmed")
    EventRegistration.objects.filter(pk=reg.pk).update(status="attended", **fields)
    return reg


def test_luxid_only_catalogue_member_is_in_the_cohort():
    user = _member("rpc_luxid")
    assert filter_catalogue_eligible(User.objects.filter(pk=user.pk)).exists()
    assert _in_cohort(user)


def test_member_outside_the_login_window_stays_in_the_cohort():
    user = _member("rpc_dormant", last_login_days_ago=400)
    # The catalogue itself still drops them by default...
    assert not filter_catalogue_eligible(User.objects.filter(pk=user.pk)).exists()
    assert filter_catalogue_eligible(
        User.objects.filter(pk=user.pk), require_recent_login=False
    ).exists()
    # ...but the coach queue keeps them.
    assert _in_cohort(user)


@pytest.mark.parametrize(
    "overrides",
    [
        {"onboarded": False},
        {"excluded_by_coach": True},
        {"photo_share_consent": False},
        {"has_luxid": False},
        {"is_approved": False},
    ],
)
def test_members_outside_the_catalogue_are_not_in_the_cohort(overrides):
    user = _member("rpc_outside", **overrides)
    assert not _in_cohort(user)


@pytest.mark.parametrize("method", ["coach_event", "premium_coach"])
def test_coach_attested_verification_method_leaves_the_cohort(method):
    user = _member(f"rpc_{method}")
    CrushProfile.objects.filter(user=user).update(verification_method=method)
    assert not _in_cohort(user)


def test_door_grant_leaves_the_cohort():
    user = _member("rpc_grant")
    _attend(user, _past_event(), checkin_granted_coach=_coach("grantcoach"))
    assert not _in_cohort(user)


def test_attested_door_photo_leaves_the_cohort():
    user = _member("rpc_photo")
    _attend(user, _past_event(), checkin_attested_photo_key="users/1/photos/a.jpg")
    assert not _in_cohort(user)


def test_event_run_by_the_assigned_coach_leaves_the_cohort():
    user = _member("rpc_assigned")
    coach = _coach("assignedcoach")
    CrushProfile.objects.filter(user=user).update(assigned_coach=coach)
    event = _past_event()
    event.coaches.add(coach)
    _attend(user, event)
    assert not _in_cohort(user)


def test_self_scanned_attendance_alone_stays_in_the_cohort():
    """A bare attended row attests nothing, so a coach should still call."""
    user = _member("rpc_selfscan")
    _attend(user, _past_event())
    assert _in_cohort(user)


def test_luxid_through_the_generic_oidc_app_is_in_the_cohort():
    user = _member("rpc_oidc", has_luxid=False)
    app = SocialApp.objects.create(
        provider="openid_connect",
        provider_id="luxid",
        name="LuxID",
        client_id="luxid-test",
    )
    account = SocialAccount.objects.create(
        user=user, provider="openid_connect", uid="rpc_oidc"
    )
    SocialToken.objects.create(app=app, account=account, token="t")
    assert _in_cohort(user)


@pytest.mark.parametrize(
    "arm",
    ["none", "self_scan", "grant", "attested_photo", "assigned_coach"],
)
def test_attendance_arms_agree_with_has_attended_event(arm):
    """The shared Q, the identity filter and the profile property agree.

    Limited to verified, non-coach-method profiles: the photo-review display
    copy of the rule has no method arm.
    """
    user = _member(f"rpc_parity_{arm}", has_luxid=False)
    event = _past_event()
    if arm == "self_scan":
        _attend(user, event)
    elif arm == "grant":
        _attend(user, event, checkin_granted_coach=_coach("paritygrant"))
    elif arm == "attested_photo":
        _attend(user, event, checkin_attested_photo_key="users/1/photos/p.jpg")
    elif arm == "assigned_coach":
        coach = _coach("parityassigned")
        CrushProfile.objects.filter(user=user).update(assigned_coach=coach)
        event.coaches.add(coach)
        _attend(user, event)

    profile = CrushProfile.objects.get(user=user)
    seen = arm in ("grant", "attested_photo", "assigned_coach")
    assert profile.has_attended_event is seen
    # Without LuxID, the identity gate is exactly the coach-attested arms.
    assert (
        filter_connect_identity_verified(User.objects.filter(pk=user.pk)).exists()
        is seen
    )


def test_cohort_command_prints_counts_only():
    user = _member("rpc_command")
    out = StringIO()
    call_command("real_person_check_cohort", stdout=out)
    output = out.getvalue()
    assert "cohort.total 1" in output
    assert "cohort.last_login_within_30d 1" in output
    assert user.username not in output
    assert user.email not in output
