"""Open #925 recovery cases for Premium captures recorded before cases existed.

Migration 0262 runs the same rules once on deploy; this command is for a
preview and manual reruns. Before #925 an unapplied Premium capture left only
a log line, and replaying its callback returns early on PAID, so nothing else
ever finds these rows. Preview by default; ``--apply`` writes the cases, and
the hourly reconciliation tick then sends the member notice and staff alert
(retry_unsent_notifications). Idempotent: one case per payment.

A capture counts as unapplied when its membership never confirmed a payment
(``payment_confirmed`` is False); when staff confirmed the membership by hand
(``confirmed_by`` set) and the capture was recorded after that confirmation;
or when the membership has several captures -- nothing recorded proves which
one it applied, so all of them are flagged for staff. Rows with no membership
are listed too.
Spec: ai-memory-hub/specs/2026-09-13-crush-premium-payment-recovery.md
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from crush_lu.models import PaymentTransaction, PremiumPaymentRecoveryCase
from crush_lu.services import premium_recovery

AMBIGUOUS_DETAIL = (
    "Member notice withheld: the membership has several captures and the "
    "applied one is unknown."
)
MANUAL_CONFIRMATION_DETAIL = (
    "Member notice withheld: staff confirmed this membership by hand, and "
    "paid_at is when the callback was processed, not when SumUp captured, so "
    "this capture may be the one that confirmation applied."
)
BACKFILL_DETAIL = (
    "Backfilled for a capture recorded before recovery cases existed; "
    "verify which capture, if any, was applied."
)


def unapplied_captures():
    """``(payment, reason, withheld)`` for each PAID Premium capture without a
    case; ``withheld`` is the staff-only reason the member gets no notice, or
    None."""
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
            yield payment, Reason.OTHER, None
            continue
        if not membership.payment_confirmed:
            # Still pending proves nothing about why confirm() refused (coach
            # full or beta revoked): no durable record says, so "other".
            reason = (
                Reason.OTHER
                if membership.status == "pending"
                else premium_recovery.reason_for_membership_status(membership.status)
            )
            yield payment, reason, None
            continue
        captures = PaymentTransaction.objects.filter(
            premium_membership=membership,
            status=PaymentTransaction.Status.PAID,
        ).count()
        if captures == 1:
            if (
                membership.confirmed_by_id
                and payment.paid_at
                and membership.payment_date
                and payment.paid_at > membership.payment_date
            ):
                # Staff confirmed by hand before this only capture was
                # recorded -- paid_at is the processing time, so a late webhook
                # for the very capture confirmed lands after: staff only.
                yield payment, Reason.DUPLICATE_CAPTURE, MANUAL_CONFIRMATION_DETAIL
            continue  # otherwise the only capture is the one applied
        # Several captures (staff-confirmed or not): at most one was applied
        # and nothing recorded ties it to a row (a
        # capture could be PAID, refused by confirm(), and a later one applied),
        # so every capture is flagged -- for staff only: telling the member a
        # payment was not applied could be wrong for the one that was.
        yield payment, Reason.DUPLICATE_CAPTURE, AMBIGUOUS_DETAIL


class Command(BaseCommand):
    help = (
        "List PAID Premium captures that were never applied and have no "
        "recovery case; --apply opens the cases (notices follow hourly)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        found = 0
        for payment, reason, withheld in unapplied_captures():
            found += 1
            self.stdout.write(
                f"Payment {payment.pk} ({payment.transaction_reference}): {reason}"
            )
            if options["apply"]:
                with transaction.atomic():
                    premium_recovery.open_case(
                        payment,
                        reason,
                        (withheld + " " if withheld else "") + BACKFILL_DETAIL,
                        staff_only=bool(withheld),
                    )
        verb = "Opened" if options["apply"] else "Would open"
        self.stdout.write(f"{verb} {found} recovery case(s).")
        if found and not options["apply"]:
            self.stdout.write("Preview only. Re-run with --apply to open them.")
