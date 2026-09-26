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

    response = FakeResponse(
        202,
        {
            "status": "ok",
            "checked": 3,
            "reconciled": 0,
            "partial": 0,
            "errors": 0,
            "unchecked": 0,
            "in_window": 5,
            "needs_review": 0,
        },
    )

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


def _counters(**overrides):
    body = {
        "status": "ok",
        "timestamp": "2026-09-23T02:34:00+00:00",
        "checked": 5,
        "reconciled": 0,
        "partial": 0,
        "errors": 0,
        "unchecked": 0,
        "in_window": 5,
        "needs_review": 0,
    }
    body.update(overrides)
    return body


def test_sweep_errors_fail_the_invocation(app, posts, monkeypatch, caplog):
    """Codex 4079912167: a total SumUp outage answers 202 - it must still fail."""
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(
        FakeResponse(202, _counters(errors=5, pii="member@example.com CRUSH-REF"))
    )
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match="errors=5"):
        app.sumup_reconciliation(FakeTimer())
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "member@example.com" not in logged
    assert "CRUSH-REF" not in logged


def test_unchecked_rows_warn_without_failing(app, posts, monkeypatch, caplog):
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(FakeResponse(202, _counters(unchecked=4)))
    with caplog.at_level(logging.WARNING):
        app.sumup_reconciliation(FakeTimer())
    assert any(
        r.levelno == logging.WARNING and "unchecked=4" in r.getMessage()
        for r in caplog.records
    )


def test_partial_refunds_warn_without_failing(app, posts, monkeypatch, caplog):
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(FakeResponse(202, _counters(partial=1)))
    with caplog.at_level(logging.WARNING):
        app.sumup_reconciliation(FakeTimer())
    assert any(
        r.levelno == logging.WARNING and "partial=1" in r.getMessage()
        for r in caplog.records
    )


def test_clean_run_logs_counts_at_info(app, posts, monkeypatch, caplog):
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(FakeResponse(202, _counters(reconciled=1)))
    with caplog.at_level(logging.INFO):
        app.sumup_reconciliation(FakeTimer())
    assert any(
        r.levelno == logging.INFO and "reconciled=1" in r.getMessage()
        for r in caplog.records
    )
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_202_without_counters_fails(app, posts, monkeypatch):
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(FakeResponse(202, {"status": "ok"}))
    with pytest.raises(RuntimeError, match="expected counters"):
        app.sumup_reconciliation(FakeTimer())


def test_other_timers_ignore_the_202_body(app, posts, monkeypatch):
    """The counter check is SumUpReconciliation's alone."""
    monkeypatch.setenv("DJANGO_ECHO_SYNC_URL", URL)
    posts.respond_with(FakeResponse(202, {"errors": 9}))
    app.echo_lu_sync(FakeTimer())  # must not raise


def test_non_202_success_other_than_the_skip_fails(app, posts, monkeypatch):
    """Codex 4080044207: only the exact flag-off skip may pass as a non-202."""
    monkeypatch.setenv(URL_VAR, URL)
    for response in (
        FakeResponse(200, {"status": "ok"}),
        FakeResponse(200, {"skipped": True, "reason": "x", "checked": 1}),
        FakeResponse(200, {"skipped": "yes", "reason": "x"}),
        FakeResponse(204, None),
    ):
        posts.respond_with(response)
        with pytest.raises(RuntimeError, match="unexpected"):
            app.sumup_reconciliation(FakeTimer())


def test_non_json_2xx_fails(app, posts, monkeypatch):
    class NotJson(FakeResponse):
        def json(self):
            raise ValueError("not json")

    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(NotJson(200, "<html>"))
    with pytest.raises(RuntimeError, match="unexpected"):
        app.sumup_reconciliation(FakeTimer())


def test_exact_skip_payload_passes(app, posts, monkeypatch):
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(
        FakeResponse(
            200, {"skipped": True, "reason": "SUMUP_RECONCILIATION_ENABLED is off"}
        )
    )
    app.sumup_reconciliation(FakeTimer())  # must not raise


def test_schedule_is_0234_utc():
    """Codex 4080044200: clear of the :32 campaign tick's 110 s tail."""
    import re

    src = MODULE_PATH.read_text(encoding="utf-8")
    block = src[src.index('@app.function_name(name="SumUpReconciliation")') :]
    assert re.search(r'schedule="0 34 2 \* \* \*"', block[:1200])


def test_needs_review_fails_the_invocation(app, posts, monkeypatch, caplog):
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(FakeResponse(202, _counters(needs_review=1, errors=1)))
    with pytest.raises(RuntimeError, match="1 need manual review"):
        app.sumup_reconciliation(FakeTimer())


def test_error_message_does_not_guess_what_the_errors_were(app, posts, monkeypatch):
    """errors also counts a committed refund whose on_commit callback raised,
    so the message must not describe every error as unchecked or unwritten."""
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(FakeResponse(202, _counters(errors=2, needs_review=1)))
    with pytest.raises(RuntimeError) as raised:
        app.sumup_reconciliation(FakeTimer())
    message = str(raised.value)
    assert message.startswith(
        "SumUpReconciliation: 2 row(s) reported as errors, 1 need manual review "
        "(per-row details in the web app's reconcile_sumup_payments log) — "
    )
    assert "errors=2" in message
    for guess in ("not checked", "not written", "could not be", "held for review"):
        assert guess not in message


def test_needs_review_alone_still_fails(app, posts, monkeypatch):
    """Even if Django ever stops folding it into errors."""
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(FakeResponse(202, _counters(needs_review=1)))
    with pytest.raises(RuntimeError, match="need manual review"):
        app.sumup_reconciliation(FakeTimer())


def test_another_endpoints_skip_does_not_pass(app, posts, monkeypatch):
    """api_admin_campaigns answers the same {skipped, reason} shape."""
    monkeypatch.setenv(URL_VAR, URL)
    posts.respond_with(
        FakeResponse(
            200, {"skipped": True, "reason": "CAMPAIGN_DISPATCH_ENABLED is off"}
        )
    )
    with pytest.raises(RuntimeError, match="unexpected"):
        app.sumup_reconciliation(FakeTimer())


def test_timer_source_never_mentions_refund_issuing():
    """Contract §8: automation observes refunds; it never issues one."""
    assert "refund_transaction" not in MODULE_PATH.read_text(encoding="utf-8")
