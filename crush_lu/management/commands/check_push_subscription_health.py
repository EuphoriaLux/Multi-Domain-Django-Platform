"""
Management command to check health of all push subscriptions.
Identifies stale, failing, or potentially expired subscriptions.

Usage:
    python manage.py check_push_subscription_health
    python manage.py check_push_subscription_health --cleanup  # Remove unhealthy ones
    python manage.py check_push_subscription_health --age-threshold 90  # Custom age threshold
    python manage.py check_push_subscription_health --include-coaches  # Also check coach subscriptions
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crush_lu.models import PushSubscription, CoachPushSubscription


class Command(BaseCommand):
    help = 'Check health of push subscriptions and optionally clean up stale ones'

    def add_arguments(self, parser):
        parser.add_argument(
            '--cleanup',
            action='store_true',
            help='Remove subscriptions with high failure counts',
        )
        parser.add_argument(
            '--age-threshold',
            type=int,
            default=90,
            help='Age threshold in days to flag old subscriptions (default: 90)',
        )
        parser.add_argument(
            '--include-coaches',
            action='store_true',
            help='Also check coach push subscriptions',
        )

    def handle(self, *args, **options):
        cleanup = options['cleanup']
        age_threshold = options['age_threshold']
        include_coaches = options['include_coaches']

        self.stdout.write(self.style.SUCCESS('Checking push subscription health...'))

        # Check user push subscriptions
        self.check_subscriptions(
            PushSubscription.objects.all(),
            'User',
            cleanup,
            age_threshold
        )

        # Check coach push subscriptions if requested
        if include_coaches:
            self.check_subscriptions(
                CoachPushSubscription.objects.all(),
                'Coach',
                cleanup,
                age_threshold
            )

        self.stdout.write(self.style.SUCCESS('\n✓ Health check complete'))

    def check_subscriptions(self, queryset, label, cleanup, age_threshold):
        total = queryset.count()
        self.stdout.write(f'\n{label} Push Subscriptions: {total} total')

        if total == 0:
            self.stdout.write('  No subscriptions found')
            return

        # Age analysis
        cutoff_date = timezone.now() - timedelta(days=age_threshold)
        old_subs = queryset.filter(created_at__lt=cutoff_date)
        self.stdout.write(f'  - {old_subs.count()} older than {age_threshold} days')

        # Failure analysis
        failing_subs = queryset.filter(failure_count__gte=3)
        self.stdout.write(self.style.WARNING(
            f'  - {failing_subs.count()} with 3+ failures (may be dead)'
        ))

        # Inactive analysis (no successful send in 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        inactive_subs = queryset.filter(
            last_used_at__lt=thirty_days_ago
        ) | queryset.filter(last_used_at__isnull=True)
        self.stdout.write(f'  - {inactive_subs.count()} inactive (no sends in 30 days)')

        # Disabled analysis
        disabled_subs = queryset.filter(enabled=False)
        self.stdout.write(f'  - {disabled_subs.count()} manually disabled')

        # Cleanup if requested
        if cleanup:
            # Only remove subscriptions with 5+ failures (dead endpoints)
            dead_subs = queryset.filter(failure_count__gte=5)
            count = dead_subs.count()
            dead_subs.delete()
            self.stdout.write(self.style.WARNING(
                f'  → Removed {count} dead subscriptions (5+ failures)'
            ))

        # Recommendations
        if failing_subs.exists() and not cleanup:
            self.stdout.write(self.style.NOTICE(
                f'\n  💡 Tip: Run with --cleanup to remove dead subscriptions'
            ))
