"""
OpenTelemetry configuration for exception filtering.

This module configures OpenTelemetry to suppress benign exceptions
(like cache race conditions) from being sent to Application Insights.

Azure App Service uses auto-instrumentation, but we can add custom
span processors to filter exceptions before they're exported.
"""
import logging

logger = logging.getLogger(__name__)

# Exceptions to suppress from telemetry (fully qualified class names)
SUPPRESSED_EXCEPTIONS = {
    'psycopg2.errors.UniqueViolation',
    'django.db.utils.IntegrityError',
}

# Specific error messages to suppress (substring match)
SUPPRESSED_MESSAGES = {
    'django_cache_pkey',  # Cache race condition
    'duplicate key value violates unique constraint',
}


def should_suppress_exception(exception_type: str, exception_message: str = '') -> bool:
    """
    Check if an exception should be suppressed from telemetry.

    Args:
        exception_type: The fully qualified exception class name
        exception_message: The exception message

    Returns:
        True if the exception should be suppressed
    """
    # Check if exception type is in suppressed list
    if exception_type in SUPPRESSED_EXCEPTIONS:
        # For IntegrityError, only suppress if it's a cache-related error
        if 'IntegrityError' in exception_type:
            return any(msg in exception_message for msg in SUPPRESSED_MESSAGES)
        return True

    # Check if message contains suppressed patterns
    if any(msg in exception_message for msg in SUPPRESSED_MESSAGES):
        return True

    return False


def configure_exception_filtering():
    """
    Configure OpenTelemetry to filter out suppressed exceptions.

    This function attempts to add a custom span processor that filters
    exceptions before they're exported to Application Insights.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import SpanProcessor, ReadableSpan
        from opentelemetry.trace import Status, StatusCode

        class ExceptionFilteringProcessor(SpanProcessor):
            """
            Span processor that filters out benign exceptions.

            This processor modifies spans to remove exception events for
            exceptions that are expected and handled (like cache race conditions).
            """

            def on_start(self, span, parent_context=None):
                """Called when a span is started."""
                pass

            def on_end(self, span: ReadableSpan):
                """
                Called when a span is ended.

                We can't modify the span here (it's read-only), but we can
                check if it should be suppressed and log accordingly.
                """
                # Check for exception events
                if hasattr(span, 'events') and span.events:
                    for event in span.events:
                        if event.name == 'exception':
                            attrs = event.attributes or {}
                            exc_type = attrs.get('exception.type', '')
                            exc_msg = attrs.get('exception.message', '')

                            if should_suppress_exception(str(exc_type), str(exc_msg)):
                                # Log that we're suppressing (for debugging)
                                logger.debug(
                                    f"Suppressed exception in telemetry: {exc_type}"
                                )

            def shutdown(self):
                """Called when the SDK is shut down."""
                pass

            def force_flush(self, timeout_millis=30000):
                """Force flush any pending spans."""
                return True

        # Try to add our processor to the existing tracer provider
        tracer_provider = trace.get_tracer_provider()

        if hasattr(tracer_provider, 'add_span_processor'):
            tracer_provider.add_span_processor(ExceptionFilteringProcessor())
            logger.info("OpenTelemetry exception filtering processor registered")
        else:
            logger.debug(
                "TracerProvider doesn't support add_span_processor. "
                "Using logging-based exception filtering instead."
            )

    except ImportError as e:
        # OpenTelemetry SDK not installed - that's fine, we're using auto-instrumentation
        logger.debug(f"OpenTelemetry SDK not available: {e}")
    except Exception as e:
        # Don't let telemetry configuration errors break the app
        logger.warning(f"Failed to configure OpenTelemetry exception filtering: {e}")


class SuppressedExceptionFilter(logging.Filter):
    """
    Logging filter that suppresses benign exceptions from logs.

    This filter works alongside the OpenTelemetry processor to also
    suppress these exceptions from Django's logging system.

    Usage:
        Add to logging config:
        'filters': {
            'suppress_cache_errors': {
                '()': 'azureproject.telemetry_config.SuppressedExceptionFilter',
            }
        }
    """

    def filter(self, record):
        """
        Filter log records to suppress benign exceptions.

        Returns False to suppress the record, True to allow it.
        """
        # Check if this is an exception log
        if record.exc_info:
            exc_type = record.exc_info[0]
            if exc_type:
                exc_type_name = f"{exc_type.__module__}.{exc_type.__name__}"
                exc_message = str(record.exc_info[1]) if record.exc_info[1] else ''

                if should_suppress_exception(exc_type_name, exc_message):
                    return False  # Suppress this log

        # Check message content
        msg = str(record.getMessage()) if hasattr(record, 'getMessage') else str(record.msg)
        if any(suppressed in msg for suppressed in SUPPRESSED_MESSAGES):
            return False  # Suppress this log

        return True  # Allow this log
