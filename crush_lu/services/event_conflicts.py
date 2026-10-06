"""Read-only, coach-scoped warnings. Never feeds registration or grouping gates."""

from collections import defaultdict

from django.db.models import F

from crush_lu.models import MeetupEvent, UserBlock

# Applications matter before selection; checked-in people still share the venue.
# Cancelled and no-show registrations are no longer potential participants.
CONFLICT_REGISTRATION_STATUSES = (
    "applied",
    "pending",
    "confirmed",
    "waitlist",
    "attended",
)


def event_conflict_pairs(events, coach, *, viewer=None):
    """Return unordered user-id pairs only for events this coach may inspect.

    Two batched queries, independent of the event/participant count. Pass the
    authenticated viewer to reuse request.user without a coach.user lookup.
    Join both registrations on the same event in SQL instead of loading the block
    graph or comparing every possible participant pair. Select only endpoint
    ids; block direction and reasons never leave this service.

    Coaches automatically receive is_staff, so that flag is NOT an access
    bypass. Only superusers retain global visibility on these coach pages.
    The caller is already guarded by coach_required (active coach required).
    """
    event_ids = [event.pk for event in events if not event.is_cancelled]
    if not event_ids:
        return {}
    viewer = viewer if viewer is not None else coach.user
    if viewer.pk != coach.user_id or not viewer.is_active or not coach.is_active:
        return {}
    if not viewer.is_superuser:
        event_ids = list(
            MeetupEvent.objects.filter(pk__in=event_ids, coaches=coach)
            .order_by()
            .values_list("pk", flat=True)
        )
    if not event_ids:
        return {}

    rows = (
        UserBlock.objects.filter(
            blocker__eventregistration__event_id__in=event_ids,
            blocker__eventregistration__status__in=CONFLICT_REGISTRATION_STATUSES,
            blocked__eventregistration__event_id=F(
                "blocker__eventregistration__event_id"
            ),
            blocked__eventregistration__status__in=CONFLICT_REGISTRATION_STATUSES,
        )
        .order_by()
        .values_list("blocker__eventregistration__event_id", "blocker_id", "blocked_id")
        .distinct()
    )
    pairs = defaultdict(set)
    for event_id, first, second in rows.iterator(chunk_size=1000):
        pairs[event_id].add(tuple(sorted((first, second))))
    return {event_id: sorted(values) for event_id, values in pairs.items()}
