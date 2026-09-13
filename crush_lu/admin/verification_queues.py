"""
Verification queues behind the Crush-admin Action Center and profile proxies.

Since the July 2026 verification pivot a member is verified self-serve by
LuxID (`signals._execute_luxid_direct_verify`) or in person at an event door
(`services.profile_verification.claim_profile_verification`). Neither path
creates a ProfileSubmission, and `complete_profile_submission` never queues a
new submitter, so every admin count keyed on ``ProfileSubmission(status=
"pending")`` read zero while hundreds of members waited. These helpers key
the queues on the profile instead: ``CrushProfile.verification_status``, plus
the member's *latest* submission for the legacy coach-review states that
still have a live writer.
"""

from django.db.models import Count, OuterRef, Subquery
from django.utils import timezone

from crush_lu.models import CrushProfile, ProfileSubmission


def holds_door_seat(now):
    """``Exists``: the member has a seat or waitlist spot on an event whose
    door can still verify them — not cancelled, not yet ended.

    Deliberately the coach "Unverified profiles" page's own ``sig_upcoming``
    rather than a copy, so the Action Center's "booked" count and that page's
    "Booked on an event" chip cannot drift apart. Unlike the index's
    upcoming-events count it does not require ``is_published``: an
    unpublished event with bookings still has a door. Correlates on
    ``user_id``, so it filters CrushProfile querysets only.
    """
    from crush_lu.views_coach import (
        _live_or_future_event_ids,
        _unverified_signal_annotations,
    )

    signals = _unverified_signal_annotations(now, _live_or_future_event_ids(now))
    return signals["sig_upcoming"]


def latest_submission_status():
    """Status of the member's latest ProfileSubmission, as a ``Subquery``
    correlated on the outer CrushProfile's primary key.

    Ordered like `ProfileSubmission.latest_for_profile`: a row behind a newer
    one is history. An older "revision" behind a newer "pending"
    resubmission, or anything behind the pivot cleanup's "expired" row, no
    longer says where the member stands.
    """
    return Subquery(
        ProfileSubmission.objects.filter(profile_id=OuterRef("pk"))
        .order_by("-submitted_at")
        .values("status")[:1]
    )


def in_legacy_review_state(profiles, status):
    """Narrow ``profiles`` to unverified members whose latest submission has
    ``status``.

    Verified members are dropped: LuxID or an event door can verify somebody
    whose legacy row is still open, and they are done. Nothing pins the
    profile's own status beyond that — a coach's revision moves it to
    ``incomplete``, a recontact leaves it ``pending``, and the resubmission
    re-queue sets it back to ``pending``.
    """
    return (
        profiles.exclude(verification_status="verified")
        .alias(latest_submission_status=latest_submission_status())
        .filter(latest_submission_status=status)
    )


def pending_action_counts(now=None):
    """Counts behind the Action Center on the admin index and the analytics
    dashboard — one function, so the two surfaces cannot disagree.

    - ``total_pending``: active members awaiting verification.
    - ``booked`` / ``unbooked``: that cohort split on `holds_door_seat` —
      verifiable at an event door, or reachable only through LuxID or a new
      booking.
    - ``legacy_reviews``: active, unverified members whose latest submission
      is ``pending``: a pre-pivot revision or recontact the member resubmitted
      (`complete_profile_submission` re-queues it) and a coach must review.
    """
    now = now or timezone.now()
    active = CrushProfile.objects.filter(is_active=True)
    counts = active.filter(verification_status="pending").aggregate(
        total_pending=Count("pk"),
        booked=Count("pk", filter=holds_door_seat(now)),
    )
    return {
        "total_pending": counts["total_pending"],
        "booked": counts["booked"],
        "unbooked": counts["total_pending"] - counts["booked"],
        "legacy_reviews": in_legacy_review_state(active, "pending").count(),
    }
