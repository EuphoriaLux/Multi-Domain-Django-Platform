import os
import sys

# Diagnostic output to help identify startup hangs
print("🔧 [STARTUP] production.py: Starting imports...", file=sys.stderr, flush=True)

from .settings import *  # noqa
from .settings import BASE_DIR

print("🔧 [STARTUP] production.py: Base settings loaded", file=sys.stderr, flush=True)

# ============================================================================
# Azure Internal IP Support (MUST be before ALLOWED_HOSTS)
# ============================================================================
# Azure's OpenTelemetry middleware runs BEFORE Django middleware and calls
# request.build_absolute_uri() which triggers ALLOWED_HOSTS validation.
# We must monkey-patch Django's validate_host to allow Azure internal IPs
# (169.254.*) since middleware-based solutions run too late.

from django.http import request as django_request
from azureproject.domains import DOMAINS

_original_validate_host = django_request.validate_host


def _custom_validate_host(host, allowed_hosts):
    """
    Custom host validation that allows:
    - Azure internal IPs (169.254.*)
    - WWW variants of known domains (for redirect middleware)

    Azure App Service uses 169.254.* IPs for internal health checks and
    OpenTelemetry instrumentation. These requests have Host headers like
    '169.254.129.4:8000' which fail standard ALLOWED_HOSTS validation.

    WWW variants (e.g., www.vinsdelux.com) are allowed through so that
    RedirectWWWToRootDomainMiddleware can redirect them to the root domain.
    Without this, Django rejects them before middleware can handle them.
    """
    # Extract hostname without port
    host_without_port = host.split(':')[0].lower() if host else ''

    # Allow Azure internal IPs (169.254.* range)
    if host_without_port.startswith('169.254.'):
        return True

    # Allow www variants of known domains
    # The RedirectWWWToRootDomainMiddleware will redirect them to root domain
    if host_without_port.startswith('www.'):
        root_domain = host_without_port[4:]  # Remove 'www.'
        if root_domain in DOMAINS:
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

CUSTOM_DOMAINS = [d.strip() for d in os.environ.get('CUSTOM_DOMAINS', '').split(',') if d.strip()]

ALLOWED_HOSTS = []
if 'WEBSITE_HOSTNAME' in os.environ:
    ALLOWED_HOSTS.append(os.environ['WEBSITE_HOSTNAME'])
# Add custom domains (crush.lu, entreprinder.lu, vinsdelux.com, etc.)
ALLOWED_HOSTS += CUSTOM_DOMAINS
# Add any additional hosts from environment
ALLOWED_HOSTS += [h.strip() for h in os.environ.get('ALLOWED_HOSTS_ENV', '').split(',') if h.strip()]
# Add localhost for development
ALLOWED_HOSTS += ['localhost', '127.0.0.1']



# Configure CSRF_TRUSTED_ORIGINS
CSRF_TRUSTED_ORIGINS = []
if 'WEBSITE_HOSTNAME' in os.environ:
    CSRF_TRUSTED_ORIGINS.append('https://' + os.environ['WEBSITE_HOSTNAME'])
CSRF_TRUSTED_ORIGINS += [f'https://{domain.strip()}' for domain in CUSTOM_DOMAINS if domain.strip()]

# Trust X-Forwarded-Host header for correct host detection behind proxy
USE_X_FORWARDED_HOST = True
# Trust X-Forwarded-Proto header for SSL detection
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

DEBUG = False

# =============================================================================
# STAGING MODE DETECTION
# =============================================================================
# STAGING_MODE is set only in the staging deployment slot (test.* domains).
# Used to modify behavior that should differ between staging and production:
# - Email subject prefixes to identify test emails
# - Analytics tracking (GA4 env vars are not set in staging)
# - Any other staging-specific behavior
#
# Note: Staging has isolated database (pythonapp_staging) and storage (media-staging)
# configured via slot-sticky settings, so data isolation is automatic.
STAGING_MODE = os.environ.get('STAGING_MODE', 'false').lower() == 'true'

# Override authentication protocol for production (always HTTPS)
ACCOUNT_DEFAULT_HTTP_PROTOCOL = 'https'

# ============================================================================
# MIDDLEWARE Configuration (extends settings.py base middleware)
# ============================================================================
# Instead of duplicating the entire list, we extend the base middleware from
# settings.py with production-specific additions for Azure hosting.

MIDDLEWARE = list(MIDDLEWARE)  # Copy the list from settings.py (imported via *)

# Insert Azure-specific middleware after SecurityMiddleware (index 1)
MIDDLEWARE.insert(2, 'azureproject.redirect_www_middleware.AzureInternalIPMiddleware')  # Handle Azure internal IPs
MIDDLEWARE.insert(3, 'azureproject.redirect_www_middleware.RedirectWWWToRootDomainMiddleware')  # WWW redirect
MIDDLEWARE.insert(4, 'azureproject.redirect_www_middleware.StagingNoIndexMiddleware')  # Noindex for test.* staging subdomains

# Add ForceAdminToEnglish after DomainURLRoutingMiddleware
domain_routing_idx = MIDDLEWARE.index('azureproject.middleware.DomainURLRoutingMiddleware')
MIDDLEWARE.insert(domain_routing_idx + 1, 'azureproject.middleware.ForceAdminToEnglishMiddleware')

# Add OAuth protection after AuthenticationMiddleware (Android PWA fix)
auth_idx = MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware')
MIDDLEWARE.insert(auth_idx + 1, 'azureproject.middleware.OAuthCallbackProtectionMiddleware')

# Set default URL configuration; this will be overridden by our middleware as needed.
# Using entreprinder as default to match middleware fallback behavior
ROOT_URLCONF = 'azureproject.urls_entreprinder'

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
# STATICFILES_STORAGE is now configured in STORAGES above (Django 4.2+)

STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static"),
]


#SITE_ID = 1

# Django 4.2+ STORAGES configuration
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.azure_storage.AzureStorage",
        "OPTIONS": {
            "account_name": os.getenv('AZURE_ACCOUNT_NAME'),
            "account_key": os.getenv('AZURE_ACCOUNT_KEY'),
            "azure_container": os.getenv('AZURE_CONTAINER_NAME'),
            "overwrite_files": True,
            # "azure_ssl": True, # Default is True
            # "upload_max_conn": 2, # Default is 2
            # "timeout": 20, # Default is 20
            # "max_memory_size": 2*1024*1024, # Default is 2MB
            # "expiration_secs": None, # Default is None
            # "location": "", # Default is ''
            # "endpoint_suffix": "core.windows.net", # Default is core.windows.net
            # "custom_domain": None, # Default is None
            # "token_credential": DefaultAzureCredential(), # For Managed Identity
        },
    },
    # Private storage for Crush.lu profile photos (SAS token access)
    "crush_private": {
        "BACKEND": "crush_lu.storage.CrushProfilePhotoStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# MEDIA_URL is now derived from STORAGES["default"]
# You can access it via default_storage.url() or by constructing it manually
# based on the STORAGES settings.
# For direct URL construction, you'd still need account name and container name.
AZURE_ACCOUNT_NAME = os.getenv('AZURE_ACCOUNT_NAME')
AZURE_CONTAINER_NAME = os.getenv('AZURE_CONTAINER_NAME')
MEDIA_URL = f'https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net/{AZURE_CONTAINER_NAME}/'

# ============================================================================
# CONTENT IMAGE URLS (Azure Blob Storage - Domain-Organized Structure)
# ============================================================================
# These provide stable URLs for content images that don't change between deployments.
# Structure: /{domain}/{category}/{filename}
# - crush-lu/branding/social-preview.jpg
# - vinsdelux/journey/step_01.png
# - powerup/defaults/profile.png
AZURE_CONTENT_BASE_URL = f'https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net/{AZURE_CONTAINER_NAME}'

# Crush.lu images
SOCIAL_PREVIEW_IMAGE_URL = os.getenv(
    'SOCIAL_PREVIEW_IMAGE_URL',
    f'{AZURE_CONTENT_BASE_URL}/crush-lu/branding/social-preview.jpg'
)
CRUSH_SOCIAL_PREVIEW_URL = os.getenv(
    'CRUSH_SOCIAL_PREVIEW_URL',
    f'{AZURE_CONTENT_BASE_URL}/crush-lu/branding/social-preview.jpg'
)

# VinsDelux images
VINSDELUX_JOURNEY_BASE_URL = os.getenv(
    'VINSDELUX_JOURNEY_BASE_URL',
    f'{AZURE_CONTENT_BASE_URL}/vinsdelux/journey/'
)
VINSDELUX_VINEYARD_DEFAULTS_URL = os.getenv(
    'VINSDELUX_VINEYARD_DEFAULTS_URL',
    f'{AZURE_CONTENT_BASE_URL}/vinsdelux/vineyard-defaults/'
)

# PowerUP/Entreprinder images
POWERUP_DEFAULT_PROFILE_URL = os.getenv(
    'POWERUP_DEFAULT_PROFILE_URL',
    f'{AZURE_CONTENT_BASE_URL}/powerup/defaults/profile.png'
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
VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '')
VAPID_ADMIN_EMAIL = os.getenv('VAPID_ADMIN_EMAIL', 'noreply@crush.lu')

# Configure Postgres database based on connection string of the libpq Keyword/Value form
from django.core.exceptions import ImproperlyConfigured

try:
    conn_str = os.environ['AZURE_POSTGRESQL_CONNECTIONSTRING']
    conn_str_params = {pair.split('=')[0]: pair.split('=')[1] for pair in conn_str.split(' ')}
except KeyError:
    raise ImproperlyConfigured(
        "AZURE_POSTGRESQL_CONNECTIONSTRING environment variable is required in production. "
        "Format: 'dbname=xxx host=xxx user=xxx password=xxx'"
    )

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': conn_str_params['dbname'],
        'HOST': conn_str_params['host'],
        'USER': conn_str_params['user'],
        'PASSWORD': conn_str_params['password'],
        'PORT': conn_str_params.get('port', '5432'),
        'OPTIONS': {
            'sslmode': conn_str_params.get('sslmode', 'require'),
        },
    }
}

# ============================================================================
# CACHE CONFIGURATION
# ============================================================================
# Using database-backed cache for rate limiting consistency across instances.
# Azure App Service can run multiple instances behind load balancer - LocMemCache
# would fail to enforce rate limits across instances. Database cache is slower
# but works correctly and doesn't require Redis (which adds Azure cost).
#
# IMPORTANT: Run `python manage.py createcachetable` in deployment to create
# the cache table. This is handled in the Azure startup script.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'django_cache',  # Table name for cache entries
        'TIMEOUT': 300,  # Default timeout: 5 minutes
        'OPTIONS': {
            'MAX_ENTRIES': 1000,  # Limit cache size
            'CULL_FREQUENCY': 3,  # Remove 1/3 of entries when max reached
        }
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.db"  # Changed from signed_cookies to db for PWA persistence

# Override session settings from base settings.py for PWA
SESSION_COOKIE_AGE = 1209600  # 14 days (2 weeks) - longer session for PWA
SESSION_SAVE_EVERY_REQUEST = True  # Extend session on each request
SESSION_EXPIRE_AT_BROWSER_CLOSE = False  # Keep session alive after browser close (critical for PWA)
SESSION_REMEMBER_ME = True

# Override login redirect (matches settings.py)
LOGIN_REDIRECT_URL = '/profile/'
ACCOUNT_SIGNUP_REDIRECT_URL = '/profile/'

# Security settings for production
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# HSTS Settings
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Referrer Policy
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# Cookie security (only over HTTPS)
SESSION_COOKIE_SECURE = True  # HTTPS only in production
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to session cookie
SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection while allowing OAuth redirects
CSRF_COOKIE_SAMESITE = 'Lax'  # Must match SESSION_COOKIE_SAMESITE for OAuth
# CSRF_COOKIE_HTTPONLY must be False to allow JavaScript access for AJAX requests
# This is Django's default and is safe because CSRF tokens are not sensitive data
CSRF_COOKIE_HTTPONLY = False

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
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'production': {
            'format': '{levelname} [{name}] {message}',
            'style': '{',
        },
    },
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
        'pii_masking': {
            '()': 'azureproject.logging_utils.PIIMaskingFilter',
        },
        'suppress_cache_errors': {
            '()': 'azureproject.telemetry_config.SuppressedExceptionFilter',
        },
    },
    'handlers': {
        # Console handler - ERROR only to avoid Gunicorn worker duplicates
        # App Insights captures everything via OpenTelemetry autoinstrumentation
        'console': {
            'level': 'ERROR',
            'class': 'logging.StreamHandler',
            'formatter': 'production',
            'filters': ['pii_masking'],
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',  # Allow INFO+ to propagate to App Insights
    },
    'loggers': {
        # =================================================================
        # Django Core Loggers - Minimal console output
        # =================================================================
        'django': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
            'filters': ['suppress_cache_errors'],  # Suppress cache race condition errors
        },

        # =================================================================
        # Azure SDK Loggers - Silence noisy logging
        # =================================================================
        'azure': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'azure.core.pipeline.policies.http_logging_policy': {
            'handlers': [],
            'level': 'ERROR',
            'propagate': False,
        },
        'Microsoft': {
            'handlers': [],
            'level': 'ERROR',
            'propagate': False,
        },
        'MiddlewareConsoleLogs': {
            'handlers': [],
            'level': 'ERROR',
            'propagate': False,
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
        'crush_lu.oauth_statekit': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,  # Propagate to App Insights
        },

        # Middleware logging (CSRF failures, routing)
        'azureproject.middleware': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': True,
        },

        # Crush.lu application logging
        'crush_lu': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },

        # CSP violation reports
        # Query in App Insights: traces | where message contains "CSP"
        'csp_reports': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': True,
        },
    },
}

# Suppress Azure's verbose logging at runtime
import logging
if 'WEBSITE_HOSTNAME' in os.environ:
    logging.getLogger('azure').setLevel(logging.ERROR)
    logging.getLogger('azure.core.pipeline.policies.http_logging_policy').setLevel(logging.ERROR)

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
print("🔧 [STARTUP] production.py: About to configure telemetry...", file=sys.stderr, flush=True)
from azureproject.telemetry_config import configure_azure_monitor_telemetry
configure_azure_monitor_telemetry()
print("🔧 [STARTUP] production.py: Telemetry configured ✅", file=sys.stderr, flush=True)

# =============================================================================
# CONTENT SECURITY POLICY (CSP) SETTINGS - Production
# =============================================================================
# Start with Report-Only mode, switch to enforcement after testing.
# Monitor CSP reports at /csp-report/ endpoint and in logs (csp_reports logger).
CSP_REPORT_ONLY = True  # Set to False after testing to enforce CSP
CSP_REPORT_URI = '/csp-report/'

# =============================================================================
# STAGING MODE SPECIFIC SETTINGS
# =============================================================================
# These settings only apply when running in the staging slot (test.* domains)
if STAGING_MODE:
    # Prefix email subjects so test emails are clearly identifiable
    EMAIL_SUBJECT_PREFIX = '[STAGING] '
    # Add a note to logs that we're in staging mode
    import logging
    logging.getLogger('azureproject').info('Running in STAGING_MODE - isolated database and storage')
    print("🔧 [STARTUP] production.py: STAGING_MODE is active", file=sys.stderr, flush=True)

print("🔧 [STARTUP] production.py: Settings module fully loaded ✅", file=sys.stderr, flush=True)


