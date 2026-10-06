"""Outbound-call spans must not export credentials carried in the URL.

The Facebook Graph photo requests put the member's live access token in the
query string, and the Azure Monitor auto-instrumentation of ``requests``
records that URL on the dependency span. ``SensitiveQueryRedactionProcessor``
rewrites URL-bearing span attributes before the span can be exported.

Run with: pytest crush_lu/tests/test_telemetry_token_redaction.py -v
"""

from django.test import SimpleTestCase
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from azureproject.telemetry_config import SensitiveQueryRedactionProcessor

TOKEN = "EAAB_SECRET_TOKEN_123"
GRAPH_URL = (
    "https://graph.facebook.com/v24.0/1234/picture?width=720&height=720"
    f"&redirect=false&access_token={TOKEN}"
)


class SensitiveQueryRedactionProcessorTests(SimpleTestCase):
    def _export(self, attributes):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        # Same order as configure_azure_monitor: ours first, exporter after.
        provider.add_span_processor(SensitiveQueryRedactionProcessor())
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        span = provider.get_tracer("test").start_span("GET", attributes=attributes)
        span.end()
        (finished,) = exporter.get_finished_spans()
        return dict(finished.attributes)

    def test_access_token_is_redacted_from_url_attributes(self):
        exported = self._export(
            {
                "http.url": GRAPH_URL,
                "url.full": GRAPH_URL,
                "http.target": "/v24.0/1234/picture?access_token=" + TOKEN,
                "url.query": f"width=720&access_token={TOKEN}",
            }
        )

        for key, value in exported.items():
            self.assertNotIn(TOKEN, value, key)
        self.assertIn("access_token=REDACTED", exported["http.url"])
        self.assertIn("width=720", exported["http.url"])
        self.assertIn("redirect=false", exported["http.url"])

    def test_other_secret_query_parameters_are_redacted_too(self):
        exported = self._export(
            {
                "http.url": "https://x.example/t?client_secret=S3CR3T&refresh_token=R3F&a=1"
            }
        )

        self.assertNotIn("S3CR3T", exported["http.url"])
        self.assertNotIn("R3F", exported["http.url"])
        self.assertIn("a=1", exported["http.url"])

    def test_spans_without_secrets_are_left_untouched(self):
        url = "https://api.example.com/v1/items?page=2"
        exported = self._export({"http.url": url, "http.method": "GET"})

        self.assertEqual(exported["http.url"], url)
        self.assertEqual(exported["http.method"], "GET")

    def test_non_string_and_missing_attributes_do_not_raise(self):
        exported = self._export({"http.status_code": 200})

        self.assertEqual(exported["http.status_code"], 200)


class SensitiveQueryRedactionOnFailedCallTests(SimpleTestCase):
    """A failed call records an ``exception`` event after the span started; its
    message and stacktrace embed the request URL, query string included."""

    def _failed_span(self):
        import requests
        from opentelemetry.trace import Status, StatusCode

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SensitiveQueryRedactionProcessor())
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        span = provider.get_tracer("test").start_span("GET")
        error = requests.ConnectionError(
            "HTTPSConnectionPool(host='graph.facebook.com', port=443): Max "
            "retries exceeded with url: /v24.0/1234/picture?width=720"
            f"&access_token={TOKEN} (Caused by NewConnectionError('refused'))"
        )
        try:
            raise error
        except requests.ConnectionError as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
        span.end()
        (finished,) = exporter.get_finished_spans()
        return finished

    def test_exception_event_does_not_export_the_token(self):
        finished = self._failed_span()

        (event,) = [e for e in finished.events if e.name == "exception"]
        for key, value in event.attributes.items():
            self.assertNotIn(TOKEN, str(value), key)
        self.assertIn("access_token=REDACTED", event.attributes["exception.message"])
        self.assertIn("width=720", event.attributes["exception.message"])
        self.assertEqual(
            event.attributes["exception.type"], "requests.exceptions.ConnectionError"
        )

    def test_error_status_description_does_not_export_the_token(self):
        finished = self._failed_span()

        self.assertNotIn(TOKEN, finished.status.description)
        self.assertIn("access_token=REDACTED", finished.status.description)
        self.assertEqual(finished.status.status_code.name, "ERROR")

    def test_clean_failures_keep_their_event_and_status_untouched(self):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SensitiveQueryRedactionProcessor())
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        span = provider.get_tracer("test").start_span("GET")
        try:
            raise ValueError("plain failure")
        except ValueError as exc:
            span.record_exception(exc)
        span.add_event("note", {"detail": "nothing secret"})
        span.end()

        (finished,) = exporter.get_finished_spans()
        names = [e.name for e in finished.events]
        self.assertEqual(names, ["exception", "note"])
        self.assertEqual(
            finished.events[0].attributes["exception.message"], "plain failure"
        )
        self.assertEqual(finished.events[1].attributes["detail"], "nothing secret")
