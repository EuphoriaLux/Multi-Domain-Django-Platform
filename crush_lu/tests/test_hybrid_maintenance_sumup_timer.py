"""The SumUpReconciliation timer in azure-functions/hybrid-maintenance.

A merge under azure-functions/ deploys that Function to production at once,
while the Django route only reaches production at the next slot swap. So the
timer ships dormant: with its URL unset it must send nothing, log a WARNING
naming the variable, and NOT fail the invocation (which would trip the
timer-failure alert daily). Every other timer keeps the "unset raises" rule —
that rule is what caught the 2026-07-30 outage, and this must not weaken it.

Loaded with a stubbed ``azure.functions`` (same idiom as
azure-functions/finops-daily-sync/tests/test_function_app.py).
"""

import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest
import requests

# conftest.py's session-scoped Site seeding is autouse, so the DB must exist.
pytestmark = pytest.mark.django_db

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "azure-functions"
    / "hybrid-maintenance"
    / "function_app.py"
)
URL_VAR = "DJANGO_SUMUP_RECONCILIATION_URL"
URL = "https://test.crush.lu/api/admin/sumup-reconciliation/"


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


class FakeTimer:
    past_due = False


@pytest.fixture
def app(monkeypatch):
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

    spec = importlib.util.spec_from_file_location("hybrid_timer", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("HYBRID_MAINTENANCE_ENABLED", "true")
    monkeypatch.setenv("ADMIN_API_KEY", "k")
    monkeypatch.delenv(URL_VAR, raising=False)
    return module


class _Posts(list):
    """Recorded POSTs, plus the response the next one gets."""

    response = FakeResponse(202, {"status": "ok"})

    def respond_with(self, response):
        self.response = response


@pytest.fixture
def posts(app, monkeypatch):
    calls = _Posts()

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return calls.response

    monkeypatch.setattr(app.requests, "post", fake_post)
    return calls


def test_unset_url_is_dormant_not_failed(app, posts, caplog):
    with caplog.at_level(logging.WARNING):
        app.sumup_reconciliation(FakeTimer())  # must not raise
    assert posts == []
    assert any(
        "DORMANT" in r.getMessage() and URL_VAR in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


def test_other_timers_still_raise_on_an_unset_url(app, posts, monkeypatch):
    monkeypatch.delenv("DJANGO_ECHO_SYNC_URL", raising=False)
    with pytest.raises(RuntimeError, match="DJANGO_ECHO_SYNC_URL"):
        app.echo_lu_sync(FakeTimer())
    assert posts == []


def test_master_switch_off_sends_nothing(app, posts, monkeypatch):
    monkeypatch.setenv("HYBRID_MAINTENANCE_ENABLED", "false")
    monkeypatch.setenv(URL_VAR, URL)
    app.sumup_reconciliation(FakeTimer())
    assert posts == []


def test_configured_timer_posts_with_bearer(app, posts, monkeypatch):
    monkeypatch.setenv(URL_VAR, URL)
    app.sumup_reconciliation(FakeTimer())
    assert len(posts) == 1
    url, kwargs = posts[0]
    assert url == URL
    assert kwargs["headers"]["Authorization"] == "Bearer k"
    assert kwargs["timeout"] == 110


def test_django_flag_off_is_logged_as_skipped(app, posts, monkeypatch, caplog):
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(
        FakeResponse(
            200, {"skipped": True, "reason": "SUMUP_RECONCILIATION_ENABLED is off"}
        )
    )
    with caplog.at_level(logging.WARNING):
        app.sumup_reconciliation(FakeTimer())
    assert any(
        "SKIPPED" in r.getMessage() and "SUMUP_RECONCILIATION_ENABLED" in r.getMessage()
        for r in caplog.records
    )


def test_server_error_fails_the_invocation(app, posts, monkeypatch):
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(FakeResponse(500, {"error": "internal_error"}))
    with pytest.raises(requests.exceptions.HTTPError):
        app.sumup_reconciliation(FakeTimer())


def test_timer_source_never_mentions_refund_issuing():
    """Contract §8: automation observes refunds; it never issues one."""
    assert "refund_transaction" not in MODULE_PATH.read_text(encoding="utf-8")
