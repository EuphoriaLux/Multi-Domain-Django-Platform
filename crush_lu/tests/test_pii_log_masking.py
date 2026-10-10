"""Member email addresses must not reach Application Insights.

Production exports Python logs through an OpenTelemetry ``LoggingHandler`` on
the root logger, attached at boot and re-attached per worker by
``RuntimeLoggingCanaryMiddleware``. ``PIIMaskingFilter`` used to sit only on
the ERROR-level console handler from ``LOGGING``, so every INFO line naming a
member went to ``AppTraces`` verbatim: 12,824 production rows holding about
2,000 distinct member addresses over 90 days (measured 2026-10-10).

The filter used to have the opposite problem as well: any argument that merely
contained an "@" was collapsed into one fake masked address, which wiped event
titles like "Speed Dating @ Urban Bar". Both halves are pinned here.
"""

import logging

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs import LoggingHandler as SdkLoggingHandler
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor

try:
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
except ImportError:  # opentelemetry-sdk before the LogRecordExporter rename
    from opentelemetry.sdk._logs.export import (
        InMemoryLogExporter as InMemoryLogRecordExporter,
    )

from azureproject import middleware as mw
from azureproject import telemetry_config
from azureproject.logging_utils import PIIMaskingFilter

OTEL_HANDLER_CLASSES = telemetry_config._otel_logging_handler_classes()

# A logger no LOGGING config touches, so its INFO records reach root.
log = logging.getLogger("pii_masking_under_test")


@pytest.fixture
def exporter(monkeypatch):
    """Root without an OTel handler, and a provider that exports to memory.

    ``AppConfig.ready()`` has already put an OTel handler on root in this
    process, bound to the no-op default provider. It is set aside so the
    attach under test builds its own, then put back. Only OTel handlers are
    touched: pytest swaps its own capture handlers on root between the setup
    and teardown phases, and restoring a setup-time snapshot would leak them.
    """
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    # attach_otel_logging_handler_to_root imports this at call time.
    monkeypatch.setattr("opentelemetry._logs.get_logger_provider", lambda: provider)

    root = logging.getLogger()
    saved_level = root.level
    saved_otel = _otel_handlers_on_root()
    for handler in saved_otel:
        root.removeHandler(handler)

    yield exporter

    for handler in _otel_handlers_on_root():
        root.removeHandler(handler)
    for handler in saved_otel:
        root.addHandler(handler)
    root.setLevel(saved_level)
    provider.shutdown()


def _otel_handlers_on_root():
    return [
        h for h in logging.getLogger().handlers if isinstance(h, OTEL_HANDLER_CLASSES)
    ]


def _pii_filters(handler):
    return [f for f in handler.filters if isinstance(f, PIIMaskingFilter)]


def _exported(exporter, needle):
    """Bodies of the exported records that contain ``needle``."""
    return [
        str(item.log_record.body)
        for item in exporter.get_finished_logs()
        if needle in str(item.log_record.body)
    ]


def _record(msg, *args):
    return logging.LogRecord("test", logging.INFO, __file__, 1, msg, args, None)


# --- through the OTel handler that feeds App Insights -----------------------


def test_a_member_address_logged_at_info_is_masked_on_export(exporter):
    assert telemetry_config.attach_otel_logging_handler_to_root()

    # azureproject/graph_email_backend.py: the recipients are a *list*, which
    # the old filter skipped because it only looked at str arguments.
    log.info(
        "Email accepted by Graph API for %s from %s; downstream delivery pending",
        ["jane.member@example.com"],
        "noreply@crush.lu",
    )
    # crush_lu/email_helpers.py: the address is already in the message.
    log.info(
        f"Skipping event registration email to {'jane.member@example.com'} "
        "- user unsubscribed"
    )

    [accepted] = _exported(exporter, "Email accepted by Graph API")
    [skipped] = _exported(exporter, "Skipping event registration email")
    for body in (accepted, skipped):
        assert "jane.member@example.com" not in body
        assert "j***r@e***.com" in body
    assert accepted.startswith("Email accepted by Graph API for ['j***r@e***.com']")


def test_an_at_sign_that_is_not_an_address_survives_export(exporter):
    assert telemetry_config.attach_otel_logging_handler_to_root()

    log.info("Event %s is full", "Speed Dating @ Urban Bar")
    log.warning(
        "[%s] %s",
        23,
        "Speed Dating @ Urban Bar: rejected; contact love@crush.lu\nsecond line",
    )

    assert _exported(exporter, "is full") == ["Event Speed Dating @ Urban Bar is full"]
    [report] = _exported(exporter, "rejected")
    assert report == (
        "[23] Speed Dating @ Urban Bar: rejected; contact l***e@c***.lu\nsecond line"
    )


def test_reattaching_keeps_one_handler_with_one_filter(exporter):
    for _ in range(3):
        assert telemetry_config.attach_otel_logging_handler_to_root()

    [handler] = _otel_handlers_on_root()
    assert len(_pii_filters(handler)) == 1


def test_the_per_worker_reattach_masks_a_handler_already_on_root(exporter, monkeypatch):
    """The runtime path: a handler is already there, so attach returns early.

    That early return is what every call after the first takes, so it has to
    mask the handler it finds rather than only the ones it creates.
    """
    from opentelemetry._logs import get_logger_provider

    bare = SdkLoggingHandler(logger_provider=get_logger_provider())
    logging.getLogger().addHandler(bare)
    monkeypatch.setattr(mw, "_runtime_logging_canary_done", False)

    canary = mw.RuntimeLoggingCanaryMiddleware(lambda request: HttpResponse("ok"))
    assert canary(RequestFactory().get("/")).status_code == 200

    assert _otel_handlers_on_root() == [bare]
    assert len(_pii_filters(bare)) == 1
    log.info("Profile submitted for review: %s", "jane.member@example.com")
    [body] = _exported(exporter, "Profile submitted for review")
    assert body == "Profile submitted for review: j***r@e***.com"


def test_the_boot_handler_from_configure_azure_monitor_is_masked(exporter, monkeypatch):
    """The boot path: configure_azure_monitor() adds its own handler class."""
    import azure.monitor.opentelemetry
    from opentelemetry._logs import get_logger_provider
    from opentelemetry.instrumentation.logging.handler import (
        LoggingHandler as InstrumentationLoggingHandler,
    )

    def fake_configure_azure_monitor(**kwargs):
        logging.getLogger().addHandler(
            InstrumentationLoggingHandler(logger_provider=get_logger_provider())
        )

    monkeypatch.setenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "InstrumentationKey=x")
    monkeypatch.setattr(
        azure.monitor.opentelemetry,
        "configure_azure_monitor",
        fake_configure_azure_monitor,
    )

    assert telemetry_config.configure_azure_monitor_telemetry("test")

    [handler] = _otel_handlers_on_root()
    assert isinstance(handler, InstrumentationLoggingHandler)
    assert len(_pii_filters(handler)) == 1
    log.info("Created data consent for new user 7 (%s)", "jane.member@example.com")
    [body] = _exported(exporter, "Created data consent")
    assert body == "Created data consent for new user 7 (j***r@e***.com)"


# --- the filter itself -------------------------------------------------------


def test_mapping_args_stay_a_mapping():
    # LogRecord turns a lone dict argument into record.args itself; the old
    # filter iterated it into a tuple of its keys and broke the message.
    record = _record("%(who)s signed up (%(n)d)", {"who": "a.b@example.com", "n": 3})
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == "a***b@e***.com signed up (3)"


def test_an_object_whose_text_is_an_address_is_masked():
    class User:  # the auth User's __str__ is its username, here the email
        def __str__(self):
            return "jane.member@example.com"

    record = _record("Login by %s", User())
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == "Login by j***r@e***.com"


def test_other_arguments_pass_through_untouched():
    marker = object()
    record = _record("%d items, ratio %.1f, %s, %s", 5, 0.25, None, marker)
    PIIMaskingFilter().filter(record)
    assert record.args == (5, 0.25, None, marker)


def test_the_callers_list_is_not_modified():
    recipients = ["jane.member@example.com"]
    record = _record("To %s", recipients)
    PIIMaskingFilter().filter(record)
    assert recipients == ["jane.member@example.com"]
    assert record.getMessage() == "To ['j***r@e***.com']"


def test_masking_twice_changes_nothing():
    # ERROR records pass the console handler's filter and then the OTel one.
    record = _record("Bounce for %s", "jane.member@sub.example.co.uk")
    PIIMaskingFilter().filter(record)
    once = record.getMessage()
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == once
    assert "jane.member" not in once


def test_an_argument_that_cannot_be_rendered_does_not_raise():
    class Broken:
        def __str__(self):
            raise RuntimeError("no")

    broken = Broken()
    record = _record("value %s", broken)
    assert PIIMaskingFilter().filter(record) is True
    assert record.args == (broken,)
