"""Coaches swap two of a member's photo slots, most often to choose the main
photo (``ProfilePhotoSwap``).

Every trust decision about a photo is keyed by its file, but two tables keep
one row per *slot* (review state and publication), and ``CrushProfile.save()``
reads a changed slot key as a member upload: it resets the primary review,
drops the in-person badge, counts the upload and deletes the old file. So a
swap writes the profile with a conditional ``update()`` and moves the per-slot
rows itself. No file is written or deleted; a coach-set focus point
(``photo_framing``) is keyed by file and follows on its own.

The in-person badge (``photo_verification_key``) is left alone: it names the
file a coach saw at the door, so it drops while another photo is the main one
and returns if that file becomes the main photo again.

Spec: ai-memory-hub/specs/2026-10-13-crush-photo-framing.md
"""

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from crush_lu.services.photo_publication import PHOTO_FIELDS

logger = logging.getLogger(__name__)

# Decisions the legacy primary columns can hold for a specific file.
LEGACY_FILE_DECISIONS = ("approved", "needs_revision")


class PhotoOrderError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


def _key(profile, photo_field):
    return getattr(getattr(profile, photo_field), "name", "") or ""


def can_become_main(profile, photo_field):
    """Only an approved photo may become the main photo: a file waiting for
    review in slot 1 hides the member from Connect and the event lobby
    (``photo_publication.primary_photo_in_review``)."""
    return bool(_key(profile, photo_field)) and (
        profile.get_photo_field_review_status(photo_field) == "approved"
    )


def _holds(photo_field, key):
    """Filter for a slot holding ``key`` (an empty slot is NULL or "")."""
    if key:
        return Q(**{photo_field: key})
    return Q(**{f"{photo_field}__isnull": True}) | Q(**{photo_field: ""})


def _materialise_legacy_primary(profile):
    """Before the main photo leaves slot 1, copy a decision recorded only in
    the legacy primary columns into a per-slot state row, so it moves with
    the file instead of reading as "pending" in its new slot."""
    from crush_lu.models import ProfilePhotoReviewState

    key = _key(profile, "photo_1")
    if (
        not key
        or profile.photo_review_key != key
        or profile.photo_review_status not in LEGACY_FILE_DECISIONS
        or ProfilePhotoReviewState.objects.filter(
            profile_id=profile.pk, photo_field="photo_1", photo_key=key
        ).exists()
    ):
        return
    ProfilePhotoReviewState.objects.update_or_create(
        profile_id=profile.pk,
        photo_field="photo_1",
        defaults={
            "photo_key": key,
            "status": profile.photo_review_status,
            "reviewed_at": profile.photo_reviewed_at or timezone.now(),
            "reviewed_by_id": profile.photo_reviewed_by_id,
            "notes": profile.photo_review_notes or "",
        },
    )


def _swap_slot_rows(model, profile_id, field_a, field_b, columns):
    """Swap a one-row-per-slot table between two slots.

    Unique on (profile, photo_field), which Postgres checks row by row: an
    in-place relabel collides mid-statement (and passes on SQLite). The rows
    are deleted and recreated with every other column kept.
    """
    rows = model.objects.filter(
        profile_id=profile_id, photo_field__in=(field_a, field_b)
    )
    values = list(rows.values("photo_field", *columns))
    rows.delete()
    other = {field_a: field_b, field_b: field_a}
    model.objects.bulk_create(
        [
            model(
                profile_id=profile_id, photo_field=other[row.pop("photo_field")], **row
            )
            for row in values
        ]
    )


def swap_photo_slots(coach, profile, field_a, field_b, key_a, key_b, *, request=None):
    """Swap the files in two slots and tell the member.

    ``key_a``/``key_b`` are the files the coach saw in those slots (``""``
    for an empty slot); a page older than the profile is refused (409).
    Returns the ``ProfilePhotoSwap`` audit row.
    """
    from crush_lu.models import (
        CrushProfile,
        ProfilePhotoReviewState,
        ProfilePhotoSwap,
        PublishedProfilePhoto,
    )

    if field_a not in PHOTO_FIELDS or field_b not in PHOTO_FIELDS or field_a == field_b:
        raise PhotoOrderError(_("Unknown photo."), status=400)
    key_a, key_b = key_a or "", key_b or ""
    if not key_a and not key_b:
        raise PhotoOrderError(_("There is no photo to move."), status=400)
    changes_main = "photo_1" in (field_a, field_b)

    with transaction.atomic():
        locked = CrushProfile.objects.select_for_update().get(pk=profile.pk)
        if (_key(locked, field_a), _key(locked, field_b)) != (key_a, key_b):
            raise PhotoOrderError(
                _("These photos changed since the page loaded. Reload and try again.")
            )
        if locked.photo_review_status == "flagged_fake":
            raise PhotoOrderError(
                _("This member is flagged as a fake profile."), status=403
            )
        if changes_main:
            incoming = field_b if field_a == "photo_1" else field_a
            if not can_become_main(locked, incoming):
                raise PhotoOrderError(
                    _(
                        "Only an approved photo can become the main photo. "
                        "Approve it first."
                    )
                )
            _materialise_legacy_primary(locked)

        _swap_slot_rows(
            ProfilePhotoReviewState,
            locked.pk,
            field_a,
            field_b,
            ("photo_key", "status", "reviewed_at", "reviewed_by_id", "notes"),
        )
        _swap_slot_rows(
            PublishedProfilePhoto,
            locked.pk,
            field_a,
            field_b,
            ("photo_key", "published_at"),
        )

        updates = {field_a: key_b or None, field_b: key_a or None}
        if changes_main:
            # The legacy primary columns describe slot 1, and a profile-wide
            # needs_revision there would brand the incoming approved file:
            # they now follow the new main photo's own decision.
            state = (
                ProfilePhotoReviewState.objects.filter(
                    profile_id=locked.pk, photo_field="photo_1"
                )
                .values("photo_key", "status", "reviewed_at", "reviewed_by_id", "notes")
                .get()
            )
            updates.update(
                photo_review_status=state["status"],
                photo_review_key=state["photo_key"],
                photo_reviewed_at=state["reviewed_at"],
                photo_reviewed_by_id=state["reviewed_by_id"],
                photo_review_notes=state["notes"],
            )
        # The conditional filter is the real guard where row locks are a
        # no-op (SQLite): a slot changed since the read writes nothing.
        updated = (
            CrushProfile.objects.filter(pk=locked.pk)
            .filter(_holds(field_a, key_a), _holds(field_b, key_b))
            .update(**updates)
        )
        if updated != 1:
            raise PhotoOrderError(
                _("These photos changed since the page loaded. Reload and try again.")
            )
        swap = ProfilePhotoSwap.objects.create(
            profile_id=locked.pk,
            coach=coach,
            field_a=field_a,
            field_b=field_b,
            key_a=key_a,
            key_b=key_b,
        )
        transaction.on_commit(lambda: _after_swap(swap.pk, request))

    logger.info(
        "Coach %s swapped %s and %s of profile %s (swap %s)",
        getattr(coach, "pk", None),
        field_a,
        field_b,
        locked.pk,
        swap.pk,
    )
    return swap


def _after_swap(swap_id, request):
    """Tell the member, and refresh their wallet pass when the main photo
    changed. The wallet signal reads a pre_save snapshot that an update()
    never takes, so it is triggered here; Outlook catches up on the next
    contact sync (``graph_contacts`` compares ``outlook_photo_key``)."""
    from crush_lu.models import ProfilePhotoSwap
    from crush_lu.notification_service import notify_photo_order_changed

    swap = ProfilePhotoSwap.objects.select_related("profile__user").get(pk=swap_id)
    profile = swap.profile
    try:
        notify_photo_order_changed(
            profile.user,
            photo_swap_id=swap.pk,
            main_changed=swap.changed_main_photo,
            request=request,
        )
    except Exception:
        logger.exception(
            "Could not tell profile %s about photo swap %s", profile.pk, swap.pk
        )
    if swap.changed_main_photo and getattr(profile, "show_photo_on_wallet", True):
        try:
            from crush_lu.signals import trigger_wallet_pass_updates

            trigger_wallet_pass_updates(profile)
        except Exception:
            logger.exception("Wallet refresh failed after photo swap %s", swap.pk)
