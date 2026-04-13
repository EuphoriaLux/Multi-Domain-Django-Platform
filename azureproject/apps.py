"""
AppConfig for the azureproject app.

The primary purpose of this AppConfig is to re-attach the OpenTelemetry
LoggingHandler to the Python root logger AFTER Django has applied its
`LOGGING` dictConfig. Without this, trace/log records from Python's logging
module fail to reach Azure Application Insights (observed in production:
`traces` and `exceptions` tables empty for 7+ days while `requests` kept
flowing — the configure-time handler was being dropped by dictConfig).
"""
from django.apps import AppConfig


class AzureprojectConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "azureproject"

    def ready(self):
        # Import lazily so this module stays import-cheap during settings load
        import logging
        import os

        from azureproject.telemetry_config import attach_otel_logging_handler_to_root

        # Safe no-op if telemetry was never configured (e.g. local dev without
        # APPLICATIONINSIGHTS_CONNECTION_STRING). Idempotent on repeat calls.
        attach_otel_logging_handler_to_root()

        # Startup canary — if this message shows up in App Insights `traces`
        # within ~2 minutes of a deploy, the logging pipeline is healthy.
        # Query: traces | where message startswith "azureproject booted"
        logging.getLogger("azureproject").info(
            "azureproject booted env=%s settings=%s",
            os.environ.get("DJANGO_ENV", "unknown"),
            os.environ.get("DJANGO_SETTINGS_MODULE", "unknown"),
        )
