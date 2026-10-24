"""GDPR handling of the Connect real-person check: export, erasure, retention.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

import json
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from crush_lu.models import (
    AccountMerge,
    CallAttempt,
    ConnectRealPersonBadge,
    ConnectRealPersonCheck,
    CrushProfile,
)
from crush_lu.real_person_check_rules import REQUIRED_CHECKLIST_KEYS
from crush_lu.services.real_person_check import (
    claim_check,
    decide_check,
    revoke_check,
)
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
)
from crush_lu.views_account import (
    ACCOUNT_ERASURE_RETAINED,
    ACCOUNT_EXPORT_REAL_PERSON_CHECK_EXCLUDED,
    export_user_data,
)

User = get_user_model()

pytestmark = pytest.mark.django_db

VOICE = {key: True for key in REQUIRED_CHECKLIST_KEYS["voice"]}
EXCLUDED = ACCOUNT_EXPORT_REAL_PERSON_CHECK_EXCLUDED


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def coach():
    return make_coach("rpc_gdpr_coach")


@pytest.fixture
def member():
    return make_member("rpc_gdpr_member")


def _export(rf, user):
    request = rf.get("/")
    request.user = User.objects.get(pk=user.pk)
    return json.loads(export_user_data(request).content)


def _check(profile, coach, status, **fields):
    return ConnectRealPersonCheck.objects.create(
        profile=profile, coach=coach, status=status, **fields
    )


def _names(model):
    return [f.name for f in model._meta.concrete_fields] + [
        f.name for f in model._meta.many_to_many
    ]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_a_member_without_checks_gets_an_empty_section(rf, member):
    block = _export(rf, member)["real_person_check"]
    assert block == {
        "passed_checks": [],
        "declined_checks": [],
        "open_checks": [],
        "badge": None,
    }


def test_the_export_shows_the_members_own_part(rf, settings, coach, member):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    profile = profile_of(member)
    declined_at = timezone.now() - timedelta(days=200)
    _check(profile, coach, "declined", decided_at=declined_at)
    check = claim_check(coach, profile.pk)
    decide_check(coach, check.pk, "pass_voice", checklist=VOICE, confirmed=True)

    block = _export(rf, member)["real_person_check"]
    assert block["passed_checks"] == [
        {
            "medium": "Voice call",
            "decided_at": ConnectRealPersonCheck.objects.get(
                pk=check.pk
            ).decided_at.isoformat(),
        }
    ]
    assert block["declined_checks"] == [{"decided_at": declined_at.isoformat()}]
    assert block["open_checks"] == []
    badge = ConnectRealPersonBadge.objects.get(profile=profile)
    assert block["badge"] == {
        "medium": "Voice call",
        "granted_at": badge.granted_at.isoformat(),
        "shown_now": True,
    }
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    assert _export(rf, member)["real_person_check"]["badge"]["shown_now"] is False


def test_an_open_check_shows_when_it_was_claimed(rf, coach, member):
    check = claim_check(coach, profile_of(member).pk)
    block = _export(rf, member)["real_person_check"]
    assert block["open_checks"] == [{"claimed_at": check.claimed_at.isoformat()}]


def test_nothing_staff_wrote_or_decided_is_exported(rf, coach, member):
    profile = profile_of(member)
    secret = "SECRET-COACH-NOTE"
    reason = "SECRET-REVOKE-REASON"
    now = timezone.now()
    _check(profile, coach, "released", notes=secret)
    _check(profile, coach, "unreachable", notes=secret, decided_at=now)
    _check(profile, coach, "suspected_fake", notes=secret, decided_at=now)
    revoked = _check(
        profile,
        coach,
        "revoked",
        notes=secret,
        medium="voice",
        decided_at=now,
        revoked_at=now,
        revoke_reason=reason,
    )
    passed = _check(
        profile,
        coach,
        "passed",
        notes=secret,
        medium="video",
        photo_key="users/1/photos/SECRET-KEY.jpg",
        checklist={"face_matches_photo": True},
        decided_at=now,
    )
    ConnectRealPersonBadge.objects.create(
        profile=profile,
        source_check=passed,
        medium="video",
        photo_key="users/1/photos/SECRET-KEY.jpg",
    )

    data = _export(rf, member)
    dump = json.dumps(data, ensure_ascii=False)
    for forbidden in (
        secret,
        reason,
        "SECRET-KEY",
        "face_matches_photo",
        coach.user.username,
        coach.user.email,
        coach.user.first_name,
    ):
        assert forbidden not in dump, forbidden
    block = data["real_person_check"]
    # Only the passed check is a "passed check"; the staff-side outcomes are not
    # exported as rows at all.
    assert len(block["passed_checks"]) == 1
    assert block["declined_checks"] == []
    assert block["open_checks"] == []
    assert revoked.revoked_at  # exists, just not exported


def test_the_export_registry_covers_every_field_and_status(rf, settings, coach, member):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    profile = profile_of(member)
    now = timezone.now()
    _check(profile, coach, "declined", decided_at=now)
    _check(profile, coach, "released")
    passed = _check(
        profile,
        coach,
        "passed",
        medium="voice",
        decided_at=now,
    )
    ConnectRealPersonBadge.objects.create(
        profile=profile, source_check=passed, medium="voice"
    )
    open_member = make_member("rpc_gdpr_open")
    claim_check(coach, profile_of(open_member).pk)

    block = _export(rf, member)["real_person_check"]
    open_block = _export(rf, open_member)["real_person_check"]
    exported_check_keys = {
        key
        for row in block["passed_checks"]
        + block["declined_checks"]
        + open_block["open_checks"]
        for key in row
    }
    exported_badge_keys = set(block["badge"])
    assert exported_check_keys and exported_badge_keys

    check_excluded = EXCLUDED["check_fields"]
    badge_excluded = EXCLUDED["badge_fields"]
    missing_check = sorted(
        name
        for name in _names(ConnectRealPersonCheck)
        if name not in exported_check_keys and name not in check_excluded
    )
    missing_badge = sorted(
        name
        for name in _names(ConnectRealPersonBadge)
        if name not in exported_badge_keys and name not in badge_excluded
    )
    assert missing_check == [], (
        "Export these ConnectRealPersonCheck fields or classify them in "
        "ACCOUNT_EXPORT_REAL_PERSON_CHECK_EXCLUDED (crush_lu/views_account.py)"
    )
    assert missing_badge == [], (
        "Export these ConnectRealPersonBadge fields or classify them in "
        "ACCOUNT_EXPORT_REAL_PERSON_CHECK_EXCLUDED (crush_lu/views_account.py)"
    )
    # The registry names real fields, gives a reason, and never contradicts the
    # export (a field cannot be both exported and excluded).
    assert set(check_excluded) <= set(_names(ConnectRealPersonCheck))
    assert set(badge_excluded) <= set(_names(ConnectRealPersonBadge))
    assert not set(check_excluded) & exported_check_keys
    assert not set(badge_excluded) & exported_badge_keys
    assert all(reason for reason in {**check_excluded, **badge_excluded}.values())

    # Every status is exported as a list or classified as not exported.
    exported_statuses = {"passed", "declined", "open"}
    all_statuses = {value for value, _label in ConnectRealPersonCheck.STATUS_CHOICES}
    status_excluded = EXCLUDED["statuses"]
    assert all_statuses - exported_statuses - set(status_excluded) == set()
    assert set(status_excluded) <= all_statuses
    assert not set(status_excluded) & exported_statuses
    assert all(status_excluded.values())


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


def _checked_member(name, coach):
    member = make_member(name)
    profile = profile_of(member)
    check = claim_check(coach, profile.pk)
    decide_check(coach, check.pk, "pass_voice", checklist=VOICE, confirmed=True)
    return member, profile.pk, check.pk


def test_the_registry_retains_only_the_staff_actor_reference():
    assert "ConnectRealPersonCheck" in ACCOUNT_ERASURE_RETAINED
    assert "ConnectRealPersonBadge" not in ACCOUNT_ERASURE_RETAINED  # no User FK
    assert "revoked_by" in ACCOUNT_ERASURE_RETAINED["ConnectRealPersonCheck"]


@patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
def test_erasing_the_profile_removes_checks_badge_and_attempts(_storage, coach):
    from crush_lu.views_account import delete_crushlu_profile_only

    member, profile_pk, check_pk = _checked_member("rpc_erase_member", coach)
    bystander, bystander_pk, bystander_check = _checked_member("rpc_bystander", coach)
    assert CallAttempt.objects.filter(real_person_check_id=check_pk).exists()

    delete_crushlu_profile_only(member)

    assert not ConnectRealPersonCheck.objects.filter(profile_id=profile_pk).exists()
    assert not ConnectRealPersonBadge.objects.filter(profile_id=profile_pk).exists()
    assert not CallAttempt.objects.filter(real_person_check_id=check_pk).exists()
    # Somebody else's check and badge are untouched.
    assert ConnectRealPersonCheck.objects.filter(pk=bystander_check).exists()
    assert ConnectRealPersonBadge.objects.filter(profile_id=bystander_pk).exists()


@patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
def test_erasing_the_keeper_also_erases_a_merged_duplicates_checks(_storage, coach):
    from crush_lu.views_account import delete_crushlu_profile_only

    keeper = User.objects.create_user("keeper@example.com", "keeper@example.com", "x")
    CrushProfile.objects.create(
        user=keeper,
        date_of_birth=date(1995, 5, 15),
        gender="M",
        location="Luxembourg",
        is_approved=True,
    )
    duplicate, dup_profile_pk, dup_check_pk = _checked_member("rpc_dup_member", coach)
    duplicate.is_active = False
    duplicate.save(update_fields=["is_active"])
    AccountMerge.objects.create(duplicate=duplicate, keeper=keeper)

    delete_crushlu_profile_only(keeper)

    assert not ConnectRealPersonCheck.objects.filter(pk=dup_check_pk).exists()
    assert not ConnectRealPersonBadge.objects.filter(profile_id=dup_profile_pk).exists()


@patch("crush_lu.storage.delete_user_storage", return_value=(True, 0))
def test_erasing_the_revoking_staff_account_keeps_the_members_check(_storage, coach):
    from crush_lu.views import delete_full_account

    member, profile_pk, check_pk = _checked_member("rpc_revoked_member", coach)
    admin = User.objects.create_superuser("rpc_root", "rpc_root@example.com", "pw")
    revoke_check(admin, check_pk, "audit")

    delete_full_account(admin)

    check = ConnectRealPersonCheck.objects.get(pk=check_pk)
    assert check.status == "revoked"  # the member's record stays, actor anonymised
    assert check.revoked_by_id == admin.pk


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


@pytest.fixture
def retention_rows(coach):
    now = timezone.now()
    old = _check(
        profile_of(make_member("rpc_ret_old")),
        coach,
        "passed",
        medium="voice",
        decided_at=now - timedelta(days=400),
        notes="old note",
        checklist={"spoke_live": True},
    )
    recent = _check(
        profile_of(make_member("rpc_ret_recent")),
        coach,
        "passed",
        medium="voice",
        decided_at=now - timedelta(days=10),
        notes="recent note",
        checklist={"spoke_live": True},
    )
    # No decision: aged by its last lease, not by its claim.
    lapsed_release = _check(
        profile_of(make_member("rpc_ret_released")),
        coach,
        "released",
        claimed_at=now - timedelta(days=401),
        lease_expires_at=now - timedelta(days=400),
        notes="released note",
    )
    already_blank = _check(
        profile_of(make_member("rpc_ret_blank")),
        coach,
        "declined",
        decided_at=now - timedelta(days=400),
    )
    lapsed_open = _check(
        profile_of(make_member("rpc_ret_lapsed")),
        coach,
        "open",
        claimed_at=now - timedelta(days=5),
        lease_expires_at=now - timedelta(hours=1),
        notes="working note",
    )
    live_open = _check(
        profile_of(make_member("rpc_ret_live")),
        coach,
        "open",
        claimed_at=now - timedelta(hours=1),
        lease_expires_at=now + timedelta(hours=71),
    )
    return {
        "old": old,
        "recent": recent,
        "lapsed_release": lapsed_release,
        "already_blank": already_blank,
        "lapsed_open": lapsed_open,
        "live_open": live_open,
    }


def _reload(rows):
    return {
        name: ConnectRealPersonCheck.objects.get(pk=r.pk) for name, r in rows.items()
    }


def test_a_dry_run_reports_and_changes_nothing(retention_rows):
    out = StringIO()
    call_command("gdpr_retention_cleanup", stdout=out)
    text = out.getvalue()
    assert "Lapsed real-person claims: 1 row(s)" in text
    # The old pass and the lapsed release hold notes; the blank decline does not.
    assert "Real-person check notes older than 365d: 2 row(s)" in text
    rows = _reload(retention_rows)
    assert rows["old"].notes == "old note"
    assert rows["lapsed_open"].status == "open"


def test_apply_blanks_old_notes_and_closes_lapsed_claims(retention_rows):
    call_command("gdpr_retention_cleanup", apply=True, stdout=StringIO())
    rows = _reload(retention_rows)

    # Blanked, but the outcome row stays.
    assert (rows["old"].notes, rows["old"].checklist) == ("", {})
    assert rows["old"].status == "passed"
    assert rows["old"].decided_at is not None
    assert rows["lapsed_release"].notes == ""
    # Newer rows keep their notes.
    assert rows["recent"].notes == "recent note"
    assert rows["recent"].checklist == {"spoke_live": True}
    assert rows["lapsed_open"].notes == "working note"
    # A lapsed claim is closed; a live one is left alone.
    assert rows["lapsed_open"].status == "released"
    assert rows["live_open"].status == "open"


def test_the_sweep_is_idempotent(retention_rows):
    call_command("gdpr_retention_cleanup", apply=True, stdout=StringIO())
    out = StringIO()
    call_command("gdpr_retention_cleanup", stdout=out)
    assert "Lapsed real-person claims: 0 row(s)" in out.getvalue()
    assert "Real-person check notes older than 365d: 0 row(s)" in out.getvalue()


def test_the_window_can_be_overridden(retention_rows):
    call_command(
        "gdpr_retention_cleanup",
        apply=True,
        real_person_check_notes_days=5,
        stdout=StringIO(),
    )
    rows = _reload(retention_rows)
    assert rows["recent"].notes == ""
    assert rows["live_open"].status == "open"


def test_a_negative_window_is_refused(retention_rows):
    with pytest.raises(CommandError):
        call_command(
            "gdpr_retention_cleanup",
            apply=True,
            real_person_check_notes_days=-1,
            stdout=StringIO(),
        )
    assert _reload(retention_rows)["old"].notes == "old note"


def test_the_default_window_is_a_year():
    from crush_lu.management.commands.gdpr_retention_cleanup import DEFAULT_RETENTION

    assert DEFAULT_RETENTION["real_person_check_notes_days"] == 365
