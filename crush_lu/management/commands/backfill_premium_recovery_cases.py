"""Open #925 recovery cases for Premium captures recorded before cases existed.

Migration 0262 runs the same rules once on deploy; this command is for a
preview and manual reruns. Before #925 an unapplied Premium capture left only
a log line, and replaying its callback returns early on PAID, so nothing else
ever finds these rows. Preview by default; ``--apply`` writes the cases, and
the hourly reconciliation tick then sends the member notice and staff alert
(retry_unsent_notifications). Idempotent: one case per payment.

A capture counts as unapplied when its membership never confirmed a payment
(``payment_confirmed`` is False); when staff confirmed the membership by hand
(``confirmed_by`` set: the SumUp path never sets it, so no capture was the
applied one); or when the membership has several captures -- nothing recorded
proves which one it applied, so all of them are flagged for staff. Rows with
no membership are listed too.
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
            ).values_list("pk", flat=True)
        )
        if len(captures) == 1:
            continue  # the membership's only capture is the one it applied
        # Several captures: nothing recorded ties the applied one to a row (a
        # capture could be PAID, refused by confirm(), and a later one applied),
        # so every capture is flagged for staff to check.
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
