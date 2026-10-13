"""Coach-set focus points for member photos (``ProfilePhotoFraming``).

A focus point is rendered as ``object-position: X% Y%``. That aligns the
image's (X%, Y%) point with the frame's (X%, Y%) point, so the chosen point
stays inside every frame ratio (circle, square, 4:5, 5:4). It does not centre
that point. A photo without one keeps the ``object-face`` default
(``DEFAULT_FOCUS``, tailwind-input.css).

The focus belongs to a file, not a slot: whoever renders a photo must look it
up for the key that viewer is actually served (the owner and coaches see the
live upload, other members the published one, ``photo_publication``).

Spec: ai-memory-hub/specs/2026-10-13-crush-photo-framing.md
"""

from django.utils import timezone
from django.utils.translation import gettext as _

from crush_lu.services.photo_publication import PHOTO_FIELDS, photo_for_viewer

# Mirrors the object-face utility in tailwind-src/crush_lu/tailwind-input.css.
DEFAULT_FOCUS = (50, 25)


class PhotoFramingError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


def get_focus(profile, photo_key):
    """The ``(x, y)`` a coach set for this file, or ``None``."""
    if not photo_key or not getattr(profile, "pk", None):
        return None
    prefetched = getattr(profile, "_prefetched_objects_cache", {}).get("photo_framings")
    if prefetched is not None:
        row = next((row for row in prefetched if row.photo_key == photo_key), None)
        return (row.focus_x, row.focus_y) if row else None
    # A page often renders the same photo more than once (drawer avatar and
    # page body): one query per file and profile instance.
    memo = profile.__dict__.setdefault("_photo_focus_memo", {})
    if photo_key not in memo:
        from crush_lu.models import ProfilePhotoFraming

        row = (
            ProfilePhotoFraming.objects.filter(
                profile_id=profile.pk, photo_key=photo_key
            )
            .values_list("focus_x", "focus_y")
            .first()
        )
        memo[photo_key] = tuple(row) if row else None
    return memo[photo_key]


def object_position(focus):
    """CSS ``object-position`` value for a focus, or ``""`` for the default."""
    return f"{focus[0]}% {focus[1]}%" if focus else ""


def focus_style(profile, photo_key):
    """Inline ``style`` value for an ``<img>`` showing this file, or ``""``."""
    position = object_position(get_focus(profile, photo_key))
    return f"object-position: {position}" if position else ""


def viewer_sees_live_photos(viewer):
    """Active coaches and superusers are served every member's live upload."""
    if viewer is None or not viewer.is_authenticated:
        return False
    if viewer.is_superuser:
        return True
    from crush_lu.models import CrushCoach

    return CrushCoach.objects.filter(user=viewer, is_active=True).exists()


def primary_photo_positions(viewer, user_ids):
    """``{user_id: object-position}`` for the primary photo each member's
    photo route serves ``viewer``; members without a focus are left out.

    Batched for lists such as the event lobby, which would otherwise query
    once per tile on every refresh.
    """
    user_ids = {user_id for user_id in user_ids if user_id}
    if not user_ids:
        return {}
    from crush_lu.models import CrushProfile

    privileged = viewer_sees_live_photos(viewer)
    profiles = CrushProfile.objects.filter(user_id__in=user_ids).prefetch_related(
        "photo_review_states", "published_photos", "photo_framings"
    )
    positions = {}
    for profile in profiles:
        photo = photo_for_viewer(viewer, profile, "photo_1", privileged=privileged)
        position = object_position(get_focus(profile, photo.name if photo else ""))
        if position:
            positions[profile.user_id] = position
    return positions


def _percent(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PhotoFramingError(_("Invalid focus point."), status=400) from None
    if number != number:  # NaN
        raise PhotoFramingError(_("Invalid focus point."), status=400)
    return int(round(min(100.0, max(0.0, number))))


def _check_current(profile, photo_field, photo_key):
    """Frame only the file the coach is looking at: a photo replaced since
    the page loaded is refused rather than framed blind."""
    if photo_field not in PHOTO_FIELDS:
        raise PhotoFramingError(_("Unknown photo."), status=400)
    from crush_lu.models import CrushProfile

    if not photo_key or not (
        CrushProfile.objects.filter(pk=profile.pk, **{photo_field: photo_key}).exists()
    ):
        raise PhotoFramingError(
            _("This photo has changed since the page loaded. Reload and try again.")
        )


def set_focus(coach, profile, photo_field, photo_key, focus_x, focus_y):
    """Store the focus a coach chose for the slot's current file."""
    x, y = _percent(focus_x), _percent(focus_y)
    _check_current(profile, photo_field, photo_key)
    from crush_lu.models import ProfilePhotoFraming

    profile.__dict__.pop("_photo_focus_memo", None)
    ProfilePhotoFraming.objects.update_or_create(
        profile_id=profile.pk,
        photo_key=photo_key,
        defaults={
            "focus_x": x,
            "focus_y": y,
            "set_by": coach,
            "set_at": timezone.now(),
        },
    )
    return x, y


def clear_focus(profile, photo_field, photo_key):
    """Back to the default framing for the slot's current file."""
    _check_current(profile, photo_field, photo_key)
    from crush_lu.models import ProfilePhotoFraming

    profile.__dict__.pop("_photo_focus_memo", None)
    ProfilePhotoFraming.objects.filter(
        profile_id=profile.pk, photo_key=photo_key
    ).delete()


def move_focus(profile, old_key, new_key):
    """Carry a focus to the file that replaced ``old_key`` with the same
    image (``reprocess_photos``)."""
    if not old_key or not new_key or old_key == new_key:
        return
    from crush_lu.models import ProfilePhotoFraming

    ProfilePhotoFraming.objects.filter(profile_id=profile.pk, photo_key=old_key).update(
        photo_key=new_key
    )


def square_crop_box(width, height, focus=None):
    """The square ``(left, top, right, bottom)`` a cover-fit square frame
    shows, with the same arithmetic as CSS ``object-position``."""
    x, y = focus or DEFAULT_FOCUS
    side = min(width, height)
    left = round((width - side) * x / 100)
    top = round((height - side) * y / 100)
    return (left, top, left + side, top + side)
