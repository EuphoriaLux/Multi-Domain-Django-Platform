"""Open a recovery case when a captured Premium payment is not applied (#925).

Runs from ``transaction.on_commit`` in ``_apply_paid_checkout``: the PAID
record is the only proof of a real charge, so nothing here may roll it back.
Every step is caught and logged. Emails go out only when this call created
the case, so a replayed webhook or browser return never mails twice.

Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

import logging

from django.conf import settings
from django.urls import reverse
from django.utils import timezone, translation

logger = logging.getLogger(__name__)


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


def open_case_safely(payment_id, reason, detail=""):
    """on_commit callback: get-or-create the case, then notify once."""
    from crush_lu.models import PaymentTransaction, PremiumPaymentRecoveryCase

    try:
        payment = PaymentTransaction.objects.select_related(
            "premium_membership__user"
        ).get(pk=payment_id)
        case, created = PremiumPaymentRecoveryCase.objects.get_or_create(
            payment=payment,
            defaults={
                "user": payment.premium_membership.user,
                "premium_membership": payment.premium_membership,
                "reason": reason,
                "detail": detail,
            },
        )
    except Exception as exc:
        logger.error(
            "Failed to open Premium recovery case for transaction %s: %s",
            payment_id,
            type(exc).__name__,
        )
        return None
    if created:
        _notify_member_safely(case)
        _alert_staff_safely(case)
    return case


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
        # English only: the Coach Panel is English, whatever language the
        # request that captured the payment was in.
        with translation.override("en"):
            reason = str(case.get_reason_display())
        message = (
            "A captured Premium payment was NOT applied and needs a human.\n\n"
            f"Reason: {reason}\n"
            f"Member: {case.user.email} (user {case.user_id})\n"
            f"Amount: {payment.amount} {payment.currency}\n"
            f"Reference: {payment.transaction_reference}\n"
            f"Case: https://crush.lu{admin_path}\n"
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
    except Exception as exc:
        logger.error(
            "Failed to send Premium recovery staff alert for case %s: %s",
            case.pk,
            type(exc).__name__,
        )
