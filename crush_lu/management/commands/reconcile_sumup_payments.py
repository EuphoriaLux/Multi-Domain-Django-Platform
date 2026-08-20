"""
reconcile_sumup_payments — background reconciliation sweep for SumUp payments.

Tier-2 refund reconciliation: SumUp sends no webhook for refunds initiated
directly in its own merchant portal or on a POS terminal. This command polls
recently-settled (PAID) SumUp transactions, detects external refunds, and
brings Django's financial, registration, credit, and membership state back in sync.

Usage::

    # Sweep PAID checkouts from the last 30 days
    python manage.py reconcile_sumup_payments

    # Preview changes without modifying the database
    python manage.py reconcile_sumup_payments --dry-run

    # Check a custom lookback window (e.g. 60 days) quietly
    python manage.py reconcile_sumup_payments --days 60 --quiet

    # Reconcile a single checkout
    python manage.py reconcile_sumup_payments --checkout-id <sumup_checkout_id>
"""

import logging
import time
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from crush_lu.models.credits import CrushCredit
from crush_lu.models.events import EventRegistration
from crush_lu.models.payments import PaymentTransaction
from crush_lu.models.profiles import PremiumMembership
from crush_lu.services.sumup import SumUpClient, SumUpError

logger = logging.getLogger(__name__)


def is_checkout_refunded(data: dict) -> bool:
    """Determine if a SumUp checkout resource indicates an external refund.

    Checks:
    - Checkout-level status == "REFUNDED"
    - Any transaction entry status == "REFUNDED"
    - Any transaction entry has non-empty "refunds" list or amount_refunded > 0
    - Top-level amount_refunded > 0
    """
    if not isinstance(data, dict):
        return False

    status = (data.get("status") or "").upper()
    if status == "REFUNDED":
        return True

    if (data.get("amount_refunded") or 0) > 0:
        return True

    transactions = data.get("transactions") or []
    for tx in transactions:
        if not isinstance(tx, dict):
            continue
        tx_status = (tx.get("status") or "").upper()
        if tx_status == "REFUNDED":
            return True
        if tx.get("refunds") or (tx.get("amount_refunded") or 0) > 0:
            return True

    return False


class Command(BaseCommand):
    help = "Reconcile recent PAID SumUp transactions against SumUp to catch external refunds."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Number of lookback days for PAID transactions (default: 30)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the sweep and print actions without saving database changes",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Only output actionable desyncs and errors",
        )
        parser.add_argument(
            "--checkout-id",
            help="Reconcile a specific SumUp checkout ID",
        )
        parser.add_argument(
            "--reference",
            help="Reconcile a specific transaction reference",
        )
        parser.add_argument(
            "--batch-delay",
            type=float,
            default=0.05,
            help="Delay in seconds between SumUp API requests (default: 0.05s)",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        quiet = options["quiet"]
        checkout_id = options.get("checkout_id")
        reference = options.get("reference")
        delay = options["batch_delay"]

        if days < 1:
            raise CommandError("--days must be at least 1")

        if checkout_id and reference:
            raise CommandError("Provide either --checkout-id or --reference, not both.")

        qs = PaymentTransaction.objects.filter(
            provider=PaymentTransaction.Provider.SUMUP,
            status=PaymentTransaction.Status.PAID,
        )

        if checkout_id:
            qs = qs.filter(sumup_checkout_id=checkout_id)
        elif reference:
            qs = qs.filter(transaction_reference=reference)
        else:
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.filter(
                created_at__gte=cutoff,
                sumup_checkout_id__isnull=False,
            ).exclude(sumup_checkout_id="")

        qs = qs.order_by("-created_at")
        total_count = qs.count()

        if not quiet:
            prefix = "[DRY RUN] " if dry_run else ""
            self.stdout.write(
                f"{prefix}Starting SumUp refund reconciliation sweep: {total_count} transactions to check."
            )

        if total_count == 0:
            if not quiet:
                self.stdout.write("No matching PAID transactions found.")
            return

        client = SumUpClient()
        checked = 0
        refunded_count = 0
        errors_count = 0

        for tx_obj in qs:
            checked += 1
            if delay > 0 and checked > 1:
                time.sleep(delay)

            try:
                remote_data = client.get_checkout(tx_obj.sumup_checkout_id)
            except SumUpError as exc:
                errors_count += 1
                logger.error(
                    "Failed to fetch SumUp checkout %s: %s",
                    tx_obj.sumup_checkout_id,
                    exc,
                )
                self.stdout.write(
                    self.style.ERROR(
                        f"Error fetching checkout {tx_obj.sumup_checkout_id} ({tx_obj.transaction_reference}): {exc}"
                    )
                )
                continue

            if is_checkout_refunded(remote_data):
                refunded_count += 1
                self._reconcile_refunded(tx_obj, remote_data, dry_run=dry_run)
            elif not quiet:
                self.stdout.write(
                    f"✓ {tx_obj.transaction_reference} ({tx_obj.sumup_checkout_id}): still PAID"
                )

        summary_msg = (
            f"Sweep complete: {checked} checked, {refunded_count} external refund(s) reconciled, "
            f"{errors_count} error(s)."
        )
        if dry_run:
            summary_msg = f"[DRY RUN] {summary_msg}"

        if refunded_count > 0:
            self.stdout.write(self.style.SUCCESS(summary_msg))
        elif not quiet:
            self.stdout.write(summary_msg)

    def _reconcile_refunded(self, tx_obj, remote_data, dry_run=False):
        """Apply external refund adjustments across PaymentTransaction, EventRegistration,
        CrushCredit, and PremiumMembership under atomic lock order."""
        ref = tx_obj.transaction_reference
        cid = tx_obj.sumup_checkout_id

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] External refund detected on {ref} (checkout {cid}). Would reconcile to REFUNDED."
                )
            )
            return

        # LOCK ORDER: PaymentTransaction FIRST, then EventRegistration / CrushProfile / CrushCredit
        with transaction.atomic():
            locked_tx = (
                PaymentTransaction.objects.select_for_update()
                .filter(pk=tx_obj.pk)
                .first()
            )
            if not locked_tx or locked_tx.status != PaymentTransaction.Status.PAID:
                logger.info(
                    "Skipping reconciliation for %s — status is already %s",
                    ref,
                    locked_tx.status if locked_tx else "None",
                )
                return

            locked_tx.status = PaymentTransaction.Status.REFUNDED
            locked_tx.raw_response = remote_data
            locked_tx.failure_reason = (
                "External refund detected and reconciled by background sweep."
            )
            locked_tx.save(
                update_fields=["status", "raw_response", "failure_reason", "updated_at"]
            )

            # 1. Reconcile EventRegistration
            if locked_tx.event_registration_id:
                reg = (
                    EventRegistration.objects.select_for_update()
                    .filter(pk=locked_tx.event_registration_id)
                    .first()
                )
                if reg:
                    reg.payment_confirmed = False
                    reg.payment_date = None
                    update_fields = ["payment_confirmed", "payment_date"]

                    # If the registration was confirmed and hasn't attended yet, cancel it.
                    # Saving status='cancelled' invokes promote_waitlist_on_cancellation automatically.
                    if reg.status == "confirmed":
                        reg.status = "cancelled"
                        update_fields.append("status")

                    reg.save(update_fields=update_fields)
                    logger.info(
                        "Reconciled event registration %s to payment_confirmed=False (status=%s) after external refund.",
                        reg.pk,
                        reg.status,
                    )

            # 2. Reconcile PremiumMembership
            if locked_tx.premium_membership_id:
                pm = (
                    PremiumMembership.objects.select_for_update()
                    .filter(pk=locked_tx.premium_membership_id)
                    .first()
                )
                if pm:
                    pm.status = "cancelled"
                    pm.payment_confirmed = False
                    pm.payment_date = None
                    pm.save(update_fields=["status", "payment_confirmed", "payment_date"])
                    logger.info(
                        "Reconciled premium membership %s to cancelled after external refund.",
                        pm.pk,
                    )

            # 3. Reconcile linked CrushCredits (to prevent double-dip of cash refund + active credit)
            credit_filters = Q(source_payment=locked_tx)
            if locked_tx.event_registration_id:
                credit_filters |= Q(source_registration_id=locked_tx.event_registration_id)

            linked_credits = (
                CrushCredit.objects.select_for_update()
                .filter(credit_filters)
            )
            for credit in linked_credits:
                if credit.status == CrushCredit.Status.ACTIVE:
                    if credit.redeemed_cents == 0:
                        credit.status = CrushCredit.Status.VOID
                        credit.note = (
                            f"{credit.note}\nVoided: External SumUp cash refund reconciled."
                        ).strip()
                        credit.save(update_fields=["status", "note"])
                        logger.info(
                            "Voided unused CrushCredit #%s following external cash refund on payment %s.",
                            credit.pk,
                            ref,
                        )
                    else:
                        credit.status = CrushCredit.Status.VOID
                        credit.note = (
                            f"{credit.note}\nVoided (WARNING: {credit.redeemed_cents} cents already redeemed): "
                            f"External SumUp cash refund reconciled."
                        ).strip()
                        credit.save(update_fields=["status", "note"])
                        logger.warning(
                            "CrushCredit #%s had %s cents already redeemed when external cash refund on payment %s was reconciled!",
                            credit.pk,
                            credit.redeemed_cents,
                            ref,
                        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled external refund for {ref} (checkout {cid}) -> status=REFUNDED"
            )
        )
