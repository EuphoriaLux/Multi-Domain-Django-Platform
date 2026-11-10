"""January admission proves a human check, independently of a photo badge."""

import inspect
import json
from io import StringIO

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.management import call_command
from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from crush_lu.connect_phase import cycle_access_open
from crush_lu.models import ConnectRealPersonCheck, CrushConnectWaitlist, CrushProfile
from crush_lu.real_person_check_rules import (
    CONNECT_VIDEO_ADMISSION_POLICY_VERSION,
    CONNECT_VIDEO_PILOT_GROUP,
    PURPOSE_BADGE_ONLY,
    PURPOSE_CONNECT_WEEK,
    REQUIRED_CHECKLIST_KEYS,
)
from crush_lu.services.connect_verification import (
    grant_video_admission,
    has_valid_video_admission,
    human_verification_method,
    video_verification_requests_open,
)
from crush_lu.services.real_person_check import RealPersonCheckError, revoke_check
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
    set_photo,
)
from crush_lu.tests.test_crush_connect import _mark_attended
from crush_lu.tests.test_real_person_check_badge import _admin_save

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def rollout(settings):
    cache.clear()
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = True
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "public"
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = True


@pytest.fixture
def member():
    user = make_member("admission_member")
    CrushProfile.objects.filter(user=user).update(approved_at=timezone.now())
    return get_user_model().objects.get(pk=user.pk)


@pytest.fixture
def coach():
    return make_coach("admission_coach")


def passed_check(member, coach, **overrides):
    profile = profile_of(member)
    fields = {
        "profile": profile,
        "coach": coach,
        "purpose": PURPOSE_CONNECT_WEEK,
        "admission_policy_version": CONNECT_VIDEO_ADMISSION_POLICY_VERSION,
        "admission_profile_approved_at": profile.approved_at,
        "status": "passed",
        "medium": "video",
        "photo_key": profile.photo_1.name,
        "checklist": {key: True for key in REQUIRED_CHECKLIST_KEYS["video"]},
        "decided_at": timezone.now(),
    }
    fields.update(overrides)
    return ConnectRealPersonCheck.objects.create(**fields)


def grant(member, coach):
    check = passed_check(member, coach)
    assert grant_video_admission(coach, check.pk, confirmed=True)
    return check


def test_explicit_video_opens_week_and_preserves_luxid_method(member, coach):
    before = profile_of(member).verification_method
    assert not cycle_access_open(member)
    check = grant(member, coach)
    assert cycle_access_open(member)
    assert human_verification_method(member) == "video"
    assert profile_of(member).verification_method == before
    check.refresh_from_db()
    assert check.admission_granted_at is not None
    assert profile_of(member).real_person_badge == ""


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"purpose": PURPOSE_BADGE_ONLY}, "not_admission_video"),
        ({"medium": "voice"}, "not_admission_video"),
        ({"admission_policy_version": None}, "not_admission_video"),
        ({"decided_at": None}, "not_admission_video"),
        ({"photo_key": "another-photo.jpg"}, "stale_photo"),
        ({"checklist": {}}, "checklist_incomplete"),
    ],
)
def test_other_checks_cannot_be_promoted_to_admission(member, coach, changes, code):
    check = passed_check(member, coach, **changes)
    with pytest.raises(RealPersonCheckError) as exc:
        grant_video_admission(coach, check.pk, confirmed=True)
    assert exc.value.code == code
    assert not has_valid_video_admission(member)


def test_grant_requires_explicit_confirmation_and_owning_coach(member, coach):
    check = passed_check(member, coach)
    with pytest.raises(RealPersonCheckError, match="not_confirmed"):
        grant_video_admission(coach, check.pk)
    other = make_coach("admission_other")
    with pytest.raises(RealPersonCheckError, match="not_found"):
        grant_video_admission(other, check.pk, confirmed=True)


@pytest.mark.parametrize(
    "restriction", ["no_consent", "ban", "block", "report", "inactive_coach"]
)
def test_grant_rechecks_member_and_coach_permissions(member, coach, restriction):
    from crush_lu.models import UserBlock, UserDataConsent, UserReport

    check = passed_check(member, coach)
    if restriction == "no_consent":
        UserDataConsent.objects.filter(user=member).update(crushlu_consent_given=False)
    elif restriction == "ban":
        UserDataConsent.objects.filter(user=member).update(crushlu_banned=True)
    elif restriction == "block":
        UserBlock.objects.create(blocker=member, blocked=coach.user)
    elif restriction == "report":
        UserReport.objects.create(
            reporter=coach.user, reported_user=member, reason="fake_profile"
        )
    else:
        type(coach).objects.filter(pk=coach.pk).update(is_active=False)
    with pytest.raises(RealPersonCheckError):
        grant_video_admission(coach, check.pk, confirmed=True)
    assert not has_valid_video_admission(member)
    check.refresh_from_db()
    assert check.admission_granted_at is None


def test_public_requests_require_the_policy_to_be_enabled(member, settings):
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = False
    assert not video_verification_requests_open(member)
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "typo"
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = True
    assert not video_verification_requests_open(member)


def test_video_requires_luxid_at_grant_and_access_time(member, coach):
    check = passed_check(member, coach)
    SocialAccount.objects.filter(user=member).delete()
    with pytest.raises(RealPersonCheckError, match="verification_changed"):
        grant_video_admission(coach, check.pk, confirmed=True)
    SocialAccount.objects.create(user=member, provider="luxid", uid="admission_again")
    assert grant_video_admission(coach, check.pk, confirmed=True)
    SocialAccount.objects.filter(user=member).delete()
    assert not has_valid_video_admission(member)
    assert not cycle_access_open(member)


def test_photo_replacement_and_badge_flags_do_not_remove_admission(
    member, coach, settings
):
    check = grant(member, coach)
    set_photo(member, f"users/{member.pk}/photos/replacement.jpg")
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "off"
    assert has_valid_video_admission(member)
    assert cycle_access_open(member)
    assert grant_video_admission(coach, check.pk, confirmed=True) is False


def test_stopped_rollout_cannot_grant_a_new_check(member, coach, settings):
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "off"
    check = passed_check(member, coach)
    with pytest.raises(RealPersonCheckError, match="video_admission_closed"):
        grant_video_admission(coach, check.pk, confirmed=True)


def test_pilot_enrollment_is_not_approval_and_pause_keeps_the_policy(
    member, coach, settings
):
    settings.CRUSH_CONNECT_LAUNCHED = True
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = False
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "pilot"
    assert cycle_access_open(member)  # non-pilot: unchanged full-launch behavior
    assert not video_verification_requests_open(member)
    group = Group.objects.create(name=CONNECT_VIDEO_PILOT_GROUP)
    member.groups.add(group)
    assert video_verification_requests_open(member)
    assert not cycle_access_open(member)
    grant(member, coach)
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "off"
    assert cycle_access_open(member)
    other = make_member("pilot_without_approval")
    other.groups.add(group)
    assert not cycle_access_open(other)


def test_full_launch_and_phase_flags_do_not_replace_human_evidence(
    member, coach, settings
):
    settings.CRUSH_CONNECT_LAUNCHED = True
    assert not cycle_access_open(member)
    grant(member, coach)
    assert cycle_access_open(member)
    settings.CRUSH_CONNECT_LAUNCHED = False
    settings.CRUSH_CONNECT_CANDIDATE_OPEN = False
    assert not cycle_access_open(member)


def test_default_off_retains_existing_launch_and_event_access(member, settings):
    settings.CONNECT_VIDEO_VERIFICATION_MODE = "off"
    settings.CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED = False
    assert not cycle_access_open(member)
    settings.CRUSH_CONNECT_LAUNCHED = True
    assert cycle_access_open(member)
    assert cycle_access_open(AnonymousUser())  # original phase helper contract
    settings.CRUSH_CONNECT_LAUNCHED = False
    _mark_attended(member)
    assert cycle_access_open(member)


def test_event_route_does_not_require_luxid_or_video(member):
    SocialAccount.objects.filter(user=member).delete()
    _mark_attended(member)
    assert cycle_access_open(member)
    assert human_verification_method(member) == "event"


@pytest.mark.urls("azureproject.urls_crush")
def test_luxid_only_member_keeps_incoming_requests_while_week_is_locked(member, client):
    assert not cycle_access_open(member)
    client.force_login(member)
    response = client.get("/en/crush-connect/week/inbox/", HTTP_HOST="crush.lu")
    assert response.status_code == 200


def test_staff_and_explicit_tester_exceptions_remain(member):
    CrushConnectWaitlist.objects.create(user=member, selected_as_tester=True)
    assert cycle_access_open(get_user_model().objects.get(pk=member.pk))
    member.is_staff = True
    assert cycle_access_open(member)


@pytest.mark.parametrize("phone_change", [False, True])
def test_admin_reverification_cannot_resurrect_an_approval(member, coach, phone_change):
    check = grant(member, coach)
    original_epoch = profile_of(member).approved_at
    admin = get_user_model().objects.create_superuser(
        "admission_root", "root@example.com", "pw"
    )
    fields = {"is_approved": False, "verification_status": "rejected"}
    if phone_change:
        CrushProfile.objects.filter(user=member).update(
            phone_number="+352621000111", phone_verified=True
        )
        fields["phone_number"] = "+352621000222"
    _admin_save(admin, profile_of(member), list(fields), **fields)
    _admin_save(
        admin,
        profile_of(member),
        ["is_approved", "verification_status"],
        is_approved=True,
        verification_status="verified",
    )
    assert profile_of(member).approved_at == original_epoch
    assert not has_valid_video_admission(member)
    check.refresh_from_db()
    assert check.admission_invalidated_at is not None
    assert check.status == "passed"  # the original decision remains history


def test_deverification_and_invalidation_roll_back_together(member, coach, monkeypatch):
    grant(member, coach)

    def fail(*args, **kwargs):
        raise RuntimeError("failed invalidation")

    monkeypatch.setattr(
        "crush_lu.services.connect_verification.invalidate_video_admissions", fail
    )
    profile = profile_of(member)
    profile.is_approved = False
    profile.verification_status = "pending"
    with pytest.raises(RuntimeError, match="failed invalidation"):
        profile.save()
    assert profile_of(member).verification_status == "verified"
    assert has_valid_video_admission(member)


@pytest.mark.parametrize("door", [False, True])
def test_queryset_deverification_invalidates_evidence(member, coach, door):
    from crush_lu.services.profile_verification import (
        reject_door_verification,
        transition_unverified_profile,
    )

    check = grant(member, coach)
    profile = profile_of(member)
    if door:
        assert reject_door_verification(profile)
    else:
        assert transition_unverified_profile(
            profile, target_status="pending", transition_from=("verified",)
        )
    check.refresh_from_db()
    assert check.admission_invalidated_at is not None
    assert not has_valid_video_admission(member)


def test_revoke_ends_video_access(member, coach):
    check = grant(member, coach)
    admin = get_user_model().objects.create_superuser(
        "revoke_root", "revoke@example.com", "pw"
    )
    revoke_check(admin, check.pk)
    assert not has_valid_video_admission(member)
    check.refresh_from_db()
    assert check.admission_invalidated_at is not None


def test_old_release_status_writes_cannot_revive_a_video_approval(member, coach):
    """Old binaries do not know the Python admission invalidation service."""
    check = grant(member, coach)
    epoch = profile_of(member).approved_at
    CrushProfile.objects.filter(user=member).update(
        verification_status="pending", is_approved=False
    )
    CrushProfile.objects.filter(user=member).update(
        verification_status="verified", is_approved=True
    )
    assert profile_of(member).approved_at == epoch
    check.refresh_from_db()
    assert check.admission_invalidated_at is not None
    assert not has_valid_video_admission(member)


def test_database_rejects_admission_without_a_video_decision(member, coach):
    with pytest.raises(IntegrityError), transaction.atomic():
        passed_check(member, coach, medium="voice", admission_granted_at=timezone.now())


def test_previous_release_insert_uses_database_defaults_and_deletion_still_works(
    member, coach
):
    """Simulate an old model which omits every new column in its INSERT."""
    old = ConnectRealPersonCheck(profile=profile_of(member), coach=coach)
    new_columns = {
        "purpose",
        "admission_policy_version",
        "admission_profile_approved_at",
        "admission_granted_at",
        "admission_invalidated_at",
    }
    fields = [
        f
        for f in old._meta.local_fields
        if not f.primary_key and f.name not in new_columns
    ]
    columns = ", ".join(connection.ops.quote_name(f.column) for f in fields)
    values = [f.get_db_prep_save(f.pre_save(old, add=True), connection) for f in fields]
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {connection.ops.quote_name(old._meta.db_table)} ({columns}) "
            f"VALUES ({', '.join(['%s'] * len(values))})",
            values,
        )
    stored = ConnectRealPersonCheck.objects.get(profile=old.profile)
    assert stored.purpose == PURPOSE_BADGE_ONLY
    assert stored.admission_granted_at is None
    assert not has_valid_video_admission(member)
    old.profile.delete()
    assert not ConnectRealPersonCheck.objects.filter(pk=stored.pk).exists()


def test_aggregate_audit_is_read_only_and_contains_no_member_identifiers(member, coach):
    grant(member, coach)
    output = StringIO()
    with CaptureQueriesContext(connection) as queries:
        call_command("audit_connect_verification", "--json", stdout=output)
    result = json.loads(output.getvalue())
    assert result["video_qualified"] == 1
    assert result["without_human_verification"] == 0
    assert member.email not in output.getvalue()
    assert member.username not in output.getvalue()
    assert not any(
        q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for q in queries
    )


def test_grant_lock_order_matches_real_person_decisions():
    source = inspect.getsource(grant_video_admission)
    assert source.index("_lock_profile(") < source.index("_lock_check(")
