"""Record a cash payment taken at the door for one event seat.

Used by the coach "Mark paid (cash)" button (``views_coach.coach_mark_paid_cash``).
It is the door-shaped slice of what ``EventRegistrationAdmin.save_model`` does
when staff tick ``payment_confirmed``, kept in the same order and with the same
guards, but only for the case a coach meets at the door: an unpaid seat holder
(``pending``, ``confirmed`` or already ``attended``) on a paid, non-cancelled
event. Cancelled rows and cancelled events stay with the admin, whose branches
for them settle credits and resale in ways a door button must not trigger.

Order, as in the admin and the checkout callback:
PaymentTransaction -> MeetupEvent -> curated group state -> EventRegistration.
"""

import logging
import uuid
from functools import partial

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from crush_lu.models.events import EventRegistration, MeetupEvent
from crush_lu.models.payments import EventCheckoutCreationClaim, PaymentTransaction

logger = logging.getLogger(__name__)

# Statuses a coach may record door cash for: the seat is held, and an
# attendee is often scanned in before the cash is counted.
CASH_PAYABLE_STATUSES = ("pending", "confirmed", "attended")


class CashPaymentRefused(Exception):
    """The payment was not recorded; ``str(exc)`` is the coach-facing reason."""


def cash_payment_refusal(registration, event):
    """Why door cash cannot be recorded for this row, or None if it can."""
    if event.is_cancelled:
        return _("This event is cancelled.")
    if event.registration_fee <= 0:
        return _("This event is free; there is nothing to pay.")
    if registration.payment_confirmed:
        return _("This registration is already paid.")
    if registration.status not in CASH_PAYABLE_STATUSES:
        return _("Payment can only be recorded for someone holding a seat.")
    return None


def record_cash_payment(registration_id, *, actor):
    """Record door cash for ``registration_id``; return the registration.

    Raises ``CashPaymentRefused`` with a coach-facing reason and records
    nothing when the row is not payable, when an open card checkout cannot be
    closed (that would be a double charge), or when a curated seat's group is
    no longer certified.
    """
    from crush_lu.services.curated_group_workflow import (
        lock_event_group_payment_state,
        registration_has_certified_payable_group,
        requires_curated_group_certification,
    )
    from crush_lu.services.credits import settle_pending_resale_credit
    from crush_lu.services.sumup import SumUpClient
    from crush_lu.views_payments import (
        _send_member_cancellation_safely,
        _send_registration_confirmation_safely,
    )

    preview = EventRegistration.objects.select_related("event").get(pk=registration_id)
    reason = cash_payment_refusal(preview, preview.event)
    if reason is not None:
        raise CashPaymentRefused(reason)

    # Phase 1, committed on its own: close every open card checkout. A still
    # open checkout beside a cash receipt is the double charge. A SumUp close
    # cannot be rolled back, so the local CANCELLED marks must survive any
    # later refusal. While online payments are paused, a checkout SumUp will
    # not close counts as closed (SumUpClient).
    unclosed = False
    with transaction.atomic():
        open_checkouts = list(
            PaymentTransaction.objects.select_for_update()
            .filter(
                event_registration_id=registration_id,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                status=PaymentTransaction.Status.PENDING,
            )
            .exclude(sumup_checkout_id="")
            .order_by("pk")
        )
        if open_checkouts:
            client = SumUpClient()
            for open_tx in open_checkouts:
                if not client.deactivate_checkout(open_tx.sumup_checkout_id):
                    unclosed = True
                    continue
                open_tx.status = PaymentTransaction.Status.CANCELLED
                open_tx.failure_reason = (
                    "Superseded by a cash payment recorded at the door, so "
                    "this checkout was closed before any card was charged."
                )
                open_tx.save(update_fields=["status", "failure_reason", "updated_at"])
    if unclosed:
        raise CashPaymentRefused(
            _(
                "This member still has an open card checkout that could not be "
                "closed. Recording cash now could charge them twice; ask staff "
                "to check it."
            )
        )

    # Phase 2: record the cash. Lock order as everywhere: payments -> event ->
    # curated group state -> registrations -> checkout claims.
    late_checkout = _(
        "A card checkout was opened for this seat a moment ago. Try again in "
        "a few seconds."
    )
    with transaction.atomic():
        # A checkout created after phase 1 (its row inserted while we waited)
        # would stay payable beside the cash. Refuse rather than race it.
        if (
            PaymentTransaction.objects.select_for_update()
            .filter(
                event_registration_id=registration_id,
                purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
                status=PaymentTransaction.Status.PENDING,
            )
            .exclude(sumup_checkout_id="")
            .exists()
        ):
            raise CashPaymentRefused(late_checkout)

        event = MeetupEvent.objects.select_for_update().get(pk=preview.event_id)
        if requires_curated_group_certification(event):
            event, _groups = lock_event_group_payment_state(event.pk)

        lock_ids = [registration_id]
        if preview.resale_source_registration_id:
            lock_ids.append(preview.resale_source_registration_id)
        locked = {
            row.pk: row
            for row in EventRegistration.objects.select_for_update()
            .filter(pk__in=lock_ids)
            .order_by("pk")
        }
        registration = locked[registration_id]

        reason = cash_payment_refusal(registration, event)
        if reason is not None:
            raise CashPaymentRefused(reason)
        if requires_curated_group_certification(
            event
        ) and not registration_has_certified_payable_group(registration, event=event):
            raise CashPaymentRefused(
                _(
                    "This curated seat no longer belongs to a complete group. "
                    "Ask staff to repair the group first."
                )
            )

        # A checkout still being created holds a claim before its payment row
        # exists; the phase-2 recheck above cannot see it yet.
        if (
            EventCheckoutCreationClaim.objects.select_for_update()
            .filter(registration_id=registration_id)
            .exists()
        ):
            raise CashPaymentRefused(late_checkout)

        paid_at = timezone.now()
        registration.payment_confirmed = True
        registration.payment_date = paid_at
        if registration.status == "pending":
            registration.status = "confirmed"
        registration.save(update_fields=["payment_confirmed", "payment_date", "status"])

        PaymentTransaction.objects.create(
            # The admin's CRUSH-MANUAL-<registration id>- shape: reports recover
            # the seat from it once the registration FK is gone.
            transaction_reference=(
                f"CRUSH-MANUAL-{registration.pk}-{uuid.uuid4().hex[:8]}"
            ),
            provider=PaymentTransaction.Provider.MANUAL,
            amount=event.registration_fee,
            currency="EUR",
            status=PaymentTransaction.Status.PAID,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            user=registration.user,
            event_registration=registration,
            paid_at=paid_at,
            raw_response={
                "recorded_by_coach_user_id": getattr(actor, "pk", None),
                "method": "cash_at_door",
            },
        )

        source = locked.get(registration.resale_source_registration_id)
        resale_credits = settle_pending_resale_credit(
            registration, source_registration=source
        )
        if resale_credits:
            transaction.on_commit(
                partial(
                    _send_member_cancellation_safely,
                    source or registration,
                    resale_credits,
                    recipient_user=resale_credits[0].user,
                )
            )
        transaction.on_commit(
            partial(_send_registration_confirmation_safely, registration)
        )

    logger.info(
        "Cash payment recorded at the door for registration %s by user %s",
        registration_id,
        getattr(actor, "pk", None),
    )
    return registration
