"""The runtime logging canary fires once per worker and never breaks a request.

It exists because the boot canary in ``azureproject.apps`` cannot detect the
failure it was written for: it is emitted inside ``AppConfig.ready()`` on the
line after the OTel handler is attached, so it only ever proved the handler
worked in that instant. On 2026-08-07 both slots had exported nothing but those
boot lines for 96 hours — 0 runtime records against 6,822 production requests —
while it read green throughout. This probe runs on the request path instead.
"""

import logging

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from azureproject import middleware as mw


@pytest.fixture(autouse=True)
def _reset_canary():
    """Each test gets a fresh process, as far as the canary is concerned."""
    mw._runtime_logging_canary_done = False
    yield
    mw._runtime_logging_canary_done = False


def _build(calls):
    def get_response(request):
        calls.append(request)
        return HttpResponse("ok")

    return mw.RuntimeLoggingCanaryMiddleware(get_response)


def test_it_fires_once_and_names_the_handlers_on_root(caplog):
    caplog.set_level(logging.ERROR, logger="azureproject.middleware")
    calls = []
    middleware = _build(calls)
    request = RequestFactory().get("/")

    assert middleware(request).status_code == 200

    records = [r for r in caplog.records if "Runtime logging canary" in r.message]
    assert len(records) == 1
    # ERROR on purpose: the console handler is ERROR-only, so the line reaches
    # the Azure Log Stream even when the OTel export path is dead. Comparing
    # the log stream against AppTraces is what localises the fault.
    assert records[0].levelno == logging.ERROR
    # The handler list is the payload — "present but not exporting" and
    # "removed from root entirely" are different bugs with different fixes.
    assert "before=" in records[0].getMessage()
    assert "after=" in records[0].getMessage()


def test_a_second_request_stays_quiet(caplog):
    """One record per worker, not one per request."""
    caplog.set_level(logging.ERROR, logger="azureproject.middleware")
    calls = []
    middleware = _build(calls)
    factory = RequestFactory()

    middleware(factory.get("/"))
    caplog.clear()
    middleware(factory.get("/other"))

    assert [r for r in caplog.records if "Runtime logging canary" in r.message] == []
    assert len(calls) == 2


def test_a_broken_probe_does_not_take_the_request_down(caplog, monkeypatch):
    """A diagnostic that 500s the site is worse than no diagnostic.

    And it must not retry forever: the flag is set before the emit runs, so a
    probe that throws does it once rather than on every subsequent request.
    """
    caplog.set_level(logging.ERROR, logger="azureproject.middleware")
    from azureproject import telemetry_config

    def _boom(*args, **kwargs):
        raise RuntimeError("no logger provider")

    monkeypatch.setattr(
        telemetry_config, "attach_otel_logging_handler_to_root", _boom
    )

    calls = []
    middleware = _build(calls)
    factory = RequestFactory()

    assert middleware(factory.get("/")).status_code == 200
    caplog.clear()
    assert middleware(factory.get("/again")).status_code == 200

    assert len(calls) == 2
    assert [r for r in caplog.records if "canary" in r.message.lower()] == []
