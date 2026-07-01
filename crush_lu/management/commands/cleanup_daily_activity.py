"""
Management command to prune old DailyUserActivity rows (issue #523).

DailyUserActivity records one row per (user, local day) to give the weekly KPI
snapshot a stable WAU. Only a rolling window of recent days is ever queried, so
older rows can be pruned to keep the table bounded.

Usage:
    # Dry run - see how many rows would be deleted (default retention 90 days)
    python manage.py cleanup_daily_activity --dry-run

    # Delete rows older than the retention window
    python manage.py cleanup_daily_activity

    # Custom retention window
    python manage.py cleanup_daily_activity --days 120
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from crush_lu.models.profiles import DailyUserActivity

DEFAULT_RETENTION_DAYS = 90


class Command(BaseCommand):
    help = "Prune DailyUserActivity rows older than the retention window"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_RETENTION_DAYS,
            help=f"Keep rows newer than this many days (default {DEFAULT_RETENTION_DAYS}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many rows would be deleted without making changes.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]

        cutoff = timezone.localdate() - timedelta(days=days)
        qs = DailyUserActivity.objects.filter(activity_date__lt=cutoff)
        count = qs.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN - would delete {count} row(s) older than {cutoff.isoformat()}."
                )
            )
            return

        deleted, _ = qs.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted} DailyUserActivity row(s) older than {cutoff.isoformat()}."
            )
        )
