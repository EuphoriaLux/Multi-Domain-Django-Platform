"""Coaches swap a member's photo slots, most often to choose the main photo.

Review state and publication keep one row per slot while every decision is
about a file, and ``CrushProfile.save()`` reads a changed slot as an upload.
These tests pin that a swap moves every per-slot fact with its file, writes
and deletes no file, and tells the member.

Spec: ai-memory-hub/specs/2026-10-13-crush-photo-framing.md
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase

from crush_lu.models import (
    CrushProfile,
    Notification,
    ProfilePhotoReviewLog,
    ProfilePhotoReviewState,
    ProfilePhotoSwap,
    ProfilePhotoUpload,
    PublishedProfilePhoto,
)
from crush_lu.notification_service import notify_photo_order_changed
from crush_lu.photo_review_reasons import photo_revision_items
from crush_lu.services.photo_order import PhotoOrderError, swap_photo_slots
from crush_lu.services.photo_publication import (
    get_public_photo_key,
    primary_photo_in_review,
)
from crush_lu.services.photo_review import (
    PhotoReviewError,
    submit_member_review,
    undo_last_photo_review,
)
from crush_lu.templatetags.crush_media import member_photo_review
from crush_lu.tests.test_coach_photo_review import _make_candidate, _make_coach

pytestmark = [pytest.mark.django_db, pytest.mark.urls("azureproject.urls_crush")]

User = get_user_model()

K1 = "users/1/photos/photo1.jpg"  # approved through the legacy primary columns
K2 = "users/1/photos/photo2.jpg"  # approved per slot
K3 = "users/1/photos/photo3.jpg"  # waiting for review


@pytest.fixture(autouse=True)
def deleted_blobs(monkeypatch):
    """Record storage deletes instead of touching files."""
    cache.clear()
    deleted = []
    for field in ("photo_1", "photo_2", "photo_3"):
        monkeypatch.setattr(
            CrushProfile._meta.get_field(field).storage,
            "delete",
            lambda name: deleted.append(name),
        )
    return deleted


@pytest.fixture
def side_effects():
    with patch(
        "crush_lu.notification_service.notify_photo_order_changed"
    ) as notify, patch("crush_lu.signals.trigger_wallet_pass_updates") as wallet:
        yield notify, wallet


def _member(**candidate):
    """Main photo K1 (legacy-only approval), K2 approved, K3 pending."""
    profile = _make_candidate(photo_key=K1, photo_review_status="approved", **candidate)
    CrushProfile.objects.filter(pk=profile.pk).update(photo_2=K2, photo_3=K3)
    ProfilePhotoReviewState.objects.create(
        profile=profile,
        photo_field="photo_2",
        photo_key=K2,
        status="approved",
        notes="lovely smile",
    )
    PublishedProfilePhoto.objects.create(
        profile=profile, photo_field="photo_2", photo_key=K2
    )
    profile.refresh_from_db()
    return profile


def _swap(coach, profile, field_a, field_b, key_a, key_b):
    with TestCase.captureOnCommitCallbacks(execute=True):
        return swap_photo_slots(coach, profile, field_a, field_b, key_a, key_b)


def _slots(model, profile):
    return dict(
        model.objects.filter(profile=profile).values_list("photo_field", "photo_key")
    )


def _fresh(profile):
    return CrushProfile.objects.get(pk=profile.pk)


# --- Choosing the main photo -------------------------------------------------


def test_make_main_moves_every_per_slot_fact_with_its_file(deleted_blobs, side_effects):
    notify, wallet = side_effects
    coach, profile = _make_coach(), _member()

    swap = _swap(coach, profile, "photo_2", "photo_1", K2, K1)

    profile = _fresh(profile)
    assert (profile.photo_1.name, profile.photo_2.name) == (K2, K1)
    # The legacy-only approval of K1 became a state row and moved with K1.
    states = {
        state.photo_field: state
        for state in ProfilePhotoReviewState.objects.filter(profile=profile)
    }
    assert (states["photo_1"].photo_key, states["photo_1"].status) == (K2, "approved")
    assert states["photo_1"].notes == "lovely smile"
    assert (states["photo_2"].photo_key, states["photo_2"].status) == (K1, "approved")
    assert _slots(PublishedProfilePhoto, profile) == {"photo_1": K2, "photo_2": K1}
    # The legacy primary columns now describe the new main photo.
    assert (profile.photo_review_status, profile.photo_review_key) == ("approved", K2)
    assert get_public_photo_key(profile, "photo_1") == K2
    assert get_public_photo_key(profile, "photo_2") == K1
    assert not primary_photo_in_review(profile)
    # Not an upload: no file deleted, nothing counted against the daily limit.
    assert deleted_blobs == []
    assert not ProfilePhotoUpload.objects.exists()
    assert (swap.field_a, swap.field_b, swap.key_a, swap.key_b, swap.coach) == (
        "photo_2",
        "photo_1",
        K2,
        K1,
        coach,
    )
    notify.assert_called_once_with(
        profile.user, photo_swap_id=swap.pk, main_changed=True, request=None
    )
    wallet.assert_called_once()


def test_only_an_approved_photo_can_become_the_main_photo(side_effects):
    coach, profile = _make_coach(), _member()

    with pytest.raises(PhotoOrderError) as refused:
        _swap(coach, profile, "photo_3", "photo_1", K3, K1)

    assert refused.value.status == 409
    assert _fresh(profile).photo_1.name == K1
    assert not ProfilePhotoSwap.objects.exists()
    side_effects[0].assert_not_called()


def test_a_page_older_than_the_profile_is_refused(side_effects):
    coach, profile = _make_coach(), _member()

    with pytest.raises(PhotoOrderError) as refused:
        _swap(coach, profile, "photo_2", "photo_1", K2, "users/1/photos/older.jpg")

    assert refused.value.status == 409
    assert _fresh(profile).photo_1.name == K1


def test_a_concurrent_change_after_the_read_writes_nothing(monkeypatch, side_effects):
    """Row locks are a no-op on SQLite: the conditional update is the guard,
    and a refused swap rolls back the per-slot rows it already moved."""
    coach, profile = _make_coach(), _member()

    def member_replaces_photo_2(locked):
        CrushProfile.objects.filter(pk=locked.pk).update(photo_2="users/1/new.jpg")

    monkeypatch.setattr(
        "crush_lu.services.photo_order._materialise_legacy_primary",
        member_replaces_photo_2,
    )
    with pytest.raises(PhotoOrderError):
        _swap(coach, profile, "photo_2", "photo_1", K2, K1)

    assert _slots(ProfilePhotoReviewState, profile) == {"photo_2": K2}
    assert _slots(PublishedProfilePhoto, profile) == {"photo_1": K1, "photo_2": K2}
    assert not ProfilePhotoSwap.objects.exists()


def test_a_fake_profile_flag_blocks_any_swap(side_effects):
    coach, profile = _make_coach(), _member()
    CrushProfile.objects.filter(pk=profile.pk).update(
        photo_review_status="flagged_fake"
    )

    with pytest.raises(PhotoOrderError) as refused:
        _swap(coach, profile, "photo_3", "photo_2", K3, K2)

    assert refused.value.status == 403


def test_a_new_main_photo_lifts_a_revision_request_on_the_old_one(side_effects):
    """Making an approved photo the main one un-pauses the member, while the
    refused photo keeps its request (and the coach's reason) in its new slot."""
    coach, profile = _make_coach(), _member()
    ProfilePhotoReviewState.objects.create(
        profile=profile, photo_field="photo_1", photo_key=K1, status="needs_revision"
    )
    CrushProfile.objects.filter(pk=profile.pk).update(
        photo_review_status="needs_revision", photo_review_key=K1
    )
    ProfilePhotoReviewLog.objects.create(
        profile=profile,
        coach=coach,
        photo_field="photo_1",
        photo_key=K1,
        decision="needs_revision",
        reason="unclear_face",
    )

    _swap(coach, profile, "photo_2", "photo_1", K2, K1)

    profile = _fresh(profile)
    assert profile.photo_review_status == "approved"
    assert profile.get_photo_field_review_status("photo_1") == "approved"
    assert profile.get_photo_field_review_status("photo_2") == "needs_revision"
    request = RequestFactory().get("/")
    request.user = profile.user
    notice = member_photo_review({"request": request}, 2)
    expected = photo_revision_items(
        [{"photo_field": "photo_2", "reason": "unclear_face"}]
    )
    assert notice["state"] == "needs_replacement"
    assert notice["reason_label"] == expected[0]["reason_label"]


def test_the_in_person_badge_follows_its_file(side_effects):
    coach, profile = _make_coach(), _member()
    CrushProfile.objects.filter(pk=profile.pk).update(photo_verification_key=K1)
    assert _fresh(profile).is_photo_verified

    _swap(coach, profile, "photo_2", "photo_1", K2, K1)
    assert not _fresh(profile).is_photo_verified

    _swap(coach, _fresh(profile), "photo_2", "photo_1", K1, K2)
    assert _fresh(profile).is_photo_verified


def test_secondary_photos_can_be_reordered_whatever_their_review(side_effects):
    notify, wallet = side_effects
    coach, profile = _make_coach(), _member()
    # A decision about a file that is no longer in slot 3: it moves with the
    # slot but names its own file, so it must not approve K3.
    stale = "users/1/photos/replaced.jpg"
    ProfilePhotoReviewState.objects.create(
        profile=profile, photo_field="photo_3", photo_key=stale, status="approved"
    )

    swap = _swap(coach, profile, "photo_3", "photo_2", K3, K2)

    profile = _fresh(profile)
    assert (profile.photo_2.name, profile.photo_3.name) == (K3, K2)
    assert _slots(ProfilePhotoReviewState, profile) == {"photo_2": stale, "photo_3": K2}
    assert profile.get_photo_field_review_status("photo_2") == "pending"
    assert profile.get_photo_field_review_status("photo_3") == "approved"
    assert _slots(PublishedProfilePhoto, profile) == {"photo_1": K1, "photo_3": K2}
    assert (profile.photo_review_status, profile.photo_review_key) == ("approved", K1)
    assert notify.call_args.kwargs["main_changed"] is False
    assert swap.changed_main_photo is False
    wallet.assert_not_called()


def test_an_approved_photo_can_fill_an_empty_main_slot(side_effects):
    coach = _make_coach()
    profile = _make_candidate(has_photo=False)
    CrushProfile.objects.filter(pk=profile.pk).update(photo_2=K2)
    ProfilePhotoReviewState.objects.create(
        profile=profile, photo_field="photo_2", photo_key=K2, status="approved"
    )
    PublishedProfilePhoto.objects.create(
        profile=profile, photo_field="photo_2", photo_key=K2
    )

    _swap(coach, _fresh(profile), "photo_2", "photo_1", K2, "")

    profile = _fresh(profile)
    assert profile.photo_1.name == K2
    assert not profile.photo_2
    assert get_public_photo_key(profile, "photo_1") == K2


def test_a_deck_decision_made_before_a_swap_cannot_be_undone(side_effects):
    coach, profile = _make_coach(), _member()
    submit_member_review(
        coach,
        profile.pk,
        [
            {
                "photo_field": "photo_3",
                "photo_key": K3,
                "decision": "approved",
                "reason": "clear_authentic",
            }
        ],
    )
    _swap(coach, _fresh(profile), "photo_3", "photo_1", K3, K1)

    with pytest.raises(PhotoReviewError):
        undo_last_photo_review(coach)

    assert _fresh(profile).get_photo_field_review_status("photo_1") == "approved"


# --- Telling the member ------------------------------------------------------


def test_the_member_is_told_once_per_swap():
    profile = _member()

    first = notify_photo_order_changed(profile.user, photo_swap_id=7, main_changed=True)
    again = notify_photo_order_changed(profile.user, photo_swap_id=7, main_changed=True)

    assert first.inapp_created and again.deduped
    bell = Notification.objects.get(user=profile.user)
    assert bell.notification_type == "photo_order_changed"
    assert bell.title == "Your coach chose your main photo"
    assert [message.subject for message in mail.outbox] == [
        "Your coach chose your main photo"
    ]


# --- Coach page ----------------------------------------------------------------


def _post_swap(client, profile, **data):
    return client.post(f"/en/coach/member/{profile.user_id}/photos/swap/", data)


def test_coach_makes_a_photo_the_main_one_from_the_page(side_effects):
    coach, profile = _make_coach(), _member()
    client = Client()
    client.force_login(coach.user)

    page = client.get(f"/en/coach/member/{profile.user_id}/photos/").content.decode()
    assert page.count("Make main photo") == 2
    assert "Approve this photo first" in page  # photo 3 is still pending

    response = _post_swap(
        client,
        profile,
        photo_field="photo_2",
        photo_key=K2,
        target_field="photo_1",
        target_key=K1,
    )

    assert response.status_code == 302
    assert _fresh(profile).photo_1.name == K2
    followed = client.get(response.url).content.decode()
    assert "Main photo changed." in followed


def test_non_coaches_cannot_swap_photos(side_effects):
    profile = _member()
    client = Client()
    client.force_login(User.objects.create_user(username="nosy", password="x"))

    _post_swap(
        client,
        profile,
        photo_field="photo_2",
        photo_key=K2,
        target_field="photo_1",
        target_key=K1,
    )

    assert _fresh(profile).photo_1.name == K1
    assert not ProfilePhotoSwap.objects.exists()
