"""
Django settings for azureproject project.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/6.0/ref/settings/
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ImproperlyConfigured
import logging

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# Load .env for local development if present (optional)
# If you want to use this, install python-dotenv and create a .env at BASE_DIR
DOTENV_PATH = BASE_DIR / ".env"
if DOTENV_PATH.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=DOTENV_PATH)
    except Exception:
        # dotenv is optional; ignore if it's not installed or fails
        logging.getLogger(__name__).debug("python-dotenv not available or failed to load .env")


def _env_bool(name, default=False):
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).lower() in ("1", "true", "yes", "on")


# SECURITY: require SECRET_KEY in production. Allow an explicit dev fallback
# only when debug is enabled to avoid accidental leakage in production.
SECRET_KEY = os.getenv("SECRET_KEY")

# Admin API Key for Azure Function App to trigger management commands
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY")

# Hybrid Coach Review System (crush_lu) — global kill-switch. Default OFF so
# the new pipeline is dormant until explicitly enabled per environment. Works
# with per-coach CrushCoach.hybrid_features_enabled for staged rollout.
HYBRID_COACH_SYSTEM_ENABLED = _env_bool("HYBRID_COACH_SYSTEM_ENABLED", False)

# Use DJANGO_DEBUG env var to control debug mode (default False)
DEBUG = _env_bool("DJANGO_DEBUG", False)

if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-insecure-key-change-in-production"
        logging.getLogger(__name__).warning(
            "Using insecure fallback SECRET_KEY in DEBUG mode"
        )
    else:
        raise ImproperlyConfigured(
            "The SECRET_KEY environment variable must be set in production."
        )

# Required for django.template.context_processors.debug to expose 'debug' in templates
INTERNAL_IPS = [
    "127.0.0.1",
    "localhost",
]

# Use centralized domain configuration for ALLOWED_HOSTS
# See azureproject/domains.py for the list of configured domains
from azureproject.domains import get_all_hosts

ALLOWED_HOSTS = get_all_hosts()

if "CODESPACE_NAME" in os.environ:
    CSRF_TRUSTED_ORIGINS = [
        f'https://{os.getenv("CODESPACE_NAME")}-8000.{os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")}'
    ]

# Local development CSRF trusted origins (for .localhost domains)
# These only apply locally - production uses real domains
CSRF_TRUSTED_ORIGINS = getattr(globals(), "CSRF_TRUSTED_ORIGINS", []) + [
    "http://arborist.localhost:8000",
    "http://crush.localhost:8000",
    "http://power-up.localhost:8000",
    "http://powerup.localhost:8000",
    "http://vinsdelux.localhost:8000",
    "http://entreprinder.localhost:8000",
    "http://tableau.localhost:8000",
    "http://delegation.localhost:8000",
    "http://portal.localhost:8000",
]

# Application definition

INSTALLED_APPS = [
    "modeltranslation",  # MUST be before admin for translation tabs in admin
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.sitemaps",  # SEO: Dynamic sitemap generation
    # App templates must come BEFORE allauth to override default allauth templates
    # Order matters: crush_lu before entreprinder so its account/ templates take priority
    "core",  # Shared templates (cookie_banner, etc.) across all domains
    "crush_lu",  # Must be before entreprinder for account/ template override on crush.lu
    "delegations",
    "vinsdelux",
    "entreprinder",  # Includes merged: matching, finops, vibe_coding
    "power_up",  # Corporate/investor site for power-up.lu
    "power_up.finops",  # FinOps Hub - Azure cost analytics (submodule)
    "power_up.crm",  # CRM - Customer relationship management (submodule)
    "power_up.onboarding",  # Onboarding - Customer onboarding email builder (submodule)
    "tableau",  # AI Art e-commerce site for tableau.lu
    "arborist",  # Tree care informational site for arborist.lu
    "hub",  # JSON API for hub.crush.lu SPA (served on api.crush.lu)
    # Allauth apps
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",  # Generic OIDC (used by LinkedIn on Entreprinder)
    "crush_lu.providers.luxid",  # LuxID CIAM (POST Luxembourg) - dedicated provider for crush.lu
    "allauth.socialaccount.providers.linkedin_oauth2",
    "allauth.socialaccount.providers.facebook",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.microsoft",
    "allauth.socialaccount.providers.apple",
    # Third-party apps
    "crispy_forms",
    "crispy_tailwind",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",  # SEC-02: required for refresh token rotation+blacklist
    "corsheaders",
    "django_htmx",  # HTMX server-side integration
    "azureproject",  # For custom analytics templatetags
    "cookie_consent",  # GDPR cookie consent banner
    "channels",  # Django Channels for WebSocket support
]

# SITE_ID must NOT be set - CurrentSiteMiddleware determines site dynamically per request
# Setting SITE_ID would force all domains to use the same Site object

MIDDLEWARE = [
    "azureproject.middleware.HealthCheckMiddleware",  # MUST be first - bypasses all other middleware for /healthz/
    "corsheaders.middleware.CorsMiddleware",  # MUST be before CommonMiddleware; adds CORS headers for api.crush.lu
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",  # Django 6.0 native CSP
    "azureproject.csp_middleware.PermissionsPolicyMiddleware",  # Browser feature restrictions
    "django.middleware.gzip.GZipMiddleware",  # Compress dynamic responses (static files served at ASGI level)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "azureproject.middleware.AuthRateLimitMiddleware",  # Rate limit password reset before CSRF
    "azureproject.middleware.DomainURLRoutingMiddleware",  # Multi-domain routing - MUST be before LocaleMiddleware
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",  # MUST be before SafeCurrentSiteMiddleware
    "azureproject.middleware.SafeCurrentSiteMiddleware",  # Safe site detection (auto-creates missing Sites)
    "azureproject.middleware.AdminLanguagePrefixRedirectMiddleware",  # Redirect /fr/admin/ -> /admin/
    "azureproject.middleware.LoginPostDebugMiddleware",  # DEBUG: Log /login/ POSTs before CSRF check
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "crush_lu.middleware.UserActivityMiddleware",  # Track user activity and PWA usage
    "crush_lu.consent_middleware.CrushConsentMiddleware",  # Enforce Crush.lu GDPR consent
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django_htmx.middleware.HtmxMiddleware",  # HTMX request detection
]


from django.contrib.messages import constants as messages

MESSAGE_TAGS = {
    messages.DEBUG: "alert-info",
    messages.INFO: "alert-info",
    messages.SUCCESS: "alert-success",
    messages.WARNING: "alert-warning",
    messages.ERROR: "alert-danger",
}

SESSION_ENGINE = "django.contrib.sessions.backends.db"  # Changed from cache to db for PWA persistence
ROOT_URLCONF = "azureproject.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR
            / "core"
            / "templates",  # Core templates first (for admin overrides, shared icons)
        ],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.csp",  # Django 6.0 CSP nonce
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",  # Ensure this line is present
                "crush_lu.context_processors.crush_user_context",  # Crush.lu user context
                "crush_lu.context_processors.social_preview_context",  # Crush.lu social preview (PR #47)
                "crush_lu.context_processors.firebase_config",  # Firebase config for phone verification
                "crush_lu.context_processors.site_config_context",  # WhatsApp button & site config
                "azureproject.content_images_context.content_images_context",  # Content images (Azure Blob)
                "azureproject.analytics_context.analytics_ids",  # Domain-specific GA4/FB Pixel IDs
                "azureproject.context_processors.admin_navigation",  # Global admin panel navigation
                "azureproject.context_processors.staging_environment",  # Staging banner detection
            ],
            "builtins": [],
        },
    },
]


WSGI_APPLICATION = "azureproject.wsgi.application"
ASGI_APPLICATION = "azureproject.asgi.application"

# Channel Layers - Redis if REDIS_URL is set, otherwise in-memory
if os.environ.get("REDIS_URL"):
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [os.environ["REDIS_URL"]],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }

# Cache - Redis if REDIS_URL is set, otherwise default LocMemCache
if os.environ.get("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": os.environ["REDIS_URL"],
            "TIMEOUT": 600,
            "KEY_PREFIX": "cache",
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "IGNORE_EXCEPTIONS": True,
            },
        }
    }


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

# Uses PostgreSQL if DBHOST is set in .env, otherwise falls back to SQLite
if os.environ.get("DBHOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DBNAME", "entreprinder"),
            "HOST": os.environ.get("DBHOST", "localhost"),
            "USER": os.environ.get("DBUSER", "postgres"),
            "PASSWORD": os.environ.get("DBPASS", ""),
            "PORT": os.environ.get("DBPORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# In settings.py

# These settings optimize the login experience.
# SOCIALACCOUNT_LOGIN_ON_GET stays True until all templates that still use
# `<a href="{% provider_login_url %}">` are migrated to POST forms with CSRF.
# Flipping this to False without that migration breaks every OAuth entry point
# (login_crush.html, auth.html, signup.html, account_settings.html,
# entreprinder/base.html, admin login, …).
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_STORE_TOKENS = True
SOCIALACCOUNT_AUTO_SIGNUP = True

# IMPORTANT: Explicitly allow email/password login (not social-only)
# This MUST be False to allow email/password login via UnifiedAuthView
SOCIALACCOUNT_ONLY = False


# Session Configuration for PWA
SESSION_COOKIE_AGE = 1209600  # 14 days (2 weeks) - longer session for PWA
# OPTIMIZATION: Changed from True to False (90% reduction in database writes)
# Sessions now only save when actually modified, not on every request
# PWA will still work - 14-day timeout is sufficient without extending on every pageview
SESSION_SAVE_EVERY_REQUEST = False  # Only save when session data changes
SESSION_COOKIE_HTTPONLY = True  # Security: prevent JavaScript access
# SEC-01: secure in production (HTTPS via Azure Front Door), relaxed for local HTTP dev.
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = "Lax"  # CSRF protection while allowing navigation
# CSRF cookie matches session cookie for symmetry. Leave HTTPONLY=False so JS
# can read the token and send it as X-CSRFToken in fetch() calls.
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = (
    False  # Keep session alive after browser close (critical for PWA)
)
SESSION_REMEMBER_ME = True

# PWA Manifest version - bump when updating icons to force cache refresh
PWA_MANIFEST_VERSION = "v16"

# Wallet settings (Apple PassKit / Google Wallet)
WALLET_APPLE_PASS_TYPE_IDENTIFIER = os.getenv("WALLET_APPLE_PASS_TYPE_IDENTIFIER", "")
WALLET_APPLE_TEAM_IDENTIFIER = os.getenv("WALLET_APPLE_TEAM_IDENTIFIER", "")
WALLET_APPLE_ORGANIZATION_NAME = os.getenv("WALLET_APPLE_ORGANIZATION_NAME", "Crush.lu")
WALLET_APPLE_WEB_SERVICE_URL = os.getenv("WALLET_APPLE_WEB_SERVICE_URL", "")
WALLET_APPLE_CERT_PATH = os.getenv("WALLET_APPLE_CERT_PATH", "")
WALLET_APPLE_KEY_PATH = os.getenv("WALLET_APPLE_KEY_PATH", "")
WALLET_APPLE_KEY_PASSWORD = os.getenv("WALLET_APPLE_KEY_PASSWORD", "")
WALLET_APPLE_WWDR_CERT_PATH = os.getenv("WALLET_APPLE_WWDR_CERT_PATH", "")
WALLET_APPLE_CERT_BASE64 = os.getenv("WALLET_APPLE_CERT_BASE64", "")
WALLET_APPLE_KEY_BASE64 = os.getenv("WALLET_APPLE_KEY_BASE64", "")
WALLET_APPLE_WWDR_CERT_BASE64 = os.getenv("WALLET_APPLE_WWDR_CERT_BASE64", "")

WALLET_GOOGLE_ISSUER_ID = os.getenv("WALLET_GOOGLE_ISSUER_ID", "")
# Note: Class IDs can only contain alphanumeric, dots, and underscores (no hyphens)
WALLET_GOOGLE_CLASS_SUFFIX = os.getenv("WALLET_GOOGLE_CLASS_SUFFIX", "crush_member")
# CLASS_ID is derived from ISSUER_ID.CLASS_SUFFIX, or can be overridden
WALLET_GOOGLE_CLASS_ID = os.getenv(
    "WALLET_GOOGLE_CLASS_ID",
    (
        f"{WALLET_GOOGLE_ISSUER_ID}.{WALLET_GOOGLE_CLASS_SUFFIX}"
        if WALLET_GOOGLE_ISSUER_ID
        else ""
    ),
)
WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL = os.getenv(
    "WALLET_GOOGLE_SERVICE_ACCOUNT_EMAIL", ""
)
WALLET_GOOGLE_PRIVATE_KEY = os.getenv("WALLET_GOOGLE_PRIVATE_KEY", "")
WALLET_GOOGLE_PRIVATE_KEY_PATH = os.getenv("WALLET_GOOGLE_PRIVATE_KEY_PATH", "")
WALLET_GOOGLE_KEY_ID = os.getenv("WALLET_GOOGLE_KEY_ID", "")
WALLET_GOOGLE_EVENT_TICKET_ENABLED = _env_bool("WALLET_GOOGLE_EVENT_TICKET_ENABLED", default=True)

# Pre-screening questionnaire (Crush.lu). Off by default; enable in production
# after all Phases have shipped and the Coach-facing rollout is ready.
PRE_SCREENING_ENABLED = _env_bool("PRE_SCREENING_ENABLED", default=False)

# Event Check-In Configuration
EVENT_CHECKIN_WINDOW_HOURS = int(os.getenv("EVENT_CHECKIN_WINDOW_HOURS", "12"))

# Referral points configuration
REFERRAL_POINTS_PER_SIGNUP = int(os.getenv("REFERRAL_POINTS_PER_SIGNUP", "100"))
REFERRAL_POINTS_PER_PROFILE_APPROVED = int(
    os.getenv("REFERRAL_POINTS_PER_PROFILE_APPROVED", "50")
)

# Membership tier thresholds (points needed to reach each tier)
MEMBERSHIP_TIER_THRESHOLDS = {
    "bronze": 200,
    "silver": 500,
    "gold": 1000,
}

# Points redemption rates
POINTS_PER_EURO_DISCOUNT = 50  # 50 points = €1 off event fees
POINTS_FOR_PRIORITY_ACCESS = 200  # Unlock priority event registration
POINTS_FOR_VISIBILITY_BOOST = 150  # Boost profile visibility temporarily


CRISPY_ALLOWED_TEMPLATE_PACKS = "tailwind"
CRISPY_TEMPLATE_PACK = "tailwind"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# AllAuth settings (updated for django-allauth 0.63+)
# Login via email only (no username)
ACCOUNT_LOGIN_METHODS = {"email"}

# Signup fields: email required (*), password twice required (*)
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]

# Keep unique email constraint
ACCOUNT_UNIQUE_EMAIL = True

# Email verification mandatory
ACCOUNT_EMAIL_VERIFICATION = "mandatory"

# Where to send anonymous users after they click the confirmation link.
# We deliberately do NOT enable ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION so users
# still authenticate with their password. Use allauth's /accounts/login/
# (mounted globally via allauth.urls) instead of Crush's /login/ — the latter
# only exists in crush_lu/urls.py, so non-Crush domains (vinsdelux,
# entreprinder, power-up, arborist, delegations, tableau, portal) would 404
# on the post-confirm redirect. The Crush prefill UX still works for users
# who arrive at /login/ directly via the success message; this setting only
# governs allauth's anonymous post-confirm redirect.
ACCOUNT_EMAIL_CONFIRMATION_ANONYMOUS_REDIRECT_URL = "/accounts/login/"

# Remember me by default
ACCOUNT_SESSION_REMEMBER = True

LOGIN_REDIRECT_URL = "/profile/"

# Don't send email verification for social account signups (email already verified by provider)
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"

# Automatic account linking settings
# When a user logs in with a social provider (e.g., Google) using an email that already exists
# in the database (from a previous signup via email/password or another social provider),
# automatically link the social account to the existing user account.
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True  # Enable email-based account linking
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = (
    True  # Auto-connect without confirmation
)

# Social account provider settings
SOCIALACCOUNT_PROVIDERS = {
    "facebook": {
        "METHOD": "oauth2",
        "SCOPE": [
            "email",
            "public_profile",
        ],  # Only basic permissions (no app review needed)
        "AUTH_PARAMS": {
            "auth_type": "rerequest"
        },  # Smoother UX - only re-prompt for declined permissions
        "FIELDS": [
            "id",
            "email",
            "name",
            "first_name",
            "last_name",
            "picture.type(large)",
        ],
        "EXCHANGE_TOKEN": True,
        # Trust Facebook emails as verified (required for auto-linking)
        "VERIFIED_EMAIL": True,
        "VERSION": "v24.0",
    },
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
        # Trust Google emails as verified (required for auto-linking)
        "VERIFIED_EMAIL": True,
    },
    "microsoft": {
        # 'common' allows any Microsoft account (personal + work/school from any org)
        # This is needed for crush.lu consumers who may use personal accounts
        # Admin panel access is restricted at the adapter level (see adapters.py)
        "TENANT": "common",
        "SCOPE": ["User.Read", "profile", "email", "openid"],
        # Trust Microsoft emails as verified (required for auto-linking)
        "VERIFIED_EMAIL": True,
    },
    "apple": {
        "SCOPE": ["email", "name"],
        "VERIFIED_EMAIL": True,
    },
    "luxid": {
        "SCOPE": ["openid", "profile", "email"],
        "OAUTH_PKCE_ENABLED": True,
        "VERIFIED_EMAIL": True,
    },
}

# LuxID CIAM (POST Luxembourg) - dedicated provider at /accounts/luxid/
# Uses crush_lu.providers.luxid instead of the generic openid_connect provider,
# so LuxID gets its own URL namespace without affecting LinkedIn's OIDC URLs.
# Callback URL: /accounts/luxid/login/callback/
# To set up: Admin > Social Applications > Add:
#   Provider: LuxID
#   Name: LuxID
#   Client ID: (from POST)
#   Secret Key: (from POST)
#   Settings (UAT): {"server_url": "https://login-uat.luxid.lu"}
#   Settings (Prod): {"server_url": "https://login.luxid.lu"}
#   Sites: test.crush.lu (UAT) or crush.lu (Prod)

# Trust emails from these providers as verified (enables auto-linking to existing accounts)
# When a user logs in with a social provider using an email that exists in the database,
# the social account will be automatically linked if the provider is in this list.
SOCIALACCOUNT_EMAIL_VERIFIED_PROVIDERS = ["google", "facebook", "microsoft", "apple", "luxid"]


# Use CustomSignupForm for Entreprinder (will be overridden by adapters for other domains)
ACCOUNT_FORMS = {"signup": "entreprinder.forms.CustomSignupForm"}

# Specify where to redirect after successful sign-up
ACCOUNT_SIGNUP_REDIRECT_URL = "/profile/"  # Redirect to profile page after signup

# Allauth adapters - Multi-domain aware
SOCIALACCOUNT_ADAPTER = "azureproject.adapters.MultiDomainSocialAccountAdapter"
ACCOUNT_ADAPTER = "azureproject.adapters.MultiDomainAccountAdapter"

# Email backend Configuration
# NOTE: For domain-specific email configuration (crush.lu, vinsdelux.com, etc.),
# use the send_domain_email() function from azureproject.email_utils
# The send_domain_email() automatically uses console backend in DEBUG mode
# This default configuration is used for powerup.lu and as fallback

if DEBUG:
    # Development: Print emails to console (includes verification emails from Allauth)
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    EMAIL_HOST_USER = None  # Not needed for console backend
    import sys
    if sys.stdout.encoding and 'utf' in sys.stdout.encoding.lower():
        print("📧 Email Backend: Console - Emails will print in terminal")
    else:
        print("[EMAIL] Backend: Console - Emails will print in terminal")
else:
    # Production: Use SMTP
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.getenv("EMAIL_HOST", "mail.power-up.lu")  # SMTP server address
    EMAIL_PORT = int(os.getenv("EMAIL_PORT", 465))  # SMTP port (465 for SSL)
    EMAIL_HOST_USER = os.getenv(
        "EMAIL_HOST_USER"
    )  # Your SMTP username (e.g., info@power-up.lu)
    EMAIL_HOST_PASSWORD = os.getenv(
        "EMAIL_HOST_PASSWORD"
    )  # Your SMTP password or App Password
    EMAIL_USE_SSL = (
        os.getenv("EMAIL_USE_SSL", "True").lower() == "true"
    )  # Use SSL since port is 465
    # EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'False').lower() == 'true' # Use TLS if port was 587

# Default email address for outgoing mail
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "noreply@powerup.lu"
)

# Domain-specific email configurations
# Crush.lu uses Microsoft Graph API (SMTP disabled by M365)
#   Set CRUSH_GRAPH_TENANT_ID, CRUSH_GRAPH_CLIENT_ID, CRUSH_GRAPH_CLIENT_SECRET
# VinsDelux can use VINSDELUX_EMAIL_* variables for custom configuration
# See azureproject/email_utils.py for implementation

# ============================================================================
# FIREBASE / GOOGLE IDENTITY PLATFORM CONFIGURATION
# ============================================================================
# Used for phone number verification in Crush.lu
# Token verification uses Google's public JWKS keys - no service account needed
# IMPORTANT: Set these environment variables in production - no defaults for security
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
FIREBASE_API_KEY = os.environ.get("FIREBASE_API_KEY", "")
FIREBASE_AUTH_DOMAIN = os.environ.get("FIREBASE_AUTH_DOMAIN", "")

# CORS — scoped to the SPA origins that call the api.crush.lu subdomain.
# JWT Bearer auth means we do NOT need CORS_ALLOW_CREDENTIALS (no cookies sent
# cross-origin). Leave it False so a compromised origin can't replay sessions.
CORS_ALLOWED_ORIGINS = [
    "https://hub.crush.lu",
    "https://delightful-water-07d8c6e10.7.azurestaticapps.net",
]
if DEBUG:
    CORS_ALLOWED_ORIGINS += [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
CORS_ALLOW_CREDENTIALS = False
# Only apply CORS to the API surface; everything else on crush.lu / other
# domains is server-rendered HTML and should not advertise CORS.
CORS_URLS_REGEX = r"^/(hub|api)/.*$"


ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    # Custom exception handler for error sanitization in production
    "EXCEPTION_HANDLER": "azureproject.api_exception_handler.custom_exception_handler",
    # Rate limiting / throttling
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/minute",  # Anonymous API users
        "user": "120/minute",  # Authenticated API users
        "login": "5/minute",  # Login attempts (custom throttle)
        "signup": "3/minute",  # Signup attempts (custom throttle)
        "phone_verify": "3/minute",  # Phone verification (custom throttle)
        "password_reset": "3/hour",  # Password reset requests (prevent email spam)
        "quiz_pin": "5/minute",  # Quiz projector PIN verification (prevent brute force)
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,  # SEC-02: rotate on each refresh; old token blacklisted
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "VERIFYING_KEY": None,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "TOKEN_TYPE_CLAIM": "token_type",
}

# Internationalization

LOCALE_PATHS = [
    BASE_DIR / "core" / "locale",
]

LANGUAGE_CODE = "en"

# Luxembourg timezone (CET/CEST - UTC+1/UTC+2 with automatic DST handling)
TIME_ZONE = "Europe/Luxembourg"

USE_I18N = True

LANGUAGES = [
    ("en", _("English")),
    ("de", _("German")),
    ("fr", _("French")),
]

# django-modeltranslation settings
# Automatically returns correct language field based on request.LANGUAGE_CODE
MODELTRANSLATION_DEFAULT_LANGUAGE = "en"
MODELTRANSLATION_LANGUAGES = ("en", "de", "fr")
MODELTRANSLATION_FALLBACK_LANGUAGES = (
    "en",
)  # Fallback to English if translation missing

# Azure AI Translator (auto-translate admin content across EN/DE/FR)
# Free tier: 2M characters/month. Create resource in Azure Portal.
AZURE_TRANSLATOR_KEY = os.getenv("AZURE_TRANSLATOR_KEY", "")
AZURE_TRANSLATOR_REGION = os.getenv("AZURE_TRANSLATOR_REGION", "westeurope")

USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")


# STATICFILES_DIRS removed - all static files now in app-level directories
# (e.g., crush_lu/static/crush_lu/, vinsdelux/static/vinsdelux/)
# Django's AppDirectoriesFinder handles these automatically

# =============================================================================
# CONTENT IMAGE URLS (Development Fallbacks)
# =============================================================================
# These provide stable URLs for content images. In production, Azure Blob URLs
# are used (configured in production.py). In development, static file URLs.

# Crush.lu images
SOCIAL_PREVIEW_IMAGE_URL = os.getenv(
    "SOCIAL_PREVIEW_IMAGE_URL",
    "https://crush.lu/static/crush_lu/crush_social_preview.jpg",
)
CRUSH_SOCIAL_PREVIEW_URL = os.getenv(
    "CRUSH_SOCIAL_PREVIEW_URL",
    "https://crush.lu/static/crush_lu/crush_social_preview.jpg",
)

# PowerUP/Entreprinder images
POWERUP_DEFAULT_PROFILE_URL = os.getenv(
    "POWERUP_DEFAULT_PROFILE_URL", "/static/core/images/default-profile.png"
)

# =============================================================================
# STORAGE CONFIGURATION
# =============================================================================
# Priority: 1) Azurite (local emulator), 2) Azure Blob Storage, 3) Local filesystem

# Azurite (Azure Storage Emulator) for local development
AZURITE_MODE = os.environ.get("USE_AZURITE", "false").lower() == "true"

if AZURITE_MODE:
    # Azurite well-known development credentials
    AZURE_ACCOUNT_NAME = "devstoreaccount1"
    AZURE_ACCOUNT_KEY = (
        "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq"
        "/K1SZFPTOtr/KBHBeksoGMGw=="
    )
    # AZURE_CONTAINER_NAME removed - platform-specific containers in use
    AZURITE_BLOB_HOST = "127.0.0.1:10000"

    # Azurite connection string for azure-storage-blob SDK
    AZURE_CONNECTION_STRING = (
        f"DefaultEndpointsProtocol=http;"
        f"AccountName={AZURE_ACCOUNT_NAME};"
        f"AccountKey={AZURE_ACCOUNT_KEY};"
        f"BlobEndpoint=http://{AZURITE_BLOB_HOST}/{AZURE_ACCOUNT_NAME};"
    )

    # Media URL for serving files (Azurite - using shared-media as fallback)
    MEDIA_URL = f"http://{AZURITE_BLOB_HOST}/{AZURE_ACCOUNT_NAME}/shared-media/"

    # Django 4.2+ STORAGES configuration for Azurite
    STORAGES = {
        # Default storage uses shared-media container (fallback only)
        # All models should have explicit storage= parameters
        "default": {
            "BACKEND": "azureproject.storage_shared.SharedMediaStorage",
        },
        # Platform-specific storage backends
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
        # Use simple StaticFilesStorage in development for instant refresh
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

    if os.environ.get("RUN_MAIN"):
        print(f"Using Azurite (Azure Storage Emulator) at {AZURITE_BLOB_HOST}")

# Azure Blob Storage Settings (Production - when running outside Azurite and production.py)
# NOTE: In production, production.py handles storage configuration
# This block is mainly for transition/testing scenarios
elif os.getenv("AZURE_ACCOUNT_NAME"):
    AZURE_ACCOUNT_NAME = os.getenv("AZURE_ACCOUNT_NAME")
    AZURE_ACCOUNT_KEY = os.getenv("AZURE_ACCOUNT_KEY")
    # AZURE_CONTAINER_NAME removed - platform-specific storage in use
    MEDIA_URL = f"https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net/shared-media/"

    # Platform-specific base URLs (using dedicated containers)
    CRUSH_MEDIA_BASE_URL = f"https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net/crush-lu-media"
    POWERUP_MEDIA_BASE_URL = f"https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net/powerup-media"

    # Override content image URLs with platform-specific paths (if not explicitly set in env)
    if "SOCIAL_PREVIEW_IMAGE_URL" not in os.environ:
        SOCIAL_PREVIEW_IMAGE_URL = f"{CRUSH_MEDIA_BASE_URL}/branding/social-preview.jpg"
    if "CRUSH_SOCIAL_PREVIEW_URL" not in os.environ:
        CRUSH_SOCIAL_PREVIEW_URL = f"{CRUSH_MEDIA_BASE_URL}/branding/social-preview.jpg"
    if "POWERUP_DEFAULT_PROFILE_URL" not in os.environ:
        POWERUP_DEFAULT_PROFILE_URL = f"{POWERUP_MEDIA_BASE_URL}/defaults/profile.png"

    if os.environ.get("RUN_MAIN"):
        print("Using Azure Blob Storage with platform-specific containers.")
else:
    MEDIA_URL = "/media/"
    MEDIA_ROOT = BASE_DIR / "media"

    # Ensure media directory exists
    if not os.path.exists(MEDIA_ROOT):
        os.makedirs(MEDIA_ROOT)
        if os.environ.get("RUN_MAIN"):  # Only print in main process
            print(f"Created media directory at: {MEDIA_ROOT}")
    elif os.environ.get("RUN_MAIN"):  # Only print in main process
        print(f"Media directory already exists at: {MEDIA_ROOT}")

    if os.environ.get("RUN_MAIN"):  # Only print in main process
        print("Using local file system for media files.")

    # Django 4.2+ STORAGES configuration for local development
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        # Platform-specific storage backends (local filesystem)
        "crush_media": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "crush_private": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "entreprinder_media": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "powerup_media": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "powerup_finops": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "shared_media": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        # Use simple StaticFilesStorage in development for instant refresh
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# CSRF Cookie Settings
# CSRF_COOKIE_HTTPONLY=True prevents JavaScript from reading the CSRF cookie
# This is safe because HTMX reads the token from a hidden input field instead
# See base.html for the HTMX CSRF token setup
CSRF_COOKIE_HTTPONLY = True

# Custom CSRF failure view with detailed logging
CSRF_FAILURE_VIEW = "azureproject.middleware.csrf_failure_view"

# =============================================================================
# PROFILE REMINDER TIMING CONFIGURATION
# =============================================================================
# Configurable timing windows for profile completion reminder emails.
# min_hours: Minimum time since profile creation before sending this reminder
# max_hours: Maximum time window - don't send reminder after this point
# Users are only eligible if they haven't received this reminder type before.
PROFILE_REMINDER_TIMING = {
    "24h": {
        "min_hours": 24,
        "max_hours": 48,
    },
    "72h": {
        "min_hours": 72,
        "max_hours": 96,
    },
    "7d": {
        "min_hours": 168,  # 7 days
        "max_hours": 192,  # 8 days
    },
}

# =============================================================================
# CONTENT SECURITY POLICY (CSP) — Django 6.0 Native
# =============================================================================
# Django 6.0's built-in CSP middleware replaces the custom azureproject/csp_middleware.py.
# Uses SECURE_CSP_REPORT_ONLY for report-only mode (violations logged, not blocked).
# Switch to SECURE_CSP (same dict) to enforce after testing.
#
# Nonce support: CSP.NONCE in script-src is replaced at runtime with a per-request
# nonce. Templates use {{ csp_nonce }} via django.template.context_processors.csp.
#
# Per-view overrides: Use @csp_override({}) to exempt a view from CSP.
# See: https://docs.djangoproject.com/en/6.0/ref/csp/

from django.utils.csp import CSP

SECURE_CSP_REPORT_ONLY = {
    "default-src": [CSP.SELF],
    # Scripts: CDNs, nonce for inline, Firebase/OAuth/Analytics
    "script-src": [
        CSP.SELF,
        CSP.NONCE,
        CSP.UNSAFE_INLINE,  # TODO: Remove once HTMX/Alpine.js handlers use nonce-based scripts.
                            # unsafe-inline negates nonce protection in script-src for CSP3 browsers.
        # CDN sources
        "https://unpkg.com",
        "https://cdn.jsdelivr.net",
        "https://cdnjs.cloudflare.com",  # GSAP animation library (VinsDelux)
        # Firebase/Google
        "https://www.gstatic.com",
        "https://apis.google.com",
        "https://www.googletagmanager.com",
        "https://www.google.com",  # reCAPTCHA
        "https://www.gstatic.com/recaptcha/",  # reCAPTCHA Enterprise
        # Facebook SDK
        "https://connect.facebook.net",
        # Microsoft
        "https://login.microsoftonline.com",
        # Azure Application Insights SDK
        "https://js.monitor.azure.com",
    ],
    # Styles: Tailwind JIT requires unsafe-inline
    "style-src": [
        CSP.SELF,
        CSP.UNSAFE_INLINE,  # Required for Tailwind JIT and inline styles
        "https://cdn.jsdelivr.net",
        "https://fonts.googleapis.com",
        "https://unpkg.com",  # Leaflet CSS (VinsDelux maps)
    ],
    # Images: data URIs, blobs for photo previews, all HTTPS
    "img-src": [
        CSP.SELF,
        "data:",
        "blob:",
        "https:",  # Allow all HTTPS images (CDNs, Azure Blob, etc.)
        "http://127.0.0.1:10000",  # Azurite dev storage
        "http://localhost:10000",
    ],
    # Audio/video from local dev storage and HTTPS
    "media-src": [
        CSP.SELF,
        "blob:",
        "https:",
        "http://127.0.0.1:10000",
        "http://localhost:10000",
    ],
    # Fonts: Google Fonts and CDN
    "font-src": [
        CSP.SELF,
        "https://fonts.gstatic.com",
        "https://cdn.jsdelivr.net",
    ],
    # API endpoints, analytics, WebSocket, Firebase, Azure
    "connect-src": [
        CSP.SELF,
        # CDN for service worker caching
        "https://cdn.jsdelivr.net",
        "https://unpkg.com",
        "https://www.gstatic.com",
        "https://apis.google.com",
        # Google Analytics GA4
        "https://www.google-analytics.com",
        "https://www.googletagmanager.com",
        "https://analytics.google.com",
        "https://region1.analytics.google.com",
        "https://region2.analytics.google.com",
        "https://region3.analytics.google.com",
        "https://region1.google-analytics.com",
        "https://region2.google-analytics.com",
        "https://region3.google-analytics.com",
        "https://stats.g.doubleclick.net",
        # Google domains for GA audiences (per-country TLDs)
        "https://www.google.com",
        "https://www.google.lu",
        "https://www.google.de",
        "https://www.google.fr",
        "https://www.google.be",
        # Firebase
        "https://identitytoolkit.googleapis.com",
        "https://securetoken.googleapis.com",
        "https://www.googleapis.com",
        "https://recaptchaenterprise.googleapis.com",  # reCAPTCHA Enterprise
        # Google Translate
        "https://translate.googleapis.com",
        # Geo-IP lookup for phone country detection
        "https://ipapi.co",
        # Azure Blob Storage
        "https://*.blob.core.windows.net",
        # Azure Application Insights
        "https://js.monitor.azure.com",
        "https://dc.services.visualstudio.com",
        "https://*.in.applicationinsights.azure.com",
        # Facebook Pixel + profile pictures
        "https://connect.facebook.net",
        "https://platform-lookaside.fbsbx.com",
        "https://*.fbcdn.net",
        # Apple Sign In
        "https://appleid.apple.com",
        # WebSocket for HTMX
        "wss:",
    ],
    # OAuth popups and Firebase reCAPTCHA
    "frame-src": [
        CSP.SELF,
        "https://accounts.google.com",
        "https://www.facebook.com",
        "https://login.microsoftonline.com",
        "https://appleid.apple.com",
        "https://www.google.com",
        "https://*.firebaseapp.com",
    ],
    "form-action": [CSP.SELF],
    "base-uri": [CSP.SELF],
    "object-src": [CSP.NONE],
    "frame-ancestors": [CSP.SELF],
    "report-uri": "/csp-report/",
}

# Django 6.0: Opt into HTTPS as the default protocol for urlize/urlizetrunc
# This will become the default in Django 7.0
URLIZE_ASSUME_HTTPS = True

# =============================================================================
# PASSKIT (APPLE WALLET) SETTINGS
# =============================================================================
PASSKIT_WEB_SERVICE_BASE_PATH = "/wallet/v1"
PASSKIT_AUTH_TOKEN = os.getenv("PASSKIT_AUTH_TOKEN")
PASSKIT_AUTH_TOKEN_RESOLVER = os.getenv("PASSKIT_AUTH_TOKEN_RESOLVER")
PASSKIT_PASS_PROVIDER = os.getenv(
    "PASSKIT_PASS_PROVIDER",
    "crush_lu.wallet.apple_pass.provide_pass_for_serial",
)
PASSKIT_PASS_JSON_PROVIDER = os.getenv("PASSKIT_PASS_JSON_PROVIDER")
PASSKIT_PASS_PACKAGE_BUILDER = os.getenv("PASSKIT_PASS_PACKAGE_BUILDER")
PASSKIT_APNS_KEY_ID = os.getenv("PASSKIT_APNS_KEY_ID")
PASSKIT_APNS_TEAM_ID = os.getenv("PASSKIT_APNS_TEAM_ID")
PASSKIT_APNS_PRIVATE_KEY = os.getenv("PASSKIT_APNS_PRIVATE_KEY")
PASSKIT_APNS_USE_SANDBOX = os.getenv("PASSKIT_APNS_USE_SANDBOX", "").lower() in (
    "1",
    "true",
    "yes",
)

# =============================================================================
# DJANGO 6.0 BACKGROUND TASKS FRAMEWORK
# =============================================================================
# Native task system for running code outside the HTTP request/response cycle.
# Default is ImmediateBackend (runs inline) — safe for dev without a worker.
# Production should install the `django-tasks` PyPI package, add `django_tasks`
# to INSTALLED_APPS, run `manage.py db_worker` alongside gunicorn, and set
#   DJANGO_TASKS_BACKEND=django_tasks.backends.database.DatabaseBackend
# Tests override to ImmediateBackend in conftest.py regardless of env.
# See: https://docs.djangoproject.com/en/6.0/topics/tasks/
TASKS = {
    "default": {
        "BACKEND": os.environ.get(
            "DJANGO_TASKS_BACKEND",
            "django.tasks.backends.immediate.ImmediateBackend",
        ),
    }
}

# =============================================================================
# DJANGO-COMPONENTS SETTINGS
# =============================================================================
COMPONENTS = {
    "dirs": [
        BASE_DIR / "crush_lu" / "components",
        BASE_DIR / "shared" / "components",
    ],
    "app_dirs": ["components"],
}

# Production-only security headers (SSL redirect, HSTS, nosniff, referrer,
# X-Frame-Options) live in azureproject/production.py. settings.py is used by
# local dev and pytest, both of which speak plain HTTP and would break under
# SSL redirect.
