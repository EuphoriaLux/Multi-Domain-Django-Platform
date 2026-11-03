"""Deletion guard sharing the payment -> event -> roster -> claim lock order."""

from django.db.models import Q
from django.db.models.deletion import ProtectedError


def protect_live_event_checkout_deletion(
    *, event_ids, registration_ids, using="default", lock_event_ids=()
):
    """Block a destructive cascade while a provider checkout can still pay.

    Only checkouts the cascade would orphan count: every checkout on an event
    in ``event_ids`` (the event itself is being deleted) and the own checkouts
    of ``registration_ids``. ``lock_event_ids`` are locked with their rosters
    and claims, in the same order, for the caller's later writes; their other
    members' checkouts never block. One member's erasure must not wait on a
    co-registrant's abandoned checkout. If it degrades a curated group, the
    on_commit repair retires the others' checkouts, as it does when a member
    cancels.
    """

    from crush_lu.models.events import EventRegistration, MeetupEvent
    from crush_lu.models.payments import (
        EventCheckoutCreationClaim,
        PaymentTransaction,
    )

    deleted_event_ids = {event_id for event_id in event_ids if event_id}
    event_ids = sorted(
        deleted_event_ids.union(event_id for event_id in lock_event_ids if event_id)
    )
    registration_ids = sorted(
        {registration_id for registration_id in registration_ids if registration_id}
    )
    pending_payments = list(
        PaymentTransaction.objects.using(using)
        .select_for_update(of=("self",))
        .filter(
            Q(event_registration_id__in=registration_ids) | Q(event_id__in=event_ids),
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            status=PaymentTransaction.Status.PENDING,
        )
        .order_by("pk")
    )
    list(
        MeetupEvent.objects.using(using)
        .select_for_update()
        .filter(pk__in=event_ids)
        .order_by("pk")
    )
    locked_registrations = list(
        EventRegistration.objects.using(using)
        .select_for_update()
        .filter(Q(pk__in=registration_ids) | Q(event_id__in=event_ids))
        .order_by("pk")
        .values_list("pk", "event_id")
    )
    all_registration_ids = sorted(
        set(registration_ids).union(pk for pk, _event_id in locked_registrations)
    )
    # The registrations this cascade removes: those named, plus every one of
    # a deleted event, including one created while we waited for its row.
    deleted_registration_ids = set(registration_ids).union(
        pk for pk, event_id in locked_registrations if event_id in deleted_event_ids
    )
    claims = list(
        EventCheckoutCreationClaim.objects.using(using)
        .select_for_update()
        .filter(
            Q(registration_id__in=all_registration_ids)
            | Q(registration_id_snapshot__in=all_registration_ids)
            | Q(event_id_snapshot__in=event_ids)
        )
        .exclude(state=EventCheckoutCreationClaim.State.RETIRED)
        .order_by("pk")
    )
    claims = [
        claim
        for claim in claims
        if claim.registration_id in deleted_registration_ids
        or claim.registration_id_snapshot in deleted_registration_ids
        or claim.event_id_snapshot in deleted_event_ids
    ]
    # Detect a payment inserted while this transaction waited for the event
    # row. Creation cannot add another after this point because it needs the
    # same event/registration locks.
    live_pending_ids = set(
        PaymentTransaction.objects.using(using)
        .filter(
            Q(event_registration_id__in=deleted_registration_ids)
            | Q(event_id__in=deleted_event_ids),
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            status=PaymentTransaction.Status.PENDING,
        )
        .values_list("pk", flat=True)
    )
    pending_payments = [
        payment for payment in pending_payments if payment.pk in live_pending_ids
    ]
    if live_pending_ids != {payment.pk for payment in pending_payments}:
        pending_payments = list(
            PaymentTransaction.objects.using(using)
            .filter(pk__in=live_pending_ids)
            .order_by("pk")
        )
    protected_objects = [*pending_payments, *claims]
    if protected_objects:
        raise ProtectedError(
            "Retire or reconcile every live event checkout before deleting "
            "this registration or event.",
            protected_objects,
        )
