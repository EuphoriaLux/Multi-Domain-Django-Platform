"""
Tests for the sync_daily_costs orchestration.

This command's whole job under the finops_daily_sync webhook is to leave a
usable trail in App Insights when the request 504s, so the assertions here are
about the log records rather than the sync's effects. The behaviour that has
regressed twice is a step reporting status=ok over a child that swallowed
failures — pin that in both directions.
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


def clean_children(name, *args, **kwargs):
    """Every child succeeds and reports nothing."""
    return None


class TestStepStatus:
    def test_clean_run_reports_every_step_ok(self, monkeypatch, sync_log):
        run_sync(monkeypatch, clean_children)

        for slug in ('import', 'aggregations', 'anomalies', 'forecasts', 'reservations'):
            assert status_of(sync_log, slug) == 'ok'
        assert any('run finished' in m for m in messages(sync_log))

    def test_swallowed_import_failures_are_not_reported_ok(self, monkeypatch, sync_log):
        """import_cost_data keeps going past a failed export and returns
        normally, so only its own tally distinguishes this from a clean run."""
        child_log = logging.getLogger(f'{PKG}.import_cost_data')

        def children(name, *args, **kwargs):
            if name == 'import_cost_data':
                child_log.warning(
                    '[finops_sync] child=import_cost_data '
                    'imported=%s duplicates=%s failed=%s', 1240, 3, 2,
                )
            return None

        run_sync(monkeypatch, children)

        assert status_of(sync_log, 'import') == 'partial'
        partial = [m for m in messages(sync_log) if 'step=import status=partial' in m][0]
        assert 'failed=2' in partial
        # The child's tally must not be swallowed by the parent's own prefix.
        assert 'child=import_cost_data' in partial

    def test_reservation_failure_counted_while_printing_warn(self, monkeypatch, sync_log):
        """sync_reservation_costs increments error_count while printing
        '[WARN] No pricing found', so scanning output for an [ERROR] marker
        missed it. The tally is what makes this detectable."""
        child_log = logging.getLogger(f'{PKG}.sync_reservation_costs')

        def children(name, *args, **kwargs):
            if name == 'sync_reservation_costs':
                child_log.warning(
                    '[finops_sync] child=sync_reservation_costs '
                    'synced=%s skipped=%s errors=%s', 3, 1, 1,
                )
            return None

        run_sync(monkeypatch, children)

        assert status_of(sync_log, 'reservations') == 'partial'

    def test_child_reporting_no_failures_stays_ok(self, monkeypatch, sync_log):
        """A child that logs its tally at INFO has nothing wrong with it."""
        child_log = logging.getLogger(f'{PKG}.import_cost_data')

        def children(name, *args, **kwargs):
            if name == 'import_cost_data':
                child_log.info(
                    '[finops_sync] child=import_cost_data '
                    'imported=%s duplicates=%s failed=%s', 1240, 3, 0,
                )
            return None

        run_sync(monkeypatch, children)

        assert status_of(sync_log, 'import') == 'ok'


class TestFailureHandling:
    def test_raising_step_is_recorded_and_later_steps_still_run(
        self, monkeypatch, sync_log
    ):
        """A late step failing must not discard the earlier steps' work."""
        def children(name, *args, **kwargs):
            if name == 'detect_cost_anomalies':
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
        def children(name, *args, **kwargs):
            if name == 'detect_cost_anomalies':
                raise RuntimeError('boom')
            return None

        run_sync(monkeypatch, children)

        finished = [m for m in messages(sync_log) if 'run finished' in m][0]
        assert 'anomalies=' in finished and '/error' in finished
        assert 'exports=41/41' in finished


class TestWatcherHygiene:
    def test_no_handler_is_left_on_child_loggers(self, monkeypatch, sync_log):
        """The watcher is attached per step; leaking it would make every later
        run accumulate handlers and mis-attribute failures across steps."""
        import_log = logging.getLogger(f'{PKG}.import_cost_data')
        reservations_log = logging.getLogger(f'{PKG}.sync_reservation_costs')
        before = (list(import_log.handlers), list(reservations_log.handlers))

        run_sync(monkeypatch, clean_children)

        assert list(import_log.handlers) == before[0]
        assert list(reservations_log.handlers) == before[1]

    def test_watcher_is_removed_even_when_the_step_raises(
        self, monkeypatch, sync_log
    ):
        import_log = logging.getLogger(f'{PKG}.import_cost_data')
        before = list(import_log.handlers)

        def children(name, *args, **kwargs):
            if name == 'import_cost_data':
                raise RuntimeError('boom from import')
            return None

        run_sync(monkeypatch, children)

        assert status_of(sync_log, 'import') == 'error'
        assert list(import_log.handlers) == before

    def test_one_child_failing_does_not_taint_another_step(
        self, monkeypatch, sync_log
    ):
        """Both watched steps run in the same process; import's failures must
        not leak into the reservations verdict."""
        import_log = logging.getLogger(f'{PKG}.import_cost_data')

        def children(name, *args, **kwargs):
            if name == 'import_cost_data':
                import_log.warning(
                    '[finops_sync] child=import_cost_data '
                    'imported=%s duplicates=%s failed=%s', 10, 0, 5,
                )
            return None

        run_sync(monkeypatch, children)

        assert status_of(sync_log, 'import') == 'partial'
        assert status_of(sync_log, 'reservations') == 'ok'


class TestConsoleOutput:
    def test_step_counters_are_numbered_out_of_five(self, monkeypatch, sync_log):
        stdout = run_sync(monkeypatch, clean_children)

        for number in range(1, 6):
            assert f'[{number}/5]' in stdout

    def test_child_stdout_still_reaches_the_console(self, monkeypatch, sync_log):
        def children(name, *args, **kwargs):
            if name == 'import_cost_data':
                kwargs['stdout'].write('[OK] Import completed!\n')
            return None

        stdout = run_sync(monkeypatch, children)

        assert '[OK] Import completed!' in stdout
