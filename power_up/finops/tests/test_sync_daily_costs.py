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

PKG = "power_up.finops.management.commands"
MOD = f"{PKG}.sync_daily_costs"


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
        marker = f"step={slug} status="
        if marker in message:
            return message.split(marker, 1)[1].split(" ", 1)[0]
    return None


def child_of(command_or_name):
    """Name the child being invoked, whether by name or as a command object."""
    if isinstance(command_or_name, str):
        return command_or_name
    return command_or_name.__class__.__module__.rsplit(".", 1)[-1]


def run_sync(monkeypatch, fake_call_command):
    """Run the command with its children and DB summary stubbed out."""
    monkeypatch.setattr(f"{MOD}.call_command", fake_call_command)

    class FakeAggregator:
        @staticmethod
        def refresh_windows(**kwargs):
            return {
                "daily_aggregations": 7,
                "monthly_aggregations": 2,
                "period": "2026-06-03..2026-08-02",
            }

    class FakeManager:
        def filter(self, **kwargs):
            return self

        def count(self):
            return 41

    class FakeCostExport:
        objects = FakeManager()

    monkeypatch.setattr(f"{MOD}.CostAggregator", FakeAggregator)
    monkeypatch.setattr(f"{MOD}.CostExport", FakeCostExport)

    out = io.StringIO()
    call_command("sync_daily_costs", stdout=out, stderr=out)
    return out.getvalue()


def clean_children(command_or_name, *args, **kwargs):
    """Every child succeeds with nothing swallowed."""
    if child_of(command_or_name) == "generate_cost_forecasts":
        # A healthy forecast run writes rows; zero means it produced nothing.
        command_or_name.forecasts_written = 30
    return None


def failing_child(child, **counts):
    """A fake call_command where one child reports swallowed failures."""

    def children(command_or_name, *args, **kwargs):
        clean_children(command_or_name, *args, **kwargs)
        if child_of(command_or_name) == child:
            for attribute, value in counts.items():
                setattr(command_or_name, attribute, value)
        return None

    return children


class TestStepStatus:
    def test_clean_run_reports_every_step_ok(self, monkeypatch, sync_log):
        run_sync(monkeypatch, clean_children)

        for slug in (
            "import",
            "aggregations",
            "anomalies",
            "forecasts",
            "reservations",
        ):
            assert status_of(sync_log, slug) == "ok"
        assert any("run finished" in m for m in messages(sync_log))

    def test_swallowed_import_failures_are_not_reported_ok(self, monkeypatch, sync_log):
        """import_cost_data keeps going past a failed export and returns
        normally, so only its own count distinguishes this from a clean run."""
        run_sync(monkeypatch, failing_child("import_cost_data", failed_exports=2))

        assert status_of(sync_log, "import") == "partial"
        partial = [m for m in messages(sync_log) if "step=import status=partial" in m][
            0
        ]
        assert "child=import_cost_data failed_exports=2" in partial

    def test_reservation_failure_counted_while_printing_warn(
        self, monkeypatch, sync_log
    ):
        """sync_reservation_costs increments error_count while printing
        '[WARN] No pricing found', so scanning output for an [ERROR] marker
        missed it. Reading the count is what makes this detectable."""
        run_sync(monkeypatch, failing_child("sync_reservation_costs", error_count=1))

        assert status_of(sync_log, "reservations") == "partial"
        partial = [
            m for m in messages(sync_log) if "step=reservations status=partial" in m
        ][0]
        assert "errors=1" in partial

    def test_child_reporting_zero_failures_stays_ok(self, monkeypatch, sync_log):
        run_sync(monkeypatch, failing_child("import_cost_data", failed_exports=0))

        assert status_of(sync_log, "import") == "ok"

    def test_rejected_rows_are_not_reported_ok(self, monkeypatch, sync_log):
        """process_export swallows each unparseable or invalid row and the
        export still 'succeeds', so an import that dropped every row looks
        exactly like a clean one at the export level."""
        run_sync(monkeypatch, failing_child("import_cost_data", failed_records=814))

        assert status_of(sync_log, "import") == "partial"
        partial = [m for m in messages(sync_log) if "step=import status=partial" in m][
            0
        ]
        assert "rejected_records=814" in partial

    def test_empty_forecast_is_not_reported_ok(self, monkeypatch, sync_log):
        """generate_cost_forecasts writes an 'Insufficient data' warning and
        returns normally, producing no forecast at all."""
        run_sync(
            monkeypatch, failing_child("generate_cost_forecasts", forecasts_written=0)
        )

        assert status_of(sync_log, "forecasts") == "partial"
        partial = [
            m for m in messages(sync_log) if "step=forecasts status=partial" in m
        ][0]
        assert "forecasts=0" in partial


class TestChildInvocation:
    def test_import_skips_its_own_aggregation_refresh(self, monkeypatch, sync_log):
        """The child would otherwise run a full 60-day refresh that step 2
        repeats verbatim, and its refresh failure is swallowed into a stdout
        warning raised after the import tally — invisible to the caller."""
        seen = {}

        def children(command_or_name, *args, **kwargs):
            seen[child_of(command_or_name)] = args
            return clean_children(command_or_name, *args, **kwargs)

        run_sync(monkeypatch, children)

        assert "--skip-aggregation" in seen["import_cost_data"]

    def test_aggregations_still_refreshed_by_step_two(self, monkeypatch, sync_log):
        """Skipping the child's refresh must not lose the refresh entirely."""
        run_sync(monkeypatch, clean_children)

        assert any("aggregations daily=7 monthly=2" in m for m in messages(sync_log))
        assert status_of(sync_log, "aggregations") == "ok"


class TestFailureHandling:
    def test_raising_step_is_recorded_and_later_steps_still_run(
        self, monkeypatch, sync_log
    ):
        """A late step failing must not discard the earlier steps' work."""

        def children(command_or_name, *args, **kwargs):
            if child_of(command_or_name) == "detect_cost_anomalies":
                raise RuntimeError("boom from anomalies")
            return clean_children(command_or_name, *args, **kwargs)

        stdout = run_sync(monkeypatch, children)

        assert status_of(sync_log, "anomalies") == "error"
        assert status_of(sync_log, "forecasts") == "ok"
        assert status_of(sync_log, "reservations") == "ok"
        # The traceback has to survive; stdout is discarded by the webhook.
        assert any(r.exc_info for r in sync_log.records)
        # ...but a developer running this by hand still gets the detail.
        assert "boom from anomalies" in stdout

    def test_run_always_finishes_with_a_breakdown(self, monkeypatch, sync_log):
        """The breakdown is how you tell a slow step from a dead one."""

        def children(command_or_name, *args, **kwargs):
            if child_of(command_or_name) == "detect_cost_anomalies":
                raise RuntimeError("boom")
            return clean_children(command_or_name, *args, **kwargs)

        run_sync(monkeypatch, children)

        finished = [m for m in messages(sync_log) if "run finished" in m][0]
        assert "anomalies=" in finished and "/error" in finished
        assert "exports=41/41" in finished


class TestRunIsolation:
    def test_one_failing_run_does_not_taint_the_next(self, monkeypatch, sync_log):
        """Two sync requests can overlap in one worker after a 504 prompts a
        retry. Failure counts must not be shared between runs — reading them
        off a fresh command instance per run is what keeps them separate."""
        run_sync(monkeypatch, failing_child("import_cost_data", failed_exports=5))
        assert status_of(sync_log, "import") == "partial"

        second = RecordingHandler()
        logger = logging.getLogger(MOD)
        logger.addHandler(second)
        try:
            run_sync(monkeypatch, clean_children)
        finally:
            logger.removeHandler(second)

        assert status_of(second, "import") == "ok"
        assert status_of(second, "reservations") == "ok"

    def test_one_child_failing_does_not_taint_another_step(self, monkeypatch, sync_log):
        run_sync(monkeypatch, failing_child("import_cost_data", failed_exports=5))

        assert status_of(sync_log, "import") == "partial"
        assert status_of(sync_log, "reservations") == "ok"

    def test_no_handler_is_attached_to_child_loggers(self, monkeypatch, sync_log):
        """The child-to-parent channel must stay process-local. Handlers on a
        shared logger would dispatch one run's tally to another run's watcher."""
        import_log = logging.getLogger(f"{PKG}.import_cost_data")
        reservations_log = logging.getLogger(f"{PKG}.sync_reservation_costs")
        before = (list(import_log.handlers), list(reservations_log.handlers))

        run_sync(monkeypatch, clean_children)

        assert list(import_log.handlers) == before[0]
        assert list(reservations_log.handlers) == before[1]


class TestConsoleOutput:
    def test_step_counters_are_numbered_out_of_five(self, monkeypatch, sync_log):
        stdout = run_sync(monkeypatch, clean_children)

        for number in range(1, 6):
            assert f"[{number}/5]" in stdout

    def test_child_stdout_still_reaches_the_console(self, monkeypatch, sync_log):
        def children(command_or_name, *args, **kwargs):
            if child_of(command_or_name) == "import_cost_data":
                kwargs["stdout"].write("[OK] Import completed!\n")
            return clean_children(command_or_name, *args, **kwargs)

        stdout = run_sync(monkeypatch, children)

        assert "[OK] Import completed!" in stdout


@pytest.mark.django_db
class TestForecastRefreshIsNotDestructive:
    """--refresh used to delete before the forecast was computed, so a run
    with too little history wiped the table and generated nothing."""

    def _build_forecast(self, days_ahead=1, cost="100.00"):
        """An unsaved forecast row; the unique key is (date, type, value)."""
        from datetime import date, timedelta
        from decimal import Decimal

        from power_up.finops.models import CostForecast

        today = date.today()
        return CostForecast(
            forecast_date=today + timedelta(days=days_ahead),
            dimension_type="overall",
            dimension_value="Total",
            forecast_cost=Decimal(cost),
            lower_bound=Decimal("90.00"),
            upper_bound=Decimal("110.00"),
            confidence=Decimal("95.00"),
            training_period_start=today - timedelta(days=90),
            training_period_end=today,
            training_days=90,
        )

    def _existing_forecast(self, days_ahead=1):
        forecast = self._build_forecast(days_ahead=days_ahead)
        forecast.save()
        return forecast

    def test_insufficient_history_keeps_the_existing_forecasts(self, monkeypatch):
        from power_up.finops.models import CostForecast

        self._existing_forecast()
        monkeypatch.setattr(
            "power_up.finops.management.commands.generate_cost_forecasts"
            ".CostForecaster.forecast_costs",
            lambda **kwargs: [],
        )

        out = io.StringIO()
        call_command("generate_cost_forecasts", "--refresh", stdout=out, stderr=out)

        assert "Insufficient data" in out.getvalue()
        # The old forecast must survive a run that produced no replacement.
        assert CostForecast.objects.count() == 1

    def test_a_successful_run_still_clears_the_old_forecasts(self, monkeypatch):
        """--refresh must still drop rows the new run won't overwrite, or
        forecasts for dates that fell out of the horizon would linger."""
        from power_up.finops.models import CostForecast

        # A stale row 5 days out that the new run does not replace.
        self._existing_forecast(days_ahead=5)
        replacement = self._build_forecast(days_ahead=1, cost="250.00")
        monkeypatch.setattr(
            "power_up.finops.management.commands.generate_cost_forecasts"
            ".CostForecaster.forecast_costs",
            lambda **kwargs: [replacement],
        )

        out = io.StringIO()
        call_command("generate_cost_forecasts", "--refresh", stdout=out, stderr=out)

        assert "Deleted 1 old forecasts" in out.getvalue()
        remaining = list(CostForecast.objects.all())
        assert len(remaining) == 1
        assert remaining[0].forecast_date == replacement.forecast_date


class TestAggregationWindows:
    """Each night rebuilds the last week plus one rotating older slice.

    This replaced a Sunday-only 60-day rebuild that ran as one request: 176s
    on 2026-11-01 and 190s on 2026-11-08, against the Function's 225s read
    timeout.
    """

    @staticmethod
    def _windows(**kwargs):
        from power_up.finops.management.commands.sync_daily_costs import (
            aggregation_windows,
        )

        return aggregation_windows(**kwargs)

    @staticmethod
    def _covers(windows, day):
        return any(start <= day <= end for start, end in windows)

    def test_monday_rebuilds_the_last_week_and_offsets_8_to_15(self):
        from datetime import date

        assert self._windows(today=date(2026, 11, 2)) == [
            (date(2026, 10, 26), date(2026, 11, 2)),
            (date(2026, 10, 18), date(2026, 10, 25)),
        ]

    def test_sunday_rebuilds_the_last_week_and_offsets_56_to_63(self):
        from datetime import date

        assert self._windows(today=date(2026, 11, 8)) == [
            (date(2026, 11, 1), date(2026, 11, 8)),
            (date(2026, 9, 6), date(2026, 9, 13)),
        ]

    def test_full_is_still_one_sixty_day_window(self):
        from datetime import date

        assert self._windows(full=True, today=date(2026, 11, 4)) == [
            (date(2026, 9, 5), date(2026, 11, 4))
        ]

    def test_no_night_rebuilds_more_than_sixteen_days(self):
        from datetime import date, timedelta

        for offset in range(7):
            today = date(2026, 11, 2) + timedelta(days=offset)
            recent, older = self._windows(today=today)
            assert older[1] < recent[0], "the two windows must not overlap"
            days = sum((end - start).days + 1 for start, end in (recent, older))
            assert days == 16

    def test_a_week_of_nights_reaches_back_past_the_old_full_window(self):
        from datetime import date, timedelta

        from power_up.finops.management.commands.sync_daily_costs import (
            FULL_DAYS_BACK,
        )

        for first in range(7):
            ages = set()
            for night in range(7):
                today = date(2026, 11, 2) + timedelta(days=first + night)
                for start, end in self._windows(today=today):
                    ages |= set(range((today - end).days, (today - start).days + 1))
            assert ages == set(range(64))
            assert set(range(FULL_DAYS_BACK + 1)) <= ages

    def test_no_recent_day_waits_more_than_eight_nights(self):
        """Every day is rebuilt at least once in any 8 consecutive nights
        until it is older than the old 60-day window (the Sunday rebuild
        managed 7)."""
        from datetime import date, timedelta

        from power_up.finops.management.commands.sync_daily_costs import (
            FULL_DAYS_BACK,
        )

        for first in range(14):
            day = date(2026, 11, 2) + timedelta(days=first)
            last_rebuilt = None
            for age in range(FULL_DAYS_BACK + 1):
                if self._covers(self._windows(today=day + timedelta(days=age)), day):
                    last_rebuilt = age
                assert last_rebuilt is not None
                assert age - last_rebuilt <= 7, (day, age)

    def test_the_weekday_comes_from_the_aggregator_clock(self, monkeypatch):
        """The weekday and the dates must come from one clock; date.today()
        and timezone.now() can disagree around midnight."""
        from datetime import date, datetime, timezone as dt_timezone

        monkeypatch.setattr(
            f"{MOD}.timezone.now",
            lambda: datetime(2026, 11, 8, 23, 30, tzinfo=dt_timezone.utc),
        )
        assert self._windows()[0] == (date(2026, 11, 1), date(2026, 11, 8))

    def test_command_passes_tonights_windows_to_the_aggregator(
        self, monkeypatch, sync_log
    ):
        from datetime import date

        seen = {}
        windows = [
            (date(2026, 11, 2), date(2026, 11, 9)),
            (date(2026, 10, 10), date(2026, 10, 17)),
        ]

        class FakeAggregator:
            @staticmethod
            def refresh_windows(**kwargs):
                seen.update(kwargs)
                return {
                    "daily_aggregations": 1,
                    "monthly_aggregations": 1,
                    "period": "",
                }

        monkeypatch.setattr(f"{MOD}.call_command", clean_children)
        monkeypatch.setattr(f"{MOD}.CostAggregator", FakeAggregator)
        monkeypatch.setattr(
            f"{MOD}.aggregation_windows", lambda full=False, today=None: windows
        )

        class FakeCostExport:
            class objects:
                @staticmethod
                def filter(**kwargs):
                    return FakeCostExport.objects

                @staticmethod
                def count():
                    return 0

        monkeypatch.setattr(f"{MOD}.CostExport", FakeCostExport)
        call_command("sync_daily_costs", "--step", "aggregations", stdout=io.StringIO())
        assert seen["windows"] == windows
        assert any(
            "aggregation_windows=2026-11-02..2026-11-09,2026-10-10..2026-10-17" in m
            for m in messages(sync_log)
        )


class TestRefreshWindows:
    """The aggregator side: daily per window, each touched month once."""

    def _record(self, monkeypatch):
        from power_up.finops.utils.aggregation import CostAggregator

        calls = {"daily": [], "monthly": []}

        def daily(start, end, currency):
            calls["daily"].append((start, end))
            return (end - start).days + 1

        def monthly(year, month, currency):
            calls["monthly"].append((year, month))
            return 1

        monkeypatch.setattr(CostAggregator, "aggregate_daily", staticmethod(daily))
        monkeypatch.setattr(CostAggregator, "aggregate_monthly", staticmethod(monthly))
        return CostAggregator, calls

    def test_a_month_touched_by_both_windows_is_rolled_up_once(self, monkeypatch):
        from datetime import date

        aggregator, calls = self._record(monkeypatch)
        result = aggregator.refresh_windows(
            [
                (date(2026, 10, 29), date(2026, 11, 5)),
                (date(2026, 10, 13), date(2026, 10, 20)),
            ]
        )
        assert calls["daily"] == [
            (date(2026, 10, 29), date(2026, 11, 5)),
            (date(2026, 10, 13), date(2026, 10, 20)),
        ]
        assert calls["monthly"] == [(2026, 10), (2026, 11)]
        assert result == {
            "daily_aggregations": 16,
            "monthly_aggregations": 2,
            "period": "2026-10-29 to 2026-11-05, 2026-10-13 to 2026-10-20",
        }

    def test_refresh_all_is_one_window_ending_today(self, monkeypatch):
        from datetime import date, datetime, timezone as dt_timezone

        aggregator, calls = self._record(monkeypatch)
        monkeypatch.setattr(
            "power_up.finops.utils.aggregation.timezone.now",
            lambda: datetime(2026, 11, 8, 3, 0, tzinfo=dt_timezone.utc),
        )
        result = aggregator.refresh_all(days_back=60)
        assert calls["daily"] == [(date(2026, 9, 9), date(2026, 11, 8))]
        assert calls["monthly"] == [(2026, 9), (2026, 10), (2026, 11)]
        assert result["period"] == "2026-09-09 to 2026-11-08"


class TestStepSelection:
    def _run(self, monkeypatch, *args):
        invoked = []

        def children(command_or_name, *a, **kw):
            invoked.append(child_of(command_or_name))
            return clean_children(command_or_name, *a, **kw)

        monkeypatch.setattr(f"{MOD}.call_command", children)
        monkeypatch.setattr(
            f"{MOD}.CostAggregator",
            type(
                "A",
                (),
                {
                    "refresh_windows": staticmethod(
                        lambda **k: {
                            "daily_aggregations": 0,
                            "monthly_aggregations": 0,
                            "period": "",
                        }
                    )
                },
            ),
        )
        monkeypatch.setattr(
            f"{MOD}.CostExport",
            type(
                "E",
                (),
                {
                    "objects": type(
                        "M", (), {"filter": lambda s, **k: s, "count": lambda s: 0}
                    )()
                },
            ),
        )
        from power_up.finops.management.commands.sync_daily_costs import Command

        command = Command()
        call_command(command, *args, stdout=io.StringIO())
        return invoked, command.step_results

    def test_only_the_requested_step_runs(self, monkeypatch):
        invoked, results = self._run(monkeypatch, "--step", "anomalies")
        assert invoked == ["detect_cost_anomalies"]
        assert [r["step"] for r in results] == ["anomalies"]
        assert results[0]["status"] == "ok"

    def test_no_selection_runs_all_five(self, monkeypatch):
        _, results = self._run(monkeypatch)
        assert [r["step"] for r in results] == [
            "import",
            "aggregations",
            "anomalies",
            "forecasts",
            "reservations",
        ]
