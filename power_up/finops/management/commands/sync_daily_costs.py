"""
Management command to sync daily Azure cost data
Runs daily to automatically import new cost exports and refresh aggregations
Usage:
    python manage.py sync_daily_costs

Why this command logs as well as writing to stdout: it is normally invoked over
HTTP by the ``finops_daily_sync`` Azure Function via ``/finops/api/sync/``,
where all five steps run inline in the request. The App Service front end
abandons that request at ~230s and answers 504, so the caller never learns how
far the run got — and ``trigger_cost_sync`` captures stdout into a StringIO it
then discards. Anything we want to be able to diagnose therefore has to go
through ``logging``, which reaches App Insights via OpenTelemetry.

Reading the result: a run that never emits ``run finished`` was killed
mid-flight, and the last ``step=... started`` line names the step it died in.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.core.management import call_command
from power_up.finops.utils.aggregation import CostAggregator
from power_up.finops.models import CostExport

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync daily Azure cost data (import new exports + refresh aggregations)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting daily cost data sync'))
        logger.info('[finops_sync] run started')

        run_started = time.monotonic()
        timings = []

        def run_step(number, slug, description, step):
            """Run one step, timing it and logging on both sides.

            Exceptions stay swallowed so a late step failing cannot discard the
            earlier steps' work — that was the original behaviour and it is
            deliberate. What changes is that the failure is now recorded rather
            than vanishing into the stdout buffer the webhook throws away.
            """
            self.stdout.write(f'\n[{number}/5] {description}...')
            logger.info('[finops_sync] step=%s started', slug)
            started = time.monotonic()
            try:
                step()
            except Exception:
                elapsed = time.monotonic() - started
                timings.append((slug, elapsed, 'error'))
                logger.exception(
                    '[finops_sync] step=%s status=error elapsed=%.1fs', slug, elapsed
                )
                self.stdout.write(self.style.ERROR(f'{description} failed'))
                return
            elapsed = time.monotonic() - started
            timings.append((slug, elapsed, 'ok'))
            logger.info('[finops_sync] step=%s status=ok elapsed=%.1fs', slug, elapsed)

        def _import():
            call_command(
                'import_cost_data', '--batch-size=1000',
                stdout=self.stdout, stderr=self.stderr,
            )

        def _aggregations():
            result = CostAggregator.refresh_all(days_back=60, currency='EUR')
            self.stdout.write(self.style.SUCCESS(f'  ✓ Daily aggregations: {result["daily_aggregations"]}'))
            self.stdout.write(self.style.SUCCESS(f'  ✓ Monthly aggregations: {result["monthly_aggregations"]}'))
            self.stdout.write(self.style.SUCCESS(f'  ✓ Period: {result["period"]}'))
            logger.info(
                '[finops_sync] aggregations daily=%s monthly=%s period=%s',
                result['daily_aggregations'],
                result['monthly_aggregations'],
                result['period'],
            )

        def _anomalies():
            call_command(
                'detect_cost_anomalies', '--days-back=7',
                stdout=self.stdout, stderr=self.stderr,
            )

        def _forecasts():
            call_command(
                'generate_cost_forecasts', '--forecast-days=30', '--refresh',
                stdout=self.stdout, stderr=self.stderr,
            )

        def _reservations():
            call_command(
                'sync_reservation_costs', stdout=self.stdout, stderr=self.stderr,
            )

        run_step(1, 'import', 'Importing new cost exports', _import)
        run_step(2, 'aggregations', 'Refreshing cost aggregations', _aggregations)
        run_step(3, 'anomalies', 'Detecting cost anomalies', _anomalies)
        run_step(4, 'forecasts', 'Generating cost forecasts', _forecasts)
        run_step(5, 'reservations', 'Syncing reservation costs', _reservations)

        # Summary
        completed = CostExport.objects.filter(import_status='completed').count()
        total = CostExport.objects.count()

        elapsed_total = time.monotonic() - run_started
        breakdown = ' '.join(
            f'{slug}={elapsed:.1f}s/{status}' for slug, elapsed, status in timings
        )
        logger.info(
            '[finops_sync] run finished total=%.1fs exports=%s/%s %s',
            elapsed_total, completed, total, breakdown,
        )

        self.stdout.write(self.style.SUCCESS(f'\n✓ Daily sync completed in {elapsed_total:.1f}s'))
        self.stdout.write(f'Total exports: {total} ({completed} completed)')
