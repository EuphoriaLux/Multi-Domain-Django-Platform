"""
OpenTelemetry configuration for Azure Monitor with exception filtering.

This module configures the azure-monitor-opentelemetry SDK to:
1. Send telemetry to Azure Application Insights
2. Filter out benign exceptions (cache race conditions) before export
3. Preserve logging filter functionality

IMPORTANT: This replaces Azure App Service auto-instrumentation.
Set ApplicationInsightsAgent_EXTENSION_VERSION=disabled in Azure.
"""
import logging
import os

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


def _span_has_only_suppressed_exceptions(span) -> bool:
    """
    Check if a span contains ONLY suppressed exceptions.

    Returns True only if:
    - The span has exception events, AND
    - ALL exception events should be suppressed

    This ensures we don't drop spans with real errors mixed with cache errors.
    """
    if not hasattr(span, 'events') or not span.events:
        return False

    exception_events = [e for e in span.events if e.name == 'exception']
    if not exception_events:
        return False

    # Check if ALL exceptions should be suppressed
    for event in exception_events:
        attrs = event.attributes or {}
        exc_type = str(attrs.get('exception.type', ''))
        exc_msg = str(attrs.get('exception.message', ''))

        if not should_suppress_exception(exc_type, exc_msg):
            return False  # Found a real exception - don't suppress this span

    # All exceptions in this span should be suppressed
    logger.debug(f"Suppressing span with {len(exception_events)} cache exceptions")
    return True


class ExceptionFilteringProcessor:
    """
    Span processor that filters out spans containing only suppressed exceptions.

    This processor marks spans for non-export by setting TraceFlags.DEFAULT
    when the span contains only benign exceptions (like cache race conditions).
    """

    def __init__(self):
        from opentelemetry.trace import SpanContext, TraceFlags
        self._SpanContext = SpanContext
        self._TraceFlags = TraceFlags

    def on_start(self, span, parent_context=None):
        """Called when a span is started. We don't filter here."""
        pass

    def on_end(self, span):
        """
        Called when a span is ended.

        If the span contains only suppressed exceptions, we modify its context
        to prevent export by setting TraceFlags.DEFAULT (not sampled).
        """
        if _span_has_only_suppressed_exceptions(span):
            # Mark span as not sampled to prevent export
            span._context = self._SpanContext(
                span.context.trace_id,
                span.context.span_id,
                span.context.is_remote,
                self._TraceFlags(self._TraceFlags.DEFAULT),
                span.context.trace_state,
            )
            logger.debug(f"Filtered span {span.name} containing only suppressed exceptions")

    def shutdown(self):
        """Called when the SDK is shut down."""
        pass

    def force_flush(self, timeout_millis=30000):
        """Force flush any pending spans."""
        return True


def configure_azure_monitor_telemetry():
    """
    Configure Azure Monitor OpenTelemetry SDK with exception filtering.

    This function:
    1. Checks for Application Insights connection string
    2. Configures azure-monitor-opentelemetry with custom span processor
    3. Sets up Django instrumentation automatically
    4. Falls back gracefully if connection string is missing (local dev)

    Call this once at application startup (in production.py).

    Environment Variables:
        APPLICATIONINSIGHTS_CONNECTION_STRING: Required for telemetry
        ENABLE_LIVE_METRICS: Set to 'false' to disable Live Metrics (default: true)
    """
    connection_string = os.environ.get('APPLICATIONINSIGHTS_CONNECTION_STRING')

    if not connection_string:
        logger.info(
            "APPLICATIONINSIGHTS_CONNECTION_STRING not set. "
            "Telemetry will not be sent to Azure Monitor."
        )
        return False

    # Allow disabling Live Metrics to avoid timeout issues during deployment
    enable_live_metrics = os.environ.get('ENABLE_LIVE_METRICS', 'true').lower() != 'false'

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        # Create the exception filtering processor
        exception_filter = ExceptionFilteringProcessor()

        # Configure Azure Monitor with our custom processor
        # The SDK automatically instruments Django, requests, urllib, psycopg2
        configure_azure_monitor(
            connection_string=connection_string,
            # Add our custom span processor for exception filtering
            span_processors=[exception_filter],
            # Configure logging integration - use root logger namespace
            logger_name="",  # Empty string = root logger
            # Enable Live Metrics for real-time dashboard monitoring (1-second latency)
            # Can be disabled by setting ENABLE_LIVE_METRICS=false
            enable_live_metrics=enable_live_metrics,
        )

        logger.info(
            f"Azure Monitor OpenTelemetry configured with exception filtering "
            f"(Live Metrics: {'enabled' if enable_live_metrics else 'disabled'}). "
            "Auto-instrumentation should be DISABLED in Azure App Service."
        )
        return True

    except ImportError as e:
        logger.warning(
            f"azure-monitor-opentelemetry package not installed: {e}. "
            "Telemetry will not be sent to Azure Monitor."
        )
        return False
    except Exception as e:
        # Don't let telemetry configuration errors break the app
        logger.error(f"Failed to configure Azure Monitor telemetry: {e}")
        return False


# Legacy function for backward compatibility
def configure_exception_filtering():
    """
    Legacy function - now calls configure_azure_monitor_telemetry().

    Kept for backward compatibility with existing imports.
    """
    return configure_azure_monitor_telemetry()


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
