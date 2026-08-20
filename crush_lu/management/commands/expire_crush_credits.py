"""
Flip ACTIVE Crush Credit rows past ``expires_at`` to EXPIRED.

CrushCredit.is_spendable, available_credit_cents and CashRefundQueueFilter
all already check ``expires_at`` at READ time — a credit past its clock is
unspendable everywhere in the product the instant it lapses, whether or not
this command has ever run. So this sweep is bookkeeping, not correctness: it
exists so the admin's status column and any report grouping "by status" tell
the truth, not so the balance is right. Until it runs, an unswept row shows
as "Active" in list_display's ordering column and gets a distinct "Expired
(unswept)" badge in ``CrushCreditAdmin.get_status_badge`` — cosmetic, but
worth clearing regularly.

Idempotent — safe to run as often as you like. A credit already EXPIRED, or
still genuinely ACTIVE (not yet past expires_at), is never touched twice.

Usage:
    # Preview what would change
    python manage.py expire_crush_credits --dry-run

    # Flip everything currently due
    python manage.py expire_crush_credits

Scheduling: NOT wired to an Azure Function timer as of this command's
introduction — see the PR description. Run it by hand, or from a Kudu/manual
shell, until a timer is added.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crush_lu.models.credits import CrushCredit


class Command(BaseCommand):
    help = (
        "Flip ACTIVE Crush Credit rows past their expires_at to EXPIRED. "
        "Idempotent bookkeeping — is_spendable already treats them as dead "
        "whether or not this has run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()

        candidates = CrushCredit.objects.filter(
            status=CrushCredit.Status.ACTIVE, expires_at__lte=now
        )
        total = candidates.count()
        self.stdout.write(
            f"{total} active credit(s) past expiry as of {now.isoformat()}"
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry run — {total} credit(s) NOT modified.")
            )
            return

        expired = 0
        with transaction.atomic():
            # Lock the candidate rows and re-check under the lock: a credit
            # can be voided, or spent down to CONSUMED, between the count
            # above and this write. Re-reading `pk__in=candidates.values('pk')`
            # as a subquery of the same (unlocked) filter, then filtering the
            # locked objects again in Python, keeps this safe against a
            # concurrent void/redeem without assuming anything about isolation
            # level.
            locked = list(
                CrushCredit.objects.select_for_update().filter(
                    pk__in=candidates.values("pk")
                )
            )
            for credit in locked:
                if (
                    credit.status != CrushCredit.Status.ACTIVE
                    or credit.expires_at > timezone.now()
                ):
                    continue
                credit.status = CrushCredit.Status.EXPIRED
                credit.save(update_fields=["status"])
                expired += 1

        self.stdout.write(
            self.style.SUCCESS(f"Expired {expired} credit(s) past their clock.")
        )
