"""Let a submitted ("pending") member ask a coach to verify them on a call.

Since the July 2026 verification pivot a pending member gets verified only
with LuxID or at an event door, and submitting no longer opens a coach review
(``views_profile`` creates no ``ProfileSubmission``). On 2026-11-21, 761
members were pending and none had linked LuxID, so most of them wait for an
event they may never attend.

This module reopens one door on request: the member taps "Request a call with
a coach", which creates an unassigned pending ``ProfileSubmission``. That row
is exactly what the coach Verification Channel already lists, so a coach
claims it, makes the screening call (phone or the in-app video call) and
approves it through the normal review page, which requires the call before
approval. Nothing here verifies anyone by itself.

Gated by ``VERIFICATION_COACH_CALL_REQUESTS_ENABLED`` (default off).
"""

import logging

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


def coach_call_requests_enabled():
    return bool(getattr(settings, "VERIFICATION_COACH_CALL_REQUESTS_ENABLED", False))


def can_request_coach_call(profile):
    """True when this member may open a coach-call verification request now.

    Only a pending, active profile with no live coach review: a latest
    submission that is pending, in revision, recontact, approved or rejected
    already tells its own story. An expired latest row counts as none
    (``latest_for_profile`` returns ``None`` for it).
    """
    from crush_lu.models import ProfileSubmission

    if not coach_call_requests_enabled() or profile is None:
        return False
    if profile.verification_status != "pending":
        return False
    if not profile.is_active or not profile.user.is_active:
        return False
    return ProfileSubmission.latest_for_profile(profile) is None


def request_coach_call(profile):
    """Open the request. Returns the new submission, or ``None`` if not allowed.

    The profile row is locked so a double tap creates one submission. Coaches
    are told on commit, through the same channel broadcast a submission used
    to send.
    """
    from crush_lu.models import CrushProfile, ProfileSubmission

    if not coach_call_requests_enabled():
        return None
    with transaction.atomic():
        locked = (
            CrushProfile.objects.select_for_update()
            .select_related("user")
            .filter(pk=profile.pk)
            .first()
        )
        if not can_request_coach_call(locked):
            return None
        submission = ProfileSubmission.objects.create(
            profile=locked, status="pending", coach=None
        )
        transaction.on_commit(lambda: _broadcast(submission))
    logger.info(
        "Coach-call verification requested: profile=%s submission=%s",
        locked.pk,
        submission.pk,
    )
    return submission


def _broadcast(submission):
    from crush_lu.coach_notifications import broadcast_new_submission_to_channel

    try:
        broadcast_new_submission_to_channel(submission)
    except Exception:
        logger.exception(
            "Channel broadcast failed for coach-call request %s", submission.pk
        )
