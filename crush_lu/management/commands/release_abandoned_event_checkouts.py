"""Remind, then release, unpaid seats whose online checkout was abandoned.

Usage:
    # Standard run (AbandonedCheckoutSweep timer, every 10 minutes,
    # POST /api/admin/abandoned-checkout-sweep/)
    python manage.py release_abandoned_event_checkouts

    # Count the seats it would look at, without asking Mollie, sending or
    # releasing anything
    python manage.py release_abandoned_event_checkouts --dry-run

The rules live in ``crush_lu/services/abandoned_checkouts.py``.
"""

from django.core.management.base import BaseCommand

from crush_lu.services.abandoned_checkouts import run_sweep


class Command(BaseCommand):
    help = (
        "Send one 'finish your payment' email, then release unpaid event seats "
        "whose online checkout was abandoned"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only count candidate seats; no Mollie reads, emails or releases",
        )

    def handle(self, *args, **options):
        counts = run_sweep(dry_run=options["dry_run"])
        summary = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        self.stdout.write(
            self.style.SUCCESS(f"Abandoned checkouts: {summary or 'nothing to do'}")
        )
