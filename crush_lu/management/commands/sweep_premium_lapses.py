"""
End Premium memberships whose billing period ran out.

Two cases, both decided by ``services.premium_billing``:

- the member cancelled and the paid period ended;
- no renewal was recorded and the 3-day grace after the period end is over.

Each is ended through ``PremiumMembership.cancel_active``, which releases the
coach it assigned. Entitlement does not wait for this: ``has_active_premium``
already stops granting Premium when the time is up, so a missed run only delays
the status change and the coach release. A membership with no period end (the
comped beta testers) is never touched.

Usage:
    # Standard run (hourly timer, POST /api/admin/premium-lapse-sweep/)
    python manage.py sweep_premium_lapses

    # List what would end, without writing
    python manage.py sweep_premium_lapses --dry-run
"""

from django.core.management.base import BaseCommand

from crush_lu.services import premium_billing


class Command(BaseCommand):
    help = "End Premium memberships whose paid period (plus grace) is over."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the memberships that would end, change nothing.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            due = list(premium_billing.lapse_due().values_list("pk", flat=True))
            self.stdout.write(f"would end {len(due)} membership(s): {due}")
            return
        ended = premium_billing.sweep_lapsed()
        self.stdout.write(f"ended {ended} membership(s)")
