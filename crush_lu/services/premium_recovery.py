"""Open a recovery case when a captured Premium payment is not applied (#925).

The case is written in the PAID transaction; emails go out on commit and only
when that call created the case (replays never mail twice).
Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

import logging

from django.conf import settings
from django.urls import reverse
from django.utils import timezone, translation

logger = logging.getLogger(__name__)


def member_notice(payment):
    """The D1 notice text for ``payment``, in the active language."""
    from django.utils.formats import localize
    from django.utils.translation import gettext as _

    return _(
        "We received your payment of %(amount)s %(currency)s, "
        "but it has not been applied to your Premium "
        "membership. Please do not pay again. Reference: "
        "%(reference)s. Contact support@crush.lu if you need "
        "help."
    ) % {
        # localize() matches {{ amount }} (10,00 in DE/FR).
        "amount": localize(payment.amount),
        "currency": payment.currency,
        "reference": payment.transaction_reference,
    }


def blocks_new_charge(user):
    """True while ``user`` must not be charged again (#925): any OPEN case, or
    any case (even resolved) whose membership still has a checkout that SumUp
    has not confirmed closed."""
    from django.db.models import Q

    from crush_lu.models import PaymentTransaction, PremiumPaymentRecoveryCase

    return (
        PremiumPaymentRecoveryCase.objects.filter(user=user)
        .filter(
            Q(status=PremiumPaymentRecoveryCase.Status.OPEN)
            | Q(
                premium_membership__payment_transactions__status=(
                    PaymentTransaction.Status.PENDING
                )
            )
        )
        .exists()
    )


def reason_for_membership_status(status):
    """Map the membership state a capture found to the case reason."""
    from crush_lu.models import PremiumPaymentRecoveryCase

    Reason = PremiumPaymentRecoveryCase.Reason
    return {
        "active": Reason.DUPLICATE_CAPTURE,
        "cancelled": Reason.REQUEST_CANCELLED,
        # Still pending after confirm() refused: the coach was full.
        "pending": Reason.COACH_UNAVAILABLE,
    }.get(status, Reason.OTHER)


def open_case(payment, reason, detail=""):
    """Get-or-create the case inside the caller's PAID transaction.

    Takes no lock and may raise: a failure must roll PAID back so the capture
    is retried, never commit without its case."""
    from crush_lu.models import PremiumPaymentRecoveryCase

    membership = payment.premium_membership
    # Unlinked: only legacy rows (the FK is PROTECT now), so payment.user is
    # the only member evidence left, as in email_helpers._receipt_recipient.
    owner = membership.user if membership else payment.user
    return PremiumPaymentRecoveryCase.objects.get_or_create(
        payment=payment,
        defaults={
            "user": owner,
            "premium_membership": membership,
            "reason": reason,
            "detail": detail,
        },
    )


def notify_safely(case_pk):
    """on_commit callback: member notice + staff alert for a NEW case."""
    from crush_lu.models import PremiumPaymentRecoveryCase

    try:
        case = PremiumPaymentRecoveryCase.objects.select_related("payment", "user").get(
            pk=case_pk
        )
    except Exception as exc:
        logger.error(
            "Failed to load Premium recovery case %s: %s",
            case_pk,
            type(exc).__name__,
        )
        return
    _close_sibling_checkouts_safely(case)
    _notify_member_safely(case)
    _alert_staff_safely(case)


def _close_sibling_checkouts_safely(case):
    """Close the membership's other PENDING SumUp checkouts (deactivate only,
    never a refund): with a capture recorded, none of them may take another
    payment. Post-commit, no lock held during the network calls."""
    from django.db import transaction

    from crush_lu.models import PaymentTransaction

    membership = case.premium_membership
    if (
        membership is None
        or not PaymentTransaction.objects.filter(
            premium_membership=membership, status=PaymentTransaction.Status.PENDING
        ).exists()
    ):
        return
    from crush_lu.views_payments import (
        SumUpClient,
        _lock_premium_checkout_state,
        _settle_pending_premium_checkouts,
    )

    try:
        state, _reuse, retired, _known = _settle_pending_premium_checkouts(
            SumUpClient(), membership, captured=True
        )
        if retired:
            with transaction.atomic():
                _lock_premium_checkout_state(membership.pk, retired)
        if state == "open":
            # Still PENDING, so the hourly retry picks the case up again.
            logger.warning(
                "Sibling checkout still open for recovery case %s; will retry",
                case.pk,
            )
    except Exception as exc:
        logger.error(
            "Failed to close sibling checkouts for recovery case %s: %s",
            case.pk,
            type(exc).__name__,
        )


def retry_unsent_notifications(
    budget_seconds, per_case_seconds, limit=5, settle_minutes=10
):
    """Hourly retry of what failed after a case opened: a member notice, a
    staff alert, or closing a sibling checkout.

    Run by the SumUp reconciliation tick with what is left of its budget: a
    case is started only while ``per_case_seconds`` (two worst-case sends)
    still fit. Skips cases newer than ``settle_minutes`` (their on-commit
    send may still be running). No age cut-off: an open case keeps being
    retried until it is delivered or resolved; random order so one that can
    never be delivered cannot starve the others."""
    import time
    from datetime import timedelta

    from django.db import transaction
    from django.db.models import Exists, OuterRef, Q

    from crush_lu.models import PaymentTransaction, PremiumPaymentRecoveryCase

    Case = PremiumPaymentRecoveryCase
    now = timezone.now()
    case_ids = list(
        Case.objects.annotate(
            # A sibling checkout that could not be closed is retried too.
            has_open_checkout=Exists(
                PaymentTransaction.objects.filter(
                    premium_membership_id=OuterRef("premium_membership_id"),
                    status=PaymentTransaction.Status.PENDING,
                )
            )
        )
        .filter(
            # Notices only for open cases; closing a checkout that could
            # still take money continues even after staff resolve the case.
            (Q(member_notified_at__isnull=True) | Q(staff_alerted_at__isnull=True))
            & Q(status=Case.Status.OPEN)
            | Q(has_open_checkout=True),
            created_at__lte=now - timedelta(minutes=settle_minutes),
        )
        .order_by("?")
        .values_list("pk", flat=True)[:limit]
    )
    deadline = time.monotonic() + budget_seconds
    sent = 0
    for pk in case_ids:
        if time.monotonic() + per_case_seconds > deadline:
            break
        with transaction.atomic():
            # Claim: an overlapping tick skips a case another run holds, and
            # the timestamps are re-read under the lock, so no double send.
            case = (
                Case.objects.select_for_update(skip_locked=True)
                .select_related("payment", "user", "premium_membership")
                .filter(pk=pk)
                .first()
            )
            if case is None:
                continue
            sent += 1
            _close_sibling_checkouts_safely(case)
            if case.status != Case.Status.OPEN:
                continue
            if case.member_notified_at is None:
                _notify_member_safely(case)
            if case.staff_alerted_at is None:
                _alert_staff_safely(case)
    return sent


def _notify_member_safely(case):
    from crush_lu.email_helpers import send_premium_payment_recovery_notice

    try:
        if send_premium_payment_recovery_notice(case.payment):
            case.member_notified_at = timezone.now()
            case.save(update_fields=["member_notified_at"])
    except Exception as exc:
        logger.error(
            "Failed to send Premium recovery notice for case %s: %s",
            case.pk,
            type(exc).__name__,
        )


def _alert_staff_safely(case):
    from azureproject.email_utils import send_domain_email

    try:
        payment = case.payment
        admin_path = reverse(
            "crush_admin:crush_lu_premiumpaymentrecoverycase_change",
            args=[case.pk],
            urlconf="azureproject.urls_crush",
        )
        # English only: the Coach Panel is English.
        with translation.override("en"):
            reason = str(case.get_reason_display())
        message = (
            "A captured Premium payment was NOT applied and needs a human.\n\n"
            f"Reason: {reason}\n"
            f"Member: {case.user.email} (user {case.user_id})\n"
            f"Amount: {payment.amount} {payment.currency}\n"
            f"Reference: {payment.transaction_reference}\n"
            f"Case: {settings.PREMIUM_RECOVERY_ADMIN_BASE_URL}{admin_path}\n"
        )
        if send_domain_email(
            subject=f"[Crush.lu] Premium payment not applied: "
            f"{payment.transaction_reference}",
            message=message,
            recipient_list=[settings.PREMIUM_RECOVERY_ALERT_EMAIL],
            domain="crush.lu",
            fail_silently=False,
        ):
            case.staff_alerted_at = timezone.now()
            case.save(update_fields=["staff_alerted_at"])
        else:
            logger.warning("Premium recovery staff alert not sent: case %s", case.pk)
    except Exception as exc:
        logger.error(
            "Failed to send Premium recovery staff alert for case %s: %s",
            case.pk,
            type(exc).__name__,
        )
