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

from django.db.models import Count, Exists, OuterRef, Subquery
from django.utils import timezone

from crush_lu.models import CrushProfile, EventRegistration, ProfileSubmission
from crush_lu.services.event_doors import (
    DOOR_VISIBLE_REGISTRATION_STATUSES,
    live_or_future_event_ids,
)


def holds_door_seat(now):
    """``Exists``: the member has a seat or waitlist spot on an event whose
    door can still verify them — not cancelled, not yet ended.

    The coach "Unverified profiles" page's ``sig_upcoming`` signal, built from
    the `services.event_doors` event list and door statuses that page uses
    too, so the Action Center's "booked" count and the page's "Booked on an
    event" chip cannot drift apart. Unlike the index's upcoming-events count
    it does not require ``is_published``: an unpublished event with bookings
    still has a door. Correlates on ``user_id``, so it filters CrushProfile
    querysets only.
    """
    return Exists(
        EventRegistration.objects.filter(
            user_id=OuterRef("user_id"),
            event_id__in=live_or_future_event_ids(now),
            status__in=DOOR_VISIBLE_REGISTRATION_STATUSES,
        )
    )


def latest_submission_value(field):
    """``field`` of the member's latest ProfileSubmission, as a ``Subquery``
    correlated on the outer CrushProfile's primary key. See
    `latest_submission_status` for why only the latest row counts."""
    return Subquery(
        ProfileSubmission.objects.filter(profile_id=OuterRef("pk"))
        .order_by("-submitted_at")
        .values(field)[:1]
    )


def latest_submission_status():
    """Status of the member's latest ProfileSubmission, as a ``Subquery``
    correlated on the outer CrushProfile's primary key.

    Ordered like `ProfileSubmission.latest_for_profile`: a row behind a newer
    one is history. An older "revision" behind a newer "pending"
    resubmission, or anything behind the pivot cleanup's "expired" row, no
    longer says where the member stands.
    """
    return latest_submission_value("status")


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


def never_submitted_profiles(profiles):
    """Narrow ``profiles`` to members who never submitted their profile:
    still ``incomplete``, with no submission row.

    Submitting used to create a ProfileSubmission. Since the pivot it only
    moves the profile to ``pending`` (`complete_profile_submission`), so "no
    submission row" alone also matches every member who submitted after July
    2026. The row check still matters: a coach's revision request sends a
    member who did submit back to ``incomplete``.
    """
    return profiles.filter(verification_status="incomplete").filter(
        ~Exists(ProfileSubmission.objects.filter(profile_id=OuterRef("pk")))
    )


def resubmitted_after_revision(profiles):
    """Narrow ``profiles`` to members whose latest submission came back after
    a coach's revision request.

    Every revision verdict bumps ``revision_round`` on the row: the coach
    review, the bulk action and a hand edit in the admin. Resubmitting
    re-queues that same row (`complete_profile_submission`), so a
    resubmission never adds a second row. A latest row still at ``revision``
    is waiting on the member; an ``expired`` one was closed by the pivot
    cleanup without recording whether they resubmitted.
    """
    return (
        profiles.alias(
            latest_submission_status=latest_submission_status(),
            latest_revision_round=latest_submission_value("revision_round"),
        )
        .filter(latest_revision_round__gte=1)
        .exclude(latest_submission_status__in=("revision", "expired"))
    )


def pending_profiles():
    """Active members awaiting verification: the Action Center's "Pending
    Verification" cohort. Its counts and `recent_pending_profiles` both read
    this, so a recent-activity list is always the top of that tile's list."""
    return CrushProfile.objects.filter(is_active=True, verification_status="pending")


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
    counts = pending_profiles().aggregate(
        total_pending=Count("pk"),
        booked=Count("pk", filter=holds_door_seat(now)),
    )
    return {
        "total_pending": counts["total_pending"],
        "booked": counts["booked"],
        "unbooked": counts["total_pending"] - counts["booked"],
        "legacy_reviews": in_legacy_review_state(active, "pending").count(),
    }


def recent_pending_profiles(limit, now=None):
    """The ``limit`` members most recently updated while awaiting
    verification, newest first: the index's Today's Focus tab and the
    analytics dashboard's table.

    Each carries ``has_door_seat`` (`holds_door_seat`), the Action Center's
    booked / not-booked split. Ordered by ``updated_at``, like the coach
    page's "Recently updated": the profile keeps no "became pending"
    timestamp, and `complete_profile_submission` saves the profile as it
    moves it to ``pending``.
    """
    now = now or timezone.now()
    return (
        pending_profiles()
        .select_related("user")
        .annotate(has_door_seat=holds_door_seat(now))
        .order_by("-updated_at", "-pk")[:limit]
    )
