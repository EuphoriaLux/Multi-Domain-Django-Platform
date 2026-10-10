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


def test_internationalised_addresses_are_masked():
    # Django's EmailValidator accepts IDN domains; the old ASCII-only
    # pattern let them through untouched.
    record = _record("Invite sent to %s and %s", "jane@müller.de", "jürgen@example.com")
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == "Invite sent to j***e@m***.de and j***n@e***.com"


def test_addresses_used_as_mapping_keys_are_masked_without_merging():
    # A lone dict argument becomes record.args itself and prints whole.
    record = _record(
        "Results: %s", {"jane@example.com": "sent", "jose@example.com": "failed"}
    )
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == (
        "Results: {'j***e@e***.com': 'sent', 'j***e@e***.com#2': 'failed'}"
    )


def test_quoted_local_parts_are_masked():
    # Valid for Django's EmailValidator; the quote next to "@" used to stop
    # the match. A quoted address in ordinary JSON-ish text still masks only
    # the address.
    record = _record(
        "Sent to %s, %s and %s",
        '"john..doe"@example.com',
        '"a@b"@example.com',
        '{"email": "jane@example.com"}',
    )
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == (
        'Sent to "***"@e***.com, "***"@e***.com and {"email": "j***e@e***.com"}'
    )


def test_domains_with_combining_marks_are_masked():
    # Devanagari vowel signs are combining marks, outside Python's \w.
    record = _record("Sent to %s and %s", "jane@example.कॉम", "jane@कंपनी.com")
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == "Sent to j***e@e***.कॉम and j***e@क***.com"


def test_non_bmp_domains_are_masked():
    # Django accepts these through its IDNA fallback; a one-character TLD
    # is fine when it is non-ASCII.
    record = _record("Sent to %s and %s", "jane@example.𐌀", "jane@😀.com")
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == "Sent to j***e@e***.𐌀 and j***e@😀***.com"


def test_masking_time_stays_linear_on_hostile_text():
    """The filter runs inline on every exported record.

    One regex over the whole text took seconds on these shapes (it restarts
    at every position of a long run and backtracks), stalling the request
    that logged, say, an upstream error body.
    """
    import time

    hostile = [
        "x@" + "a." * 50_000 + "1",
        "a" * 100_000 + "@b",
        ("a" * 1_000 + "@") * 100,
        '"' + "a" * 100_000 + '"@example.com',
    ]
    started = time.perf_counter()
    for text in hostile:
        PIIMaskingFilter()._mask_text(text)
    assert time.perf_counter() - started < 1.0


def test_version_strings_are_not_addresses():
    record = _record("Requires %s", "pkg@1.2.3")
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == "Requires pkg@1.2.3"


def test_localhost_and_ip_literal_domains_are_masked():
    # EmailValidator's `localhost` allowlist and bracketed IP literals.
    record = _record(
        "Signup for %s, %s and %s",
        "member@localhost",
        "member@[127.0.0.1]",
        "admin@[::1]",
    )
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == (
        "Signup for m***r@l***, m***r@[***.1] and a***n@[***"
    )


def test_every_local_part_character_django_accepts_is_caught():
    # A character next to "@" outside the pattern used to leave the whole
    # address in clear. Delimiters before it must not swallow a log label.
    record = _record(
        "Sent to %s, %s, %s and user=%s",
        "member!@example.com",
        "a=b@example.com",
        "o'brien@example.com",
        "jane@example.com",
    )
    PIIMaskingFilter().filter(record)
    assert record.getMessage() == (
        "Sent to m***!@e***.com, a=b***@e***.com, o'b***n@e***.com "
        "and user=j***e@e***.com"
    )


# --- exceptions attached with exc_info -----------------------------------------

MEMBER_EMAIL = "jane.member" + "@example.com"


def _raise_duplicate_signup(email):
    """The shape of the duplicate-email signup failure in views_account.py.

    The address arrives as a value, as it does in production: a traceback
    prints source lines, and a literal address here would be one of them.
    """
    from django.db import IntegrityError

    try:
        raise ValueError(
            'duplicate key value violates unique constraint "account_email_key"\n'
            f"DETAIL:  Key (email)=({email}) already exists."
        )
    except ValueError as cause:
        raise IntegrityError(f"Signup failed for {email}") from cause


def test_an_exception_mentioning_an_address_is_masked_on_export(exporter):
    """The OTel handler reads exc_info itself, not record.msg."""
    assert telemetry_config.attach_otel_logging_handler_to_root()

    try:
        _raise_duplicate_signup(MEMBER_EMAIL)
    except Exception as exc:
        log.error(f"Signup failed for email: {exc}", exc_info=True)
        original = exc

    [exported] = [
        item.log_record
        for item in exporter.get_finished_logs()
        if "Signup failed for email" in str(item.log_record.body)
    ]
    attributes = dict(exported.attributes)
    exported_text = "\n".join(
        [str(exported.body)] + [str(value) for value in attributes.values()]
    )
    assert "jane.member@example.com" not in exported_text
    assert attributes["exception.type"] == "IntegrityError"
    assert attributes["exception.message"] == "Signup failed for j***r@e***.com"
    stacktrace = attributes["exception.stacktrace"]
    # Still a useful trace: real frames, the original class names, the cause.
    assert "_raise_duplicate_signup" in stacktrace
    assert "django.db.utils.IntegrityError: Signup failed for j***r@e***.com" in (
        stacktrace
    )
    assert "ValueError: duplicate key value" in stacktrace
    assert "Key (email)=(j***r@e***.com) already exists." in stacktrace
    assert "direct cause of the following exception" in stacktrace
    # The caller's exception is untouched.
    assert "jane.member@example.com" in str(original)
    assert "jane.member@example.com" in str(original.__cause__)


def test_extra_attributes_are_masked_on_export(exporter):
    """extra= fields are exported as custom dimensions next to the message.

    crush_lu/pre_screening_notifications.py copies str(exc) into
    extra={"error": ...} beside a masked exc_info.
    """
    assert telemetry_config.attach_otel_logging_handler_to_root()

    log.error(
        "pre_screening.user_push_failed",
        extra={"submission_id": 7, "error": f"no device for {MEMBER_EMAIL}"},
    )

    [exported] = [
        item.log_record
        for item in exporter.get_finished_logs()
        if item.log_record.body == "pre_screening.user_push_failed"
    ]
    assert exported.attributes["error"] == "no device for j***r@e***.com"
    assert exported.attributes["submission_id"] == 7


def test_an_exception_without_an_address_is_left_as_it_is():
    try:
        raise ValueError("no address here, just a @ sign")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "failed", (), exc_info
    )
    PIIMaskingFilter().filter(record)
    assert record.exc_info is exc_info


def test_an_address_inside_an_exception_group_is_masked():
    # The group's own text is only "label (N sub-exceptions)", so the
    # address sits in a child that the traceback still prints.
    import sys
    import traceback
    from builtins import ExceptionGroup  # named for ruff; see logging_utils

    try:
        raise ExceptionGroup(
            "2 invitations failed",
            [ValueError(f"bounce for {MEMBER_EMAIL}"), KeyError("slot")],
        )
    except ExceptionGroup:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1, "failed", (), exc_info
    )
    PIIMaskingFilter().filter(record)

    rendered = "".join(traceback.format_exception(*record.exc_info))
    assert MEMBER_EMAIL not in rendered
    assert "ValueError: bounce for j***r@e***.com" in rendered
    assert "ExceptionGroup: 2 invitations failed (2 sub-exceptions)" in rendered
    assert "KeyError: 'slot'" in rendered
    assert MEMBER_EMAIL in str(exc_info[1].exceptions[0])  # caller's is untouched
