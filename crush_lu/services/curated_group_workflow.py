"""Atomic lifecycle operations for curated speed-dating groups.

The projector is deliberately pure.  This module is the narrow bridge from its
deterministic result to durable groups, schedules and the event-registration
lifecycle. Projection-only entry points use the shared roster lock order:

    event -> registrations -> groups -> memberships -> pairings -> participants

Payment-aware remedy entry points acquire ``PaymentTransaction`` first, then
the same roster order, and checkout claims last. Keeping both orders explicit
prevents a re-projection from publishing half a generation or deadlocking a
payment/cancellation path.
"""

import logging
from dataclasses import dataclass
from functools import partial

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crush_lu.models.events import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    CuratedEventPairing,
    CuratedEventPairingParticipant,
    EventRegistration,
    MeetupEvent,
)
from crush_lu.services.event_grouping import GroupProjection, project_event_groups

logger = logging.getLogger(__name__)


ACTIVE_GROUP_STATUSES = (
    CuratedEventGroup.STATUS_DRAFT,
    CuratedEventGroup.STATUS_PROVISIONAL,
    CuratedEventGroup.STATUS_LOCKED,
)
PAYABLE_GROUP_STATUSES = (
    CuratedEventGroup.STATUS_PROVISIONAL,
    CuratedEventGroup.STATUS_LOCKED,
)
REPLACEABLE_GROUP_STATUSES = (
    CuratedEventGroup.STATUS_DRAFT,
    CuratedEventGroup.STATUS_PROVISIONAL,
    CuratedEventGroup.STATUS_DEGRADED,
)


@dataclass(frozen=True, slots=True)
class StoredProjection:
    """Result of publishing one complete projection generation."""

    generation: int
    group_ids: tuple[int, ...]
    status: str
    projection: GroupProjection

    @property
    def group_count(self):
        return len(self.group_ids)


@dataclass(frozen=True, slots=True)
class ApprovedGeneration:
    generation: int
    group_ids: tuple[int, ...]
    applied_registration_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DegradedEventRemedy:
    action: str
    degraded_group_ids: tuple[int, ...]
    replacement_generation: int | None = None
    compensated_registration_ids: tuple[int, ...] = ()
    credit_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _CheckoutRetirementPlan:
    """Provider checkout IDs that must be proven closed outside the DB lock."""

    checkout_ids: tuple[str, ...]
    leased_claims: tuple[tuple[int, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _CheckoutRetirementBlocked:
    """A live or ambiguous checkout prevents irreversible roster release."""

    reason: str


@dataclass(frozen=True, slots=True)
class _RetryLockedRepair:
    """A checkout row appeared while the operation waited for the event lock."""


def requires_curated_group_certification(event):
    """Whether selection/payment must be backed by the new group workflow."""

    return bool(event.uses_curated_registration and event.group_size)


def _event_id(event_or_id):
    event_id = getattr(event_or_id, "pk", event_or_id)
    if not event_id:
        raise ValidationError("Save the event before generating curated groups.")
    return event_id


def _lock_event_state(event_or_id):
    """Lock and return an event plus all group state in the global order."""

    event_id = _event_id(event_or_id)
    event = MeetupEvent.objects.select_for_update().get(pk=event_id)

    # Lock every registration, not only current candidates.  Cancellation,
    # selection and check-in also use this event-wide ordered batch, so a row
    # cannot enter or leave the candidate set midway through projection.
    list(
        EventRegistration.objects.select_for_update()
        .filter(event_id=event_id)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    groups = list(
        CuratedEventGroup.objects.select_for_update()
        .filter(event_id=event_id)
        .order_by("generation", "group_number", "pk")
    )
    list(
        CuratedEventGroupMembership.objects.select_for_update()
        .filter(event_id=event_id)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    list(
        CuratedEventPairing.objects.select_for_update()
        .filter(event_id=event_id)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    list(
        CuratedEventPairingParticipant.objects.select_for_update()
        .filter(event_id=event_id)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    return event, groups


def lock_event_group_payment_state(event_or_id):
    """Public payment bridge; caller must already be inside ``atomic``.

    Payment paths first lock their ``PaymentTransaction`` rows, then call here
    before locking/reading any registration.  Group lifecycle paths do not lock
    payment rows, so the combined global order is:

        payment transactions -> event -> registrations -> group state
    """

    return _lock_event_state(event_or_id)


def _current_active_groups(groups):
    active = [group for group in groups if group.status in ACTIVE_GROUP_STATUSES]
    if not active:
        return []
    generation = max(group.generation for group in active)
    return [group for group in active if group.generation == generation]


def _projection_audit(projected_group):
    """Build the lifecycle method's whitelisted, non-identifying evidence."""

    return {
        "fairness_decision": {
            "min_required": projected_group.minimum_dates_required,
            "min_achieved": projected_group.minimum_dates_achieved,
            "target_requested": projected_group.target_dates_requested,
            "members_meeting_target": projected_group.members_meeting_target,
            "target_achieved": projected_group.target_achieved,
            "track_size": projected_group.compatibility_track_size,
            "track_ordinal": projected_group.group_ordinal_in_track,
            "underserved_priority": projected_group.underserved_priority,
            "alternative_scarcity_score": projected_group.alternative_scarcity_score,
            "one_drop_resilient": projected_group.one_drop_resilient,
            "pinned_member_count": len(projected_group.pinned_registration_ids),
        },
    }


def _projected_generation_signature(projection):
    """Canonical non-PII structure produced by the deterministic projector."""

    return tuple(
        (
            tuple(projected_group.registration_ids),
            tuple(
                (
                    scheduled_round.number,
                    table_number,
                    min(
                        scheduled_pair.registration_a_id,
                        scheduled_pair.registration_b_id,
                    ),
                    max(
                        scheduled_pair.registration_a_id,
                        scheduled_pair.registration_b_id,
                    ),
                )
                for scheduled_round in projected_group.rounds
                for table_number, scheduled_pair in enumerate(
                    scheduled_round.pairs, start=1
                )
            ),
            _projection_audit(projected_group)["fairness_decision"],
        )
        for projected_group in projection.viable_groups
    )


def _stored_generation_signature(groups):
    """Canonical structure of one stored draft generation."""

    signature = []
    for group in sorted(groups, key=lambda item: (item.group_number, item.pk)):
        member_ids = tuple(
            group.memberships.filter(released_at__isnull=True)
            .order_by("position", "pk")
            .values_list("registration_id", flat=True)
        )
        pair_rows = []
        for pairing in group.pairings.prefetch_related("participants").order_by(
            "round_number", "table_number", "pk"
        ):
            participant_ids = sorted(
                participant.registration_id
                for participant in pairing.participants.all()
            )
            if len(participant_ids) != 2:
                raise ValidationError(
                    "The stored draft schedule no longer has two people at every table."
                )
            pair_rows.append(
                (
                    pairing.round_number,
                    pairing.table_number,
                    participant_ids[0],
                    participant_ids[1],
                )
            )
        signature.append(
            (
                member_ids,
                tuple(pair_rows),
                group.audit_data.get("fairness_decision"),
            )
        )
    return tuple(signature)


def _validate_pre_round_event(event, *, action, allow_during_late_check_in=False):
    """Refuse lifecycle work once cancellation or delivered round one wins."""

    if event.is_cancelled:
        raise ValidationError(f"Cannot {action} curated groups for a cancelled event.")
    if event.curated_rounds_started_at is not None:
        raise ValidationError(
            f"Cannot {action} curated groups after round one has started."
        )
    now = timezone.now()
    if allow_during_late_check_in:
        if event.end_time <= now:
            raise ValidationError(
                f"Cannot {action} curated groups after the event has ended."
            )
        return
    if event.date_time <= now:
        raise ValidationError(
            f"Cannot {action} curated groups after the scheduled start."
        )


def _validate_selection_window_closed(event, *, groups=()):
    """Keep final selection fair to everyone who could still apply."""

    if timezone.now() < event.registration_deadline:
        raise ValidationError(
            "Close the application window before selecting and inviting groups."
        )
    if any(group.created_at < event.registration_deadline for group in groups):
        raise ValidationError(
            "Regenerate the projection after the application deadline so every "
            "eligible applicant is considered."
        )


def _store_group_schedule(group, projected_group, *, actor=None):
    memberships = [
        CuratedEventGroupMembership(
            event_id=group.event_id,
            group=group,
            registration_id=registration_id,
            position=position,
            assigned_by=actor,
        )
        for position, registration_id in enumerate(
            projected_group.registration_ids, start=1
        )
    ]
    CuratedEventGroupMembership.objects.bulk_create(memberships)

    pairings = []
    scheduled_pairs = []
    for scheduled_round in projected_group.rounds:
        for table_number, scheduled_pair in enumerate(scheduled_round.pairs, start=1):
            pairings.append(
                CuratedEventPairing(
                    event_id=group.event_id,
                    group=group,
                    round_number=scheduled_round.number,
                    table_number=table_number,
                )
            )
            scheduled_pairs.append(scheduled_pair)
    CuratedEventPairing.objects.bulk_create(pairings)

    participants = []
    for pairing, scheduled_pair in zip(pairings, scheduled_pairs, strict=True):
        participants.extend(
            (
                CuratedEventPairingParticipant(
                    event_id=group.event_id,
                    group=group,
                    pairing=pairing,
                    round_number=pairing.round_number,
                    registration_id=scheduled_pair.registration_a_id,
                    seat=CuratedEventPairingParticipant.SEAT_A,
                ),
                CuratedEventPairingParticipant(
                    event_id=group.event_id,
                    group=group,
                    pairing=pairing,
                    round_number=pairing.round_number,
                    registration_id=scheduled_pair.registration_b_id,
                    seat=CuratedEventPairingParticipant.SEAT_B,
                ),
            )
        )
    CuratedEventPairingParticipant.objects.bulk_create(participants)


def generate_group_projection(event_or_id, *, actor=None, deterministic_seed=None):
    """Project and atomically publish a complete replacement generation.

    A locked generation is final.  Drafts and provisional generations may be
    replaced, but only if the projector retains every already invited, paid or
    checked-in registration.  When such pinned people exist, the replacement is
    certified as provisional inside the same transaction so no payment window
    opens onto a draft or stale generation. A degraded generation must instead
    use :func:`repair_degraded_event_groups`, which retires checkouts before it
    invokes the private verified-reprojection path below.
    """

    return _generate_group_projection(
        event_or_id,
        actor=actor,
        deterministic_seed=deterministic_seed,
        allow_degraded_replacement=False,
    )


@transaction.atomic
def _generate_group_projection(
    event_or_id,
    *,
    actor=None,
    deterministic_seed=None,
    allow_degraded_replacement,
):
    """Publish a projection; degraded replacement is repair-only."""

    event, existing_groups = _lock_event_state(event_or_id)
    _validate_pre_round_event(event, action="generate")
    if not requires_curated_group_certification(event):
        raise ValidationError(
            "Set a valid group size on a curated speed-dating event first."
        )
    _validate_selection_window_closed(event)

    active_groups = [
        group for group in existing_groups if group.status in ACTIVE_GROUP_STATUSES
    ]
    if any(group.status == CuratedEventGroup.STATUS_LOCKED for group in active_groups):
        raise ValidationError(
            "The current generation is locked for the evening and cannot be reprojected."
        )
    if not allow_degraded_replacement and any(
        group.status == CuratedEventGroup.STATUS_DEGRADED for group in existing_groups
    ):
        raise ValidationError(
            "A degraded generation must use the audited repair action so every "
            "dropped member checkout is retired before roster release."
        )

    projection = project_event_groups(event, deterministic_seed=deterministic_seed)
    if not projection.viable_groups:
        capacity_suffix = (
            " within the event gender pool caps" if event.gender_limits_active else ""
        )
        raise ValidationError(
            "No viable group currently gives every selected member five compatible "
            f"dates{capacity_suffix}."
        )
    if not projection.retains_all_pinned:
        count = len(projection.pinned_unassigned_registration_ids)
        raise ValidationError(
            f"Reprojection would displace {count} invited, paid or checked-in "
            "member(s); the existing generation was left unchanged."
        )

    preserve_provisional = bool(projection.pinned_registration_ids)
    next_generation = (
        max((group.generation for group in existing_groups), default=0) + 1
    )
    now = timezone.now()
    replaceable_groups = [
        group for group in existing_groups if group.status in REPLACEABLE_GROUP_STATUSES
    ]
    old_group_ids = [group.pk for group in replaceable_groups]
    if old_group_ids:
        # Canonical atomic-reprojection bypass: ordinary child save/delete is
        # intentionally frozen on a provisional group.  All replacement data
        # is created and certified below before this transaction can commit.
        degraded_ids = [
            group.pk
            for group in replaceable_groups
            if group.status == CuratedEventGroup.STATUS_DEGRADED
        ]
        for group in replaceable_groups:
            if group.pk in degraded_ids:
                group.release_degraded_memberships_for_remedy(
                    by=actor,
                    reason="Superseded by atomic curated-group reprojection.",
                )
        CuratedEventGroupMembership.objects.filter(
            group_id__in=set(old_group_ids) - set(degraded_ids),
            released_at__isnull=True,
        ).update(
            released_at=now,
            released_by=actor,
            release_reason="Superseded by atomic curated-group reprojection.",
        )
        CuratedEventGroup.objects.filter(pk__in=old_group_ids).update(
            status=CuratedEventGroup.STATUS_CANCELLED,
            cancelled_at=now,
            cancelled_by=actor,
            cancellation_reason="Superseded by atomic curated-group reprojection.",
        )

    stored_groups = []
    for group_number, projected_group in enumerate(projection.viable_groups, start=1):
        group = CuratedEventGroup(
            event=event,
            generation=next_generation,
            group_number=group_number,
            policy_version=projection.policy_version,
            seed=projection.deterministic_seed,
            created_by=actor,
        )
        group.audit_data = _projection_audit(projected_group)
        group.save()
        _store_group_schedule(group, projected_group, actor=actor)
        summary = group.schedule_viability()
        if preserve_provisional:
            group.mark_provisional(by=actor, audit_data=group.audit_data)
        else:
            summary["projection_input_digest"] = projection.input_digest
            group.viability_summary = summary
            group.save(update_fields=["viability_summary", "updated_at"])
        stored_groups.append(group)

    status = (
        CuratedEventGroup.STATUS_PROVISIONAL
        if preserve_provisional
        else CuratedEventGroup.STATUS_DRAFT
    )
    return StoredProjection(
        generation=next_generation,
        group_ids=tuple(group.pk for group in stored_groups),
        status=status,
        projection=projection,
    )


@transaction.atomic
def approve_current_generation(event_or_id, *, actor=None):
    """Certify every group in the current generation and return invitees."""

    event, groups = _lock_event_state(event_or_id)
    _validate_pre_round_event(event, action="approve")
    current = _current_active_groups(groups)
    if not current:
        raise ValidationError("Generate a viable curated-group projection first.")
    if any(group.status == CuratedEventGroup.STATUS_LOCKED for group in current):
        raise ValidationError("The current curated-group generation is already locked.")
    _validate_selection_window_closed(event, groups=current)

    draft_groups = [
        group for group in current if group.status == CuratedEventGroup.STATUS_DRAFT
    ]
    if draft_groups:
        if len(draft_groups) != len(current):
            raise ValidationError(
                "A current generation cannot mix draft and approved groups."
            )
        refreshed = project_event_groups(
            event,
            deterministic_seed=current[0].seed,
        )
        input_digests = {
            group.viability_summary.get("projection_input_digest")
            for group in draft_groups
        }
        if (
            input_digests != {refreshed.input_digest}
            or any(
                group.policy_version != refreshed.policy_version for group in current
            )
            or any(group.seed != refreshed.deterministic_seed for group in current)
            or _stored_generation_signature(current)
            != _projected_generation_signature(refreshed)
        ):
            raise ValidationError(
                "Applications, preferences or event settings changed after this "
                "draft was generated. Regenerate before selecting anyone."
            )

    for group in current:
        if group.status == CuratedEventGroup.STATUS_DRAFT:
            fairness_decision = group.audit_data.get("fairness_decision")
            group.mark_provisional(
                by=actor,
                audit_data={"fairness_decision": fairness_decision},
            )
            continue
        if group.status != CuratedEventGroup.STATUS_PROVISIONAL:
            raise ValidationError("Only a complete draft generation can be approved.")
        summary = group.schedule_viability(evaluate_preferences=False)
        if summary["schedule_digest"] != group.schedule_digest:
            raise ValidationError(
                "A provisional roster or schedule no longer matches its certification."
            )

    group_ids = tuple(group.pk for group in current)
    applied_ids = tuple(
        CuratedEventGroupMembership.objects.filter(
            group_id__in=group_ids,
            released_at__isnull=True,
            registration__status="applied",
        )
        .order_by("registration_id")
        .values_list("registration_id", flat=True)
    )
    return ApprovedGeneration(
        generation=current[0].generation,
        group_ids=group_ids,
        applied_registration_ids=applied_ids,
    )


@transaction.atomic
def get_approved_current_generation(event_or_id):
    """Return invitees only when staff already approved the whole generation.

    This deliberately has no transition side effect.  The admin's Invite
    action must never turn a just-generated DRAFT into a payable group and
    thereby bypass the separate human review/Approve decision.
    """

    event, groups = _lock_event_state(event_or_id)
    _validate_pre_round_event(event, action="invite")
    current = _current_active_groups(groups)
    if not current:
        raise ValidationError("Approve a viable curated-group generation first.")
    _validate_selection_window_closed(event, groups=current)
    if any(group.status != CuratedEventGroup.STATUS_PROVISIONAL for group in current):
        raise ValidationError(
            "Every current group must be explicitly approved before invitations."
        )
    for group in current:
        summary = group.schedule_viability(evaluate_preferences=False)
        if summary["schedule_digest"] != group.schedule_digest:
            raise ValidationError(
                "A provisional roster or schedule no longer matches its certification."
            )

    group_ids = tuple(group.pk for group in current)
    applied_ids = tuple(
        CuratedEventGroupMembership.objects.filter(
            group_id__in=group_ids,
            released_at__isnull=True,
            registration__status="applied",
        )
        .order_by("registration_id")
        .values_list("registration_id", flat=True)
    )
    return ApprovedGeneration(
        generation=current[0].generation,
        group_ids=group_ids,
        applied_registration_ids=applied_ids,
    )


@transaction.atomic
def lock_current_generation(event_or_id, *, actor=None):
    """Lock all checked-in groups together before round one.

    Locking freezes the all-evening roster but is not proof that service was
    delivered. ``start_curated_rounds`` owns that separate, explicit marker so
    a group that fails between final check-in and the first conversation still
    receives the pre-service remedy.
    """

    event, groups = _lock_event_state(event_or_id)
    _validate_pre_round_event(
        event,
        action="lock",
        allow_during_late_check_in=True,
    )
    if any(group.status == CuratedEventGroup.STATUS_DEGRADED for group in groups):
        raise ValidationError(
            "Resolve every degraded group before locking the evening or "
            "marking round one as started."
        )
    current = _current_active_groups(groups)
    if not current:
        raise ValidationError("There is no current curated-group generation to lock.")
    if any(group.status != CuratedEventGroup.STATUS_PROVISIONAL for group in current):
        raise ValidationError(
            "Every current group must be provisional before the generation can lock."
        )
    for group in current:
        group.lock(by=actor)
    return tuple(group.pk for group in current)


@transaction.atomic
def start_curated_rounds(event_or_id, *, actor=None):
    """Record the explicit point at which round one is actually delivered."""

    event, groups = _lock_event_state(event_or_id)
    if event.is_cancelled:
        raise ValidationError("Cannot start curated rounds for a cancelled event.")
    if event.curated_rounds_started_at is not None:
        raise ValidationError("Round one has already been marked as started.")
    if event.end_time <= timezone.now():
        raise ValidationError("Cannot start curated rounds after the event has ended.")
    if any(group.status == CuratedEventGroup.STATUS_DEGRADED for group in groups):
        raise ValidationError(
            "Resolve every degraded group before marking round one as started."
        )
    current = _current_active_groups(groups)
    if not current or any(
        group.status != CuratedEventGroup.STATUS_LOCKED for group in current
    ):
        raise ValidationError(
            "Lock every checked-in group before marking round one as started."
        )

    started_at = timezone.now()
    MeetupEvent.objects.filter(pk=event.pk).update(
        curated_rounds_started_at=started_at,
        curated_rounds_started_by=actor,
    )
    return tuple(group.pk for group in current)


def registration_has_certified_payable_group(registration, *, event=None):
    """Return whether a configured curated seat still has a valid proof.

    Used both before checkout creation and after capture.  Re-validating the
    structural digest catches cancellations, account erasure and any stale
    generation without re-reading mutable profile/preferences after the group
    was certified.
    """

    event = event or registration.event
    if not requires_curated_group_certification(event):
        return True

    # A locked all-evening group may become structurally incomplete only
    # after delivery (for example through GDPR erasure).  The explicit round
    # one marker plus the group's audited LOCKED origin proves this attendee
    # received the service; a late-arriving capture must be recorded normally,
    # not converted into a refund remedy for an evening already delivered.
    if (
        event.curated_rounds_started_at is not None
        and registration.status == "attended"
    ):
        delivered_membership = (
            CuratedEventGroupMembership.objects.select_related("group")
            .filter(
                event_id=event.pk,
                registration_id=registration.pk,
                released_at__isnull=True,
                group__status=CuratedEventGroup.STATUS_DEGRADED,
            )
            .order_by("-group__generation", "pk")
            .first()
        )
        if (
            delivered_membership is not None
            and delivered_membership.group.audit_data.get("degradation", {}).get(
                "from_status"
            )
            == CuratedEventGroup.STATUS_LOCKED
        ):
            return True

    latest_generation = (
        CuratedEventGroup.objects.filter(
            event_id=event.pk,
            status__in=PAYABLE_GROUP_STATUSES,
        )
        .order_by("-generation")
        .values_list("generation", flat=True)
        .first()
    )
    if latest_generation is None:
        return False
    membership = (
        CuratedEventGroupMembership.objects.select_related("group")
        .filter(
            event_id=event.pk,
            registration_id=registration.pk,
            released_at__isnull=True,
            group__generation=latest_generation,
            group__status__in=PAYABLE_GROUP_STATUSES,
        )
        .first()
    )
    if membership is None:
        return False
    roster_ids = CuratedEventGroupMembership.objects.filter(
        group_id=membership.group_id,
        released_at__isnull=True,
    ).values_list("registration_id", flat=True)
    if (
        EventRegistration.objects.filter(pk__in=roster_ids)
        .exclude(status__in=("pending", "confirmed", "attended"))
        .exists()
    ):
        return False
    try:
        summary = membership.group.schedule_viability(evaluate_preferences=False)
    except ValidationError:
        return False
    return summary["schedule_digest"] == membership.group.schedule_digest


def repair_degraded_event_groups(event_or_id, *, actor=None):
    """Reproject a degraded pre-event generation or compensate its payers.

    Provider I/O deliberately sits between short atomic phases. The first phase
    leases known checkout claims only after taking the global payment -> event
    -> roster -> claim lock order. The final phase revalidates that same state
    and releases the roster only after every still-pending SumUp checkout is
    proven non-payable. Locked/started groups are never reassigned.
    """

    from crush_lu.services.sumup import SumUpClient

    event_id = _event_id(event_or_id)
    retirement_proofs = {}
    # claim PK -> (provider checkout ID, transaction reference). Once this
    # invocation leases a claim, it owns that exact snapshot until a later
    # locked phase deletes it safely or restores it ACTIVE and blocks.
    leased_claims = {}
    client = None
    # A checkout worker that was already between its DB phases may publish one
    # new row after our first snapshot. Re-running from the payment lock closes
    # that race without ever taking payment locks after the event lock.
    for _attempt in range(4):
        with transaction.atomic():
            result = _repair_degraded_event_groups_locked(
                event_id,
                actor=actor,
                retirement_proofs=retirement_proofs,
                leased_claims=leased_claims,
            )
        if isinstance(result, DegradedEventRemedy):
            return result
        if isinstance(result, _RetryLockedRepair):
            continue
        if isinstance(result, _CheckoutRetirementBlocked):
            raise ValidationError(result.reason)

        if client is None:
            client = SumUpClient()
        leased_claims.update(
            {
                claim_id: (checkout_id, transaction_reference)
                for claim_id, checkout_id, transaction_reference in result.leased_claims
            }
        )
        for checkout_id in result.checkout_ids:
            retirement_proofs[checkout_id] = client.ensure_checkout_not_payable(
                checkout_id
            )

    raise ValidationError(
        "Checkout state kept changing while the degraded group was being "
        "repaired. The group remains unavailable; retry the repair action."
    )


def _reconcile_owned_checkout_leases(
    *,
    locked_payments,
    leased_claims,
    retirement_proofs,
    claim_model,
    payment_model,
):
    """Finish or safely restore every claim leased by this repair invocation."""

    if not leased_claims:
        return None

    claims_by_id = {
        claim.pk: claim
        for claim in claim_model.objects.select_for_update()
        .filter(pk__in=leased_claims)
        .exclude(state=claim_model.State.RETIRED)
        .order_by("pk")
    }
    unresolved = []
    for claim_id, (checkout_id, transaction_reference) in leased_claims.items():
        claim = claims_by_id.get(claim_id)
        matching_payments = [
            payment
            for payment in locked_payments
            if payment.transaction_reference == transaction_reference
            or payment.sumup_checkout_id == checkout_id
        ]
        pending_payments = [
            payment
            for payment in matching_payments
            if payment.status == payment_model.Status.PENDING
        ]
        has_terminal_payment = any(
            payment.status != payment_model.Status.PENDING
            for payment in matching_payments
        )
        snapshot_matches = claim is None or (
            claim.provider_checkout_id == checkout_id
            and claim.transaction_reference == transaction_reference
        )
        provider_safe = retirement_proofs.get(checkout_id, False)

        if provider_safe:
            for payment in pending_payments:
                payment.status = payment_model.Status.CANCELLED
                payment.failure_reason = (
                    "Certified curated group changed while its checkout was "
                    "being retired; SumUp proved this checkout non-payable."
                )
                payment.save(update_fields=["status", "failure_reason", "updated_at"])

        safe_to_finish = snapshot_matches and (
            provider_safe or (has_terminal_payment and not pending_payments)
        )
        if safe_to_finish:
            if claim is not None:
                claim.delete()
            continue

        if claim is not None and claim.state == claim_model.State.RETIRING:
            claim.state = claim_model.State.ACTIVE
            claim.save(update_fields=["state"])
        unresolved.append(checkout_id)

    if unresolved:
        return _CheckoutRetirementBlocked(
            reason=(
                "The degraded roster was not released because provider checkouts "
                "not proven closed were restored for safe recovery: "
                + ", ".join(sorted(set(unresolved)))
                + "."
            )
        )
    return None


def _repair_degraded_event_groups_locked(
    event_id, *, actor=None, retirement_proofs=None, leased_claims=None
):
    """Run one locked repair phase without making provider network calls."""

    from crush_lu.models.payments import (
        EventCheckoutCreationClaim,
        PaymentTransaction,
    )
    from crush_lu.services.credits import (
        credit_registration_for_unavailable_curated_group,
    )
    from crush_lu.services.curated_group_notifications import (
        deliver_curated_group_notifications,
        enqueue_remedy_notification,
        enqueue_withdrawal_notification,
    )

    retirement_proofs = retirement_proofs or {}
    if leased_claims is None:
        leased_claims = {}
    registration_ids = list(
        EventRegistration.objects.filter(event_id=event_id)
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    locked_payments = list(
        PaymentTransaction.objects.select_for_update(of=("self",))
        .filter(
            Q(event_id=event_id) | Q(event_registration_id__in=registration_ids),
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
        )
        .order_by("pk")
    )
    event, groups = _lock_event_state(event_id)

    # A checkout may have been inserted while this transaction waited for the
    # event row. Do not lock it in reverse order; release everything and begin
    # again so the new row is owned before the event/roster locks.
    current_payment_ids = set(
        PaymentTransaction.objects.filter(
            Q(event_id=event_id) | Q(event_registration_id__in=registration_ids),
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
        ).values_list("pk", flat=True)
    )
    if current_payment_ids != {payment.pk for payment in locked_payments}:
        return _RetryLockedRepair()

    lease_block = _reconcile_owned_checkout_leases(
        locked_payments=locked_payments,
        leased_claims=leased_claims,
        retirement_proofs=retirement_proofs,
        claim_model=EventCheckoutCreationClaim,
        payment_model=PaymentTransaction,
    )
    if lease_block is not None:
        return lease_block

    degraded = [
        group for group in groups if group.status == CuratedEventGroup.STATUS_DEGRADED
    ]
    degraded_ids = tuple(group.pk for group in degraded)
    if not degraded:
        return DegradedEventRemedy(action="noop", degraded_group_ids=())
    if event.is_cancelled:
        # The event-cancellation workflow owns its premium/cash remedy. Do not
        # issue a second, differently valued credit here.
        return DegradedEventRemedy(
            action="event_cancelled", degraded_group_ids=degraded_ids
        )
    had_locked_roster = any(
        group.audit_data.get("degradation", {}).get("from_status")
        == CuratedEventGroup.STATUS_LOCKED
        for group in degraded
    )
    # Locking is final roster certification, not delivery. Only the explicit
    # staff action taken when round one actually begins closes the automatic
    # pre-service remedy, regardless of the scheduled wall clock.
    delivered = bool(event.curated_rounds_started_at)
    if delivered:
        # Once round one has started, the promised service has already begun.
        # Later account erasure or retention may make the historical roster
        # incomplete, but it must not mint credits or cash-refund liabilities
        # for an evening that was delivered. Keep the group degraded as a
        # privacy-safe audit fact until normal retention removes it.
        return DegradedEventRemedy(
            action="post_start_audit_only", degraded_group_ids=degraded_ids
        )

    may_reproject = not had_locked_roster and timezone.now() < event.date_time
    replacement_projection = None
    if may_reproject:
        projection = project_event_groups(event)
        if projection.viable_groups and projection.retains_all_pinned:
            replacement_projection = projection

    affected_generations = {}
    for registration_id, generation in (
        CuratedEventGroupMembership.objects.filter(
            group_id__in=degraded_ids,
            released_at__isnull=True,
        )
        .order_by("registration_id")
        .values_list("registration_id", "group__generation")
    ):
        affected_generations[registration_id] = max(
            generation, affected_generations.get(registration_id, 0)
        )
    affected_ids = sorted(affected_generations)
    retirement_ids = affected_ids
    if replacement_projection is not None:
        retained_ids = set(replacement_projection.selected_registration_ids)
        retirement_ids = sorted(set(affected_ids) - retained_ids)
    registrations = {
        registration.pk: registration
        for registration in EventRegistration.objects.filter(pk__in=affected_ids)
        .select_related("event", "user")
        .order_by("pk")
    }

    claims = list(
        EventCheckoutCreationClaim.objects.select_for_update()
        .filter(
            Q(registration_id__in=retirement_ids)
            | Q(registration_id_snapshot__in=retirement_ids)
        )
        .exclude(state=EventCheckoutCreationClaim.State.RETIRED)
        .order_by("pk")
    )
    relevant_payments = [
        payment
        for payment in locked_payments
        if payment.event_registration_id in retirement_ids
    ]
    pending_sumup = [
        payment
        for payment in relevant_payments
        if payment.provider == PaymentTransaction.Provider.SUMUP
        and payment.status == PaymentTransaction.Status.PENDING
    ]
    payments_by_reference = {
        payment.transaction_reference: payment for payment in relevant_payments
    }

    unknown_payment_ids = [
        payment.pk for payment in pending_sumup if not payment.sumup_checkout_id
    ]
    unknown_claim_ids = [
        claim.pk
        for claim in claims
        if claim.payment_method == "card"
        and not claim.provider_checkout_id
        and claim.transaction_reference not in payments_by_reference
    ]
    checkout_ids = {
        payment.sumup_checkout_id
        for payment in pending_sumup
        if payment.sumup_checkout_id
    }
    checkout_ids.update(
        claim.provider_checkout_id
        for claim in claims
        if claim.payment_method == "card"
        and claim.provider_checkout_id
        and (
            claim.transaction_reference not in payments_by_reference
            or payments_by_reference[claim.transaction_reference].status
            == PaymentTransaction.Status.PENDING
        )
    )
    missing_proofs = sorted(checkout_ids - retirement_proofs.keys())
    if missing_proofs:
        # RETIRING makes an in-flight creator compensate a newly-created remote
        # checkout instead of publishing it after the group became unavailable.
        newly_leased_claims = []
        for claim in claims:
            if (
                claim.payment_method == "card"
                and claim.provider_checkout_id in missing_proofs
                and claim.state == EventCheckoutCreationClaim.State.ACTIVE
            ):
                claim.state = EventCheckoutCreationClaim.State.RETIRING
                claim.save(update_fields=["state"])
                newly_leased_claims.append(
                    (
                        claim.pk,
                        claim.provider_checkout_id,
                        claim.transaction_reference,
                    )
                )
        return _CheckoutRetirementPlan(
            checkout_ids=tuple(missing_proofs),
            leased_claims=tuple(newly_leased_claims),
        )

    unproven_checkout_ids = sorted(
        checkout_id
        for checkout_id in checkout_ids
        if not retirement_proofs.get(checkout_id, False)
        and any(
            payment.sumup_checkout_id == checkout_id
            and payment.status == PaymentTransaction.Status.PENDING
            for payment in pending_sumup
        )
    )
    unproven_checkout_ids.extend(
        checkout_id
        for checkout_id in sorted(checkout_ids)
        if not retirement_proofs.get(checkout_id, False)
        and any(
            claim.provider_checkout_id == checkout_id
            and (
                claim.transaction_reference not in payments_by_reference
                or payments_by_reference[claim.transaction_reference].status
                == PaymentTransaction.Status.PENDING
            )
            for claim in claims
        )
        and checkout_id not in unproven_checkout_ids
    )
    if unknown_payment_ids or unknown_claim_ids or unproven_checkout_ids:
        # Leave claims reusable by the provider-safe recovery command/admin
        # retry. No roster, credit, or registration state changes in this path.
        for claim in claims:
            if (
                claim.pk in leased_claims
                and claim.state == EventCheckoutCreationClaim.State.RETIRING
            ):
                claim.state = EventCheckoutCreationClaim.State.ACTIVE
                claim.save(update_fields=["state"])
        details = []
        if unknown_payment_ids:
            details.append(
                "pending payment rows without a provider ID: "
                + ", ".join(map(str, unknown_payment_ids))
            )
        if unknown_claim_ids:
            details.append(
                "ambiguous checkout claims without a provider ID: "
                + ", ".join(map(str, unknown_claim_ids))
            )
        if unproven_checkout_ids:
            details.append(
                "provider checkouts not proven closed: "
                + ", ".join(unproven_checkout_ids)
            )
        return _CheckoutRetirementBlocked(
            reason=(
                "The degraded roster was not released because live checkout "
                "retirement is incomplete (" + "; ".join(details) + ")."
            )
        )

    for payment in relevant_payments:
        if payment.status != PaymentTransaction.Status.PENDING:
            continue
        payment.status = PaymentTransaction.Status.CANCELLED
        payment.failure_reason = (
            "Certified curated group became unavailable; this checkout was "
            "retired before the roster was released."
        )
        payment.save(update_fields=["status", "failure_reason", "updated_at"])

    # Card claims are now either provider-proven safe or backed by a terminal
    # payment row. Credit claims never opened an external checkout. Deleting the
    # lease lets a member pay normally if a later projection selects them again.
    for claim in claims:
        claim.delete()

    if replacement_projection is not None:
        try:
            stored = _generate_group_projection(
                event,
                actor=actor,
                deterministic_seed=replacement_projection.deterministic_seed,
                allow_degraded_replacement=True,
            )
        except ValidationError:
            logger.warning(
                "Could not replace degraded curated groups for event %s after "
                "retiring dropped-member checkouts; leaving the group degraded.",
                event.pk,
                exc_info=True,
            )
            return _CheckoutRetirementBlocked(
                reason=(
                    "Dropped-member checkouts were retired, but the replacement "
                    "projection could not be certified. The group remains degraded."
                )
            )
        return DegradedEventRemedy(
            action="reprojected",
            degraded_group_ids=degraded_ids,
            replacement_generation=stored.generation,
        )

    latest_paid = {}
    for payment in locked_payments:
        if (
            payment.event_registration_id in registrations
            and payment.status == PaymentTransaction.Status.PAID
        ):
            latest_paid[payment.event_registration_id] = payment

    compensated = []
    credit_ids = []
    queued_notice_ids = set()
    for registration_id in affected_ids:
        registration = registrations.get(registration_id)
        if registration is None:
            continue
        paid_payment = latest_paid.get(registration_id)
        if (
            registration.payment_confirmed or paid_payment is not None
        ) and registration.status not in {
            "cancelled",
            "no_show",
        }:
            credits = credit_registration_for_unavailable_curated_group(
                registration,
                payment=paid_payment,
            )
            compensated.append(registration_id)
            credit_ids.extend(credit.pk for credit in credits)
            notice = enqueue_remedy_notification(registration, credits)
            if notice is not None:
                queued_notice_ids.add(notice.pk)
        elif registration.status in {"pending", "confirmed"}:
            EventRegistration.objects.filter(pk=registration_id).update(
                status="applied",
                payment_confirmed=False,
                payment_date=None,
            )
            registration.status = "applied"
            registration.payment_confirmed = False
            registration.payment_date = None
            notice = enqueue_withdrawal_notification(
                registration, affected_generations[registration_id]
            )
            if notice is not None:
                queued_notice_ids.add(notice.pk)

    for group in degraded:
        group.release_degraded_memberships_for_remedy(
            by=actor,
            reason="Certified group unavailable; payers compensated.",
        )
        group.cancel(
            by=actor,
            reason="Certified group unavailable; payers compensated.",
        )

    if queued_notice_ids:
        # Drain exactly this repair's committed outbox rows in bounded slices.
        # A failed early row is attempted once and cannot starve later members;
        # every failure remains durable for the admin recovery action.
        transaction.on_commit(
            partial(
                deliver_curated_group_notifications,
                notice_ids=sorted(queued_notice_ids),
                drain=True,
            )
        )

    return DegradedEventRemedy(
        action="compensated",
        degraded_group_ids=degraded_ids,
        compensated_registration_ids=tuple(compensated),
        credit_ids=tuple(credit_ids),
    )
