"""
Management command to detect cost anomalies in recent data.

Usage:
    python manage.py detect_cost_anomalies
    python manage.py detect_cost_anomalies --days-back 14 --currency USD
    python manage.py detect_cost_anomalies --dry-run
"""

from django.core.management.base import BaseCommand
from power_up.finops.utils.anomaly_detector import CostAnomalyDetector
from power_up.finops.models import CostAnomaly


class Command(BaseCommand):
    help = 'Detect cost anomalies in recent data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days-back',
            type=int,
            default=7,
            help='Number of days to analyze (default: 7)'
        )
        parser.add_argument(
            '--currency',
            type=str,
            default='EUR',
            help='Currency to analyze (default: EUR)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Detect but do not save anomalies'
        )

    def handle(self, *args, **options):
        days_back = options['days_back']
        currency = options['currency']
        dry_run = options['dry_run']

        self.stdout.write(
            self.style.NOTICE(f'Detecting anomalies for last {days_back} days ({currency})...')
        )

        # Run detection
        anomalies = CostAnomalyDetector.detect_daily_anomalies(
            currency=currency,
            days_back=days_back
        )

        if not anomalies:
            self.stdout.write(self.style.SUCCESS('✓ No anomalies detected'))
            return

        # Summary by severity
        by_severity = {}
        for a in anomalies:
            by_severity[a.severity] = by_severity.get(a.severity, 0) + 1

        self.stdout.write(f'\nDetected {len(anomalies)} anomalies:')
        for severity in ['critical', 'high', 'medium', 'low']:
            count = by_severity.get(severity, 0)
            if count > 0:
                if severity == 'critical':
                    style = self.style.ERROR
                elif severity == 'high':
                    style = self.style.WARNING
                else:
                    style = self.style.NOTICE
                self.stdout.write(style(f'  {severity.upper()}: {count}'))

        # Show sample anomalies
        self.stdout.write('\nTop 5 anomalies:')
        for anomaly in sorted(anomalies, key=lambda x: abs(float(x.deviation_percent)), reverse=True)[:5]:
            self.stdout.write(
                f'  [{anomaly.severity.upper()}] {anomaly.dimension_value}: '
                f'{anomaly.actual_cost} {anomaly.currency} '
                f'({anomaly.deviation_percent:+.1f}% deviation)'
            )

        if dry_run:
            self.stdout.write(self.style.WARNING('\n⚠ Dry run - not saving to database'))
            return

        # Save to database (use bulk_create with ignore_conflicts to avoid duplicates)
        try:
            CostAnomaly.objects.bulk_create(
                anomalies,
                ignore_conflicts=True,
                batch_size=100
            )
            self.stdout.write(self.style.SUCCESS(f'\n✓ Saved {len(anomalies)} anomalies to database'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n✗ Error saving anomalies: {str(e)}'))
            raise
