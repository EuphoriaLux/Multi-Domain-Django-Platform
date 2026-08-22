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
import sys
import types
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
        return FakeResponse(200, {"success": True, "regions": list(REGIONS)})

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


def run(timer_app, past_due=False):
    timer_app.daily_retail_price_sync(SimpleNamespace(past_due=past_due))


def test_posts_each_region_separately_to_the_documented_urls(timer_app, transport):
    run(timer_app)

    assert transport.region_list_url == WEBHOOK.rstrip("/") + "/regions/"
    assert transport.posted == REGIONS
    assert transport.urls == [WEBHOOK] * len(REGIONS)
    assert transport.bodies == [{"region": region} for region in REGIONS]


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


def test_an_unreachable_region_list_aborts_before_syncing_anything(
    timer_app, transport
):
    transport.set_get(lambda url, **kwargs: FakeResponse(503, {"error": "down"}))

    with pytest.raises(requests.exceptions.HTTPError):
        run(timer_app)

    assert transport.posted == [], "synced without knowing which regions to walk"


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
