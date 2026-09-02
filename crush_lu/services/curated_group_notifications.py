"""Durable, bounded delivery for curated-group transactional email."""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crush_lu.models.credits import CrushCredit
from crush_lu.models.events import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    CuratedGroupNotification,
    EventRegistration,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryBatch:
    attempted: int
    sent: int
    failed: int
    cancelled: int
    remaining: int


def enqueue_selection_notifications(generation_by_registration):
    """Persist one invitation notice per registration and group generation."""

    generation_by_registration = {
        int(registration_id): int(generation)
        for registration_id, generation in generation_by_registration.items()
    }
    if not generation_by_registration:
        return 0
    rows = EventRegistration.objects.filter(
        pk__in=generation_by_registration
    ).values_list("pk", "event_id")
    notices = [
        CuratedGroupNotification(
            event_id=event_id,
            registration_id=registration_id,
            event_id_snapshot=event_id,
            registration_id_snapshot=registration_id,
            kind=CuratedGroupNotification.Kind.SELECTION,
            dedupe_key=(
                f"selection:{registration_id}:"
                f"{generation_by_registration[registration_id]}"
            ),
            payload={"generation": generation_by_registration[registration_id]},
        )
        for registration_id, event_id in rows
    ]
    created = CuratedGroupNotification.objects.bulk_create(
        notices, ignore_conflicts=True
    )
    return len(created)


def enqueue_remedy_notification(registration, credits):
    """Persist one remedy notice for the exact payment value returned."""

    credits = [credit for credit in credits if credit is not None]
    if not credits:
        return None
    source_payment = next(
        (credit.source_payment for credit in credits if credit.source_payment_id),
        None,
    )
    identity = (
        f"payment:{source_payment.pk}"
        if source_payment is not None
        else f"credits:{min(credit.pk for credit in credits)}"
    )
    notice, _created = CuratedGroupNotification.objects.get_or_create(
        dedupe_key=f"remedy:{registration.pk}:{identity}",
        defaults={
            "event": registration.event,
            "registration": registration,
            "source_payment": source_payment,
            "event_id_snapshot": registration.event_id,
            "registration_id_snapshot": registration.pk,
            "kind": CuratedGroupNotification.Kind.REMEDY,
            "payload": {"credit_ids": [credit.pk for credit in credits]},
        },
    )
    return notice


def enqueue_withdrawal_notification(registration, generation):
    """Persist one unpaid-member withdrawal notice for a group generation."""

    generation = int(generation)
    notice, _created = CuratedGroupNotification.objects.get_or_create(
        dedupe_key=(
            f"withdrawal:{registration.event_id}:{registration.pk}:{generation}"
        ),
        defaults={
            "event_id": registration.event_id,
            "registration": registration,
            "event_id_snapshot": registration.event_id,
            "registration_id_snapshot": registration.pk,
            "kind": CuratedGroupNotification.Kind.WITHDRAWAL,
            "payload": {"generation": generation},
        },
    )
    return notice


def _pending_notices(
    *, event_ids=None, registration_ids=None, kinds=None, notice_ids=None
):
    lease_minutes = max(
        5, int(getattr(settings, "CURATED_NOTIFICATION_LEASE_MINUTES", 10))
    )
    stale_before = timezone.now() - timedelta(minutes=lease_minutes)
    queryset = CuratedGroupNotification.objects.filter(
        Q(status=CuratedGroupNotification.Status.PENDING)
        | Q(
            status=CuratedGroupNotification.Status.SENDING,
            claimed_at__lt=stale_before,
        )
        | Q(
            status=CuratedGroupNotification.Status.SENDING,
            claimed_at__isnull=True,
        )
    )
    if event_ids is not None:
        queryset = queryset.filter(event_id_snapshot__in=event_ids)
    if registration_ids is not None:
        queryset = queryset.filter(registration_id_snapshot__in=registration_ids)
    if kinds is not None:
        queryset = queryset.filter(kind__in=kinds)
    if notice_ids is not None:
        queryset = queryset.filter(pk__in=notice_ids)
    return queryset.order_by("created_at", "pk")


def _claim_notice(notice_id):
    lease_minutes = max(
        5, int(getattr(settings, "CURATED_NOTIFICATION_LEASE_MINUTES", 10))
    )
    stale_before = timezone.now() - timedelta(minutes=lease_minutes)
    with transaction.atomic():
        notice = (
            CuratedGroupNotification.objects.select_for_update()
            .filter(pk=notice_id)
            .first()
        )
        if notice is None or notice.status in {
            CuratedGroupNotification.Status.SENT,
            CuratedGroupNotification.Status.CANCELLED,
        }:
            return None
        if (
            notice.status == CuratedGroupNotification.Status.SENDING
            and notice.claimed_at is not None
            and notice.claimed_at >= stale_before
        ):
            return None
        notice.status = CuratedGroupNotification.Status.SENDING
        notice.claim_token = uuid.uuid4()
        notice.claimed_at = timezone.now()
        notice.attempt_count += 1
        notice.last_error = ""
        notice.save(
            update_fields=[
                "status",
                "claim_token",
                "claimed_at",
                "attempt_count",
                "last_error",
            ]
        )
        return notice.pk, notice.claim_token


def _deliver_selection_notice(notice):
    registration = (
        EventRegistration.objects.select_related("event", "user")
        .filter(pk=notice.registration_id)
        .first()
    )
    if registration is None or registration.status not in {"pending", "confirmed"}:
        return "stale", "Registration is no longer awaiting selection mail."
    generation = notice.payload.get("generation")
    membership_exists = CuratedEventGroupMembership.objects.filter(
        registration_id=registration.pk,
        released_at__isnull=True,
        group__generation=generation,
        group__status__in=(
            CuratedEventGroup.STATUS_PROVISIONAL,
            CuratedEventGroup.STATUS_LOCKED,
        ),
    ).exists()
    if not membership_exists:
        return "stale", "The selected group generation is no longer current."

    from crush_lu.services.curated_group_workflow import (
        registration_has_certified_payable_group,
    )

    if not registration_has_certified_payable_group(registration):
        return "stale", "The selected group is no longer certified."
    if registration.status == "pending":
        from crush_lu.email_helpers import send_event_payment_pending_notification

        delivered = send_event_payment_pending_notification(registration)
    else:
        from crush_lu.email_helpers import send_event_registration_confirmation

        delivered = send_event_registration_confirmation(registration)
    return ("sent", "") if delivered else ("retry", "Mail delivery returned false.")


def _deliver_remedy_notice(notice):
    registration = (
        EventRegistration.objects.select_related("event", "user")
        .filter(pk=notice.registration_id)
        .first()
    )
    if registration is None:
        return "stale", "Registration was erased before remedy delivery."
    credit_ids = [int(pk) for pk in notice.payload.get("credit_ids", [])]
    credits = list(
        CrushCredit.objects.filter(
            pk__in=credit_ids,
            user_id=registration.user_id,
            reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
        ).order_by("pk")
    )
    if len(credits) != len(set(credit_ids)):
        return "retry", "The complete remedy ledger could not be loaded."
    from crush_lu.email_helpers import send_curated_group_payment_remedy

    delivered = send_curated_group_payment_remedy(registration, credits)
    return ("sent", "") if delivered else ("retry", "Mail delivery returned false.")


def _deliver_withdrawal_notice(notice):
    registration = (
        EventRegistration.objects.select_related("event", "user")
        .filter(
            pk=notice.registration_id_snapshot,
            event_id=notice.event_id_snapshot,
        )
        .first()
    )
    if registration is None:
        return "stale", "Registration no longer belongs to the notice event."
    if registration.status != "applied":
        return "stale", "Registration is no longer awaiting reconsideration."
    if registration.payment_confirmed:
        return "stale", "Registration is now paid; an unpaid notice is invalid."
    try:
        withdrawn_generation = int(notice.payload["generation"])
    except (KeyError, TypeError, ValueError):
        return "stale", "Withdrawal notice has no valid group generation."

    active_membership_exists = CuratedEventGroupMembership.objects.filter(
        event_id=notice.event_id_snapshot,
        registration_id=registration.pk,
        released_at__isnull=True,
        group__generation__gte=withdrawn_generation,
        group__status__in=(
            CuratedEventGroup.STATUS_PROVISIONAL,
            CuratedEventGroup.STATUS_LOCKED,
        ),
    ).exists()
    if active_membership_exists:
        return "stale", "Registration was selected in a current group generation."

    from crush_lu.models.payments import PaymentTransaction

    paid_or_refunded_payment_exists = PaymentTransaction.objects.filter(
        event_registration_id=registration.pk,
        purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
        status__in=(
            PaymentTransaction.Status.PAID,
            PaymentTransaction.Status.REFUNDED,
        ),
        paid_at__gte=notice.created_at,
    ).exists()
    remedy_credit_exists = CrushCredit.objects.filter(
        source_registration_id=registration.pk,
        reason=CrushCredit.Reason.CURATED_GROUP_UNAVAILABLE,
        issued_at__gte=notice.created_at,
    ).exists()
    remedy_notice_exists = (
        CuratedGroupNotification.objects.filter(
            event_id_snapshot=notice.event_id_snapshot,
            registration_id_snapshot=registration.pk,
            kind=CuratedGroupNotification.Kind.REMEDY,
            created_at__gte=notice.created_at,
        )
        .exclude(status=CuratedGroupNotification.Status.CANCELLED)
        .exists()
    )
    if paid_or_refunded_payment_exists or remedy_credit_exists or remedy_notice_exists:
        return "stale", "A paid-group remedy supersedes this unpaid notice."

    from crush_lu.email_helpers import send_curated_group_withdrawal_notice

    delivered = send_curated_group_withdrawal_notice(registration)
    return ("sent", "") if delivered else ("retry", "Mail delivery returned false.")


def _deliver_claimed_notice(notice_id, token):
    notice = CuratedGroupNotification.objects.filter(
        pk=notice_id,
        claim_token=token,
        status=CuratedGroupNotification.Status.SENDING,
    ).first()
    if notice is None:
        return "skipped"
    try:
        if notice.kind == CuratedGroupNotification.Kind.SELECTION:
            outcome, error = _deliver_selection_notice(notice)
        elif notice.kind == CuratedGroupNotification.Kind.REMEDY:
            outcome, error = _deliver_remedy_notice(notice)
        elif notice.kind == CuratedGroupNotification.Kind.WITHDRAWAL:
            outcome, error = _deliver_withdrawal_notice(notice)
        else:
            outcome, error = "stale", "Unknown curated notification kind."
    except Exception as exc:  # noqa: BLE001 - a mail outage stays retryable
        logger.exception("Curated notification %s delivery failed", notice_id)
        outcome, error = "retry", type(exc).__name__

    with transaction.atomic():
        locked = (
            CuratedGroupNotification.objects.select_for_update()
            .filter(
                pk=notice_id,
                claim_token=token,
                status=CuratedGroupNotification.Status.SENDING,
            )
            .first()
        )
        if locked is None:
            return "skipped"
        locked.claim_token = None
        locked.claimed_at = None
        locked.last_error = (error or "")[:255]
        if outcome == "sent":
            locked.status = CuratedGroupNotification.Status.SENT
            locked.sent_at = timezone.now()
        elif outcome == "stale":
            locked.status = CuratedGroupNotification.Status.CANCELLED
        else:
            locked.status = CuratedGroupNotification.Status.PENDING
        locked.save(
            update_fields=[
                "status",
                "claim_token",
                "claimed_at",
                "sent_at",
                "last_error",
            ]
        )
    return outcome


def deliver_curated_group_notifications(
    *,
    event_ids=None,
    registration_ids=None,
    kinds=None,
    notice_ids=None,
    limit=None,
    drain=False,
):
    """Deliver one queue slice, or a finite fair snapshot of up to 50 rows.

    Never-attempted rows sort ahead of retries. ``drain=True`` snapshots a
    larger, hard-bounded scope and attempts each selected ID exactly once, so a
    failed early row cannot starve later recipients or create an unbounded
    synchronous mail loop. Any remainder stays durable for admin recovery.
    """

    configured_limit = int(
        getattr(settings, "CURATED_NOTIFICATION_ADMIN_BATCH_SIZE", 10)
    )
    limit = max(1, min(int(limit or configured_limit), 50))
    event_ids = list(event_ids) if event_ids is not None else None
    registration_ids = list(registration_ids) if registration_ids is not None else None
    kinds = list(kinds) if kinds is not None else None
    notice_ids = list(notice_ids) if notice_ids is not None else None
    automatic_limit = max(
        1,
        min(
            int(getattr(settings, "CURATED_NOTIFICATION_AUTOMATIC_LIMIT", 50)),
            50,
        ),
    )
    candidate_limit = automatic_limit if drain else limit
    candidates = _pending_notices(
        event_ids=event_ids,
        registration_ids=registration_ids,
        kinds=kinds,
        notice_ids=notice_ids,
    ).order_by("attempt_count", "created_at", "pk")
    candidate_ids = list(candidates.values_list("pk", flat=True)[:candidate_limit])
    counts = {"sent": 0, "retry": 0, "stale": 0}
    attempted = 0
    for start in range(0, len(candidate_ids), limit):
        for notice_id in candidate_ids[start : start + limit]:
            claimed = _claim_notice(notice_id)
            if claimed is None:
                continue
            attempted += 1
            outcome = _deliver_claimed_notice(*claimed)
            if outcome in counts:
                counts[outcome] += 1
    remaining = _pending_notices(
        event_ids=event_ids,
        registration_ids=registration_ids,
        kinds=kinds,
        notice_ids=notice_ids,
    ).count()
    if drain and remaining:
        logger.warning(
            "Curated notification automatic drain left %s scoped row(s) "
            "retryable after attempting %s.",
            remaining,
            attempted,
        )
    return DeliveryBatch(
        attempted=attempted,
        sent=counts["sent"],
        failed=counts["retry"],
        cancelled=counts["stale"],
        remaining=remaining,
    )
