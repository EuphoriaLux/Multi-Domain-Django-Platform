"""Which profile photo other members see, and how often a slot may change.

A member's live ``CrushProfile.photo_N`` is what they uploaded last. Other
members only ever see a slot's *published* file (``PublishedProfilePhoto``):
the last one a coach approved. A replacement is therefore held back until a
coach approves it, and a rejected replacement leaves the earlier approved
photo in place. The owner, active coaches and superusers see the live file.

Deleting a photo is never held: the slot's published file goes with it.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import ngettext

logger = logging.getLogger(__name__)

PHOTO_FIELDS = ("photo_1", "photo_2", "photo_3")
MODERATED_STATUSES = ("needs_revision", "flagged_fake")
UPLOAD_WINDOW = timedelta(hours=24)


def uploads_per_slot_per_day():
    """First upload plus three replacements per slot and rolling 24 hours."""
    return getattr(settings, "PHOTO_UPLOADS_PER_SLOT_PER_DAY", 4)


def get_published_key(profile, photo_field):
    """The published key for a slot, using a prefetch when one is loaded."""
    if not profile.pk:
        return ""
    prefetched = getattr(profile, "_prefetched_objects_cache", {}).get(
        "published_photos"
    )
    if prefetched is not None:
        return next(
            (row.photo_key for row in prefetched if row.photo_field == photo_field),
            "",
        )
    from crush_lu.models import PublishedProfilePhoto

    return (
        PublishedProfilePhoto.objects.filter(
            profile_id=profile.pk, photo_field=photo_field
        )
        .values_list("photo_key", flat=True)
        .first()
        or ""
    )


def get_public_photo_key(profile, photo_field):
    """The key other members may see for ``photo_field``, or ``""``."""
    if profile.photo_review_status == "flagged_fake":
        return ""
    live = getattr(getattr(profile, photo_field), "name", "") or ""
    status = profile.get_photo_field_review_status(photo_field) if live else ""
    if live and status == "approved":
        return live
    published = get_published_key(profile, photo_field)
    if not published:
        return ""
    if published == live:
        # Published before held replacements existed, then moderated.
        return "" if status in MODERATED_STATUSES else live
    # An earlier approved photo stays visible while its replacement waits
    # for review or was refused.
    return published


def get_public_photo(profile, photo_field):
    """The published file as a ``FieldFile``, or ``None``."""
    key = get_public_photo_key(profile, photo_field)
    if not key:
        return None
    live = getattr(profile, photo_field)
    if live and live.name == key:
        return live
    field = profile._meta.get_field(photo_field)
    return field.attr_class(profile, field, key)


def photo_for_viewer(viewer, profile, photo_field, *, privileged=False):
    """The file this viewer sees: live for the owner and ``privileged``
    viewers (active coaches, superusers), the published file otherwise."""
    if privileged or (viewer is not None and viewer.pk == profile.user_id):
        live = getattr(profile, photo_field)
        return live or None
    return get_public_photo(profile, photo_field)


def _delete_blob_on_commit(profile, photo_field, key):
    storage = profile._meta.get_field(photo_field).storage

    def _delete():
        try:
            storage.delete(key)
        except Exception:
            logger.warning("Could not delete superseded profile photo %s", key)

    transaction.on_commit(_delete)


def publish_photo(profile, photo_field, photo_key):
    """Publish an approved file; return the key it replaced (or ``""``).

    Call inside the decision transaction, with the profile row locked. The
    superseded file is deleted after commit unless it is still live.
    """
    from crush_lu.models import PublishedProfilePhoto

    row = (
        PublishedProfilePhoto.objects.select_for_update()
        .filter(profile_id=profile.pk, photo_field=photo_field)
        .first()
    )
    previous = row.photo_key if row else ""
    if row is None:
        PublishedProfilePhoto.objects.create(
            profile_id=profile.pk, photo_field=photo_field, photo_key=photo_key
        )
    elif previous != photo_key:
        row.photo_key = photo_key
        row.published_at = timezone.now()
        row.save(update_fields=["photo_key", "published_at"])
    live = getattr(getattr(profile, photo_field), "name", "") or ""
    if previous and previous not in (photo_key, live):
        _delete_blob_on_commit(profile, photo_field, previous)
    return previous


def unpublish_photo(profile, photo_field, photo_key):
    """Stop showing ``photo_key`` if it is the slot's published file."""
    from crush_lu.models import PublishedProfilePhoto

    return bool(
        PublishedProfilePhoto.objects.filter(
            profile_id=profile.pk, photo_field=photo_field, photo_key=photo_key
        ).delete()[0]
    )


def restore_publication(profile, photo_field, photo_key):
    """Undo: republish ``photo_key`` (only ever the slot's still-live file)."""
    from crush_lu.models import PublishedProfilePhoto

    PublishedProfilePhoto.objects.update_or_create(
        profile_id=profile.pk,
        photo_field=photo_field,
        defaults={"photo_key": photo_key, "published_at": timezone.now()},
    )


def drop_publication(profile, photo_field):
    """The member removed the slot: the published file goes too.

    Called after the profile row was saved without the photo. The row goes
    now and the file after commit, so a removed photo never stays reachable
    and a rolled-back save keeps the approved photo intact.
    """
    from crush_lu.models import PublishedProfilePhoto

    rows = PublishedProfilePhoto.objects.filter(
        profile_id=profile.pk, photo_field=photo_field
    )
    keys = [key for key in rows.values_list("photo_key", flat=True) if key]
    # Row first, file after commit: a file is never deleted while a row
    # still points at it.
    rows.delete()
    for key in keys:
        _delete_blob_on_commit(profile, photo_field, key)


def held_photo_keys(profile):
    """Published files that are no longer live (kept for other members)."""
    from crush_lu.models import PublishedProfilePhoto

    live = {getattr(getattr(profile, f), "name", "") or "" for f in PHOTO_FIELDS}
    return [
        (row["photo_field"], row["photo_key"])
        for row in PublishedProfilePhoto.objects.filter(profile_id=profile.pk)
        .values("photo_field", "photo_key")
        .order_by("photo_field")
        if row["photo_key"] and row["photo_key"] not in live
    ]


def delete_held_photos_on_commit(profile):
    """Schedule deletion of published files that are no longer live.

    Call before deleting a profile's publication rows: those rows are the
    only reference to a held approved photo, so it would otherwise stay in
    storage with nothing pointing at it.
    """
    for photo_field, key in held_photo_keys(profile):
        _delete_blob_on_commit(profile, photo_field, key)


def record_photo_upload(profile, photo_field):
    from crush_lu.models import ProfilePhotoUpload

    ProfilePhotoUpload.objects.create(profile_id=profile.pk, photo_field=photo_field)


def photo_upload_refusal(profile, photo_field):
    """A member-facing refusal when the slot's daily limit is used up."""
    if profile is None or not profile.pk:
        return ""
    from crush_lu.models import ProfilePhotoUpload

    since = timezone.now() - UPLOAD_WINDOW
    recent = list(
        ProfilePhotoUpload.objects.filter(
            profile_id=profile.pk, photo_field=photo_field, created_at__gte=since
        )
        .order_by("created_at")
        .values_list("created_at", flat=True)
    )
    limit = uploads_per_slot_per_day()
    if len(recent) < limit:
        return ""
    retry_at = recent[len(recent) - limit] + UPLOAD_WINDOW
    hours = max(1, int((retry_at - timezone.now()).total_seconds() // 3600) + 1)
    return ngettext(
        "You have changed this photo too often today. Please try again in "
        "%(hours)s hour.",
        "You have changed this photo too often today. Please try again in "
        "%(hours)s hours.",
        hours,
    ) % {"hours": hours}


def primary_photo_in_review(profile):
    """The member has a main photo, but no coach has approved one yet.

    Others cannot see them (see ``get_public_photo_key``), so Connect and the
    event lobby do not let them see others either until a coach approves it.
    A coach-moderated photo has its own, actionable gates.
    """
    return bool(
        profile is not None
        and profile.photo_1
        and profile.photo_review_status not in MODERATED_STATUSES
        and not get_public_photo_key(profile, "photo_1")
    )
