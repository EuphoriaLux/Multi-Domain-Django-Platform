"""Coach/member pair safety in the current catalogue and coach-pick flow."""

import pytest

from crush_lu.models import CrushCoach, PremiumMembership
from crush_lu.services.crush_connect import (
    get_eligible_pool,
    is_assigned_coach_pair,
)
from crush_lu.tests.test_crush_connect import _make_user
from crush_lu.views_crush_connect import _connect_done_url


def _make_coach_member(username, **kwargs):
    user = _make_user(username=username, premium=False, **kwargs)
    coach = CrushCoach.objects.create(
        user=user,
        bio="Test coach",
        specializations="General",
        phone_number="+352123456",
        is_active=True,
    )
    user.refresh_from_db()
    return user, coach


def _assign_coach(member, coach):
    member.crushprofile.assigned_coach = coach
    member.crushprofile.save(update_fields=["assigned_coach"])
    member.refresh_from_db()
    return member


@pytest.mark.django_db
def test_assigned_coach_pair_detected_in_both_directions():
    coach_user, coach = _make_coach_member("coach")
    member = _assign_coach(_make_user(username="member"), coach)

    assert is_assigned_coach_pair(member, coach_user)
    assert is_assigned_coach_pair(coach_user, member)


@pytest.mark.django_db
def test_unrelated_coach_is_not_an_assigned_pair():
    coach_user, _coach = _make_coach_member("coach")
    member = _make_user(username="member")

    assert not is_assigned_coach_pair(member, coach_user)


@pytest.mark.django_db
def test_own_assigned_coach_is_excluded_from_member_pool():
    coach_user, coach = _make_coach_member("coach", gender="F")
    member = _assign_coach(
        _make_user(username="member", preferred_genders=["F"]), coach
    )

    assert coach_user not in get_eligible_pool(member)


@pytest.mark.django_db
def test_coach_pool_excludes_own_assigned_members():
    coach_user, coach = _make_coach_member("coach", gender="F", preferred_genders=["M"])
    PremiumMembership.objects.create(
        user=coach_user,
        coach=coach,
        status="active",
        payment_confirmed=True,
    )
    own_member = _assign_coach(
        _make_user(username="own", premium=False, preferred_genders=["F"]), coach
    )
    unrelated = _make_user(username="unrelated", premium=False, preferred_genders=["F"])

    pool = get_eligible_pool(coach_user)

    assert own_member not in pool
    assert unrelated in pool


@pytest.mark.django_db
def test_onboarding_destination_is_connect_week_when_launched(settings):
    settings.CRUSH_CONNECT_LAUNCHED = True
    member = _make_user(username="member", premium=False)

    assert _connect_done_url(member) == "crush_lu:connect_week_home"


@pytest.mark.django_db
def test_onboarding_destination_is_catalogue_when_beta_member_lacks_cycle_access(
    settings,
):
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    member = _make_user(username="member", premium=False)

    assert _connect_done_url(member) == "crush_lu:crush_connect_catalogue_status"
