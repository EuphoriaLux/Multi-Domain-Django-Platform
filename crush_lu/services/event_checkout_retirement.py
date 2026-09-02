"""Deletion guard sharing the payment -> event -> roster -> claim lock order."""

from django.db.models import Q
from django.db.models.deletion import ProtectedError


def protect_live_event_checkout_deletion(
    *, event_ids, registration_ids, using="default"
):
    """Block a destructive cascade while a provider checkout can still pay."""

    from crush_lu.models.events import EventRegistration, MeetupEvent
    from crush_lu.models.payments import (
        EventCheckoutCreationClaim,
        PaymentTransaction,
    )

    event_ids = sorted({event_id for event_id in event_ids if event_id})
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
    locked_registration_ids = list(
        EventRegistration.objects.using(using)
        .select_for_update()
        .filter(Q(pk__in=registration_ids) | Q(event_id__in=event_ids))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    all_registration_ids = sorted(set(registration_ids).union(locked_registration_ids))
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
    # Detect a payment inserted while this transaction waited for the event
    # row. Creation cannot add another after this point because it needs the
    # same event/registration locks.
    live_pending_ids = set(
        PaymentTransaction.objects.using(using)
        .filter(
            Q(event_registration_id__in=all_registration_ids)
            | Q(event_id__in=event_ids),
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            status=PaymentTransaction.Status.PENDING,
        )
        .values_list("pk", flat=True)
    )
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
