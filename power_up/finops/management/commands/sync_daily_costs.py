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
failures along the way — see ``_ChildFailureWatcher`` below.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.core.management import call_command
from power_up.finops.utils.aggregation import CostAggregator
from power_up.finops.models import CostExport

logger = logging.getLogger(__name__)

_COMMANDS = 'power_up.finops.management.commands'


class _ChildFailureWatcher(logging.Handler):
    """Notice when a child command reports failures it swallowed.

    Two of the children keep going past a failed item — import_cost_data past
    a failed export, sync_reservation_costs past a reservation it could not
    price — so call_command() returns normally even when every item failed,
    and timing the call alone would record a confident status=ok over a run
    that imported nothing.

    Both now count their own failures and log the tally at WARNING. Reading
    that record is what makes this reliable: the number comes from the command
    itself, so a step is never judged by scanning its prose for error markers,
    which would miss any path whose wording did not match (sync_reservation
    _costs, for one, counts a failure while printing "[WARN] No pricing
    found").

    The other two children need no watcher: detect_cost_anomalies re-raises
    after reporting and generate_cost_forecasts has no swallowed-failure path,
    so run_step already sees both as status=error.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.reports = []

    def emit(self, record):
        # The child stamps its own '[finops_sync] ' so it stays greppable when
        # run on its own; drop it here or the merged line carries it twice.
        self.reports.append(record.getMessage().removeprefix('[finops_sync] '))


class Command(BaseCommand):
    help = 'Sync daily Azure cost data (import new exports + refresh aggregations)'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting daily cost data sync'))
        logger.info('[finops_sync] run started')

        run_started = time.monotonic()
        timings = []

        def run_step(number, slug, description, step, style, child=None):
            """Run one step, timing it and logging on both sides.

            Exceptions stay swallowed so a late step failing cannot discard the
            earlier steps' work — that was the original behaviour and it is
            deliberate, as is each step's console severity (``style``). What
            changes is that the failure is now recorded rather than vanishing
            into the stdout buffer the webhook throws away.

            ``child`` names the command module to watch for swallowed failures;
            when it reports any, the step is marked partial rather than ok.
            """
            self.stdout.write(f'\n[{number}/5] {description}...')
            logger.info('[finops_sync] step=%s started', slug)
            started = time.monotonic()

            watcher = _ChildFailureWatcher() if child else None
            child_logger = logging.getLogger(f'{_COMMANDS}.{child}') if child else None
            if child_logger is not None:
                child_logger.addHandler(watcher)
            try:
                step()
            except Exception as exc:
                elapsed = time.monotonic() - started
                timings.append((slug, elapsed, 'error'))
                logger.exception(
                    '[finops_sync] step=%s status=error elapsed=%.1fs', slug, elapsed
                )
                self.stdout.write(style(f'{description} failed: {exc}'))
                return
            finally:
                if child_logger is not None:
                    child_logger.removeHandler(watcher)
            elapsed = time.monotonic() - started

            if watcher is not None and watcher.reports:
                timings.append((slug, elapsed, 'partial'))
                logger.warning(
                    '[finops_sync] step=%s status=partial elapsed=%.1fs %s',
                    slug, elapsed, ' | '.join(watcher.reports),
                )
                return

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

        # Console severity per step matches the original: import and
        # aggregations were ERROR, the three non-fatal tail steps WARNING.
        run_step(1, 'import', 'Importing new cost exports', _import, self.style.ERROR,
                 child='import_cost_data')
        run_step(2, 'aggregations', 'Refreshing cost aggregations', _aggregations, self.style.ERROR)
        run_step(3, 'anomalies', 'Detecting cost anomalies', _anomalies, self.style.WARNING)
        run_step(4, 'forecasts', 'Generating cost forecasts', _forecasts, self.style.WARNING)
        run_step(5, 'reservations', 'Syncing reservation costs', _reservations, self.style.WARNING,
                 child='sync_reservation_costs')

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
