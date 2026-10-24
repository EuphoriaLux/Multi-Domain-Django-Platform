"""CrushProfile.real_person_badge and its photo bookkeeping.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase
from django.utils import timezone

from crush_lu.models import (
    ConnectRealPersonBadge,
    ConnectRealPersonCheck,
    CrushProfile,
)
from crush_lu.real_person_check_rules import REQUIRED_CHECKLIST_KEYS
from crush_lu.services.real_person_check import (
    claim_check,
    decide_check,
    real_person_badge_visible,
    real_person_check_enabled,
)
from crush_lu.tests.real_person_check_helpers import (
    make_coach,
    make_member,
    profile_of,
    set_photo,
)

pytestmark = pytest.mark.django_db

VOICE = {key: True for key in REQUIRED_CHECKLIST_KEYS["voice"]}
VIDEO = {key: True for key in REQUIRED_CHECKLIST_KEYS["video"]}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def flags_on(settings):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True


@pytest.fixture
def coach():
    return make_coach("rpc_badge_coach")


@pytest.fixture
def member():
    return make_member("rpc_badge_member")


def _grant(coach, member, outcome):
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


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_both_flags_default_to_off(settings):
    from django.conf import settings as live

    # The code defaults, not whatever the environment of this run sets.
    assert hasattr(live, "CONNECT_REAL_PERSON_CHECK_ENABLED")
    assert hasattr(live, "CONNECT_REAL_PERSON_BADGE_VISIBLE")
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    assert not real_person_check_enabled()
    assert not real_person_badge_visible()


def test_the_badge_flag_alone_shows_nothing(settings):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    assert not real_person_check_enabled()
    assert not real_person_badge_visible()
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    assert real_person_check_enabled()
    assert not real_person_badge_visible()
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    assert real_person_badge_visible()


def test_the_template_context_exposes_both_flags(settings, rf):
    from crush_lu.context_processors import crush_user_context

    request = rf.get("/", HTTP_HOST="crush.lu")
    request.user = type("Anon", (), {"is_authenticated": False})()
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    context = crush_user_context(request)
    assert context["connect_real_person_check_enabled"] is True
    assert context["connect_real_person_badge_visible"] is False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    assert crush_user_context(request)["connect_real_person_badge_visible"] is True
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    context = crush_user_context(request)
    assert context["connect_real_person_check_enabled"] is False
    assert context["connect_real_person_badge_visible"] is False


# ---------------------------------------------------------------------------
# The property
# ---------------------------------------------------------------------------


def test_no_badge_row_means_no_badge(flags_on, member):
    assert profile_of(member).real_person_badge == ""


def test_a_voice_pass_shows_voice(flags_on, coach, member):
    _grant(coach, member, "pass_voice")
    assert profile_of(member).real_person_badge == "voice"


def test_a_video_pass_shows_video(flags_on, coach, member):
    _grant(coach, member, "pass_video")
    assert profile_of(member).real_person_badge == "video"


def test_replacing_the_photo_downgrades_video_to_voice_and_back(
    flags_on, coach, member
):
    _grant(coach, member, "pass_video")
    compared = profile_of(member).photo_1.name
    set_photo(member, "users/777/photos/replacement.jpg")
    assert profile_of(member).real_person_badge == "voice"
    set_photo(member, compared)
    assert profile_of(member).real_person_badge == "video"


def test_a_voice_pass_never_shows_as_video(flags_on, coach, member):
    _grant(coach, member, "pass_voice")
    badge = ConnectRealPersonBadge.objects.get(profile__user=member)
    assert badge.photo_key == ""
    assert profile_of(member).real_person_badge == "voice"


def test_a_changed_approval_ends_the_badge(flags_on, coach, member):
    """De-verification and re-verification both move approved_at."""
    _grant(coach, member, "pass_video")
    CrushProfile.objects.filter(user=member).update(approved_at=timezone.now())
    assert profile_of(member).real_person_badge == ""


def test_the_badge_is_valid_only_while_the_profile_is_verified_and_active(
    flags_on, coach, member
):
    _grant(coach, member, "pass_voice")
    assert profile_of(member).real_person_badge == "voice"
    CrushProfile.objects.filter(user=member).update(verification_status="pending")
    assert profile_of(member).real_person_badge == ""
    CrushProfile.objects.filter(user=member).update(verification_status="verified")
    assert profile_of(member).real_person_badge == "voice"
    CrushProfile.objects.filter(user=member).update(is_active=False)
    assert profile_of(member).real_person_badge == ""
    CrushProfile.objects.filter(user=member).update(is_active=True)
    member.__class__.objects.filter(pk=member.pk).update(is_active=False)
    assert profile_of(member).real_person_badge == ""


def test_the_in_person_badge_takes_precedence(flags_on, coach, member):
    _grant(coach, member, "pass_video")
    profile = profile_of(member)
    CrushProfile.objects.filter(pk=profile.pk).update(
        photo_verification_key=profile.photo_1.name
    )
    assert profile_of(member).real_person_badge == "in_person"


def test_in_person_shows_even_with_the_flags_off(settings, member):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = False
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = False
    profile = profile_of(member)
    CrushProfile.objects.filter(pk=profile.pk).update(
        photo_verification_key=profile.photo_1.name
    )
    assert profile_of(member).real_person_badge == "in_person"


def test_flags_off_hides_a_granted_badge_without_a_query(
    settings, django_assert_num_queries, coach, member
):
    settings.CONNECT_REAL_PERSON_CHECK_ENABLED = True
    settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = True
    _grant(coach, member, "pass_video")
    profile = profile_of(member)  # loads the user too
    for check_flag, badge_flag in ((False, False), (True, False), (False, True)):
        settings.CONNECT_REAL_PERSON_CHECK_ENABLED = check_flag
        settings.CONNECT_REAL_PERSON_BADGE_VISIBLE = badge_flag
        with django_assert_num_queries(0):
            assert profile.real_person_badge == ""


def test_with_the_flags_on_the_cost_is_constant(
    flags_on, django_assert_num_queries, coach, member
):
    _grant(coach, member, "pass_video")
    # Loaded the way the card querysets will: badge and user in the one query.
    profile = CrushProfile.objects.select_related(
        "user", "connect_real_person_badge"
    ).get(user=member)
    with django_assert_num_queries(0):
        assert profile.real_person_badge == "video"
    plain = make_member("rpc_badge_plain")
    unbadged = CrushProfile.objects.select_related(
        "user", "connect_real_person_badge"
    ).get(user=plain)
    with django_assert_num_queries(0):
        assert unbadged.real_person_badge == ""
    # Without select_related it is one query for the badge, never one per call.
    lazy = profile_of(member)
    with django_assert_num_queries(1):
        assert lazy.real_person_badge == "video"
        assert lazy.real_person_badge == "video"


def test_a_revoked_badge_disappears(flags_on, coach, member):
    from django.contrib.auth import get_user_model

    from crush_lu.services.real_person_check import revoke_check

    check = _grant(coach, member, "pass_voice")
    assert profile_of(member).real_person_badge == "voice"
    admin = get_user_model().objects.create_superuser("rpc_root", "r@example.com", "pw")
    revoke_check(admin, check.pk)
    assert profile_of(member).real_person_badge == ""


# ---------------------------------------------------------------------------
# A de-verification ends the badge for good, even where approved_at stays put
# ---------------------------------------------------------------------------


def _admin_save(admin_user, profile, changed, **fields):
    """What the Django admin change form does: mutate the loaded row, save_model.

    ``approved_at`` is read-only on that form, so it is never in ``changed``
    and never moves. The form is a stub: ``save_model`` reads only
    ``changed_data``.
    """
    from types import SimpleNamespace

    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    from crush_lu.admin.profiles import CrushProfileAdmin
    from crush_lu.admin.site import crush_admin_site

    request = RequestFactory().post("/crush-admin/")
    request.user = admin_user
    request.session = {}
    request._messages = FallbackStorage(request)
    obj = CrushProfile.objects.get(pk=profile.pk)
    for name, value in fields.items():
        setattr(obj, name, value)
    CrushProfileAdmin(CrushProfile, crush_admin_site).save_model(
        request, obj, SimpleNamespace(changed_data=list(changed)), change=True
    )


@pytest.fixture
def superuser():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_superuser(
        "rpc_badge_root", "rpc_badge_root@example.com", "pw12345678"
    )


@pytest.mark.parametrize("outcome", ["pass_voice", "pass_video"])
def test_ticking_a_member_verified_again_in_the_admin_form_revives_no_badge(
    flags_on, coach, member, superuser, outcome
):
    """The form moves is_approved and verification_status but never approved_at.

    The badge's approved_at snapshot therefore still matches after a
    de-verify and re-verify made there, so the old badge came back with no
    new call.
    """
    from crush_lu.services.real_person_check import real_person_check_queue

    _grant(coach, member, outcome)
    granted_at = profile_of(member).approved_at
    assert profile_of(member).real_person_badge == outcome.replace("pass_", "")

    _admin_save(
        superuser,
        profile_of(member),
        ["is_approved", "verification_status"],
        is_approved=False,
        verification_status="rejected",
    )
    assert profile_of(member).real_person_badge == ""
    assert not ConnectRealPersonBadge.objects.filter(profile__user=member).exists()

    _admin_save(
        superuser,
        profile_of(member),
        ["is_approved", "verification_status"],
        is_approved=True,
        verification_status="verified",
    )
    profile = profile_of(member)
    assert profile.verification_status == "verified"
    # The precondition that made the bug: nothing moved the snapshot's anchor.
    assert profile.approved_at == granted_at
    assert profile.real_person_badge == ""
    # And the member is a candidate for a coach again.
    assert member.pk in set(real_person_check_queue(coach).values_list("pk", flat=True))


def test_a_de_verifying_save_deletes_the_badge_row_but_keeps_the_history(
    flags_on, coach, member
):
    check = _grant(coach, member, "pass_voice")
    profile = profile_of(member)
    profile.verification_status = "pending"
    profile.is_approved = False
    profile.save()

    assert not ConnectRealPersonBadge.objects.filter(profile=profile).exists()
    check.refresh_from_db()
    assert check.status == ConnectRealPersonCheck.STATUS_PASSED

    profile = profile_of(member)
    profile.verification_status = "verified"
    profile.save()
    assert profile_of(member).real_person_badge == ""


def test_the_admin_phone_correction_path_also_ends_the_badge(
    flags_on, coach, member, superuser
):
    """save_model's phone branch bypasses CrushProfile.save() entirely."""
    _grant(coach, member, "pass_voice")
    CrushProfile.objects.filter(user=member).update(
        phone_number="+352621000111", phone_verified=True
    )

    _admin_save(
        superuser,
        profile_of(member),
        ["phone_number", "verification_status", "is_approved"],
        phone_number="+352621000222",
        is_approved=False,
        verification_status="rejected",
    )
    assert profile_of(member).phone_number == "+352621000222"
    assert profile_of(member).verification_status == "rejected"
    assert not ConnectRealPersonBadge.objects.filter(profile__user=member).exists()

    _admin_save(
        superuser,
        profile_of(member),
        ["verification_status", "is_approved"],
        is_approved=True,
        verification_status="verified",
    )
    assert profile_of(member).real_person_badge == ""


def test_saves_that_keep_the_profile_verified_keep_the_badge(flags_on, coach, member):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _grant(coach, member, "pass_voice")
    profile = profile_of(member)
    profile.bio = "A new bio"
    with CaptureQueriesContext(connection) as queries:
        profile.save()
        profile.save(update_fields=["bio"])
    assert not [
        q for q in queries.captured_queries if "connectrealpersonbadge" in q["sql"]
    ]
    assert profile_of(member).real_person_badge == "voice"


def test_a_save_that_does_not_write_the_status_leaves_the_badge(
    flags_on, coach, member
):
    _grant(coach, member, "pass_voice")
    profile = profile_of(member)
    profile.verification_status = "pending"  # in memory only
    profile.save(update_fields=["bio"])

    assert profile_of(member).verification_status == "verified"
    assert profile_of(member).real_person_badge == "voice"


def test_a_stale_instance_cannot_end_a_badge_it_did_not_de_verify(
    flags_on, coach, member
):
    """A bare save of an instance loaded before the profile was verified."""
    stale = profile_of(member)  # loaded before the badge existed
    _grant(coach, member, "pass_voice")
    stale.bio = "Edited while a coach was calling"
    stale.save()

    assert profile_of(member).real_person_badge == "voice"


# ---------------------------------------------------------------------------
# reprocess_photos follows the key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slot", ["photo_1", "photo_2"])
def test_reprocessing_moves_the_badge_and_the_checks_to_the_new_key(
    tmp_path, monkeypatch, flags_on, slot
):
    """Same image under a new key is not a replacement: a video badge survives."""
    from crush_lu.management.commands.reprocess_photos import Command
    from crush_lu.tests.test_coach_photo_review import _make_candidate

    storage = FileSystemStorage(location=tmp_path)
    monkeypatch.setattr(CrushProfile._meta.get_field(slot), "storage", storage)
    profile = _make_candidate(username="rpc_reprocess", has_photo=False)
    getattr(profile, slot).save("original.jpg", ContentFile(b"original" * 1000))
    old_key = getattr(profile, slot).name
    coach = make_coach("rpc_reprocess_coach")
    passed = ConnectRealPersonCheck.objects.create(
        profile=profile,
        coach=coach,
        status="passed",
        medium="video",
        photo_key=old_key,
        decided_at=timezone.now(),
    )
    unrelated = ConnectRealPersonCheck.objects.create(
        profile=profile,
        coach=coach,
        status="released",
        photo_key="users/0/photos/some-other-file.jpg",
    )
    ConnectRealPersonBadge.objects.create(
        profile=profile,
        source_check=passed,
        medium="video",
        photo_key=old_key,
        profile_approved_at=profile.approved_at,
    )
    monkeypatch.setattr(
        "crush_lu.management.commands.reprocess_photos.process_uploaded_image",
        lambda *args: ContentFile(b"small", name="processed.jpg"),
    )
    stats = {"processed": 0, "skipped": 0, "errors": 0}
    with TestCase.captureOnCommitCallbacks(execute=True):
        Command()._process_photo(profile, slot, getattr(profile, slot), False, stats)

    assert stats == {"processed": 1, "skipped": 0, "errors": 0}
    profile = CrushProfile.objects.select_related("user").get(pk=profile.pk)
    new_key = getattr(profile, slot).name
    assert new_key != old_key
    assert ConnectRealPersonBadge.objects.get(profile=profile).photo_key == new_key
    passed.refresh_from_db()
    unrelated.refresh_from_db()
    assert passed.photo_key == new_key
    assert unrelated.photo_key == "users/0/photos/some-other-file.jpg"
    if slot == "photo_1":
        assert profile.real_person_badge == "video"


def test_a_skipped_reprocess_leaves_the_badge_alone(tmp_path, monkeypatch, flags_on):
    from crush_lu.management.commands.reprocess_photos import Command
    from crush_lu.tests.test_coach_photo_review import _make_candidate

    storage = FileSystemStorage(location=tmp_path)
    monkeypatch.setattr(CrushProfile._meta.get_field("photo_1"), "storage", storage)
    profile = _make_candidate(username="rpc_reprocess_skip", has_photo=False)
    profile.photo_1.save("original.jpg", ContentFile(b"original" * 1000))
    old_key = profile.photo_1.name
    coach = make_coach("rpc_reprocess_coach")
    passed = ConnectRealPersonCheck.objects.create(
        profile=profile,
        coach=coach,
        status="passed",
        medium="video",
        photo_key=old_key,
        decided_at=timezone.now() - timedelta(days=1),
    )
    ConnectRealPersonBadge.objects.create(
        profile=profile,
        source_check=passed,
        medium="video",
        photo_key=old_key,
        profile_approved_at=profile.approved_at,
    )
    # Barely smaller: the command skips an already-optimised photo.
    monkeypatch.setattr(
        "crush_lu.management.commands.reprocess_photos.process_uploaded_image",
        lambda *args: ContentFile(b"original" * 1000, name="same.jpg"),
    )
    stats = {"processed": 0, "skipped": 0, "errors": 0}
    with TestCase.captureOnCommitCallbacks(execute=True):
        Command()._process_photo(profile, "photo_1", profile.photo_1, False, stats)
    assert stats["skipped"] == 1
    assert ConnectRealPersonBadge.objects.get(profile=profile).photo_key == old_key
