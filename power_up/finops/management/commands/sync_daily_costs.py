"""
Management command to sync daily Azure cost data
Runs daily to automatically import new cost exports and refresh aggregations
Usage:
    python manage.py sync_daily_costs
"""
from django.core.management.base import BaseCommand
from django.core.management import call_command
from power_up.finops.utils.aggregation import CostAggregator
from power_up.finops.models import CostExport


class Command(BaseCommand):
    help = 'Sync daily Azure cost data (import new exports + refresh aggregations)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting daily cost data sync'))

        # Step 1: Import new cost exports
        self.stdout.write('\n[1/3] Importing new cost exports...')
        try:
            call_command('import_cost_data', '--batch-size=1000', stdout=self.stdout, stderr=self.stderr)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Import failed: {str(e)}'))
            # Continue to aggregation even if import fails

        # Step 2: Refresh cost aggregations
        self.stdout.write('\n[2/3] Refreshing cost aggregations...')
        try:
            result = CostAggregator.refresh_all(days_back=60, currency='EUR')
            self.stdout.write(self.style.SUCCESS(f'  ✓ Daily aggregations: {result["daily_aggregations"]}'))
            self.stdout.write(self.style.SUCCESS(f'  ✓ Monthly aggregations: {result["monthly_aggregations"]}'))
            self.stdout.write(self.style.SUCCESS(f'  ✓ Period: {result["period"]}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Aggregation failed: {str(e)}'))
            # Continue to anomaly detection even if aggregation fails

        # Step 3: Detect cost anomalies
        self.stdout.write('\n[3/4] Detecting cost anomalies...')
        try:
            call_command('detect_cost_anomalies', '--days-back=7', stdout=self.stdout, stderr=self.stderr)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Anomaly detection failed: {str(e)}'))
            # Non-fatal - continue

        # Step 4: Generate cost forecasts
        self.stdout.write('\n[4/4] Generating cost forecasts...')
        try:
            call_command('generate_cost_forecasts', '--forecast-days=30', '--refresh', stdout=self.stdout, stderr=self.stderr)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Forecast generation failed: {str(e)}'))
            # Non-fatal - continue

        # Summary
        completed = CostExport.objects.filter(import_status='completed').count()
        total = CostExport.objects.count()

        self.stdout.write(self.style.SUCCESS(f'\n✓ Daily sync completed'))
        self.stdout.write(f'Total exports: {total} ({completed} completed)')
