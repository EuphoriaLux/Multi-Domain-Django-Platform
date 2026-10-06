"""Open #925 recovery cases for Premium captures recorded before cases existed.

Runs once on deploy (migrations run in startup.sh), so no capture left
unapplied by the old code stays without a case; the hourly reconciliation
tick then sends the member notice and staff alert. Same rules as
``manage.py backfill_premium_recovery_cases`` (kept for previews and manual
reruns), written against the historical models. Idempotent: one case per
payment. Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

from django.db import migrations

BACKFILL_DETAIL = (
    "Backfilled for a capture recorded before recovery cases existed; "
    "verify which capture, if any, was applied."
)
MEMBER_UNKNOWN_DETAIL = (
    "Member unknown: legacy unlinked checkout opened by staff; the case is "
    "filed under the staff account."
)
AMBIGUOUS_DETAIL = (
    "Member notice withheld: the membership has several captures and the "
    "applied one is unknown."
)
MANUAL_CONFIRMATION_DETAIL = (
    "Member notice withheld: staff confirmed this membership by hand, and "
    "paid_at is when the callback was processed, not when SumUp captured, so "
    "this capture may be the one that confirmation applied."
)
# Like premium_recovery.reason_for_membership_status, frozen here, except that
# a pending membership proves nothing about why confirm() refused (coach full
# or beta revoked): no durable record says, so it is "other".
REASON_FOR_STATUS = {
    "active": "duplicate_capture",
    "cancelled": "request_cancelled",
}


def backfill(apps, schema_editor):
    PaymentTransaction = apps.get_model("crush_lu", "PaymentTransaction")
    Case = apps.get_model("crush_lu", "PremiumPaymentRecoveryCase")
    rows = (
        PaymentTransaction.objects.filter(
            purpose="premium_membership",
            status="paid",
            premium_recovery_case__isnull=True,
        )
        .select_related("premium_membership", "user")
        .order_by("pk")
    )
    for payment in rows:
        ambiguous = manual = False
        membership = payment.premium_membership
        if membership is None:
            reason = "other"
        elif not membership.payment_confirmed:
            reason = REASON_FOR_STATUS.get(membership.status, "other")
        elif membership.confirmed_by_id:
            # Staff confirmed by hand: a capture recorded before that may be
            # the one confirmed. One recorded after may be too -- paid_at is
            # the processing time, and a late webhook lands after -- so it is
            # flagged for staff only, never a definitive member notice.
            if not (
                payment.paid_at
                and membership.payment_date
                and payment.paid_at > membership.payment_date
            ):
                continue
            reason, manual = "duplicate_capture", True
        elif (
            PaymentTransaction.objects.filter(
                premium_membership=membership, status="paid"
            ).count()
            > 1
        ):
            # The applied one is not provable: staff only, no member notice.
            reason, ambiguous = "duplicate_capture", True
        else:
            continue  # the membership's only capture is the one it applied
        owner = membership.user if membership else payment.user
        if owner is None:
            continue
        member_unknown = membership is None and owner.is_staff
        staff_only = member_unknown or ambiguous or manual
        detail = BACKFILL_DETAIL
        if manual:
            detail = f"{MANUAL_CONFIRMATION_DETAIL} {detail}"
        if ambiguous:
            detail = f"{AMBIGUOUS_DETAIL} {detail}"
        if member_unknown:
            detail = f"{MEMBER_UNKNOWN_DETAIL} {detail}"
        Case.objects.get_or_create(
            payment=payment,
            defaults={
                "user": owner,
                "premium_membership": membership,
                "reason": reason,
                "detail": detail,
                "staff_only": staff_only,
                "member_unknown": member_unknown,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0261_paymenttransaction_premium_membership_protect"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
