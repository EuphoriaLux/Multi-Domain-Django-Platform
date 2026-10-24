"""Guards and queue exclusions of the Connect real-person check.

``check_guards`` is the one place the coach views will ask "may this coach work
on this member right now", so each guard code is pinned here, and the queue is
pinned to the same rules.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from crush_lu.models import (
    ConnectRealPersonBadge,
    ConnectRealPersonCheck,
    CrushCoach,
    CrushProfile,
    PremiumMembership,
    ProfilePhotoReviewState,
    UserBlock,
    UserDataConsent,
    UserReport,
)
from crush_lu.real_person_check_rules import REQUIRED_CHECKLIST_KEYS
from crush_lu.services.real_person_check import (
    RealPersonCheckError,
    check_guards,
    claim_check,
    decide_check,
    log_outreach,
    open_checks_for_coach,
    real_person_check_queue,
)
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
    set_photo,
)
from crush_lu.tests.test_crush_connect import _get_coach

User = get_user_model()

pytestmark = pytest.mark.django_db

VOICE = {key: True for key in REQUIRED_CHECKLIST_KEYS["voice"]}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def coach():
    return make_coach("rpc_guard_coach")


@pytest.fixture
def member():
    return make_member("rpc_guard_member")


def _guards(coach, user, **kwargs):
    return check_guards(coach, profile_of(user), **kwargs)


def _queue_ids(coach, **kwargs):
    return set(real_person_check_queue(coach, **kwargs).values_list("pk", flat=True))


# ---------------------------------------------------------------------------
# check_guards
# ---------------------------------------------------------------------------


def test_a_clean_member_has_no_failures(coach, member):
    assert _guards(coach, member) == []
    assert _guards(coach, member, medium="video", confirmed=True) == []


def test_a_coach_cannot_work_on_their_own_profile(member):
    own = CrushCoach.objects.create(user=member, bio="coach and member")
    failures = _guards(own, member)
    assert "self" in failures
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(own, profile_of(member).pk)
    assert excinfo.value.code == "self"
    assert member.pk not in _queue_ids(own)


def test_an_inactive_coach_is_refused(member):
    assert "coach_inactive" in _guards(make_coach("rpc_gone", active=False), member)


@pytest.mark.parametrize("what", ["user", "profile"])
def test_an_inactive_member_is_refused(coach, member, what):
    if what == "user":
        User.objects.filter(pk=member.pk).update(is_active=False)
    else:
        CrushProfile.objects.filter(user=member).update(is_active=False)
    failures = _guards(coach, member)
    assert "inactive" in failures
    assert "not_in_cohort" in failures


def test_a_member_without_crush_consent_is_refused(coach, member):
    UserDataConsent.objects.filter(user=member).update(crushlu_consent_given=False)
    assert "no_consent" in _guards(coach, member)
    UserDataConsent.objects.filter(user=member).delete()
    assert "no_consent" in _guards(coach, member)


def test_a_banned_member_is_refused(coach, member):
    UserDataConsent.objects.filter(user=member).update(crushlu_banned=True)
    failures = _guards(coach, member)
    assert "banned" in failures
    assert "no_consent" not in failures


def test_a_member_without_a_photo_is_refused(coach, member):
    CrushProfile.objects.filter(user=member).update(photo_1="")
    failures = _guards(coach, member)
    assert "no_photo" in failures
    # Nothing to compare, so the stale-photo guard has nothing to add.
    assert "stale_photo" not in _guards(coach, member, photo_key="users/1/old.jpg")


def test_a_photo_that_changed_since_the_page_rendered_is_refused(coach, member):
    key = profile_of(member).photo_1.name
    assert "stale_photo" not in _guards(coach, member, photo_key=key)
    assert "stale_photo" not in _guards(coach, member)  # claim: nothing posted yet
    assert "stale_photo" in _guards(coach, member, photo_key="users/1/older.jpg")
    assert "stale_photo" in _guards(coach, member, photo_key="")


def test_a_video_pass_needs_the_live_photo_to_be_the_public_one(coach, member):
    """Otherwise the coach compared a face no other member can see."""
    ProfilePhotoReviewState.objects.filter(profile__user=member).update(
        status="pending"
    )
    assert "photo_not_public" in _guards(coach, member, medium="video")
    assert "photo_not_public" not in _guards(coach, member, medium="voice")
    assert "photo_not_public" not in _guards(coach, member)


@pytest.mark.parametrize("direction", ["coach_blocked_member", "member_blocked_coach"])
def test_a_blocked_pair_is_refused_in_either_direction(coach, member, direction):
    blocker, blocked = (
        (coach.user, member)
        if direction == "coach_blocked_member"
        else (member, coach.user)
    )
    UserBlock.objects.create(blocker=blocker, blocked=blocked)
    assert "blocked" in _guards(coach, member)
    assert member.pk not in _queue_ids(coach)


def test_another_coachs_premium_member_is_refused(coach):
    premium = make_member("rpc_premium", premium=True)  # assigned to cc_coach
    assert "other_coach_premium" in _guards(coach, premium)
    assert premium.pk not in _queue_ids(coach)
    owner = _get_coach()
    assert "other_coach_premium" not in _guards(owner, premium)
    assert premium.pk in _queue_ids(owner)


def test_an_assigned_coach_without_active_premium_is_not_premium(coach, member):
    """assigned_coach is granted free on first attendance; only an active
    PremiumMembership is the entitlement."""
    CrushProfile.objects.filter(user=member).update(assigned_coach=_get_coach())
    assert "other_coach_premium" not in _guards(coach, member)
    assert member.pk in _queue_ids(coach)
    PremiumMembership.objects.create(
        user=member,
        coach=_get_coach(),
        status="active",
        payment_confirmed=True,
        payment_date=timezone.now(),
    )
    assert "other_coach_premium" in _guards(coach, member)
    assert member.pk not in _queue_ids(coach)
    PremiumMembership.objects.filter(user=member).update(status="cancelled")
    assert "other_coach_premium" not in _guards(coach, member)
    assert member.pk in _queue_ids(coach)


@pytest.mark.parametrize(
    ("status", "reason", "guarded"),
    [
        ("open", "fake_profile", True),
        ("reviewing", "fake_profile", True),
        ("actioned", "fake_profile", False),
        ("dismissed", "fake_profile", False),
        ("open", "spam", False),
    ],
)
def test_an_unresolved_fake_profile_report_is_a_guard_like_it_is_a_queue_rule(
    coach, member, status, reason, guarded
):
    """The guard and the queue read the same rule, so they cannot disagree."""
    reporter = make_member("rpc_guard_reporter")
    UserReport.objects.create(
        reporter=reporter, reported_user=member, reason=reason, status=status
    )
    assert ("fake_report_pending" in _guards(coach, member)) is guarded
    assert (member.pk in _queue_ids(coach)) is not guarded
    if guarded:
        with pytest.raises(RealPersonCheckError) as excinfo:
            claim_check(coach, profile_of(member).pk)
        assert excinfo.value.code == "fake_report_pending"
        assert excinfo.value.status == 403
        assert not ConnectRealPersonCheck.objects.exists()
    else:
        claim_check(coach, profile_of(member).pk)


def test_a_suspected_fake_cannot_be_claimed_from_a_stale_queue_page(coach, member):
    """Coach A flags a member; coach B still has the queue page from before."""
    other = make_coach("rpc_guard_stale")
    assert member.pk in _queue_ids(other)  # B's page, rendered before A acted
    check = claim_check(coach, profile_of(member).pk)
    decide_check(coach, check.pk, "suspected_fake", notes="stolen photos")
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(other, profile_of(member).pk)  # B clicks "claim" anyway
    assert excinfo.value.code == "fake_report_pending"
    assert not ConnectRealPersonCheck.objects.filter(status="open").exists()
    assert not ConnectRealPersonBadge.objects.exists()


def test_a_fake_report_filed_during_a_claim_blocks_contact_and_a_pass_only(
    coach, member
):
    check = claim_check(coach, profile_of(member).pk)
    UserReport.objects.create(
        reporter=make_member("rpc_guard_reporter"),
        reported_user=member,
        reason="fake_profile",
        status="open",
    )
    with pytest.raises(RealPersonCheckError) as excinfo:
        log_outreach(coach, check.pk, result="sms_sent")
    assert excinfo.value.code == "fake_report_pending"
    with pytest.raises(RealPersonCheckError) as excinfo:
        decide_check(coach, check.pk, "pass_voice", checklist=VOICE, confirmed=True)
    assert excinfo.value.code == "fake_report_pending"
    assert not ConnectRealPersonBadge.objects.exists()
    # The coach is never stranded: closing the claim needs no guard.
    assert decide_check(coach, check.pk, "declined").status == "declined"


def test_a_pass_must_be_confirmed(coach, member):
    assert "not_confirmed" not in _guards(coach, member)  # claim, GET, log
    assert "not_confirmed" not in _guards(coach, member, confirmed=True)
    assert "not_confirmed" in _guards(coach, member, confirmed=False)


@pytest.mark.parametrize("method", ["coach_event", "premium_coach"])
def test_a_member_who_left_the_cohort_is_refused(coach, member, method):
    assert "not_in_cohort" not in _guards(coach, member)
    CrushProfile.objects.filter(user=member).update(verification_method=method)
    assert "not_in_cohort" in _guards(coach, member)
    assert member.pk not in _queue_ids(coach)


def test_a_member_with_a_valid_badge_is_refused(coach, member):
    check = claim_check(coach, profile_of(member).pk)
    decide_check(coach, check.pk, "pass_voice", checklist=VOICE, confirmed=True)
    other = make_coach("rpc_guard_other")
    assert "already_verified" in _guards(other, member)
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(other, profile_of(member).pk)
    assert excinfo.value.code == "already_verified"


def test_several_failures_are_all_reported(coach, member):
    UserDataConsent.objects.filter(user=member).update(crushlu_banned=True)
    CrushProfile.objects.filter(user=member).update(photo_1="")
    assert {"banned", "no_photo", "not_in_cohort"} <= set(_guards(coach, member))


def test_claim_reports_every_failed_guard(coach, member):
    UserBlock.objects.create(blocker=member, blocked=coach.user)
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(coach, profile_of(member).pk)
    assert excinfo.value.status == 403
    assert excinfo.value.code == "blocked"
    assert "blocked" in excinfo.value.failures
    assert not ConnectRealPersonCheck.objects.exists()


# ---------------------------------------------------------------------------
# real_person_check_queue
# ---------------------------------------------------------------------------


def test_the_queue_holds_catalogue_members_whatever_their_login_age(coach):
    fresh = make_member("rpc_q_fresh", last_login_days_ago=1)
    dormant = make_member("rpc_q_dormant", last_login_days_ago=400)
    never = make_member("rpc_q_never", last_login_days_ago=None)
    assert {fresh.pk, dormant.pk, never.pk} <= _queue_ids(coach)


def test_the_queue_is_ordered_by_last_login_with_never_last(coach):
    old = make_member("rpc_q_old", last_login_days_ago=200)
    new = make_member("rpc_q_new", last_login_days_ago=2)
    mid = make_member("rpc_q_mid", last_login_days_ago=30)
    never = make_member("rpc_q_never", last_login_days_ago=None)
    ordered = list(real_person_check_queue(coach).values_list("pk", flat=True))
    assert ordered == [new.pk, mid.pk, old.pk, never.pk]


def test_the_queue_leaves_out_the_coach_themself(coach):
    own = make_member("rpc_q_own")
    CrushCoach.objects.create(user=own, bio="x")
    assert own.pk not in _queue_ids(CrushCoach.objects.get(user=own))
    assert own.pk in _queue_ids(coach)


def test_the_queue_leaves_out_valid_badge_holders_only(coach):
    member = make_member("rpc_q_badge")
    check = claim_check(coach, profile_of(member).pk)
    decide_check(coach, check.pk, "pass_voice", checklist=VOICE, confirmed=True)
    other = make_coach("rpc_q_other")
    assert member.pk not in _queue_ids(other)
    # The profile was re-verified: its approved_at moved, the badge is stale and
    # the member may be checked again.
    CrushProfile.objects.filter(user=member).update(approved_at=timezone.now())
    assert ConnectRealPersonBadge.objects.filter(profile__user=member).exists()
    assert member.pk in _queue_ids(other)
    claim_check(other, profile_of(member).pk)


def test_a_badge_snapshot_of_a_null_approval_matches_a_null_approval(coach):
    """Fixtures and legacy profiles can have approved_at NULL on both sides."""
    member = make_member("rpc_q_null")
    assert profile_of(member).approved_at is None
    check = claim_check(coach, profile_of(member).pk)
    decide_check(coach, check.pk, "pass_voice", checklist=VOICE, confirmed=True)
    assert member.pk not in _queue_ids(make_coach("rpc_q_other"))


def test_the_queue_hides_another_coachs_live_claim_but_not_an_expired_or_own_one(
    coach,
):
    member = make_member("rpc_q_claimed")
    other = make_coach("rpc_q_other")
    t0 = timezone.now()
    claim_check(other, profile_of(member).pk, now=t0)
    assert member.pk not in _queue_ids(coach, now=t0 + timedelta(hours=1))
    assert member.pk in _queue_ids(other, now=t0 + timedelta(hours=1))
    assert member.pk in _queue_ids(coach, now=t0 + timedelta(hours=73))


def test_the_queue_follows_consent_and_bans(coach):
    unconsented = make_member("rpc_q_noconsent")
    banned = make_member("rpc_q_banned")
    UserDataConsent.objects.filter(user=unconsented).update(crushlu_consent_given=False)
    UserDataConsent.objects.filter(user=banned).update(crushlu_banned=True)
    ids = _queue_ids(coach)
    assert unconsented.pk not in ids
    assert banned.pk not in ids


@pytest.mark.parametrize(
    ("status", "queued"),
    [("open", False), ("reviewing", False), ("actioned", True), ("dismissed", True)],
)
def test_an_unresolved_fake_profile_report_keeps_the_member_out(coach, status, queued):
    member = make_member("rpc_q_reported")
    reporter = make_member("rpc_q_reporter")
    UserReport.objects.create(
        reporter=reporter,
        reported_user=member,
        reason="fake_profile",
        status=status,
    )
    assert (member.pk in _queue_ids(coach)) is queued


def test_other_report_reasons_do_not_hide_the_member(coach):
    member = make_member("rpc_q_spam")
    reporter = make_member("rpc_q_reporter")
    UserReport.objects.create(
        reporter=reporter, reported_user=member, reason="spam", status="open"
    )
    assert member.pk in _queue_ids(coach)


def test_the_queue_leaves_out_members_who_are_not_in_the_catalogue(coach):
    outside = make_member("rpc_q_outside", excluded_by_coach=True)
    attested = make_member("rpc_q_attested")
    CrushProfile.objects.filter(user=attested).update(verification_method="coach_event")
    ids = _queue_ids(coach)
    assert outside.pk not in ids
    assert attested.pk not in ids


def test_a_declined_member_comes_back_after_the_cooldown(coach):
    member = make_member("rpc_q_declined")
    t0 = timezone.now()
    check = claim_check(coach, profile_of(member).pk, now=t0)
    decide_check(coach, check.pk, "declined", now=t0)
    assert member.pk not in _queue_ids(coach, now=t0 + timedelta(days=89))
    assert member.pk in _queue_ids(coach, now=t0 + timedelta(days=91))


def test_open_checks_lists_only_the_coachs_live_claims(coach):
    mine = make_member("rpc_q_mine")
    theirs = make_member("rpc_q_theirs")
    other = make_coach("rpc_q_other")
    t0 = timezone.now()
    claim_check(coach, profile_of(mine).pk, now=t0)
    claim_check(other, profile_of(theirs).pk, now=t0)
    live = list(open_checks_for_coach(coach, now=t0 + timedelta(hours=1)))
    assert [check.profile.user_id for check in live] == [mine.pk]
    assert not list(open_checks_for_coach(coach, now=t0 + timedelta(hours=73)))


def test_set_photo_helper_keeps_the_member_in_the_catalogue(coach):
    """Guards the fixtures: a per-user key must not drop the member."""
    member = make_member("rpc_q_rekeyed")
    set_photo(member, "users/9/photos/other.jpg")
    assert member.pk in _queue_ids(coach)
