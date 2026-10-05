"""Open a recovery case when a captured Premium payment is not applied (#925).

The case is written in the PAID transaction; emails go out on commit and only
when that call created the case (replays never mail twice).
Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

import contextvars
import logging
import time

from django.conf import settings
from django.urls import reverse
from django.utils import timezone, translation

logger = logging.getLogger(__name__)

# Siblings re-read per membership and run; the rest wait for the next hourly
# tick.
SIBLING_SYNC_LIMIT = 2
# Worst case of one Graph send (GRAPH_SEND_TIMEOUT_SECONDS in api_admin_sumup).
SEND_SECONDS = 30

# Set by retry_unsent_notifications for the hourly tick: every network step
# below, nested on-commit work included, starts only while it still fits.
# Unset (None) on the member's own request paths.
_deadline = contextvars.ContextVar("premium_recovery_deadline", default=None)


def _fits(seconds):
    deadline = _deadline.get()
    return deadline is None or time.monotonic() + seconds <= deadline


def _close_seconds():
    """One settle pass: its own retire budget plus the close in flight when
    that budget runs out (a DELETE and one read)."""
    from crush_lu.management.commands.reconcile_sumup_payments import (
        SUMUP_REQUEST_WORST_CASE_SECONDS,
    )
    from crush_lu.views_payments import _PREMIUM_CHECKOUT_RETIRE_BUDGET_SECONDS

    return (
        _PREMIUM_CHECKOUT_RETIRE_BUDGET_SECONDS + 2 * SUMUP_REQUEST_WORST_CASE_SECONDS
    )


def _sync_seconds():
    from crush_lu.management.commands.reconcile_sumup_payments import (
        SUMUP_REQUEST_WORST_CASE_SECONDS,
    )

    return SUMUP_REQUEST_WORST_CASE_SECONDS


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
    """While the case is OPEN, close every PENDING checkout the member has,
    whichever membership it belongs to: a checkout published just before the
    case was inserted must not stay payable. A resolved case only keeps
    closing its own membership's."""
    from crush_lu.models import PaymentTransaction, PremiumMembership

    if case.status == case.Status.OPEN and case.user_id:
        memberships = list(
            PremiumMembership.objects.filter(
                user_id=case.user_id,
                payment_transactions__status=PaymentTransaction.Status.PENDING,
            )
            .distinct()
            .order_by("pk")
        )
    else:
        memberships = [case.premium_membership] if case.premium_membership else []
    close_open_checkouts_safely(memberships, f"recovery case {case.pk}")


def close_open_checkouts_safely(memberships, label):
    """Close the PENDING SumUp checkouts of ``memberships`` (deactivate only,
    never a refund): with a capture recorded, none of them may take another
    payment. Post-commit, no lock held during the network calls. A checkout
    SumUp already captured is recorded (applied, or its own recovery case),
    from the payload the close read when there is one -- the sweep reads PAID
    rows only."""
    from django.db import transaction

    from crush_lu.models import PaymentTransaction

    for membership in memberships:
        if not PaymentTransaction.objects.filter(
            premium_membership=membership, status=PaymentTransaction.Status.PENDING
        ).exists():
            continue
        if not _fits(_close_seconds()):
            logger.warning("No time left to close checkouts for %s; will retry", label)
            return
        from crush_lu.views_payments import (
            SumUpClient,
            _apply_paid_checkout,
            _lock_premium_checkout_state,
            _settle_pending_premium_checkouts,
            _sync_checkout_with_sumup,
        )

        try:
            paid_payloads = {}
            state, _reuse, retired, _known = _settle_pending_premium_checkouts(
                SumUpClient(), membership, captured=True, paid_payloads=paid_payloads
            )
            if retired:
                with transaction.atomic():
                    _lock_premium_checkout_state(membership.pk, retired)
            synced = 0
            for row in PaymentTransaction.objects.filter(
                premium_membership=membership,
                status=PaymentTransaction.Status.PENDING,
                sumup_checkout_id__isnull=False,
            ).order_by("pk"):
                if row.pk in paid_payloads:
                    _apply_paid_checkout(row, paid_payloads[row.pk])
                elif synced < SIBLING_SYNC_LIMIT and _fits(_sync_seconds()):
                    synced += 1
                    _sync_checkout_with_sumup(row)
            if state == "open":
                # Still PENDING, so the hourly retry picks it up again.
                logger.warning("Checkout still open for %s; will retry", label)
        except Exception as exc:
            logger.error(
                "Failed to close checkouts for %s: %s", label, type(exc).__name__
            )


def retry_unsent_notifications(budget_seconds, limit=5, settle_minutes=10):
    """Hourly retry of what failed after a case opened: a member notice, a
    staff alert, or closing a checkout that can still take money.

    Run by the SumUp reconciliation tick with what is left of its budget:
    each send, close or read starts only while its own worst case still fits
    (``_fits``); what does not fit stays undone for the next tick. Skips cases newer than ``settle_minutes`` (their on-commit
    send may still be running). No age cut-off: an open case keeps being
    retried until it is delivered or resolved; random order so one that can
    never be delivered cannot starve the others."""
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
            ),
            # An open case closes the member's other memberships' too.
            member_has_open_checkout=Exists(
                PaymentTransaction.objects.filter(
                    premium_membership__user_id=OuterRef("user_id"),
                    status=PaymentTransaction.Status.PENDING,
                )
            ),
        )
        .filter(
            # Notices only for open cases; closing a checkout that could
            # still take money continues even after staff resolve the case.
            (
                Q(member_notified_at__isnull=True)
                | Q(staff_alerted_at__isnull=True)
                | Q(member_has_open_checkout=True)
            )
            & Q(status=Case.Status.OPEN)
            | Q(has_open_checkout=True),
            created_at__lte=now - timedelta(minutes=settle_minutes),
        )
        .order_by("?")
        .values_list("pk", flat=True)[:limit]
    )
    token = _deadline.set(time.monotonic() + budget_seconds)
    sent = 0
    try:
        for pk in case_ids:
            # Nothing per case is cheaper than a send.
            if not _fits(SEND_SECONDS):
                break
            with transaction.atomic():
                # Claim: an overlapping tick skips a case another run holds,
                # and the timestamps are re-read under the lock, so no double
                # send.
                case = (
                    # of=("self",): PostgreSQL refuses FOR UPDATE on the
                    # nullable side of the premium_membership outer join.
                    Case.objects.select_for_update(skip_locked=True, of=("self",))
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
    finally:
        _deadline.reset(token)
    return sent


def _notify_member_safely(case):
    from crush_lu.email_helpers import send_premium_payment_recovery_notice

    if not _fits(SEND_SECONDS):
        return
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

    if not _fits(SEND_SECONDS):
        return
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
