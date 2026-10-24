"""Claims, leases, outcomes and revoke of the Connect real-person check.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

import inspect
import json
from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from crush_lu.models import (
    CallAttempt,
    ConnectRealPersonBadge,
    ConnectRealPersonCheck,
    CrushCoach,
    UserReport,
)
from crush_lu.real_person_check_rules import (
    CLAIM_TTL,
    DECLINE_COOLDOWN,
    MAX_OPEN_CLAIMS_PER_COACH,
    REQUIRED_CHECKLIST_KEYS,
)
from crush_lu.services import real_person_check as rpc
from crush_lu.services.real_person_check import (
    RealPersonCheckError,
    claim_check,
    decide_check,
    log_outreach,
    real_person_check_queue,
    release_check,
    revoke_check,
)
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
)
from crush_lu.tests.test_crush_connect import _grant_consent

User = get_user_model()

pytestmark = pytest.mark.django_db

VOICE = {key: True for key in REQUIRED_CHECKLIST_KEYS["voice"]}
VIDEO = {key: True for key in REQUIRED_CHECKLIST_KEYS["video"]}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def coach():
    return make_coach("rpc_coach_a")


@pytest.fixture
def other_coach():
    return make_coach("rpc_coach_b")


@pytest.fixture
def member():
    return make_member("rpc_member")


def _profile_id(user):
    return profile_of(user).pk


def _code(excinfo):
    return excinfo.value.code


# ---------------------------------------------------------------------------
# Claiming and leases
# ---------------------------------------------------------------------------


def test_claim_opens_a_check_with_a_72_hour_lease(coach, member):
    now = timezone.now()
    check = claim_check(coach, _profile_id(member), now=now)
    assert check.status == "open"
    assert check.coach_id == coach.pk
    assert check.claimed_at == now
    assert check.lease_expires_at == now + CLAIM_TTL == now + timedelta(hours=72)


def test_claim_is_idempotent_for_the_holder(coach, member):
    first = claim_check(coach, _profile_id(member))
    again = claim_check(coach, _profile_id(member))
    assert again.pk == first.pk
    assert ConnectRealPersonCheck.objects.count() == 1


def test_live_lease_blocks_another_coach(coach, other_coach, member):
    claim_check(coach, _profile_id(member))
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(other_coach, _profile_id(member))
    assert _code(excinfo) == "taken"


def test_expired_lease_is_reclaimable_and_the_old_row_released(
    coach, other_coach, member
):
    t0 = timezone.now()
    old = claim_check(coach, _profile_id(member), now=t0)
    later = t0 + CLAIM_TTL + timedelta(minutes=1)
    new = claim_check(other_coach, _profile_id(member), now=later)
    old.refresh_from_db()
    assert old.status == "released"
    assert new.coach_id == other_coach.pk
    assert new.status == "open"


def test_partial_unique_constraint_allows_one_open_row_per_profile(coach, member):
    profile = profile_of(member)
    claim_check(coach, profile.pk)
    with pytest.raises(IntegrityError), transaction.atomic():
        ConnectRealPersonCheck.objects.create(
            profile=profile, coach=coach, lease_expires_at=timezone.now()
        )
    # Closed rows are history and never collide.
    ConnectRealPersonCheck.objects.filter(profile=profile).update(status="released")
    ConnectRealPersonCheck.objects.create(
        profile=profile, coach=coach, status="released"
    )
    ConnectRealPersonCheck.objects.create(
        profile=profile, coach=coach, status="released"
    )


def test_losing_the_claim_race_maps_to_taken(monkeypatch, coach, other_coach, member):
    """The unique index is the guard when the row lock saw nothing."""
    pid = _profile_id(member)
    claim_check(coach, pid)
    monkeypatch.setattr(rpc, "_open_check_for", lambda profile: None)
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(other_coach, pid)
    assert _code(excinfo) == "taken"
    # The savepoint kept the outer transaction usable, and nothing doubled.
    assert (
        ConnectRealPersonCheck.objects.filter(profile_id=pid, status="open").count()
        == 1
    )


def test_capacity_counts_live_leases_only(coach):
    members = [
        make_member(f"rpc_cap_{i}") for i in range(MAX_OPEN_CLAIMS_PER_COACH + 1)
    ]
    t0 = timezone.now()
    for user in members[:MAX_OPEN_CLAIMS_PER_COACH]:
        claim_check(coach, _profile_id(user), now=t0)
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(coach, _profile_id(members[-1]), now=t0)
    assert _code(excinfo) == "capacity"
    # Once those leases lapse they no longer count, though the rows are still
    # open until something closes them.
    later = t0 + CLAIM_TTL + timedelta(hours=1)
    assert (
        ConnectRealPersonCheck.objects.filter(coach=coach, status="open").count()
        == MAX_OPEN_CLAIMS_PER_COACH
    )
    claim_check(coach, _profile_id(members[-1]), now=later)


def test_the_capacity_count_runs_after_the_coach_lock(monkeypatch, coach):
    """A claim that waited for the coach lock must see the claims that finished
    meanwhile. SQLite never waits, so stand in for the rival: right after the
    lock is granted, MAX claims by this coach "commit"."""
    members = [
        make_member(f"rpc_race_{i}") for i in range(MAX_OPEN_CLAIMS_PER_COACH + 1)
    ]
    real_lock = rpc._lock_coach

    def lock_then_let_rival_claims_land(coach_id):
        locked = real_lock(coach_id)
        for user in members[:MAX_OPEN_CLAIMS_PER_COACH]:
            ConnectRealPersonCheck.objects.create(
                profile=profile_of(user),
                coach=locked,
                lease_expires_at=timezone.now() + CLAIM_TTL,
            )
        return locked

    monkeypatch.setattr(rpc, "_lock_coach", lock_then_let_rival_claims_land)
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(coach, _profile_id(members[-1]))
    assert _code(excinfo) == "capacity"
    assert not ConnectRealPersonCheck.objects.filter(profile__user=members[-1]).exists()


def test_a_claim_judges_the_coach_by_their_current_row(coach, member):
    """The caller's CrushCoach may be stale; the locked row is the truth."""
    CrushCoach.objects.filter(pk=coach.pk).update(is_active=False)
    assert coach.is_active  # the in-memory copy still says yes
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(coach, _profile_id(member))
    assert _code(excinfo) == "coach_inactive"
    assert not ConnectRealPersonCheck.objects.exists()


def test_a_claim_by_a_coach_who_no_longer_exists_is_not_found(coach, member):
    pid = _profile_id(member)
    CrushCoach.objects.filter(pk=coach.pk).delete()
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(coach, pid)
    assert _code(excinfo) == "not_found"
    assert excinfo.value.status == 404


def test_logging_renews_the_lease(coach, member):
    t0 = timezone.now()
    check = claim_check(coach, _profile_id(member), now=t0)
    later = t0 + timedelta(hours=48)
    attempt = log_outreach(coach, check.pk, result="sms_sent", now=later)
    check.refresh_from_db()
    assert check.lease_expires_at == later + CLAIM_TTL
    assert attempt.real_person_check_id == check.pk
    assert attempt.profile_id == check.profile_id
    assert attempt.coach_id == coach.pk
    assert attempt.logged_by_id == coach.user_id
    assert attempt.result == "sms_sent"


def test_a_failed_attempt_needs_a_reason(coach, member):
    check = claim_check(coach, _profile_id(member))
    with pytest.raises(RealPersonCheckError) as excinfo:
        log_outreach(coach, check.pk, result="failed")
    assert _code(excinfo) == "invalid_failure_reason"
    with pytest.raises(RealPersonCheckError) as excinfo:
        log_outreach(coach, check.pk, result="success")
    assert _code(excinfo) == "invalid_result"
    attempt = log_outreach(
        coach, check.pk, result="failed", failure_reason="voicemail", notes="left one"
    )
    assert attempt.failure_reason == "voicemail"


def test_logging_after_the_lease_lapsed_releases_the_row_and_refuses(coach, member):
    t0 = timezone.now()
    check = claim_check(coach, _profile_id(member), now=t0)
    with pytest.raises(RealPersonCheckError) as excinfo:
        log_outreach(
            coach, check.pk, result="sms_sent", now=t0 + CLAIM_TTL + timedelta(hours=1)
        )
    assert _code(excinfo) == "lease_expired"
    check.refresh_from_db()
    assert check.status == "released"  # the expiry was committed, not rolled back
    assert not CallAttempt.objects.filter(real_person_check=check).exists()


def test_release_returns_the_member_to_the_queue(coach, other_coach, member):
    check = claim_check(coach, _profile_id(member))
    assert member.pk not in set(
        real_person_check_queue(other_coach).values_list("pk", flat=True)
    )
    release_check(coach, check.pk)
    check.refresh_from_db()
    assert check.status == "released"
    assert check.decided_at is not None
    assert member.pk in set(
        real_person_check_queue(other_coach).values_list("pk", flat=True)
    )
    claim_check(other_coach, _profile_id(member))


def test_a_closed_check_cannot_be_logged_released_or_decided(coach, member):
    check = claim_check(coach, _profile_id(member))
    release_check(coach, check.pk)
    for call in (
        lambda: log_outreach(coach, check.pk, result="sms_sent"),
        lambda: release_check(coach, check.pk),
        lambda: decide_check(coach, check.pk, "declined"),
    ):
        with pytest.raises(RealPersonCheckError) as excinfo:
            call()
        assert _code(excinfo) == "not_open"


def test_another_coachs_check_is_not_found(coach, other_coach, member):
    check = claim_check(coach, _profile_id(member))
    for call in (
        lambda: log_outreach(other_coach, check.pk, result="sms_sent"),
        lambda: release_check(other_coach, check.pk),
        lambda: decide_check(other_coach, check.pk, "declined"),
    ):
        with pytest.raises(RealPersonCheckError) as excinfo:
            call()
        assert _code(excinfo) == "not_found"
        assert excinfo.value.status == 404
    check.refresh_from_db()
    assert check.status == "open"


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def test_pass_voice_grants_a_badge_and_logs_a_success(coach, member):
    check = claim_check(coach, _profile_id(member))
    decided = decide_check(
        coach,
        check.pk,
        "pass_voice",
        checklist=VOICE,
        notes="  clear  ",
        confirmed=True,
    )
    assert decided.status == "passed"
    assert decided.medium == "voice"
    assert decided.photo_key == ""
    assert decided.notes == "clear"
    assert decided.checklist == VOICE
    assert decided.decided_at is not None
    badge = ConnectRealPersonBadge.objects.get(profile__user=member)
    assert badge.source_check_id == check.pk
    assert badge.medium == "voice"
    assert badge.photo_key == ""
    assert badge.profile_approved_at == profile_of(member).approved_at
    attempt = CallAttempt.objects.get(real_person_check=check)
    assert attempt.result == "success"
    assert attempt.coach_id == coach.pk


def test_pass_video_records_the_exact_photo_key(coach, member):
    profile = profile_of(member)
    check = claim_check(coach, profile.pk)
    decided = decide_check(
        coach,
        check.pk,
        "pass_video",
        checklist=VIDEO,
        photo_key=profile.photo_1.name,
        confirmed=True,
    )
    assert decided.medium == "video"
    assert decided.photo_key == profile.photo_1.name
    badge = ConnectRealPersonBadge.objects.get(profile=profile)
    assert (badge.medium, badge.photo_key) == ("video", profile.photo_1.name)


def test_a_video_pass_must_say_which_photo_it_compared(coach, member):
    check = claim_check(coach, _profile_id(member))
    with pytest.raises(RealPersonCheckError) as excinfo:
        decide_check(coach, check.pk, "pass_video", checklist=VIDEO, confirmed=True)
    assert _code(excinfo) == "stale_photo"
    assert not ConnectRealPersonBadge.objects.exists()


def test_a_pass_needs_the_confirmation_the_full_checklist_and_the_current_photo(
    coach, member
):
    profile = profile_of(member)
    check = claim_check(coach, profile.pk)
    with pytest.raises(RealPersonCheckError) as excinfo:
        decide_check(coach, check.pk, "pass_voice", checklist=VOICE, confirmed=False)
    assert _code(excinfo) == "not_confirmed"
    partial = {**VOICE, "spoke_live": False}
    with pytest.raises(RealPersonCheckError) as excinfo:
        decide_check(coach, check.pk, "pass_voice", checklist=partial, confirmed=True)
    assert _code(excinfo) == "checklist_incomplete"
    with pytest.raises(RealPersonCheckError) as excinfo:
        decide_check(
            coach,
            check.pk,
            "pass_voice",
            checklist=VOICE,
            photo_key="users/1/photos/an-older-photo.jpg",
            confirmed=True,
        )
    assert _code(excinfo) == "stale_photo"
    check.refresh_from_db()
    assert check.status == "open"
    assert not ConnectRealPersonBadge.objects.exists()


def test_a_second_pass_replaces_the_badge_of_a_revoked_one(coach, other_coach, member):
    pid = _profile_id(member)
    first = claim_check(coach, pid)
    decide_check(coach, first.pk, "pass_voice", checklist=VOICE, confirmed=True)
    admin = User.objects.create_superuser("rpc_root", "rpc_root@example.com", "pw")
    revoke_check(admin, first.pk, "re-check")
    second = claim_check(other_coach, pid)
    profile = profile_of(member)
    decide_check(
        other_coach,
        second.pk,
        "pass_video",
        checklist=VIDEO,
        photo_key=profile.photo_1.name,
        confirmed=True,
    )
    badge = ConnectRealPersonBadge.objects.get(profile=profile)
    assert badge.source_check_id == second.pk
    assert badge.medium == "video"


def test_unreachable_logs_a_failed_attempt_and_the_member_stays_queued(
    coach, other_coach, member
):
    check = claim_check(coach, _profile_id(member))
    decided = decide_check(coach, check.pk, "unreachable", failure_reason="no_answer")
    assert decided.status == "unreachable"
    attempt = CallAttempt.objects.get(real_person_check=check)
    assert (attempt.result, attempt.failure_reason) == ("failed", "no_answer")
    assert not ConnectRealPersonBadge.objects.exists()
    assert member.pk in set(
        real_person_check_queue(other_coach).values_list("pk", flat=True)
    )
    claim_check(other_coach, _profile_id(member))


def test_declined_starts_a_90_day_cooldown(coach, other_coach, member):
    t0 = timezone.now()
    check = claim_check(coach, _profile_id(member), now=t0)
    decide_check(coach, check.pk, "declined", now=t0)
    assert DECLINE_COOLDOWN == timedelta(days=90)
    soon = t0 + timedelta(days=89)
    assert member.pk not in set(
        real_person_check_queue(other_coach, now=soon).values_list("pk", flat=True)
    )
    with pytest.raises(RealPersonCheckError) as excinfo:
        claim_check(other_coach, _profile_id(member), now=soon)
    assert _code(excinfo) == "cooldown"
    after = t0 + timedelta(days=91)
    assert member.pk in set(
        real_person_check_queue(other_coach, now=after).values_list("pk", flat=True)
    )
    claim_check(other_coach, _profile_id(member), now=after)


def test_suspected_fake_opens_a_report_that_names_only_the_check(
    coach, other_coach, member
):
    check = claim_check(coach, _profile_id(member))
    secret = "photo looks stolen from an influencer"
    decided = decide_check(coach, check.pk, "suspected_fake", notes=secret)
    assert decided.status == "suspected_fake"
    assert decided.notes == secret  # kept on the check, for staff
    report = UserReport.objects.get()
    assert report.status == "open"
    assert report.reason == "fake_profile"
    assert report.reporter_id == coach.user_id
    assert report.reported_user_id == member.pk
    assert report.details == f"Real-person check #{check.pk}"
    assert report.source == "real_person"
    assert report.source_id == check.pk
    assert secret not in json.dumps(
        list(UserReport.objects.values("details", "resolution_notes"))
    )
    # Not offered to the next coach before a superuser has acted.
    assert member.pk not in set(
        real_person_check_queue(other_coach).values_list("pk", flat=True)
    )
    assert not ConnectRealPersonBadge.objects.exists()


def test_a_negative_outcome_still_closes_a_check_that_went_stale(coach, member):
    """A banned or blocked member must not strand the coach's claim."""
    from crush_lu.models import UserDataConsent

    check = claim_check(coach, _profile_id(member))
    UserDataConsent.objects.filter(user=member).update(crushlu_banned=True)
    with pytest.raises(RealPersonCheckError) as excinfo:
        decide_check(coach, check.pk, "pass_voice", checklist=VOICE, confirmed=True)
    assert _code(excinfo) == "banned"
    assert decide_check(coach, check.pk, "declined").status == "declined"


def test_a_decision_after_the_lease_lapsed_releases_and_refuses(coach, member):
    t0 = timezone.now()
    check = claim_check(coach, _profile_id(member), now=t0)
    with pytest.raises(RealPersonCheckError) as excinfo:
        decide_check(
            coach,
            check.pk,
            "pass_voice",
            checklist=VOICE,
            confirmed=True,
            now=t0 + CLAIM_TTL + timedelta(minutes=5),
        )
    assert _code(excinfo) == "lease_expired"
    check.refresh_from_db()
    assert check.status == "released"
    assert not ConnectRealPersonBadge.objects.exists()


def test_notes_over_the_limit_are_refused(coach, member):
    check = claim_check(coach, _profile_id(member))
    with pytest.raises(RealPersonCheckError) as excinfo:
        decide_check(coach, check.pk, "declined", notes="x" * 1001)
    assert _code(excinfo) == "notes_too_long"
    decide_check(coach, check.pk, "declined", notes="x" * 1000)


def test_unknown_outcome_is_refused(coach, member):
    check = claim_check(coach, _profile_id(member))
    with pytest.raises(RealPersonCheckError) as excinfo:
        decide_check(coach, check.pk, "approved")
    assert _code(excinfo) == "invalid_outcome"


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


def _passed_check(coach, member, outcome="pass_voice"):
    profile = profile_of(member)
    check = claim_check(coach, profile.pk)
    return decide_check(
        coach,
        check.pk,
        outcome,
        checklist=VIDEO if outcome == "pass_video" else VOICE,
        photo_key=profile.photo_1.name,
        confirmed=True,
    )


def test_revoke_is_superuser_only_and_removes_the_badge(coach, member):
    check = _passed_check(coach, member)
    with pytest.raises(RealPersonCheckError) as excinfo:
        revoke_check(coach.user, check.pk, "no")
    assert _code(excinfo) == "forbidden"
    assert ConnectRealPersonBadge.objects.exists()

    admin = User.objects.create_superuser("rpc_root", "rpc_root@example.com", "pw")
    revoked = revoke_check(admin, check.pk, "member asked for a re-check")
    assert revoked.status == "revoked"
    assert revoked.revoked_by_id == admin.pk
    assert revoked.revoked_at is not None
    assert revoked.revoke_reason == "member asked for a re-check"
    assert not ConnectRealPersonBadge.objects.exists()


def test_an_inactive_superuser_cannot_revoke(coach, member):
    check = _passed_check(coach, member)
    admin = User.objects.create_superuser("rpc_root", "rpc_root@example.com", "pw")
    User.objects.filter(pk=admin.pk).update(is_active=False)
    admin.refresh_from_db()
    with pytest.raises(RealPersonCheckError) as excinfo:
        revoke_check(admin, check.pk, "no")
    assert _code(excinfo) == "forbidden"
    assert ConnectRealPersonBadge.objects.exists()


def test_the_check_prints_as_its_id_only(coach, member):
    """__str__ labels read-only links on admin pages staff can open."""
    check = claim_check(coach, _profile_id(member))
    decided = decide_check(coach, check.pk, "suspected_fake")
    assert str(decided) == f"Real-person check #{check.pk}"


def test_only_a_passed_check_can_be_revoked(coach, member):
    check = claim_check(coach, _profile_id(member))
    admin = User.objects.create_superuser("rpc_root", "rpc_root@example.com", "pw")
    with pytest.raises(RealPersonCheckError) as excinfo:
        revoke_check(admin, check.pk)
    assert _code(excinfo) == "not_passed"


def test_revoking_an_old_pass_leaves_a_newer_badge(coach, other_coach, member):
    """The badge row belongs to the check that granted it."""
    old = _passed_check(coach, member)
    profile = profile_of(member)
    # A newer pass took over the badge (the old check was never revoked).
    new = ConnectRealPersonCheck.objects.create(
        profile=profile,
        coach=other_coach,
        status="passed",
        medium="voice",
        decided_at=timezone.now(),
    )
    ConnectRealPersonBadge.objects.filter(profile=profile).update(source_check=new)
    admin = User.objects.create_superuser("rpc_root", "rpc_root@example.com", "pw")
    revoke_check(admin, old.pk)
    old.refresh_from_db()
    assert old.status == "revoked"
    assert ConnectRealPersonBadge.objects.get(profile=profile).source_check_id == new.pk


# ---------------------------------------------------------------------------
# Lock order and metrics
# ---------------------------------------------------------------------------


def test_locks_are_taken_coach_then_profile_then_check_row():
    """SQLite ignores select_for_update, so a reversed lock passes every
    behavioural test and deadlocks only on PostgreSQL. Assert the source, as
    test_coach_unverified_profiles does for the verification paths.

    Only ``claim_check`` takes the coach lock, and first: it is the one step
    that counts a coach's open claims. ``PremiumMembership.confirm`` locks the
    same two rows in the same order (coach, then profile)."""
    assert 'CrushProfile.objects.select_for_update(of=("self",))' in (
        inspect.getsource(rpc._lock_profile)
    )
    assert "ConnectRealPersonCheck.objects.select_for_update()" in (
        inspect.getsource(rpc._lock_check)
    )
    coach_lock = inspect.getsource(rpc._lock_coach)
    assert "CrushCoach.objects.select_for_update(" in coach_lock
    # FOR NO KEY UPDATE: claims by one coach still queue up, but an insert that
    # carries the coach FK (a logged CallAttempt) is not blocked, so a claim and
    # a log on the same member cannot deadlock each other.
    assert 'of=("self",)' in coach_lock
    assert "no_key=True" in coach_lock

    owned = inspect.getsource(rpc._lock_owned_check)
    assert owned.index("_lock_profile(") < owned.index("_lock_check(")

    claim = inspect.getsource(rpc.claim_check)
    coach_first = claim.index("_lock_coach(")
    lock = claim.index("_lock_profile(")
    expire = claim.index("_close_expired(")
    existing = claim.index("_open_check_for(")
    count = claim.index("MAX_OPEN_CLAIMS_PER_COACH")
    create = claim.index("ConnectRealPersonCheck.objects.create(")
    assert coach_first < lock < expire < existing < count < create
    assert "_lock_check(" not in claim

    revoke = inspect.getsource(rpc.revoke_check)
    assert revoke.index("_lock_profile(") < revoke.index("_lock_check(")

    for function in (rpc.log_outreach, rpc.release_check, rpc.decide_check):
        source = inspect.getsource(function)
        assert "_lock_owned_check(" in source, function.__name__
        assert "_lock_check(" not in source.replace("_lock_owned_check(", "")

    # Nothing else takes the check lock on its own, and no other step locks a
    # coach (every other change starts at the member's profile).
    module = inspect.getsource(rpc)
    assert module.count("_lock_check(") == 3  # def, _lock_owned_check, revoke_check
    assert module.count("_lock_coach(") == 2  # def, claim_check


CRUSH_LU_URL_SETTINGS = {"ROOT_URLCONF": "azureproject.urls_crush"}


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CheckAttemptsStayOutOfScreeningMetricsTests(TestCase):
    """A check's contact attempts and pass are not screening calls."""

    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.coach = make_coach("rpc_metrics_coach")
        self.member = make_member("rpc_metrics_member")
        check = claim_check(self.coach, _profile_id(self.member))
        log_outreach(self.coach, check.pk, result="sms_sent")
        decide_check(
            self.coach, check.pk, "pass_voice", checklist=VOICE, confirmed=True
        )
        # One genuine screening call by the same coach.
        CallAttempt.objects.create(
            profile=profile_of(self.member), result="success", coach=self.coach
        )

    def test_admin_dashboard_counts_only_the_screening_call(self):
        User.objects.create_superuser("rpc_root", "rpc_root@example.com", "pass")
        client = Client()
        client.login(username="rpc_root", password="pass")
        response = client.get("/crush-admin/dashboard/")
        self.assertEqual(response.status_code, 200)
        workload = {c["name"]: c for c in response.context["coach_workload"]}
        row = workload[self.coach.user.get_full_name() or self.coach.user.username]
        self.assertEqual(row["calls"], 1)
        self.assertEqual(row["call_success_pct"], 100)
        self.assertEqual(response.context["call_summary"]["total"], 1)
        self.assertEqual(response.context["call_summary"]["successful"], 1)
        # The attempts exist; they are only filtered out of the figures.
        self.assertEqual(CallAttempt.objects.count(), 3)

    def test_business_plan_metrics_ignore_them(self):
        out = StringIO()
        call_command("business_plan_metrics", "--json", stdout=out)
        data = json.loads(out.getvalue())
        self.assertEqual(data["coach_operations"]["total_call_attempts"], 1)
        self.assertEqual(data["coach_operations"]["call_success_rate"], "100.0%")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CheckOutreachStaysOffTheMemberOverviewTests(TestCase):
    """The coach panel's member page lists call attempts for every coach, and
    for the member too when they are a coach. A check's contact attempts, with
    the checking coach's notes, are not for that page."""

    NOTE = "rpc-outreach-note-for-the-checking-coach"
    SCREENING_NOTE = "rpc-ordinary-screening-note"

    def setUp(self):
        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.client.defaults["HTTP_HOST"] = "crush.lu"
        self.checking_coach = make_coach("rpc_ov_checker")
        self.other_coach = make_coach("rpc_ov_other")
        for coach in (self.checking_coach, self.other_coach):
            _grant_consent(coach.user)  # the coach panel sits behind consent
        self.member = make_member("rpc_ov_member")
        CrushCoach.objects.create(user=self.member, bio="a coach who is a member too")
        check = claim_check(self.checking_coach, _profile_id(self.member))
        log_outreach(self.checking_coach, check.pk, result="sms_sent", notes=self.NOTE)
        # A screening call has no check and stays listed, as before.
        CallAttempt.objects.create(
            profile=profile_of(self.member),
            result="failed",
            failure_reason="no_answer",
            notes=self.SCREENING_NOTE,
            coach=self.checking_coach,
        )

    def _overview_as(self, user):
        self.client.force_login(user)
        response = self.client.get(f"/en/coach/member/{self.member.pk}/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_another_coach_does_not_see_the_outreach_note(self):
        html = self._overview_as(self.other_coach.user)
        self.assertNotIn(self.NOTE, html)
        self.assertIn(self.SCREENING_NOTE, html)  # the filter is selective

    def test_the_member_reading_their_own_page_does_not_see_it(self):
        html = self._overview_as(self.member)
        self.assertNotIn(self.NOTE, html)
        self.assertIn(self.SCREENING_NOTE, html)

    def test_the_checking_coach_does_not_see_it_here_either(self):
        # Their check page (a later change) is where their own notes live.
        self.assertNotIn(self.NOTE, self._overview_as(self.checking_coach.user))


@override_settings(**CRUSH_LU_URL_SETTINGS)
class CallAttemptAdminDoesNotLeakTheOutcomeTests(TestCase):
    """CallAttemptAdmin shows the check as a read-only link labelled with its
    __str__; staff who are not superusers can open that page."""

    def test_a_staff_member_sees_the_check_id_but_not_the_outcome(self):
        from django.contrib.auth.models import Permission
        from django.urls import reverse

        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        coach = make_coach("rpc_leak_coach")
        member = make_member("rpc_leak_member")
        check = claim_check(coach, _profile_id(member))
        attempt = log_outreach(coach, check.pk, result="sms_sent")
        decide_check(coach, check.pk, "suspected_fake", notes="secret verdict")
        staff = User.objects.create_user(
            "rpc_leak_staff", "rpc_leak_staff@example.com", "pw", is_staff=True
        )
        staff.user_permissions.add(Permission.objects.get(codename="view_callattempt"))
        self.client.force_login(staff)

        response = self.client.get(
            reverse("crush_admin:crush_lu_callattempt_change", args=[attempt.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Real-person check #{check.pk}")
        self.assertNotContains(response, "suspected_fake")
        self.assertNotContains(response, "Suspected fake")
        self.assertNotContains(response, "secret verdict")


@override_settings(**CRUSH_LU_URL_SETTINGS)
class RealPersonCheckAdminTests(TestCase):
    """Superuser-only oversight, with revoke as the one write."""

    def setUp(self):
        from django.urls import reverse

        cache.clear()
        Site.objects.get_or_create(
            id=1, defaults={"domain": "testserver", "name": "Test Server"}
        )
        self.coach = make_coach("rpc_admin_coach")
        self.member = make_member("rpc_admin_member")
        check = claim_check(self.coach, _profile_id(self.member))
        self.check = decide_check(
            self.coach,
            check.pk,
            "pass_voice",
            checklist=VOICE,
            notes="visible to superusers only",
            confirmed=True,
        )
        self.superuser = User.objects.create_superuser(
            "rpc_root", "rpc_root@example.com", "pw"
        )
        self.reverse = reverse

    def _url(self, model, kind, *args):
        return self.reverse(f"crush_admin:crush_lu_{model}_{kind}", args=args)

    def test_both_models_are_registered_and_render_with_rows(self):
        self.client.force_login(self.superuser)
        for model, pk in (
            ("connectrealpersoncheck", self.check.pk),
            ("connectrealpersonbadge", self.member.crushprofile.pk),
        ):
            changelist = self.client.get(self._url(model, "changelist"))
            self.assertEqual(changelist.status_code, 200, model)
            detail = self.client.get(self._url(model, "change", pk))
            self.assertEqual(detail.status_code, 200, model)

    def test_the_notes_are_visible_to_a_superuser(self):
        self.client.force_login(self.superuser)
        response = self.client.get(
            self._url("connectrealpersoncheck", "change", self.check.pk)
        )
        self.assertContains(response, "visible to superusers only")

    def test_staff_who_are_not_superusers_cannot_see_them(self):
        staff = User.objects.create_user(
            "rpc_staff", "rpc_staff@example.com", "pw", is_staff=True
        )
        self.client.force_login(staff)
        for model in ("connectrealpersoncheck", "connectrealpersonbadge"):
            self.assertEqual(
                self.client.get(self._url(model, "changelist")).status_code, 403
            )

    def test_nothing_can_be_added_changed_or_deleted(self):
        from crush_lu.admin import crush_admin_site

        request = type("R", (), {"user": self.superuser})()
        for model in (ConnectRealPersonCheck, ConnectRealPersonBadge):
            model_admin = crush_admin_site._registry[model]
            self.assertTrue(model_admin.has_view_permission(request))
            self.assertFalse(model_admin.has_add_permission(request))
            self.assertFalse(model_admin.has_change_permission(request))
            self.assertFalse(model_admin.has_delete_permission(request))
            editable = [f.name for f in model._meta.fields if f.name != "id"]
            self.assertTrue(
                set(editable) <= set(model_admin.get_readonly_fields(request))
            )

    def test_revoke_action_withdraws_the_check_and_badge(self):
        self.client.force_login(self.superuser)
        response = self.client.post(
            self._url("connectrealpersoncheck", "changelist"),
            {
                "action": "revoke_passed_checks",
                "_selected_action": [self.check.pk],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.check.refresh_from_db()
        self.assertEqual(self.check.status, "revoked")
        self.assertEqual(self.check.revoked_by_id, self.superuser.pk)
        self.assertFalse(ConnectRealPersonBadge.objects.exists())
        self.assertContains(response, "revoked")

    def test_revoke_action_skips_checks_that_are_not_passed(self):
        released = ConnectRealPersonCheck.objects.create(
            profile=profile_of(self.member), coach=self.coach, status="released"
        )
        self.client.force_login(self.superuser)
        self.client.post(
            self._url("connectrealpersoncheck", "changelist"),
            {"action": "revoke_passed_checks", "_selected_action": [released.pk]},
            follow=True,
        )
        released.refresh_from_db()
        self.assertEqual(released.status, "released")
        self.assertTrue(ConnectRealPersonBadge.objects.exists())
