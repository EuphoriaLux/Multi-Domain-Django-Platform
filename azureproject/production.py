import os

# ─── Environment detection ─────────────────────────────────────────────────────
# Set DJANGO_ENV as a slot-sticky App Service setting:
#   Staging slot  → DJANGO_ENV=staging
#   Production    → DJANGO_ENV=production  (or omit; defaults to "production")
DJANGO_ENV = os.environ.get("DJANGO_ENV", "production")

# ============================================================================
# Azure Internal IP Support (MUST be before ALLOWED_HOSTS)
# ============================================================================
# Azure's OpenTelemetry middleware runs BEFORE Django middleware and calls
# request.build_absolute_uri() which triggers ALLOWED_HOSTS validation.
# We must monkey-patch Django's validate_host to allow Azure internal IPs
# (169.254.*) since middleware-based solutions run too late.
from django.http import request as django_request

from .settings import *  # noqa
from .settings import BASE_DIR

_original_validate_host = django_request.validate_host


def _custom_validate_host(host, allowed_hosts):
    """
    Custom host validation for Azure App Service with OpenTelemetry.

    This handles two scenarios:
    1. Azure internal IPs (169.254.*) - health checks and instrumentation
    2. test.* and test-* subdomains - staging slots OR external scanner probes

    Why this monkey-patch is needed:
    - Azure auto-injects OpenTelemetry middleware BEFORE our middleware stack
    - OpenTelemetry calls request.build_absolute_uri() during request processing
    - This triggers Django's get_host() → validate_host() → DisallowedHost exception
    - The exception crashes OpenTelemetry and can cause app restarts

    By returning True for test.*/test-* hosts:
    - OpenTelemetry proceeds without crashing
    - If test.* is in ALLOWED_HOSTS (staging slot): request proceeds normally
    - If test.* is NOT in ALLOWED_HOSTS (scanner probe): Django returns 400 later
    - Either way, no crash and no restart

    Azure Slot Configuration:
    - Production slot: CUSTOM_DOMAINS = "crush.lu,www.crush.lu,..." (no test.*)
    - Staging slot: CUSTOM_DOMAINS = "test.crush.lu,test.power-up.lu,..."
    - Mark CUSTOM_DOMAINS as "slot setting" so it stays with the slot during swap
    """
    # Extract hostname without port
    host_without_port = host.split(":")[0] if host else ""

    # Allow Azure internal IPs (169.254.* range)
    if host_without_port.startswith("169.254."):
        return True

    # Allow test.*/test-* subdomains through validation to prevent OpenTelemetry crashes
    # These will still be rejected with 400 by Django's normal request handling
    # but without causing exceptions that trigger app restarts
    # test. = most staging domains (test.crush.lu), test- = portal (test-portal.powerup.lu)
    if host_without_port.startswith("test.") or host_without_port.startswith("test-"):
        return True

    # Fall back to standard validation
    return _original_validate_host(host, allowed_hosts)


# Apply the monkey-patch
django_request.validate_host = _custom_validate_host

# ============================================================================
# ALLOWED_HOSTS Configuration
# ============================================================================
# IMPORTANT: Only hostnames go here, NOT client IPs.
# Azure internal IPs (169.254.*) are handled by the monkey-patch above.

CUSTOM_DOMAINS = [
    d.strip() for d in os.environ.get("CUSTOM_DOMAINS", "").split(",") if d.strip()
]

ALLOWED_HOSTS = []
if "WEBSITE_HOSTNAME" in os.environ:
    ALLOWED_HOSTS.append(os.environ["WEBSITE_HOSTNAME"])
# Allow all *.azurewebsites.net hostnames (covers staging slots, swaps, etc.)
ALLOWED_HOSTS.append(".azurewebsites.net")
# Add custom domains (crush.lu, entreprinder.lu, vinsdelux.com, etc.)
ALLOWED_HOSTS += CUSTOM_DOMAINS
# Add any additional hosts from environment
ALLOWED_HOSTS += [
    h.strip() for h in os.environ.get("ALLOWED_HOSTS_ENV", "").split(",") if h.strip()
]
# Add localhost for development
ALLOWED_HOSTS += ["localhost", "127.0.0.1"]
# Auto-include all domains and aliases from domains.py (e.g. test-portal.powerup.lu)
# This ensures staging aliases are always allowed without manual env var management
from azureproject.domains import DOMAINS

for _config in DOMAINS.values():
    for _alias in _config.get("aliases", []):
        if _alias not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_alias)


# Configure CSRF_TRUSTED_ORIGINS
CSRF_TRUSTED_ORIGINS = []
if "WEBSITE_HOSTNAME" in os.environ:
    CSRF_TRUSTED_ORIGINS.append("https://" + os.environ["WEBSITE_HOSTNAME"])
CSRF_TRUSTED_ORIGINS += [
    f"https://{domain.strip()}" for domain in CUSTOM_DOMAINS if domain.strip()
]
# Trust all *.azurewebsites.net origins (staging slots, swaps, etc.)
CSRF_TRUSTED_ORIGINS.append("https://*.azurewebsites.net")

# Trust X-Forwarded-Host header for correct host detection behind proxy
USE_X_FORWARDED_HOST = True
# Trust X-Forwarded-Proto header for SSL detection
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

DEBUG = False

# Override authentication protocol for production (always HTTPS)
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"

# ============================================================================
# MIDDLEWARE Configuration (extends settings.py base middleware)
# ============================================================================
# Instead of duplicating the entire list, we extend the base middleware from
# settings.py with production-specific additions for Azure hosting.

MIDDLEWARE = list(MIDDLEWARE)  # Copy the list from settings.py (imported via *)

# Insert Azure-specific middleware after SecurityMiddleware (index 1)
MIDDLEWARE.insert(
    2, "azureproject.redirect_www_middleware.AzureInternalIPMiddleware"
)  # Handle Azure internal IPs
MIDDLEWARE.insert(
    3, "azureproject.redirect_www_middleware.RedirectWWWToRootDomainMiddleware"
)  # WWW redirect
MIDDLEWARE.insert(
    4, "azureproject.redirect_www_middleware.StagingNoIndexMiddleware"
)  # Noindex for test.* staging subdomains

# Add ForceAdminToEnglish after DomainURLRoutingMiddleware
domain_routing_idx = MIDDLEWARE.index(
    "azureproject.middleware.DomainURLRoutingMiddleware"
)
MIDDLEWARE.insert(
    domain_routing_idx + 1, "azureproject.middleware.ForceAdminToEnglishMiddleware"
)

# Add OAuth protection after AuthenticationMiddleware (Android PWA fix)
auth_idx = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")
MIDDLEWARE.insert(
    auth_idx + 1, "azureproject.middleware.OAuthCallbackProtectionMiddleware"
)

# Set default URL configuration; this will be overridden by our middleware as needed.
# Using entreprinder as default to match middleware fallback behavior
ROOT_URLCONF = "azureproject.urls_entreprinder"

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

# STATICFILES_DIRS removed - all static files now in app-level directories
# (e.g., crush_lu/static/crush_lu/, core/static/core/ for shared vendor files)
# Django's AppDirectoriesFinder handles these automatically


# SITE_ID = 1

# Django 4.2+ STORAGES configuration
STORAGES = {
    # Default storage removed - all models now use platform-specific storage backends
    # Platform containers: crush-lu-media, crush-lu-private, vinsdelux-media, etc.
    # Legacy 'media' container deleted as part of platform-specific migration
    "default": {
        "BACKEND": "azureproject.storage_shared.SharedMediaStorage",
        # Fallback to shared-media container for any edge cases
        # In practice, all model fields should have explicit storage= parameters
    },
    # Platform-specific storage backends (production)
    "crush_media": {
        "BACKEND": "crush_lu.storage.CrushMediaStorage",
    },
    "crush_private": {
        "BACKEND": "crush_lu.storage.CrushProfilePhotoStorage",
    },
    "entreprinder_media": {
        "BACKEND": "entreprinder.storage.EntreprinderMediaStorage",
    },
    "powerup_media": {
        "BACKEND": "power_up.storage.PowerUpMediaStorage",
    },
    "powerup_finops": {
        "BACKEND": "power_up.storage.FinOpsStorage",
    },
    "shared_media": {
        "BACKEND": "azureproject.storage_shared.SharedMediaStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# MEDIA_URL is now derived from platform-specific storage backends
# Each platform uses its own container (crush-lu-media, vinsdelux-media, etc.)
# Legacy AZURE_CONTAINER_NAME removed - use storage backend's .url() method instead
AZURE_ACCOUNT_NAME = os.getenv("AZURE_ACCOUNT_NAME")
# MEDIA_URL uses CDN if configured, falls back to direct storage
_CDN_MEDIA = os.getenv("AZURE_CDN_DOMAIN")
if _CDN_MEDIA:
    MEDIA_URL = f"https://{_CDN_MEDIA}/shared-media/"
else:
    MEDIA_URL = f"https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net/shared-media/"

# ============================================================================
# CONTENT IMAGE URLS (Azure Blob Storage - Platform-Specific Containers)
# ============================================================================
# These provide stable URLs for content images in platform-specific containers.
# IMPORTANT: After deleting legacy 'media' container, these files need to be
# re-uploaded to their respective platform containers with the same paths.
#
# Structure: https://{account}.blob.core.windows.net/{container}/{path}
# - crush-lu-media/branding/social-preview.jpg
# - vinsdelux-media/journey/step_01.png
# - powerup-media/defaults/profile.png

# CDN support: if AZURE_CDN_DOMAIN is set, serve media through CDN/Front Door.
# Private containers (crush-lu-private) are also served via CDN/Front Door.
# SAS tokens are tied to the storage account name (not the access hostname),
# so Front Door passes query strings (including SAS tokens) to the blob storage origin.
_CDN_DOMAIN = os.getenv("AZURE_CDN_DOMAIN")  # e.g., "cdn.crush.lu"
if _CDN_DOMAIN:
    _MEDIA_ORIGIN = f"https://{_CDN_DOMAIN}"
else:
    _MEDIA_ORIGIN = f"https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net"

# Crush.lu images (in crush-lu-media container)
CRUSH_MEDIA_BASE_URL = f"{_MEDIA_ORIGIN}/crush-lu-media"
SOCIAL_PREVIEW_IMAGE_URL = os.getenv(
    "SOCIAL_PREVIEW_IMAGE_URL",
    f"{CRUSH_MEDIA_BASE_URL}/branding/social-preview.jpg",
)
CRUSH_SOCIAL_PREVIEW_URL = os.getenv(
    "CRUSH_SOCIAL_PREVIEW_URL",
    f"{CRUSH_MEDIA_BASE_URL}/branding/social-preview.jpg",
)

# PowerUP/Entreprinder images (in powerup-media container)
POWERUP_MEDIA_BASE_URL = f"{_MEDIA_ORIGIN}/powerup-media"
POWERUP_DEFAULT_PROFILE_URL = os.getenv(
    "POWERUP_DEFAULT_PROFILE_URL",
    f"{POWERUP_MEDIA_BASE_URL}/defaults/profile.png",
)

# ============================================================================
# PUSH NOTIFICATIONS (Web Push / PWA)
# ============================================================================
# VAPID keys for Web Push API notifications
# Generate using: python generate_vapid_keys.py
# Add to Azure App Service Environment Variables:
# - VAPID_PUBLIC_KEY (can be exposed to frontend)
# - VAPID_PRIVATE_KEY (keep secret!)
# - VAPID_ADMIN_EMAIL (your contact email)
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_ADMIN_EMAIL = os.getenv("VAPID_ADMIN_EMAIL", "noreply@crush.lu")

# Configure Postgres database from Azure Connection String
# Uses the Connection Strings section in Azure App Service configuration
from django.core.exceptions import ImproperlyConfigured

try:
    # Get Connection String (POSTGRESQLCONNSTR_ prefix from Azure Connection Strings section)
    # Azure automatically prefixes connection strings with type (POSTGRESQLCONNSTR_, SQLCONNSTR_, etc.)
    conn_str_key = next(
        (k for k in os.environ.keys() if k.startswith("POSTGRESQLCONNSTR_")), None
    )

    if not conn_str_key:
        raise KeyError("No PostgreSQL connection string found")

    conn_str = os.environ[conn_str_key]
    conn_str_params = {
        pair.split("=")[0]: pair.split("=")[1] for pair in conn_str.split(" ")
    }
except (KeyError, StopIteration):
    raise ImproperlyConfigured(
        "PostgreSQL connection string is required in production. "
        "Add a connection string in Azure Portal → App Service → Configuration → Connection Strings. "
        "Name: pythonappConnection, Type: PostgreSQL, "
        "Value: 'dbname=xxx host=xxx user=xxx password=xxx'"
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": conn_str_params["dbname"],
        "HOST": conn_str_params["host"],
        "USER": conn_str_params["user"],
        "PASSWORD": conn_str_params["password"],
        "CONN_MAX_AGE": 0,  # Close after each request - prevents pool exhaustion with ASGI workers
        "CONN_HEALTH_CHECKS": True,
    }
}

# ============================================================================
# CACHE CONFIGURATION
# ============================================================================
# Using Redis cache backend (django-redis) for sub-millisecond cache operations.
# Azure App Service runs multiple instances behind a load balancer — Redis is
# shared across all instances, ensuring rate limiting, site detection, and
# session reads are consistent and fast.
#
# AZURE_REDIS_CONNECTIONSTRING is already configured in Azure App Service for
# both production (DB 0) and staging (DB 1) slots. KEY_PREFIX provides namespace
# isolation from Django Channels keys on the same Redis DB.
#
# IGNORE_EXCEPTIONS=True ensures graceful degradation: if Redis is unreachable,
# cache operations return None/fail silently instead of raising 500 errors.
# Rate limiting and site detection already handle cache misses gracefully.
#
# NOTE: The legacy django_cache DB table is no longer used and can be dropped
# in a future migration. `python manage.py createcachetable` is no longer needed.
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ.get(
            "AZURE_REDIS_CONNECTIONSTRING", "redis://localhost:6379/0"
        ),
        "TIMEOUT": 600,  # 10 minutes
        "KEY_PREFIX": "cache",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {"max_connections": 50},
            "IGNORE_EXCEPTIONS": True,
        },
    }
}

# Channel Layers - Redis for production WebSocket support (Django Channels)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [
                os.environ.get(
                    "AZURE_REDIS_CONNECTIONSTRING", "redis://localhost:6379/0"
                )
            ],
        },
    },
}

# cached_db: reads from Redis (fast), writes to both Redis + DB (durable).
# If Redis is down, falls back to DB reads automatically.
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# Override session settings from base settings.py for PWA
SESSION_COOKIE_AGE = 1209600  # 14 days (2 weeks) - longer session for PWA
# OPTIMIZATION: Changed from True to False (90% reduction in database writes)
# Sessions now only save when actually modified, not on every request
# PWA will still work - 14-day timeout is sufficient without extending on every pageview
SESSION_SAVE_EVERY_REQUEST = False  # Only save when session data changes
SESSION_EXPIRE_AT_BROWSER_CLOSE = (
    False  # Keep session alive after browser close (critical for PWA)
)
SESSION_REMEMBER_ME = True

# Override login redirect (matches settings.py)
LOGIN_REDIRECT_URL = "/profile/"
ACCOUNT_SIGNUP_REDIRECT_URL = "/profile/"

# Security settings for production
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# HSTS Settings
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Referrer Policy
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

# Cookie security (only over HTTPS)
SESSION_COOKIE_SECURE = True  # HTTPS only in production
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to session cookie
SESSION_COOKIE_SAMESITE = "Lax"  # CSRF protection while allowing OAuth redirects
CSRF_COOKIE_SAMESITE = "Lax"  # Must match SESSION_COOKIE_SAMESITE for OAuth
# CSRF_COOKIE_HTTPONLY=True prevents JavaScript from reading the CSRF cookie
# This is safe because HTMX reads the token from a hidden input field instead
# See base.html for the HTMX CSRF token setup
CSRF_COOKIE_HTTPONLY = True

# Multi-domain cookie configuration
# ============================================================================
# IMPORTANT: For multi-domain apps serving crush.lu, entreprinder.lu, vinsdelux.com,
# we must NOT set a fixed SESSION_COOKIE_DOMAIN or CSRF_COOKIE_DOMAIN.
#
# Setting a fixed domain (e.g., '.crush.lu') would make cookies ONLY work for
# that domain, breaking auth on other domains like entreprinder.lu.
#
# By leaving these unset, Django uses the default behavior:
# - Cookie domain matches the request host
# - Each domain gets its own session cookies
# - OAuth and login work correctly on each domain
#
# Note: This means sessions are NOT shared across domains, which is correct
# for this app since each domain has separate user pools anyway.

# SSL redirect - Azure App Service handles this at load balancer level
# Setting to False avoids redirect loops since Azure terminates SSL
SECURE_SSL_REDIRECT = False

# Logging configuration - reduce verbosity in production
# ============================================================================
# Azure Application Insights Integration
# ============================================================================
# Using azure-monitor-opentelemetry SDK for exception filtering capability.
# Auto-instrumentation MUST be disabled for SDK to work correctly.
#
# SDK provides:
# - Automatic request/dependency tracking (Django, requests, urllib, psycopg2)
# - Exception logging with filtering (cache race conditions suppressed)
# - Logs sent to Application Insights 'traces' table
#
# Azure App Service config required:
#   ApplicationInsightsAgent_EXTENSION_VERSION=disabled
#   APPLICATIONINSIGHTS_CONNECTION_STRING=<your-connection-string>
#
# Query logs in App Insights: traces | where timestamp > ago(1h)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "production": {
            "format": "{levelname} [{name}] [{environment}] {message}",
            "style": "{",
            "defaults": {"environment": DJANGO_ENV},
        },
    },
    "filters": {
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
        "pii_masking": {
            "()": "azureproject.logging_utils.PIIMaskingFilter",
        },
        "suppress_cache_errors": {
            "()": "azureproject.telemetry_config.SuppressedExceptionFilter",
        },
    },
    "handlers": {
        # Console handler - ERROR only to avoid Gunicorn worker duplicates
        # App Insights captures everything via OpenTelemetry autoinstrumentation
        "console": {
            "level": "ERROR",
            "class": "logging.StreamHandler",
            "formatter": "production",
            "filters": ["pii_masking"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",  # Allow INFO+ to propagate to App Insights
    },
    "loggers": {
        # =================================================================
        # Django Core Loggers - Minimal console output
        # =================================================================
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            # Propagate to root so App Insights captures Django warnings/errors
            "propagate": True,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            # Propagate to root so 4xx/5xx and CSRF failures reach App Insights
            "propagate": True,
        },
        "django.db.backends": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
            "filters": [
                "suppress_cache_errors"
            ],  # Suppress cache race condition errors
        },
        # =================================================================
        # Azure SDK Loggers - Silence noisy logging
        # =================================================================
        "azure": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "azure.core.pipeline.policies.http_logging_policy": {
            "handlers": [],
            "level": "ERROR",
            "propagate": False,
        },
        "azure.monitor.opentelemetry.exporter._quickpulse": {
            "handlers": [],
            "level": "CRITICAL",
            "propagate": False,
        },
        "Microsoft": {
            "handlers": [],
            "level": "ERROR",
            "propagate": False,
        },
        "MiddlewareConsoleLogs": {
            "handlers": [],
            "level": "ERROR",
            "propagate": False,
        },
        # =================================================================
        # Application Loggers - Propagate to App Insights
        # Console shows ERROR only, App Insights captures INFO+
        # =================================================================
        # OAuth state management (database-backed)
        # DEBUG: state storage/retrieval details
        # INFO: cross-browser fallback used
        # WARNING: state not found
        # ERROR: database failures
        "crush_lu.oauth_statekit": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": True,  # Propagate to App Insights
        },
        # ASGI transport errors (e.g. CurrentThreadExecutor broken)
        # These occur before Django's request pipeline starts
        "azureproject.asgi": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": True,  # Propagate to root → App Insights
        },
        # Middleware logging (CSRF failures, routing)
        "azureproject.middleware": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": True,
        },
        # Crush.lu application logging
        "crush_lu": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": True,
        },
        # CSP violation reports
        # Query in App Insights: traces | where message contains "CSP"
        "csp_reports": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": True,
        },
    },
}

# Suppress Azure's verbose logging at runtime
import logging

if "WEBSITE_HOSTNAME" in os.environ:
    logging.getLogger("azure").setLevel(logging.ERROR)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(
        logging.ERROR
    )

# =============================================================================
# AZURE MONITOR OPENTELEMETRY SDK
# =============================================================================
# Using SDK-based instrumentation instead of auto-instrumentation for:
# 1. Full control over span processing (exception filtering)
# 2. Better integration with Django logging
# 3. Suppresses cache race condition exceptions from telemetry
#
# IMPORTANT: Disable auto-instrumentation in Azure App Service:
#   ApplicationInsightsAgent_EXTENSION_VERSION=disabled
#
# The SDK automatically instruments: Django, requests, urllib, psycopg2
from azureproject.telemetry_config import configure_azure_monitor_telemetry

telemetry_ok = configure_azure_monitor_telemetry(environment=DJANGO_ENV)
if not telemetry_ok:
    import sys

    print(
        "WARNING: Azure Monitor telemetry not configured. "
        "Check APPLICATIONINSIGHTS_CONNECTION_STRING is set.",
        file=sys.stderr,
    )

# =============================================================================
# CONTENT SECURITY POLICY (CSP) - Production Notes
# =============================================================================
# CSP is configured via SECURE_CSP_REPORT_ONLY in settings.py (Django 6.0 native).
# Currently in report-only mode. Monitor /csp-report/ endpoint and csp_reports logger,
# then switch to CONTENT_SECURITY_POLICY (enforcing) when ready.

# =============================================================================
# SECURITY HEADERS (SEC-04)
# =============================================================================
# Production is served exclusively over HTTPS via Azure Front Door, which
# terminates TLS and forwards X-Forwarded-Proto so Django's SSL redirect logic
# works correctly. HealthCheckMiddleware is first in MIDDLEWARE and short-
# circuits /healthz/ before SecurityMiddleware runs, so Azure health probes
# remain unaffected.
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# HSTS ramp: start at 1 hour so any cert / mixed-content / subdomain issue
# clears from browser caches quickly. Step up only after a week of clean
# observation across all 7 apex domains:
#   1h -> 1d -> 1w -> 30d -> 1y (+ includeSubDomains -> + preload + submit
#   to hstspreload.org). includeSubDomains is off until every subdomain
#   (staging.*, test.*, api.*, admin.*, mail.*, cdn.*) is confirmed HTTPS.
SECURE_HSTS_SECONDS = 3600  # 1 hour
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

# `manage.py check --deploy` warns on anything weaker than the preload-ready
# end state. The ramp above is intentional, so silence just those two checks
# and remove each entry as the corresponding setting reaches target:
#   W005 -> remove when SECURE_HSTS_INCLUDE_SUBDOMAINS flips to True
#   W021 -> remove when SECURE_HSTS_PRELOAD flips to True
# Keeping this list tight means any NEW security warning (e.g. someone
# dropping SESSION_COOKIE_SECURE) will still fail CI.
SILENCED_SYSTEM_CHECKS = [
    "security.W005",  # SECURE_HSTS_INCLUDE_SUBDOMAINS not True during ramp
    "security.W021",  # SECURE_HSTS_PRELOAD not True during ramp
]

# =============================================================================
# DJANGO 6.0 BACKGROUND TASKS
# =============================================================================
# Inherits ImmediateBackend from settings.py (tasks run synchronously).
# This is intentional — no separate db_worker process is running on Azure.
# Switch to DatabaseBackend when async execution is needed (requires adding
# `python manage.py db_worker &` to startup.sh or a separate Azure WebJob).
