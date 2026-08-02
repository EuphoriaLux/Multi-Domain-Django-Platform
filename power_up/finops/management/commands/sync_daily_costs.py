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
A step reporting ``status=partial`` finished, but the child command swallowed
failures along the way.

Two of the children keep going past a failed item — import_cost_data past a
failed export, sync_reservation_costs past a reservation it cannot price — so
``call_command()`` returns normally even when every item failed, and timing the
call alone would record a confident ``status=ok`` over a run that imported
nothing. Both count their own failures, and this command reads that count back
off the command *instance*: ``call_command`` accepts a command object for
exactly this purpose. The count comes from the command itself, so a step is
never judged by scanning its prose for error markers (which would miss any
path whose wording did not match — sync_reservation_costs counts a failure
while printing "[WARN] No pricing found"), and nothing is passed through a
process-global channel, which would cross-contaminate if two sync requests
overlapped in one worker.

The other two children need no such read: detect_cost_anomalies re-raises
after reporting and generate_cost_forecasts has no swallowed-failure path, so
run_step already sees both as ``status=error``.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.core.management import call_command
from power_up.finops.management.commands import (
    import_cost_data,
    sync_reservation_costs,
)
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

        def run_step(number, slug, description, step, style):
            """Run one step, timing it and logging on both sides.

            Exceptions stay swallowed so a late step failing cannot discard the
            earlier steps' work — that was the original behaviour and it is
            deliberate, as is each step's console severity (``style``). What
            changes is that the failure is now recorded rather than vanishing
            into the stdout buffer the webhook throws away.

            A step returning a string is reporting failures its child swallowed;
            the step is then marked partial rather than ok.
            """
            self.stdout.write(f'\n[{number}/5] {description}...')
            logger.info('[finops_sync] step=%s started', slug)
            started = time.monotonic()
            try:
                swallowed = step()
            except Exception as exc:
                elapsed = time.monotonic() - started
                timings.append((slug, elapsed, 'error'))
                logger.exception(
                    '[finops_sync] step=%s status=error elapsed=%.1fs', slug, elapsed
                )
                self.stdout.write(style(f'{description} failed: {exc}'))
                return
            elapsed = time.monotonic() - started

            if swallowed:
                timings.append((slug, elapsed, 'partial'))
                logger.warning(
                    '[finops_sync] step=%s status=partial elapsed=%.1fs %s',
                    slug, elapsed, swallowed,
                )
                return

            timings.append((slug, elapsed, 'ok'))
            logger.info('[finops_sync] step=%s status=ok elapsed=%.1fs', slug, elapsed)

        def _import():
            command = import_cost_data.Command()
            # --skip-aggregation: the child would otherwise run a full 60-day
            # CostAggregator.refresh_all itself, which step 2 then repeats
            # verbatim — duplicated work in a command that already times out.
            # It also swallows its own refresh failure into a stdout warning
            # raised *after* the import tally, so leaving it on would hide a
            # failure behind status=ok.
            call_command(
                command, '--batch-size=1000', '--skip-aggregation',
                stdout=self.stdout, stderr=self.stderr,
            )
            if command.failed_exports:
                return f'child=import_cost_data failed={command.failed_exports}'
            return None

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
            command = sync_reservation_costs.Command()
            call_command(command, stdout=self.stdout, stderr=self.stderr)
            if command.error_count:
                return f'child=sync_reservation_costs errors={command.error_count}'
            return None

        # Console severity per step matches the original: import and
        # aggregations were ERROR, the three non-fatal tail steps WARNING.
        run_step(1, 'import', 'Importing new cost exports', _import, self.style.ERROR)
        run_step(2, 'aggregations', 'Refreshing cost aggregations', _aggregations, self.style.ERROR)
        run_step(3, 'anomalies', 'Detecting cost anomalies', _anomalies, self.style.WARNING)
        run_step(4, 'forecasts', 'Generating cost forecasts', _forecasts, self.style.WARNING)
        run_step(5, 'reservations', 'Syncing reservation costs', _reservations, self.style.WARNING)

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
