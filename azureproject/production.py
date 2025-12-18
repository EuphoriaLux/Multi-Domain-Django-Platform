import os
from .settings import *  # noqa
from .settings import BASE_DIR

# ============================================================================
# ALLOWED_HOSTS Configuration
# ============================================================================
# IMPORTANT: Only hostnames go here, NOT client IPs.
# Azure internal IPs (169.254.*) are handled by AzureInternalIPMiddleware.
# Health check IPs are CLIENT IPs, not Host headers - they don't belong here.

CUSTOM_DOMAINS = [d.strip() for d in os.environ.get('CUSTOM_DOMAINS', '').split(',') if d.strip()]

ALLOWED_HOSTS = []
if 'WEBSITE_HOSTNAME' in os.environ:
    ALLOWED_HOSTS.append(os.environ['WEBSITE_HOSTNAME'])
# Add custom domains (crush.lu, powerup.lu, vinsdelux.com, etc.)
ALLOWED_HOSTS += CUSTOM_DOMAINS
# Add any additional hosts from environment
ALLOWED_HOSTS += [h.strip() for h in os.environ.get('ALLOWED_HOSTS_ENV', '').split(',') if h.strip()]
# Add localhost for development
ALLOWED_HOSTS += ['localhost', '127.0.0.1']

# NOTE: Azure internal IPs (169.254.*) for OpenTelemetry health checks are
# handled by AzureInternalIPMiddleware, NOT by monkey-patching validate_host.



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

# Override authentication protocol for production (always HTTPS)
ACCOUNT_DEFAULT_HTTP_PROTOCOL = 'https'

# WhiteNoise configuration and Middleware list
MIDDLEWARE = [
    'azureproject.middleware.HealthCheckMiddleware',  # MUST be first - bypasses all other middleware for /healthz/
    'django.middleware.security.SecurityMiddleware',
    'azureproject.redirect_www_middleware.AzureInternalIPMiddleware',  # Handle Azure internal IPs first
    'azureproject.redirect_www_middleware.RedirectWWWToRootDomainMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',  # MUST be before SafeCurrentSiteMiddleware
    'azureproject.middleware.SafeCurrentSiteMiddleware',  # Safe site detection (auto-creates missing Sites)
    'azureproject.middleware.DomainURLRoutingMiddleware',  # Multi-domain routing
    'azureproject.middleware.ForceAdminToEnglishMiddleware',  # Force admin to English
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'azureproject.middleware.OAuthCallbackProtectionMiddleware',  # Prevent duplicate OAuth callbacks (Android PWA fix)
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

# Set default URL configuration; this will be overridden by our middleware as needed.
# Using powerup as default to match middleware fallback behavior
ROOT_URLCONF = 'azureproject.urls_powerup'

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
            "overwrite_files": True, # Explicitly set to True for testing
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
conn_str = os.environ['AZURE_POSTGRESQL_CONNECTIONSTRING']
conn_str_params = {pair.split('=')[0]: pair.split('=')[1] for pair in conn_str.split(' ')}
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': conn_str_params['dbname'],
        'HOST': conn_str_params['host'],
        'USER': conn_str_params['user'],
        'PASSWORD': conn_str_params['password'],
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
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

# Cookie security (only over HTTPS)
SESSION_COOKIE_SECURE = True  # HTTPS only in production
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to session cookie
SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection while allowing OAuth redirects
CSRF_COOKIE_SAMESITE = 'Lax'  # Must match SESSION_COOKIE_SAMESITE for OAuth
# CSRF_COOKIE_HTTPONLY must be False to allow JavaScript access for AJAX requests
# This is Django's default and is safe because CSRF tokens are not sensitive data
CSRF_COOKIE_HTTPONLY = False

# CRITICAL FIX: Cookie domain for OAuth/PWA session persistence
# Setting domain to ".domain.tld" ensures cookies work across:
# - apex domain (domain.tld)
# - www subdomain (www.domain.tld)
# - Any subdomain variation during OAuth redirects
#
# This prevents cookie loss when host header varies during OAuth.
# The domain is set based on CUSTOM_DOMAINS environment variable.
_crush_domain = any('crush.lu' in d for d in CUSTOM_DOMAINS)
_powerup_domain = any('powerup.lu' in d for d in CUSTOM_DOMAINS)
_vinsdelux_domain = any('vinsdelux.com' in d for d in CUSTOM_DOMAINS)

if _crush_domain:
    SESSION_COOKIE_DOMAIN = '.crush.lu'
    CSRF_COOKIE_DOMAIN = '.crush.lu'
elif _powerup_domain:
    SESSION_COOKIE_DOMAIN = '.powerup.lu'
    CSRF_COOKIE_DOMAIN = '.powerup.lu'
elif _vinsdelux_domain:
    SESSION_COOKIE_DOMAIN = '.vinsdelux.com'
    CSRF_COOKIE_DOMAIN = '.vinsdelux.com'
# else: leave unset - Django uses request host by default

# SSL redirect - Azure App Service handles this at load balancer level
# Setting to False avoids redirect loops since Azure terminates SSL
SECURE_SSL_REDIRECT = False

# Uncomment and configure the following if you wish to use cache-backed sessions:
# SESSION_ENGINE = "django.contrib.sessions.backends.cache"
# CACHES = {
#    "default": {
#        "BACKEND": "django_redis.cache.RedisCache",
#        "LOCATION": os.environ.get('AZURE_REDIS_CONNECTIONSTRING'),
#        "OPTIONS": {
#            "CLIENT_CLASS": "django_redis.client.DefaultClient",
#            "COMPRESSOR": "django_redis.compressors.zlib.ZlibCompressor",
#        },
#    }
# }

# Logging configuration - reduce verbosity in production
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
    },
    'handlers': {
        'console': {
            'level': 'WARNING',  # Only log WARNING and above to console
            'class': 'logging.StreamHandler',
            'formatter': 'simple'
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': '/tmp/django.log',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'WARNING',  # Only WARNING and above for Django
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',  # Only ERROR for request handling
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'ERROR',  # No SQL query logging
            'propagate': False,
        },
        'azure': {
            'handlers': ['console'],
            'level': 'WARNING',  # Reduce Azure SDK logging
            'propagate': False,
        },
        # Silence noisy Azure middleware logs
        'Microsoft': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'Microsoft.AspNetCore': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'MiddlewareConsoleLogs': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        # OAuth debugging - temporarily INFO level
        'crush_lu.oauth_statekit': {
            'handlers': ['console'],
            'level': 'INFO',  # Enable INFO logging for OAuth debugging
            'propagate': False,
        },
    },
}

# Set Azure App Service logging level via environment variable
import logging
# This affects the Azure middleware logs
if 'WEBSITE_HOSTNAME' in os.environ:
    # Suppress Azure's verbose logging
    logging.getLogger('azure').setLevel(logging.WARNING)
    logging.getLogger('azure.core.pipeline.policies.http_logging_policy').setLevel(logging.WARNING)
    # Set root logger to WARNING to suppress Trace and Debug logs
    logging.root.setLevel(logging.WARNING)


