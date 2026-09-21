"""Cover the retail-price timer's own control flow.

This module ships to the Azure Functions runtime rather than to Django, so
nothing else in the suite touches it. The loop it drives is the fix for the
timer that failed every night against the ~240s App Service request ceiling:
it walks the catalogue one region per request, and the parts worth pinning are
that a single bad region does not stop the walk, that the wall-clock budget
stops short of the host's 10-minute kill, and that a failed night still raises
so the alert fires.
"""
import importlib.util
import logging
import sys
import types
from datetime import datetime, time, timezone
from types import SimpleNamespace

import pytest
import requests

MODULE_PATH = (
    __import__("pathlib").Path(__file__).resolve().parent.parent / "function_app.py"
)
REGIONS = ["westeurope", "northeurope", "uksouth"]
WEBHOOK = "https://example.test/finops/api/sync/retail-prices/"


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"{self.status_code} Server Error", response=self
            )


@pytest.fixture
def timer_app(monkeypatch):
    """Load function_app.py with a stubbed ``azure.functions``.

    ``azure`` itself is a real, installed namespace package used elsewhere in
    the suite, so only the missing ``azure.functions`` submodule is injected,
    and monkeypatch removes it again afterwards. Replacing ``azure`` wholesale
    would break every sibling test that imports ``azure.storage``.
    """

    class _FunctionApp:
        def function_name(self, **kwargs):
            return lambda fn: fn

        def timer_trigger(self, **kwargs):
            return lambda fn: fn

    stub = types.ModuleType("azure.functions")
    stub.FunctionApp = _FunctionApp
    stub.TimerRequest = object

    import azure

    monkeypatch.setitem(sys.modules, "azure.functions", stub)
    monkeypatch.setattr(azure, "functions", stub, raising=False)

    spec = importlib.util.spec_from_file_location("finops_timer", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("RETAIL_PRICE_SYNC_ENABLED", "true")
    monkeypatch.setenv("DJANGO_RETAIL_PRICE_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setenv("SECRET_SYNC_TOKEN", "token")
    return module


@pytest.fixture
def transport(timer_app, monkeypatch):
    """Record every call the timer makes and script the responses."""
    calls = SimpleNamespace(posted=[], urls=[], bodies=[], region_list_url=None)

    def fake_get(url, **kwargs):
        calls.region_list_url = url
        return FakeResponse(
            200,
            {
                "success": True,
                "regions": list(REGIONS),
                "snapshot_date": "2026-08-21",
            },
        )

    def fake_post(url, **kwargs):
        calls.urls.append(url)
        calls.bodies.append(kwargs.get("json"))
        calls.posted.append(kwargs["json"]["region"])
        return FakeResponse(200, {"message": "ok"})

    monkeypatch.setattr(timer_app.requests, "get", fake_get)
    monkeypatch.setattr(timer_app.requests, "post", fake_post)
    calls.set_get = lambda fn: monkeypatch.setattr(timer_app.requests, "get", fn)
    calls.set_post = lambda fn: monkeypatch.setattr(timer_app.requests, "post", fn)
    return calls


LAST_RUN = datetime(2026, 8, 21, 6, 40, 0, 12000, tzinfo=timezone.utc)
FIRST_RUN = datetime(2026, 8, 21, 4, 0, 0, 12000, tzinfo=timezone.utc)


def run(timer_app, past_due=False, now=LAST_RUN):
    """Run the timer; by default as the window's last run, which may alert."""
    timer_app._utcnow = lambda: now
    timer_app.daily_retail_price_sync(SimpleNamespace(past_due=past_due))


def test_posts_each_region_separately_to_the_documented_urls(timer_app, transport):
    run(timer_app)

    assert transport.region_list_url == WEBHOOK.rstrip("/") + "/regions/"
    assert transport.posted == REGIONS
    assert transport.urls == [WEBHOOK] * len(REGIONS)
    assert transport.bodies == [
        {"region": region, "snapshot_date": "2026-08-21"} for region in REGIONS
    ]


def test_every_region_of_one_invocation_shares_the_same_snapshot_date(
    timer_app, transport
):
    """A walk that straddles local midnight must not split across two dates.

    The date is fixed once from the region-list response rather than being
    re-derived per request, so a past-due invocation cannot file half its
    regions under yesterday and half under today.
    """
    run(timer_app)

    dates = {body["snapshot_date"] for body in transport.bodies}
    assert dates == {"2026-08-21"}


def test_a_server_that_omits_the_date_falls_back_to_its_own_default(
    timer_app, transport
):
    transport.set_get(
        lambda url, **kwargs: FakeResponse(200, {"regions": ["westeurope"]})
    )

    run(timer_app)

    assert transport.bodies == [{"region": "westeurope"}]


def test_one_failing_region_does_not_stop_the_others(timer_app, transport):
    """A bad region must cost one region, not the night."""

    def flaky_post(url, **kwargs):
        region = kwargs["json"]["region"]
        transport.posted.append(region)
        if region == "northeurope":
            return FakeResponse(500, {"error": "boom"})
        return FakeResponse(200, {"message": "ok"})

    transport.set_post(flaky_post)

    with pytest.raises(RuntimeError) as excinfo:
        run(timer_app)

    assert transport.posted == REGIONS, "the walk stopped at the first failure"
    assert "northeurope (HTTP 500)" in str(excinfo.value)
    assert "captured 2 of 3" in str(excinfo.value)


def test_running_out_of_budget_names_the_skipped_regions(timer_app, transport):
    """Better a logged summary than a silent kill at the host's 10-minute cap."""
    timer_app.BUDGET_SECONDS = -1

    with pytest.raises(RuntimeError) as excinfo:
        run(timer_app)

    assert transport.posted == []
    assert "captured 0 of 3" in str(excinfo.value)
    assert "3 skipped for time" in str(excinfo.value)
    for region in REGIONS:
        assert region in str(excinfo.value)


def test_a_backend_timeout_stops_the_walk_instead_of_occupying_more_workers(
    timer_app, transport
):
    """A timeout here does not cancel the work still running in the worker.

    Continuing to the next region would tie up a second worker while the first
    is still fetching, and so on through the pool — so the walk stops.
    """

    def timing_out_post(url, **kwargs):
        transport.posted.append(kwargs["json"]["region"])
        raise requests.exceptions.Timeout("no response")

    transport.set_post(timing_out_post)

    with pytest.raises(RuntimeError) as excinfo:
        run(timer_app)

    assert transport.posted == [REGIONS[0]], "kept posting after a timeout"
    assert "timeout" in str(excinfo.value)
    assert "2 region(s) not attempted" in str(excinfo.value)


def test_a_region_is_only_started_with_a_full_timeout_left(timer_app, transport):
    """The old walk posted with whatever time was left, down to a second.

    That gave up on Django mid-region, left the worker fetching for nobody,
    and was misreported as a backend timeout. Now a region that cannot be
    waited on in full is left for a later run instead.
    """
    timer_app.BUDGET_SECONDS = timer_app.PER_REGION_TIMEOUT - 1

    run(timer_app, now=FIRST_RUN)

    assert transport.posted == []


def test_every_post_waits_the_full_per_region_timeout(timer_app, transport):
    timeouts = []

    def recording_post(url, **kwargs):
        timeouts.append(kwargs["timeout"])
        return FakeResponse(200, {"message": "ok"})

    transport.set_post(recording_post)

    run(timer_app)

    assert timeouts == [timer_app.PER_REGION_TIMEOUT] * len(REGIONS)


def test_an_earlier_run_leaves_failures_to_the_next_slot(
    timer_app, transport, caplog
):
    """Before the window's last slot an incomplete run is expected, not an alert."""

    def flaky_post(url, **kwargs):
        region = kwargs["json"]["region"]
        transport.posted.append(region)
        if region == "northeurope":
            return FakeResponse(500, {"error": "boom"})
        return FakeResponse(200, {"message": "ok"})

    transport.set_post(flaky_post)

    with caplog.at_level(logging.WARNING):
        run(timer_app, now=FIRST_RUN)

    assert transport.posted == REGIONS
    assert "northeurope (HTTP 500)" in caplog.text
    assert "later run in the window retries them" in caplog.text


def test_only_the_pending_regions_are_posted(timer_app, transport):
    transport.set_get(
        lambda url, **kwargs: FakeResponse(
            200,
            {
                "regions": list(REGIONS),
                "pending": ["uksouth"],
                "snapshot_date": "2026-08-21",
            },
        )
    )

    run(timer_app)

    assert transport.posted == ["uksouth"]


def test_nothing_pending_means_a_quiet_no_op_even_on_the_last_run(
    timer_app, transport
):
    transport.set_get(
        lambda url, **kwargs: FakeResponse(
            200, {"regions": list(REGIONS), "pending": [], "snapshot_date": "2026-08-21"}
        )
    )

    run(timer_app)

    assert transport.posted == []


def test_a_django_build_without_pending_gets_every_region(timer_app, transport):
    """This app deploys on merge; Django may still be the pre-swap build."""
    run(timer_app, now=FIRST_RUN)

    assert transport.posted == REGIONS


@pytest.mark.parametrize(
    "clock, expected",
    [
        (time(4, 0), False),
        (time(6, 20), False),
        (time(6, 39, 30), True),  # a trigger firing a hair early
        (time(6, 40), True),
        (time(8, 15), True),  # a past-due run after the window
    ],
)
def test_last_run_detection(timer_app, clock, expected):
    now = datetime.combine(datetime(2026, 8, 21).date(), clock, tzinfo=timezone.utc)
    assert timer_app._is_last_run_of_window(now) is expected


def test_the_last_run_constant_matches_the_schedule(timer_app):
    """Drift between these would mean the alert never fires, or fires early."""
    _, minutes, hours, *_ = timer_app.RETAIL_SCHEDULE.split()
    last_minute = max(int(m) for m in minutes.split(","))
    last_hour = int(hours.split("-")[-1])
    assert timer_app.RETAIL_LAST_RUN_UTC == time(last_hour, last_minute)
    # Slots must be further apart than one run can last, so runs never overlap.
    slots = sorted(int(m) for m in minutes.split(","))
    gap_minutes = min(b - a for a, b in zip(slots, slots[1:]))
    assert gap_minutes * 60 > timer_app.BUDGET_SECONDS


def test_the_client_waits_longer_than_the_backend_budget(timer_app):
    """Django must always answer before this side stops listening.

    Inverting these two is what lets abandoned requests accumulate, so pin the
    ordering rather than relying on a comment.
    """
    assert timer_app.PER_REGION_TIMEOUT > 90 + 50


def test_an_unreachable_region_list_aborts_before_syncing_anything(
    timer_app, transport
):
    transport.set_get(lambda url, **kwargs: FakeResponse(503, {"error": "down"}))

    with pytest.raises(requests.exceptions.HTTPError):
        run(timer_app)

    assert transport.posted == [], "synced without knowing which regions to walk"


def test_a_404_region_list_explains_the_pending_production_swap(
    timer_app, transport
):
    """This app auto-deploys to production; Django waits on a manual swap.

    That window is real, so the failure has to name it — and must never fall
    back to the old whole-catalogue POST, which is the request that timed out
    every night in the first place.
    """
    transport.set_get(lambda url, **kwargs: FakeResponse(404, {"detail": "nope"}))

    with pytest.raises(RuntimeError) as excinfo:
        run(timer_app)

    message = str(excinfo.value)
    assert "swap" in message.lower()
    assert "No regions were synced" in message
    assert transport.posted == [], "fell back to syncing without a region list"


def test_an_empty_region_list_is_treated_as_a_failure(timer_app, transport):
    transport.set_get(lambda url, **kwargs: FakeResponse(200, {"regions": []}))

    with pytest.raises(RuntimeError, match="empty region list"):
        run(timer_app)

    assert transport.posted == []


def test_the_kill_switch_makes_no_requests_at_all(timer_app, transport, monkeypatch):
    monkeypatch.setenv("RETAIL_PRICE_SYNC_ENABLED", "false")

    run(timer_app)

    assert transport.posted == []
    assert transport.region_list_url is None


@pytest.mark.parametrize("missing", ["DJANGO_RETAIL_PRICE_WEBHOOK_URL", "SECRET_SYNC_TOKEN"])
def test_missing_configuration_fails_loudly(timer_app, transport, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValueError, match=missing):
        run(timer_app)

    assert transport.posted == []
