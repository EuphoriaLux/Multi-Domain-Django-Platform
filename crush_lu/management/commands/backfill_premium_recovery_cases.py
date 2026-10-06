"""Open #925 recovery cases for Premium captures recorded before cases existed.

Deploy step after migration 0260. Before #925 an unapplied Premium capture
left only a log line, and replaying its callback returns early on PAID, so
nothing else ever finds these rows. Preview by default; ``--apply`` writes the
cases, and the hourly reconciliation tick then sends the member notice and
staff alert (retry_unsent_notifications). Idempotent: one case per payment.

A capture counts as unapplied when its membership never confirmed a payment
(``payment_confirmed`` is False); when staff confirmed the membership by hand
(``confirmed_by`` set: the SumUp path never sets it, so no capture was the
applied one); or when the membership has several captures and this is not the
first one recorded (``paid_at``; checkout creation order proves nothing). If
any of them lacks ``paid_at``, all are flagged. Rows with no membership are
listed too.
Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from crush_lu.models import PaymentTransaction, PremiumPaymentRecoveryCase
from crush_lu.services import premium_recovery

BACKFILL_DETAIL = (
    "Backfilled for a capture recorded before recovery cases existed; "
    "verify which capture, if any, was applied."
)


def unapplied_captures():
    """``(payment, reason)`` for each PAID Premium capture without a case."""
    Reason = PremiumPaymentRecoveryCase.Reason
    rows = (
        PaymentTransaction.objects.filter(
            purpose=PaymentTransaction.Purpose.PREMIUM_MEMBERSHIP,
            status=PaymentTransaction.Status.PAID,
            premium_recovery_case__isnull=True,
        )
        .select_related("premium_membership", "user")
        .order_by("pk")
    )
    for payment in rows:
        membership = payment.premium_membership
        if membership is None:
            yield payment, Reason.OTHER
            continue
        if not membership.payment_confirmed:
            yield payment, premium_recovery.reason_for_membership_status(
                membership.status
            )
            continue
        if membership.confirmed_by_id:
            yield payment, Reason.DUPLICATE_CAPTURE
            continue
        captures = list(
            PaymentTransaction.objects.filter(
                premium_membership=membership,
                status=PaymentTransaction.Status.PAID,
            ).values_list("pk", "paid_at")
        )
        if len(captures) == 1:
            continue  # the membership's only capture is the one it applied
        if all(paid_at for _pk, paid_at in captures):
            # The first capture recorded is the one that activated it; the
            # later ones found the membership active (see _apply_paid_checkout).
            applied = min(captures, key=lambda row: (row[1], row[0]))[0]
            if payment.pk != applied:
                yield payment, Reason.DUPLICATE_CAPTURE
        else:
            # Not provable which one was applied: flag every capture.
            yield payment, Reason.DUPLICATE_CAPTURE


class Command(BaseCommand):
    help = (
        "List PAID Premium captures that were never applied and have no "
        "recovery case; --apply opens the cases (notices follow hourly)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        found = 0
        for payment, reason in unapplied_captures():
            found += 1
            self.stdout.write(
                f"Payment {payment.pk} ({payment.transaction_reference}): {reason}"
            )
            if options["apply"]:
                with transaction.atomic():
                    premium_recovery.open_case(payment, reason, BACKFILL_DETAIL)
        verb = "Opened" if options["apply"] else "Would open"
        self.stdout.write(f"{verb} {found} recovery case(s).")
        if found and not options["apply"]:
            self.stdout.write("Preview only. Re-run with --apply to open them.")
