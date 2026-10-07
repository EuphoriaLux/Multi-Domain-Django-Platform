"""Structured photo-revision reasons and recipient-facing translations."""

from django.utils.translation import gettext_lazy as _

PHOTO_REVISION_REASONS = {
    "inappropriate": (
        _("Inappropriate or explicit content"),
        _("Please replace the inappropriate image with a suitable photo of yourself."),
    ),
    "unclear_face": (
        _("Face unclear or covered"),
        _("Please upload a clearer profile photo where your face is visible."),
    ),
    "group_photo": (
        _("Group photo / cannot identify member"),
        _("Please upload a photo showing only you, so members can identify you."),
    ),
    "blurry_photo": (
        _("Blurry or low-quality photo"),
        _("Please upload a sharp, clear photo. Avoid blurry or pixelated images."),
    ),
    "poor_lighting": (
        _("Photo too dark or poorly lit"),
        _("Please upload a well-lit photo so your face can be seen clearly."),
    ),
    "heavy_filter": (
        _("Heavy filters or altered appearance"),
        _("Please upload a natural photo without filters that change your appearance."),
    ),
    "not_person": (
        _("Does not show the member"),
        _(
            "Please upload a photo of yourself instead of a landscape, object, drawing or screenshot."
        ),
    ),
    "cropped_face": (
        _("Face cropped or too far away"),
        _("Please upload a closer photo showing your whole face, without cropping it."),
    ),
    "other": (
        _("Other"),
        _(
            "Please replace your profile photo with a clear, suitable photo of yourself."
        ),
    ),
}

PHOTO_REVISION_REASON_CHOICES = [
    (code, label) for code, (label, feedback) in PHOTO_REVISION_REASONS.items()
]


def get_photo_revision_feedback(reason):
    """Resolve the standard feedback in the active recipient language."""
    return str(PHOTO_REVISION_REASONS.get(reason, PHOTO_REVISION_REASONS["other"])[1])
