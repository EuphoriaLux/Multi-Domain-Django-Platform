"""
Tests for the sync_daily_costs orchestration.

This command's whole job under the finops_daily_sync webhook is to leave a
usable trail in App Insights when the request 504s, so the assertions here are
about the log records rather than the sync's effects. The behaviour that has
regressed repeatedly is a step reporting status=ok over a child that swallowed
failures — pin that in both directions.

The two swallowing children report their counts on the command instance, so
the fakes below set those attributes the way the real commands do.
"""

import io
import logging

import pytest
from django.core.management import call_command

PKG = 'power_up.finops.management.commands'
MOD = f'{PKG}.sync_daily_costs'


class RecordingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def sync_log():
    """Capture what sync_daily_costs logs, at INFO and above."""
    handler = RecordingHandler()
    logger = logging.getLogger(MOD)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def messages(handler):
    return [record.getMessage() for record in handler.records]


def status_of(handler, slug):
    """The status recorded for one step, or None if it never finished."""
    for message in messages(handler):
        marker = f'step={slug} status='
        if marker in message:
            return message.split(marker, 1)[1].split(' ', 1)[0]
    return None


def child_of(command_or_name):
    """Name the child being invoked, whether by name or as a command object."""
    if isinstance(command_or_name, str):
        return command_or_name
    return command_or_name.__class__.__module__.rsplit('.', 1)[-1]


def run_sync(monkeypatch, fake_call_command):
    """Run the command with its children and DB summary stubbed out."""
    monkeypatch.setattr(f'{MOD}.call_command', fake_call_command)

    class FakeAggregator:
        @staticmethod
        def refresh_all(**kwargs):
            return {
                'daily_aggregations': 7,
                'monthly_aggregations': 2,
                'period': '2026-06-03..2026-08-02',
            }

    class FakeManager:
        def filter(self, **kwargs):
            return self

        def count(self):
            return 41

    class FakeCostExport:
        objects = FakeManager()

    monkeypatch.setattr(f'{MOD}.CostAggregator', FakeAggregator)
    monkeypatch.setattr(f'{MOD}.CostExport', FakeCostExport)

    out = io.StringIO()
    call_command('sync_daily_costs', stdout=out, stderr=out)
    return out.getvalue()


def clean_children(command_or_name, *args, **kwargs):
    """Every child succeeds with nothing swallowed."""
    return None


def failing_child(child, **counts):
    """A fake call_command where one child reports swallowed failures."""
    def children(command_or_name, *args, **kwargs):
        if child_of(command_or_name) == child:
            for attribute, value in counts.items():
                setattr(command_or_name, attribute, value)
        return None
    return children


class TestStepStatus:
    def test_clean_run_reports_every_step_ok(self, monkeypatch, sync_log):
        run_sync(monkeypatch, clean_children)

        for slug in ('import', 'aggregations', 'anomalies', 'forecasts', 'reservations'):
            assert status_of(sync_log, slug) == 'ok'
        assert any('run finished' in m for m in messages(sync_log))

    def test_swallowed_import_failures_are_not_reported_ok(self, monkeypatch, sync_log):
        """import_cost_data keeps going past a failed export and returns
        normally, so only its own count distinguishes this from a clean run."""
        run_sync(monkeypatch, failing_child('import_cost_data', failed_exports=2))

        assert status_of(sync_log, 'import') == 'partial'
        partial = [m for m in messages(sync_log) if 'step=import status=partial' in m][0]
        assert 'child=import_cost_data failed=2' in partial

    def test_reservation_failure_counted_while_printing_warn(self, monkeypatch, sync_log):
        """sync_reservation_costs increments error_count while printing
        '[WARN] No pricing found', so scanning output for an [ERROR] marker
        missed it. Reading the count is what makes this detectable."""
        run_sync(monkeypatch, failing_child('sync_reservation_costs', error_count=1))

        assert status_of(sync_log, 'reservations') == 'partial'
        partial = [m for m in messages(sync_log) if 'step=reservations status=partial' in m][0]
        assert 'errors=1' in partial

    def test_child_reporting_zero_failures_stays_ok(self, monkeypatch, sync_log):
        run_sync(monkeypatch, failing_child('import_cost_data', failed_exports=0))

        assert status_of(sync_log, 'import') == 'ok'


class TestChildInvocation:
    def test_import_skips_its_own_aggregation_refresh(self, monkeypatch, sync_log):
        """The child would otherwise run a full 60-day refresh that step 2
        repeats verbatim, and its refresh failure is swallowed into a stdout
        warning raised after the import tally — invisible to the caller."""
        seen = {}

        def children(command_or_name, *args, **kwargs):
            seen[child_of(command_or_name)] = args
            return None

        run_sync(monkeypatch, children)

        assert '--skip-aggregation' in seen['import_cost_data']

    def test_aggregations_still_refreshed_by_step_two(self, monkeypatch, sync_log):
        """Skipping the child's refresh must not lose the refresh entirely."""
        run_sync(monkeypatch, clean_children)

        assert any('aggregations daily=7 monthly=2' in m for m in messages(sync_log))
        assert status_of(sync_log, 'aggregations') == 'ok'


class TestFailureHandling:
    def test_raising_step_is_recorded_and_later_steps_still_run(
        self, monkeypatch, sync_log
    ):
        """A late step failing must not discard the earlier steps' work."""
        def children(command_or_name, *args, **kwargs):
            if child_of(command_or_name) == 'detect_cost_anomalies':
                raise RuntimeError('boom from anomalies')
            return None

        stdout = run_sync(monkeypatch, children)

        assert status_of(sync_log, 'anomalies') == 'error'
        assert status_of(sync_log, 'forecasts') == 'ok'
        assert status_of(sync_log, 'reservations') == 'ok'
        # The traceback has to survive; stdout is discarded by the webhook.
        assert any(r.exc_info for r in sync_log.records)
        # ...but a developer running this by hand still gets the detail.
        assert 'boom from anomalies' in stdout

    def test_run_always_finishes_with_a_breakdown(self, monkeypatch, sync_log):
        """The breakdown is how you tell a slow step from a dead one."""
        def children(command_or_name, *args, **kwargs):
            if child_of(command_or_name) == 'detect_cost_anomalies':
                raise RuntimeError('boom')
            return None

        run_sync(monkeypatch, children)

        finished = [m for m in messages(sync_log) if 'run finished' in m][0]
        assert 'anomalies=' in finished and '/error' in finished
        assert 'exports=41/41' in finished


class TestRunIsolation:
    def test_one_failing_run_does_not_taint_the_next(self, monkeypatch, sync_log):
        """Two sync requests can overlap in one worker after a 504 prompts a
        retry. Failure counts must not be shared between runs — reading them
        off a fresh command instance per run is what keeps them separate."""
        run_sync(monkeypatch, failing_child('import_cost_data', failed_exports=5))
        assert status_of(sync_log, 'import') == 'partial'

        second = RecordingHandler()
        logger = logging.getLogger(MOD)
        logger.addHandler(second)
        try:
            run_sync(monkeypatch, clean_children)
        finally:
            logger.removeHandler(second)

        assert status_of(second, 'import') == 'ok'
        assert status_of(second, 'reservations') == 'ok'

    def test_one_child_failing_does_not_taint_another_step(
        self, monkeypatch, sync_log
    ):
        run_sync(monkeypatch, failing_child('import_cost_data', failed_exports=5))

        assert status_of(sync_log, 'import') == 'partial'
        assert status_of(sync_log, 'reservations') == 'ok'

    def test_no_handler_is_attached_to_child_loggers(self, monkeypatch, sync_log):
        """The child-to-parent channel must stay process-local. Handlers on a
        shared logger would dispatch one run's tally to another run's watcher."""
        import_log = logging.getLogger(f'{PKG}.import_cost_data')
        reservations_log = logging.getLogger(f'{PKG}.sync_reservation_costs')
        before = (list(import_log.handlers), list(reservations_log.handlers))

        run_sync(monkeypatch, clean_children)

        assert list(import_log.handlers) == before[0]
        assert list(reservations_log.handlers) == before[1]


class TestConsoleOutput:
    def test_step_counters_are_numbered_out_of_five(self, monkeypatch, sync_log):
        stdout = run_sync(monkeypatch, clean_children)

        for number in range(1, 6):
            assert f'[{number}/5]' in stdout

    def test_child_stdout_still_reaches_the_console(self, monkeypatch, sync_log):
        def children(command_or_name, *args, **kwargs):
            if child_of(command_or_name) == 'import_cost_data':
                kwargs['stdout'].write('[OK] Import completed!\n')
            return None

        stdout = run_sync(monkeypatch, children)

        assert '[OK] Import completed!' in stdout
