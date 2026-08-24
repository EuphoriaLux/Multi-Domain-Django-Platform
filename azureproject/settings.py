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
        logging.getLogger(__name__).debug(
            "python-dotenv not available or failed to load .env"
        )


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

# "My Crush!" 24h untouched-lead reminder sweep (spec §10, Phase D) — gate for
# the /api/admin/crush-lead-reminders/ endpoint driven by the CrushLeadReminders
# Azure Function timer. Default OFF, and independent of
# HYBRID_COACH_SYSTEM_ENABLED on purpose: without its own switch the only ways
# to stage or stop this flow are disabling every hybrid-maintenance job or
# unsetting the function app's URL var, and the latter fails silently.
CRUSH_LEAD_REMINDERS_ENABLED = _env_bool("CRUSH_LEAD_REMINDERS_ENABLED", False)

# Multi-channel campaign dispatch (crush_lu campaign dashboard) — gate for the
# /api/admin/campaigns/dispatch/ endpoint driven by the CampaignDispatch Azure
# Function timer. Default OFF so scheduled campaigns never send until the
# environment explicitly opts in.
CAMPAIGN_DISPATCH_ENABLED = _env_bool("CAMPAIGN_DISPATCH_ENABLED", False)

# Recipients for the weekly Crush.lu KPI digest email (send_weekly_kpis command,
# driven on Mondays by the hybrid-maintenance Azure Function). Comma-separated
# env var; empty means "compute + persist the snapshot but email no one".
WEEKLY_KPI_RECIPIENTS = [
    addr.strip()
    for addr in os.getenv("WEEKLY_KPI_RECIPIENTS", "").split(",")
    if addr.strip()
]

# Google Search Indexing API real-time notifications for Crush.lu events (disabled by default outside production)
GOOGLE_INDEXING_ENABLED = _env_bool("GOOGLE_INDEXING_ENABLED", False)
GOOGLE_INDEXING_KEY_JSON = os.getenv("GOOGLE_INDEXING_KEY_JSON", "")
# Host whose URLs this deployment is allowed to submit. Empty disables the
# integration outright — see production.py: staging runs an isolated database,
# so staging event ID N is a *different* event from production event ID N, and
# submitting crush.lu URLs from there could deindex a live listing.
GOOGLE_INDEXING_DOMAIN = os.getenv("GOOGLE_INDEXING_DOMAIN", "")
GOOGLE_INDEXING_TIMEOUT_SECONDS = int(os.getenv("GOOGLE_INDEXING_TIMEOUT_SECONDS", "3"))

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

# SECRET_KEY rotation (Task 5.4b). Comma-separated list of previous SECRET_KEY
# values still accepted for VERIFYING existing signatures/sessions/tokens —
# never used to create new ones (Django only ever signs with SECRET_KEY
# itself; see django.core.signing.Signer). Empty by default, matching
# Django's own default and this repo's comma-separated env var convention
# (see WEEKLY_KPI_RECIPIENTS above). Keys must not contain a comma —
# django.utils.crypto.get_random_secret_key()'s alphabet has none, so a key
# generated that way is always safe to drop in here.
#
# This is Django's own SECRET_KEY_FALLBACKS mechanism (4.1+): the session
# framework, django.contrib.auth's session-auth-hash check, and
# django.core.signing.Signer/TimestampSigner (used directly, with no
# explicit key=, for the QR check-in tokens in crush_lu/views_ticket.py and
# crush_lu/views_checkin.py) all read it automatically. It does NOT cover
# every SECRET_KEY consumer in this codebase — see
# docs/ops/secret-key-rotation.md for the two known gaps (the Hub SSO JWTs
# in SIMPLE_JWT below, and crush_lu/models/ios_app.py's native auth code
# hash) and the rotation procedure itself.
SECRET_KEY_FALLBACKS = [
    key.strip()
    for key in os.getenv("SECRET_KEY_FALLBACKS", "").split(",")
    if key.strip()
]

# Required for django.template.context_processors.debug to expose 'debug' in templates
INTERNAL_IPS = [
    "127.0.0.1",
    "localhost",
]

# Use centralized domain configuration for ALLOWED_HOSTS
# See azureproject/domains.py for the list of configured domains
from azureproject.domains import get_all_hosts

ALLOWED_HOSTS = get_all_hosts()

CSRF_TRUSTED_ORIGINS = []
if "CODESPACE_NAME" in os.environ:
    CSRF_TRUSTED_ORIGINS.append(
        f'https://{os.getenv("CODESPACE_NAME")}-8000.{os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")}'
    )

# Local development CSRF trusted origins (for .localhost domains)
# These only apply locally - production uses real domains
CSRF_TRUSTED_ORIGINS += [
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
    "power_up.atmos",  # Atmos - QR bar ordering prototype (submodule)
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
    # Runtime logging probe. Deliberately AFTER HealthCheckMiddleware: that one
    # short-circuits /healthz/ and /readyz/, so the canary fires on a real
    # request rather than on an Azure liveness ping.
    "azureproject.middleware.RuntimeLoggingCanaryMiddleware",
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
    # LoginPostDebugMiddleware (azureproject.middleware) is available for local
    # CSRF debugging — insert it here, before CsrfViewMiddleware, when needed.
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

# Must stay above channels-redis' `brpop_timeout` (a class attribute on
# RedisChannelLayer, 5s). The layer listens with `BRPOP <channel> 5`, which on
# an idle channel blocks the full five seconds by design, while redis-py 8.0
# applies `DEFAULT_SOCKET_TIMEOUT = 5` where older releases used None. Two
# identical timers race, the client wins, and every idle WebSocket dies at
# exactly 5.0s with "Timeout reading from ...redis.cache.windows.net:6380".
CHANNEL_LAYER_SOCKET_TIMEOUT = 10
CHANNEL_LAYER_SOCKET_CONNECT_TIMEOUT = 5
CHANNEL_LAYER_HEALTH_CHECK_INTERVAL = 15


def channel_layer_hosts(url):
    """Channel-layer host config with a read timeout that outlives BRPOP and
    connection resilience settings (keepalive, health-check ping).

    Scoped to the channel layer on purpose: CACHES shares this Redis URL, and
    raising the timeout there too would slow how fast a wedged Redis surfaces
    on the page-render path. `decode_hosts` forwards dict entries verbatim and
    `create_pool` calls `from_url(address, **host)`, so this reaches the pool.

    Resilience settings:
    - socket_timeout: outlives channels-redis BRPOP (5s).
    - socket_connect_timeout: bounds connection establishment (5s).
    - health_check_interval: proactively pings idle pooled connections (15s) to discard
      dead/reset sockets before executing commands.
    - socket_keepalive: sends TCP keepalives to prevent Azure idle drops.

    Note: Connection-level command retries (e.g. `retry` / `retry_on_timeout`)
    are deliberately omitted here. `channels-redis` receives messages using
    destructive operations (`BZPOPMIN`/`BRPOP`). Retrying at the client connection
    level after a network reset during a receive could rerun the pop on a message
    already consumed, causing silent message loss instead of cleanly surfacing
    the connection failure and reconnecting.
    """
    return [
        {
            "address": url,
            "socket_timeout": CHANNEL_LAYER_SOCKET_TIMEOUT,
            "socket_connect_timeout": CHANNEL_LAYER_SOCKET_CONNECT_TIMEOUT,
            "health_check_interval": CHANNEL_LAYER_HEALTH_CHECK_INTERVAL,
            "socket_keepalive": True,
        }
    ]


# Channel Layers - Redis if REDIS_URL is set, otherwise in-memory
if os.environ.get("REDIS_URL"):
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": channel_layer_hosts(os.environ["REDIS_URL"])},
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
    if not os.environ.get("CI"):
        # File-based test DB (instead of :memory:) so pytest's --reuse-db can
        # skip replaying all migrations on every local run. pytest-xdist gives
        # each worker its own suffixed file (test_db.sqlite3_gw0, ...). Run
        # `pytest --create-db` after pulling new migrations or after a
        # `-m playwright` run (transaction=True teardown flushes seeded data).
        # CI runners are ephemeral — nothing to reuse — so they keep the
        # faster in-memory default (GitHub Actions always sets CI=true).
        DATABASES["default"]["TEST"] = {"NAME": BASE_DIR / "test_db.sqlite3"}


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
#
# SOCIALACCOUNT_LOGIN_ON_GET is deliberately kept True (login-CSRF finding S2,
# issue #542). This was re-triaged rather than flipped:
#   * The social buttons are GET-based by design across every surface — the
#     Android-PWA path builds an `intent://` URL to hand the flow to an external
#     browser, the popup path uses `window.open(url)`, and iOS/desktop do a plain
#     anchor redirect (see oauth-popup.js and the inline handlers in auth.html /
#     login_crush.html). None of these can carry a CSRF token, so a clean
#     POST-form conversion is infeasible for the mobile flows.
#   * Flipping to False makes allauth serve an intermediate "Continue with X"
#     confirmation page, which adds a click to *every* social login — including
#     the promoted one-tap LuxID hero flow ("verified instantly, no waiting").
#   * Residual exposure is real and is NOT covered by SameSite: because the OAuth
#     entry point is a top-level GET, SESSION_COOKIE_SAMESITE="Lax" still sends
#     the session cookie, so a cross-site page can initiate the OAuth flow in the
#     victim's session (login-CSRF). (Lax *does* block the cross-site POST vector
#     — that's why it is the stated mitigation for the POST-only push endpoint in
#     api_push.py — but it does not cover this GET flow.) Practical impact is
#     limited (a forced OAuth *start* still needs the victim to authenticate to
#     the attacker's provider account to cause account linking/takeover), but the
#     vector is genuinely open until LOGIN_ON_GET is addressed.
# Accepted risk, tracked in #542. The only mobile-compatible fix is flipping this
# to False and accepting the interstitial; revisit that trade-off, or a POST-form
# rebuild of the login flow, if the residual risk is deemed unacceptable.
SOCIALACCOUNT_LOGIN_ON_GET = False  # Login-CSRF fix (finding S2, issue #542)
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
# CSRF cookie matches session cookie for symmetry. CSRF_COOKIE_HTTPONLY is set
# further below (True — HTMX reads the token from a hidden input, not the cookie).
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = (
    False  # Keep session alive after browser close (critical for PWA)
)
SESSION_REMEMBER_ME = True

# PWA Manifest version - bump when updating icons to force cache refresh
PWA_MANIFEST_VERSION = "v17"

# Native iOS App Store wrapper settings
IOS_APP_BUNDLE_ID = os.getenv("IOS_APP_BUNDLE_ID", "lu.crush.app")
IOS_APP_TEAM_ID = os.getenv("IOS_APP_TEAM_ID", "C5XDPB2G33")
IOS_APP_NAME = os.getenv("IOS_APP_NAME", "Crush.lu")
IOS_APP_VERSION = os.getenv("IOS_APP_VERSION", "1.0.0")
IOS_APP_BUILD = os.getenv("IOS_APP_BUILD", "1")
IOS_APP_MIN_SUPPORTED_VERSION = os.getenv("IOS_APP_MIN_SUPPORTED_VERSION", "1.0.0")
IOS_APP_STORE_URL = os.getenv("IOS_APP_STORE_URL", "")
IOS_NATIVE_COMMERCE_ENABLED = _env_bool("IOS_NATIVE_COMMERCE_ENABLED", default=False)
IOS_AUTH_CODE_TTL_SECONDS = int(os.getenv("IOS_AUTH_CODE_TTL_SECONDS", "300"))
IOS_AUTH_REDIRECT_URIS = [
    uri.strip()
    for uri in os.getenv("IOS_AUTH_REDIRECT_URIS", "crushlu://auth").split(",")
    if uri.strip()
]
IOS_APNS_KEY_ID = os.getenv("IOS_APNS_KEY_ID", "")
IOS_APNS_TEAM_ID = os.getenv("IOS_APNS_TEAM_ID", IOS_APP_TEAM_ID)
IOS_APNS_BUNDLE_ID = os.getenv("IOS_APNS_BUNDLE_ID", IOS_APP_BUNDLE_ID)
IOS_APNS_PRIVATE_KEY = os.getenv("IOS_APNS_PRIVATE_KEY", "")
IOS_APNS_PRIVATE_KEY_BASE64 = os.getenv("IOS_APNS_PRIVATE_KEY_BASE64", "")
IOS_APNS_USE_SANDBOX = _env_bool("IOS_APNS_USE_SANDBOX", default=False)

# Native Android Play Store wrapper settings
ANDROID_APP_PACKAGE = os.getenv("ANDROID_APP_PACKAGE", "lu.crush.app")
ANDROID_APP_NAME = os.getenv("ANDROID_APP_NAME", "Crush.lu")
ANDROID_APP_VERSION = os.getenv("ANDROID_APP_VERSION", "1.0.0")
ANDROID_APP_BUILD = os.getenv("ANDROID_APP_BUILD", "1")
ANDROID_APP_MIN_SUPPORTED_VERSION = os.getenv(
    "ANDROID_APP_MIN_SUPPORTED_VERSION", "1.0.0"
)
ANDROID_PLAY_STORE_URL = os.getenv("ANDROID_PLAY_STORE_URL", "")
ANDROID_NATIVE_COMMERCE_ENABLED = _env_bool(
    "ANDROID_NATIVE_COMMERCE_ENABLED", default=False
)
ANDROID_AUTH_REDIRECT_URIS = [
    uri.strip()
    for uri in os.getenv("ANDROID_AUTH_REDIRECT_URIS", "crushlu://auth").split(",")
    if uri.strip()
]
# The CRUSH_ENV=local Android flavor calls back on its own scheme so the
# emulator build can be installed alongside the production app. Auto-allow it
# off-Azure only (WEBSITE_HOSTNAME is how manage.py detects Azure) — on
# production/staging the env allowlist above stays authoritative.
if not os.getenv("WEBSITE_HOSTNAME"):
    if "crushlulocal://auth" not in ANDROID_AUTH_REDIRECT_URIS:
        ANDROID_AUTH_REDIRECT_URIS.append("crushlulocal://auth")
ANDROID_APP_SHA256_CERT_FINGERPRINTS = [
    fingerprint.strip()
    for fingerprint in os.getenv("ANDROID_APP_SHA256_CERT_FINGERPRINTS", "").split(",")
    if fingerprint.strip()
]

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
WALLET_GOOGLE_EVENT_TICKET_ENABLED = _env_bool(
    "WALLET_GOOGLE_EVENT_TICKET_ENABLED", default=True
)
# How many Google Wallet member objects one event-level refresh may PATCH
# synchronously. Same shape as PASSKIT_BULK_PUSH_LIMIT below, but a stricter
# meaning: an Apple pass past its cap still updates on Wallet's next poll,
# whereas Google objects only ever change when we PATCH them — anything skipped
# here stays stale until that profile is saved again. Sized to cover a full
# event rather than a typical one, and every skip is logged at WARNING.
WALLET_GOOGLE_BULK_UPDATE_LIMIT = int(
    os.getenv("WALLET_GOOGLE_BULK_UPDATE_LIMIT", "50")
)
# Wall-clock budget for that fan-out. The count above does not bound the work:
# each pass is an HTTPS PATCH, and the whole batch runs inline in the admin
# request via on_commit (there is no background worker). Requests are clamped
# to whatever is left of this, so a slow Google API cannot hold the request for
# the per-request 30s ceiling times the number of attendees.
WALLET_GOOGLE_BULK_UPDATE_BUDGET_SECONDS = float(
    os.getenv("WALLET_GOOGLE_BULK_UPDATE_BUDGET_SECONDS", "10")
)

# Pre-screening questionnaire (Crush.lu). Off by default; enable in production
# after all Phases have shipped and the Coach-facing rollout is ready.
PRE_SCREENING_ENABLED = _env_bool("PRE_SCREENING_ENABLED", default=False)

# Crush Connect (Crush.lu) — global launch flag. When False, all Crush Connect
# routes fall back to the waitlist teaser; when True, eligible members can use
# Connect Week and the catalogue.
CRUSH_CONNECT_LAUNCHED = _env_bool("CRUSH_CONNECT_LAUNCHED", default=False)

# Crush Connect BETA phase (candidate-open). When True (and LAUNCHED still False),
# the "In the Mix" track opens to verified members. Connect Week separately
# admits event-verified members and hand-picked testers; Premium purchase can
# remain funnelled to the waitlist. Ignored once LAUNCHED is True.
CRUSH_CONNECT_CANDIDATE_OPEN = _env_bool("CRUSH_CONNECT_CANDIDATE_OPEN", default=False)

# During the "4 weeks / 4 matches" Crush Connect beta, funnel the Go-Premium flow
# into the beta waitlist instead of the coach directory. Members with an in-flight
# (pending) premium request are exempt so they can still manage it. Set False to
# fully restore the old premium flow after the beta — no code change needed.
PREMIUM_REDIRECTS_TO_BETA = _env_bool("PREMIUM_REDIRECTS_TO_BETA", default=True)

# Crush Cache (Crush.lu) — GPS + QR scavenger hunt played at events. When False,
# all cache routes 404 and the event page hides the hunt button.
CRUSH_CACHE_ENABLED = _env_bool("CRUSH_CACHE_ENABLED", default=False)

# Crush Connect Event Lobby (Crush.lu) — the live "I'd like to meet you" photo
# grid for checked-in Connect members (spec 2026-07-17). Global rollout flag
# (§17 Phase A): it controls launch and never becomes a per-event switch. The
# lobby additionally requires the Connect launch phase (candidate_access_open).
CRUSH_EVENT_LOBBY_ENABLED = _env_bool("CRUSH_EVENT_LOBBY_ENABLED", default=False)

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
        "SCOPE": ["openid", "profile", "email", "phone"],
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
SOCIALACCOUNT_EMAIL_VERIFIED_PROVIDERS = [
    "google",
    "facebook",
    "microsoft",
    "apple",
    "luxid",
]


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

    if sys.stdout.encoding and "utf" in sys.stdout.encoding.lower():
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

# ============================================================================
# WhatsApp Cloud API (Meta Graph API)
# ============================================================================
# Server-side credentials for /hub/whatsapp/* endpoints. The access token
# never reaches the browser. Webhook signature is verified with the app secret.
META_WHATSAPP_ACCESS_TOKEN = os.environ.get("META_WHATSAPP_ACCESS_TOKEN", "")
META_PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID", "")
META_WABA_ID = os.environ.get("META_WABA_ID", "")
META_WHATSAPP_APP_SECRET = os.environ.get("META_WHATSAPP_APP_SECRET", "")
META_WHATSAPP_VERIFY_TOKEN = os.environ.get("META_WHATSAPP_VERIFY_TOKEN", "")

# Hub marketing planner integrations. Credentials remain server-side; missing
# values fail closed with a clear 503 instead of simulating a successful post.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
ANTHROPIC_TIMEOUT_SECONDS = int(os.environ.get("ANTHROPIC_TIMEOUT_SECONDS", "60"))
BUFFER_API_KEY = os.environ.get(
    "BUFFER_API_KEY", os.environ.get("BUFFER_ACCESS_TOKEN", "")
)
BUFFER_ORGANIZATION_ID = os.environ.get("BUFFER_ORGANIZATION_ID", "")
BUFFER_TIMEOUT_SECONDS = int(os.environ.get("BUFFER_TIMEOUT_SECONDS", "20"))
BACKEND_BASE_URL = os.environ.get("BACKEND_BASE_URL", "http://localhost:8000")

# WhatsApp phone-verification (OTP) — sends an approved Authentication-category
# template. The template is named "<prefix>_phone_verification" and exists once
# per language (en/de/fr) under that single name, so the send call passes the
# user's language code. The prefix differs per environment (crush_staging vs
# crush) so staging and production use separate templates/WABAs.
WHATSAPP_OTP_TEMPLATE_PREFIX = os.environ.get(
    "WHATSAPP_OTP_TEMPLATE_PREFIX", "crush_staging"
)
# Minutes an OTP stays valid — keep in sync with the template's expiration
# warning and message validity period configured in WhatsApp Manager (3 min).
WHATSAPP_OTP_TTL_MINUTES = int(os.environ.get("WHATSAPP_OTP_TTL_MINUTES", "3"))

# ============================================================================
# SUMUP ONLINE PAYMENTS
# ============================================================================
# Credentials for crush_lu/services/sumup.py (event fees + Connect Premium).
# The secret key stays server-side — the Card Widget in the browser only ever
# gets a checkout id, never this key.
# Read raw here on purpose. Quote/whitespace normalisation lives in exactly one
# place — crush_lu.services.sumup.clean_credential — because the service also
# has to clean credentials passed straight to its constructor, and settings.py
# cannot import from crush_lu (the service imports django.conf.settings, so it
# would be circular). Two copies of that logic drifted once already.
SUMUP_API_KEY = os.environ.get("SUMUP_API_KEY", "")
SUMUP_MERCHANT_CODE = os.environ.get("SUMUP_MERCHANT_CODE", "")
# Only used when no merchant code is set — SumUp then resolves the payee from
# the merchant account e-mail instead.
SUMUP_PAY_TO_EMAIL = os.environ.get("SUMUP_PAY_TO_EMAIL", "")
# Monthly fee for a Crush Connect Premium membership, in EUR.
SUMUP_PREMIUM_MONTHLY_FEE = os.environ.get("SUMUP_PREMIUM_MONTHLY_FEE", "10.00").strip()

# ============================================================================
# CRUSH CREDIT
# ============================================================================
# Store credit, which replaced cash refunds as the default remedy for a
# cancelled event registration (policy v2, approved 2026-08-13). See
# crush_lu/services/credits.py. Every one of these is a published promise, so
# changing one changes the Terms — they are settings so the business can move
# them, not so they can be tuned casually.
#
# How long an issued credit stays spendable. The member-facing §7.3 says
# "valid for 6 months from the date it is issued", counted in calendar months.
CRUSH_CREDIT_EXPIRY_MONTHS = int(os.environ.get("CRUSH_CREDIT_EXPIRY_MONTHS", "6"))
# Inside this many hours of the start, cancelling earns nothing up front.
CRUSH_CREDIT_LATE_CANCELLATION_HOURS = int(
    os.environ.get("CRUSH_CREDIT_LATE_CANCELLATION_HOURS", "48")
)
# The resale clause: what a late canceller gets back if their seat is refilled
# from the waitlist before the event starts.
CRUSH_CREDIT_RESALE_SHARE_PERCENT = int(
    os.environ.get("CRUSH_CREDIT_RESALE_SHARE_PERCENT", "50")
)
# What a paid seat is worth in credit when CRUSH.LU cancels the event, in
# cents. Deliberately above the €15.50 face value: the premium costs one
# marginal seat and makes credit the obviously better choice for the member,
# which is how the cash is kept without refusing anyone who asks for it.
CRUSH_CREDIT_EVENT_CANCELLED_PREMIUM_CENTS = int(
    os.environ.get("CRUSH_CREDIT_EVENT_CANCELLED_PREMIUM_CENTS", "2000")
)

# Organiser cancellation sends run inside the admin request because production
# has no asynchronous task worker. Keep each invocation bounded; rerunning the
# same idempotent admin action resumes registrations whose email marker is empty.
CRUSH_CREDIT_CANCELLATION_EMAIL_LIMIT = int(
    os.environ.get("CRUSH_CREDIT_CANCELLATION_EMAIL_LIMIT", "50")
)
# How many days before expires_at the one-time "your credit expires soon"
# email goes out (send_crush_credit_expiry_reminders). 14 is a judgment call,
# not a published promise like the figures above — flag for Tom to confirm.
CRUSH_CREDIT_EXPIRY_REMINDER_DAYS = int(
    os.environ.get("CRUSH_CREDIT_EXPIRY_REMINDER_DAYS", "14")
)

# The "Refund via SumUp" admin action calls SumUp inline, one row at a time —
# same bound as PaymentTransactionAdmin.recheck_with_sumup, and for the same
# reason (no task worker). Read per call, not at import, so override_settings
# reaches them.
SUMUP_ADMIN_REFUND_LIMIT = int(os.environ.get("SUMUP_ADMIN_REFUND_LIMIT", "20"))
SUMUP_ADMIN_REFUND_BUDGET_SECONDS = float(
    os.environ.get("SUMUP_ADMIN_REFUND_BUDGET_SECONDS", "60")
)

# ============================================================================
# ECHO.LU EVENT SYNC
# ============================================================================
# Publishes public Crush.lu events to echo.lu, Luxembourg's national events
# portal, via its partner API (https://api.echo.lu/). See
# crush_lu/services/echo_lu.py and docs/integrations/echo-lu-sync.md.
#
# The api-key is issued per ORGANISATION from the echo.lu organiser back
# office, so the key alone identifies who the experience is published as —
# there is no separate organisation id to send.
ECHO_LU_API_KEY = os.environ.get("ECHO_LU_API_KEY", "")
# There is NO sandbox. An earlier note here promised one at test-api.echo.lu
# with its own keys; the published docs name exactly one base URL and never
# mention a test environment, and that hostname serves a byte-identical
# documentation page from the same address as api.echo.lu. Every write lands on
# the live national portal — which is why the first run is a single event by
# id, not a staging walk. See docs/integrations/echo-lu-sync.md.
ECHO_LU_API_BASE_URL = os.environ.get(
    "ECHO_LU_API_BASE_URL", "https://api.echo.lu/v1"
).strip()
# What a newly created experience asks for. echo.lu moderates listings:
# "draft" parks it in the organiser back office and does nothing else,
# "pending" saves it and submits it for validation. Create-only — see
# services.echo_lu._write_experience for why it must never ride a PUT.
ECHO_LU_CREATE_STATUS = os.environ.get("ECHO_LU_CREATE_STATUS", "pending").strip()
# Master switch, default OFF. Every sync entry point is gated on this, so an
# environment that merely inherits the key (a restored production DB on
# staging, a local shell) cannot mutate live echo.lu listings by accident.
ECHO_LU_SYNC_ENABLED = _env_bool("ECHO_LU_SYNC_ENABLED", False)
ECHO_LU_TIMEOUT_SECONDS = int(os.environ.get("ECHO_LU_TIMEOUT_SECONDS", "20"))
# What the save-signal path is allowed to spend. DJANGO_TASKS_BACKEND is unset
# in production, so TASKS uses ImmediateBackend and .enqueue() runs the sync
# inside the request that saved the event — the same trap
# PASSKIT_BULK_PUSH_BUDGET_SECONDS exists for. This timeout, with retries off,
# is what stops an unreachable echo.lu from holding an admin save open; the
# hourly sweep picks up whatever the fast path drops.
ECHO_LU_SIGNAL_TIMEOUT_SECONDS = float(
    os.environ.get("ECHO_LU_SIGNAL_TIMEOUT_SECONDS", "5")
)
# Wall-clock ceiling on one reconciliation pass. The EchoLuSync Function gives
# the endpoint 110s, and an unbounded sweep can exceed that on a handful of
# slow events — so the pass stops here and the next hour resumes where it left
# off, rather than being killed mid-event.
ECHO_LU_SWEEP_BUDGET_SECONDS = float(
    os.environ.get("ECHO_LU_SWEEP_BUDGET_SECONDS", "90")
)
# Wall-clock ceiling on echo.lu work done inside an admin request — the bulk
# publish/unpublish/cancel actions and the manual sync action. Both run inline
# (ImmediateBackend again), and the admin page size is not a bound: a slow
# echo.lu turns a two-dozen-event bulk publish into a lost response for work
# the database already committed. Whatever does not fit is left to the sweep.
ECHO_LU_ADMIN_BUDGET_SECONDS = float(
    os.environ.get("ECHO_LU_ADMIN_BUDGET_SECONDS", "30")
)

# Public organiser contact echoed onto every experience. echo.lu shows these on
# the listing page, so they must be addresses we actually monitor — not the
# noreply mailbox.
ECHO_LU_CONTACT_NAME = os.environ.get("ECHO_LU_CONTACT_NAME", "Crush.lu")
ECHO_LU_CONTACT_COMPANY = os.environ.get("ECHO_LU_CONTACT_COMPANY", "Crush.lu")
ECHO_LU_CONTACT_EMAIL = os.environ.get("ECHO_LU_CONTACT_EMAIL", "hello@crush.lu")
ECHO_LU_CONTACT_PHONE = os.environ.get("ECHO_LU_CONTACT_PHONE", "")
ECHO_LU_CONTACT_WEBSITE = os.environ.get("ECHO_LU_CONTACT_WEBSITE", "https://crush.lu")

# Taxonomy ids. echo.lu validates categories/audiences/formats/environments
# against ITS OWN vocabularies and rejects the whole experience on an unknown
# value — and it requires all four, so "blank" is NOT a safe default the way an
# earlier note here claimed. POSTing an experience without them answers 400
# with "Missing categories" and so on for each.
#
# The defaults below are real ids, read off the live API on 2026-08-08 with our
# own key (`manage.py echo_taxonomy` prints the full lists: 190 categories, 10
# audiences, 16 formats, 3 environments). They are a starting point chosen for
# a dating/social meetup, not a considered editorial decision — override per
# environment once somebody has looked at how the listings read.
ECHO_LU_DEFAULT_CATEGORIES = os.environ.get("ECHO_LU_DEFAULT_CATEGORIES", "nightlife")
# `adults` rather than `everyone`: the platform is 18+.
ECHO_LU_DEFAULT_AUDIENCES = os.environ.get("ECHO_LU_DEFAULT_AUDIENCES", "adults")
ECHO_LU_DEFAULT_FORMATS = os.environ.get("ECHO_LU_DEFAULT_FORMATS", "networking")
# Note the real id is "indoors", not "indoor".
ECHO_LU_DEFAULT_ENVIRONMENTS = os.environ.get("ECHO_LU_DEFAULT_ENVIRONMENTS", "indoors")
# Required too, and an event with no languages set cannot simply omit them.
ECHO_LU_DEFAULT_LANGUAGES = os.environ.get("ECHO_LU_DEFAULT_LANGUAGES", "en,fr")
ECHO_LU_DEFAULT_TAGS = os.environ.get("ECHO_LU_DEFAULT_TAGS", "crush.lu")
# echo.lu rejects the whole experience over an oversized banner, not just the
# banner — "The max image size (2048Kb) is exceeded: 2317Kb" — so an event
# whose own image is over this sends the fallback instead of failing.
ECHO_LU_MAX_PICTURE_BYTES = int(
    os.environ.get("ECHO_LU_MAX_PICTURE_BYTES", str(2048 * 1024))
)
# How many 100-row pages of echo.lu's venue registry to read before giving up
# and registering a new venue. The registry is 5,000+ rows with no text search
# and **each page measures 3-4 seconds**, so do the arithmetic before raising
# this: a call can make TWO passes (commune-narrowed, then the full registry),
# so N pages is up to 2N requests — at the default 3 that is ~20s worst case,
# and at 5 it would be 30-40s, i.e. the whole ECHO_LU_ADMIN_BUDGET_SECONDS on a
# single event. The admin action only checks its deadline *between* events, so
# one slow venue search cannot be interrupted once started.
#
# Hence a deliberately low default. A miss inside the cap registers a new venue
# — untidy, not broken — while a scan that outlives the request is broken. Each
# venue is searched once and then cached in EchoVenue, so this cost is paid per
# venue, not per event. Raise it (ECHO_LU_VENUE_SEARCH_PAGES=20) when running
# `sync_events_to_echo` from a shell, where the budget is generous and a
# thorough search is worth the wait.
ECHO_LU_VENUE_SEARCH_PAGES = int(os.environ.get("ECHO_LU_VENUE_SEARCH_PAGES", "3"))
# Optional override for the picture sent when an event has no image of its own.
# Left EMPTY on purpose: the fallback is resolved at call time in
# services.echo_lu, from SOCIAL_PREVIEW_IMAGE_URL. Binding it to that value
# *here* would snapshot whatever the URL happens to be at this line, and it is
# reassigned twice afterwards — once below for Azure blob storage, and again in
# production.py for the CDN domain — so the two would silently diverge on every
# real deployment. That is the exact drift this setting was added to prevent.
ECHO_LU_FALLBACK_IMAGE = os.environ.get("ECHO_LU_FALLBACK_IMAGE", "")
# Optional JSON object mapping MeetupEvent.event_type -> list of category
# slugs, layered on top of ECHO_LU_DEFAULT_CATEGORIES. Example:
#   {"speed_dating": ["rencontres"], "quiz_night": ["jeux"]}
ECHO_LU_CATEGORY_MAP = os.environ.get("ECHO_LU_CATEGORY_MAP", "")
# Optional promo video attached to every synced listing.
#
# echo.lu's `videos` field is an EMBED, not a file it re-hosts the way
# `pictures` are — it takes {"type": youtube|vimeo|other, "url": ...}.
#
# ⚠️ A self-hosted .mp4 does NOT work, and the API will not tell you so. Probed
# end to end on 2026-08-15: `type: "other"` with a direct .mp4 URL on
# cdn.crush.lu is accepted (201), stored, and read back intact — and then the
# listing renders NOTHING. The organiser preview of that same experience
# carried a real `youtube.com/embed/...` iframe for the YouTube entry beside it
# and no <video> element at all for ours. Same accept-then-ignore behaviour as
# `address.commune`. So the URL here must point at YouTube or Vimeo.
#
# Empty URL means no video is sent — and, because the key always travels,
# clearing this setting actively retracts the video from listings that already
# carry one. `videos: []` is accepted (200); see services.echo_lu.
ECHO_LU_VIDEO_URL = os.environ.get("ECHO_LU_VIDEO_URL", "")
# One of youtube / vimeo / other. Defaults to youtube because that is the only
# value observed to actually render; `other` is accepted by the API and then
# ignored by the frontend, which is the worst of both worlds — it looks like it
# worked. An unknown value is dropped locally with a warning rather than sent:
# echo.lu answers an unrecognised type with `400 Malformed videos data` and
# refuses the WHOLE experience, so a typo here would stop every event syncing.
ECHO_LU_VIDEO_TYPE = os.environ.get("ECHO_LU_VIDEO_TYPE", "youtube")
# Optional caption shown with the video. echo.lu stores this; it silently drops
# the `cover` field, so there is no poster-image setting to go with it.
ECHO_LU_VIDEO_DESCRIPTION = os.environ.get("ECHO_LU_VIDEO_DESCRIPTION", "")

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

# SWA hostnames vary by region and SWA also injects a numeric segment, so the
# real shape is e.g. `delightful-water-07d8c6e10-3.centralus.7.azurestaticapps.net`
# for previews (and `delightful-water-07d8c6e10.centralus.7.azurestaticapps.net`
# for production-style). The middle is one or more dotted segments, so this
# regex allows zero or more `.<word-or-hyphen>` segments before
# `.azurestaticapps.net`. Microsoft owns azurestaticapps.net so the suffix
# anchor is sufficient — no third party can register under it.
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://delightful-water-07d8c6e10(-\d+)?(\.[\w-]+)*\.azurestaticapps\.net$",
]

# Hub SPA session→JWT exchange. Exact (scheme, netloc, path) match — never
# prefix or startswith — to prevent open-redirect token exfiltration.
SPA_CALLBACK_ALLOWED_RETURN_URLS = {
    ("https", "hub.crush.lu", "/auth/callback"),
}
if DEBUG:
    SPA_CALLBACK_ALLOWED_RETURN_URLS |= {
        ("http", "localhost:3000", "/auth/callback"),
        ("http", "127.0.0.1:3000", "/auth/callback"),
    }


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

# KNOWN GAP (Task 5.4b, docs/ops/secret-key-rotation.md): djangorestframework
# -simplejwt's TokenBackend holds a single signing/verifying key with no
# fallback-list support (unlike django.core.signing above), so SIGNING_KEY is
# captured once at settings-module import time and does NOT read
# SECRET_KEY_FALLBACKS. Rotating SECRET_KEY invalidates every outstanding Hub
# SSO access/refresh token immediately — the Hub SPA's next API call 401s and
# it re-runs the session→JWT exchange (azureproject/views_spa_auth.py) using
# the staff member's still-valid crush.lu session (session auth DOES honour
# SECRET_KEY_FALLBACKS, so that re-bounce succeeds without a fresh login).
# Net effect: a one-time silent re-auth for Hub staff, not an outage.
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
    CRUSH_MEDIA_BASE_URL = (
        f"https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net/crush-lu-media"
    )
    POWERUP_MEDIA_BASE_URL = (
        f"https://{AZURE_ACCOUNT_NAME}.blob.core.windows.net/powerup-media"
    )

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
# min_hours sets the cadence (a stage becomes eligible this many hours after
# signup); the wide max_hours gives ~6 days of daily runs to DRAIN a backlog
# so a signup spike larger than one run's capacity is served over the next
# few days rather than aged out and permanently dropped. get_users_needing_
# reminder orders oldest-first, so under a spike the users closest to their
# max_hours are served first. The max_hours still bounds staleness, so first
# enabling this against a historical backlog only contacts recent signups,
# not months-old abandoned ones. At most one send per stage: the query
# excludes users who already have that stage's ProfileReminder row.
#
# Each later stage's max_hours must leave slack for a backlog-recovered user to
# still reach it: get_users_needing_reminder gates the next stage on the prior
# reminder's sent_at + the cadence gap (24h->72h = 48h, 72h->7d = 96h), so a
# user whose prior stage fired at the very end of ITS window only becomes
# eligible gap-hours later and needs one more daily run to be caught. So each
# later max_hours >= prior stage's max_hours + gap + 24h (one daily cycle):
#   72h: 168 + 48 + 24 = 240;  7d: 240 + 96 + 24 = 360.
# Otherwise the cadence gate would push a delayed user past this ceiling before
# any run selects them, stranding the sequence. (Codex P2)
PROFILE_REMINDER_TIMING = {
    "24h": {
        "min_hours": 24,
        "max_hours": 168,  # eligible 1-7 days after signup (6-day drain window)
    },
    "72h": {
        "min_hours": 72,
        "max_hours": 240,  # 3-10 days (leaves slack past the 48h cadence gap)
    },
    "7d": {
        "min_hours": 168,  # 7 days
        "max_hours": 360,  # 7-15 days (leaves slack past the 96h cadence gap)
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
        # SumUp card widget SDK — loaded by crush_lu/templates/crush_lu/
        # payments/sumup_widget.html. Absent until 2026-08-02, which was
        # invisible only because this policy is report-only; enforcing it
        # without this line takes payments down.
        "https://gateway.sumup.com",
    ],
    # Workers: qr-scanner's decode worker is spawned from a blob: URL
    # (vendored under crush_lu/static/crush_lu/vendor/qr-scanner/). Without
    # blob: here, enforcing this policy would silently disable QR decoding on
    # browsers lacking BarcodeDetector — notably iOS Safari at the event door.
    "worker-src": [CSP.SELF, "blob:"],
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
        # SumUp — the widget calls the gateway directly from the browser
        "https://gateway.sumup.com",
        "https://api.sumup.com",
        # Local "Server for RawBT" print bridge (crush_lu/services/ticket_printer.py,
        # triggerRawBtPrint in alpine-components.js) — the coach's own device only,
        # deliberately scoped to loopback print ports.
        "ws://127.0.0.1:40213",
        "ws://127.0.0.1:*",
        "ws://localhost:*",
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
        # Quiz Night media embeds (external video/audio stimuli). The server
        # normalizes watch URLs to these canonical embed hosts — see
        # normalize_embed_url() in crush_lu/models/quiz.py.
        "https://www.youtube-nocookie.com",
        "https://player.vimeo.com",
        "https://open.spotify.com",
        "https://w.soundcloud.com",
        # SumUp card widget: the card fields and the 3DS step are iframed.
        "https://gateway.sumup.com",
        # 3DS runs through ACI Worldwide, SumUp's payment backend, NOT through
        # sumup.com. Measured from a real sandbox payment on 2026-08-02, which
        # reported framing violations for `test.ppipe.net` and `test.oppwa.com`
        # against this policy. Apex and wildcard are both listed because a CSP
        # wildcard does not match the bare domain, and the live hosts drop the
        # `test.` prefix.
        "https://oppwa.com",
        "https://*.oppwa.com",
        "https://ppipe.net",
        "https://*.ppipe.net",
        # ⚠️ Still not a guarantee for production 3DS. The sandbox exercises
        # ACI's own demo connector; a live challenge can hand off to the card
        # ISSUER's ACS domain, which is per-bank and cannot be enumerated here.
        # "form-action" is also still CSP.SELF below. Walk a real 3DS payment
        # with the policy enforced before switching SECURE_CSP_REPORT_ONLY over
        # to CONTENT_SECURITY_POLICY, and read the report endpoint afterwards.
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
# The PassKit webServiceURL ROOT. Apple appends its own "/v1/..." protocol
# paths to this (e.g. /wallet + /v1/devices/... -> /wallet/v1/devices/...),
# so this must be the unversioned root, not /wallet/v1 — otherwise every
# PassKit web-service request 404s with /wallet/v1/v1/...
PASSKIT_WEB_SERVICE_BASE_PATH = os.getenv("PASSKIT_WEB_SERVICE_BASE_PATH", "/wallet")
PASSKIT_AUTH_TOKEN = os.getenv("PASSKIT_AUTH_TOKEN")
PASSKIT_AUTH_TOKEN_RESOLVER = os.getenv("PASSKIT_AUTH_TOKEN_RESOLVER")
# How many APNs pushes an event-level ticket refresh may send synchronously.
# on_commit runs inside the admin request and there is no background worker
# (DJANGO_TASKS_BACKEND is unset in production, so TASKS uses ImmediateBackend),
# while each push can wait up to 10s per device. Passes beyond this cap still
# update — their tag is advanced in the same bulk query — just on Wallet's next
# periodic poll rather than instantly.
PASSKIT_BULK_PUSH_LIMIT = int(os.getenv("PASSKIT_BULK_PUSH_LIMIT", "20"))
# The count above is not itself a bound on the work: one serial fans out to
# every device that registered it, each an HTTP call with a 10s timeout. This
# wall-clock budget is what actually stops a slow or unreachable APNs from
# holding the admin request open after the row has already committed.
PASSKIT_BULK_PUSH_BUDGET_SECONDS = float(
    os.getenv("PASSKIT_BULK_PUSH_BUDGET_SECONDS", "5")
)
# How many devices ONE member notification may push to synchronously, per
# channel. Same trap as PASSKIT_BULK_PUSH_LIMIT above: DJANGO_TASKS_BACKEND is
# unset in production, so NotificationService.notify fans out inside the
# request that triggered it. The caller that matters is the door — a coach
# rejecting a verification has already committed the decision by the time the
# notify runs, so an overrun costs them a failed response on a change that
# stuck, and the retry 409s. Typical members have 1-3 subscriptions; this cap
# is sized to be generous for a real person and to bound an accumulated pile of
# stale endpoints, not to ration normal delivery.
CRUSH_PUSH_FANOUT_LIMIT = int(os.getenv("CRUSH_PUSH_FANOUT_LIMIT", "10"))
# The count above is not itself a bound on the work: each send is an HTTP call
# to a third-party push service that can hang for its full timeout, so 10 dead
# endpoints is 10 × that timeout. This wall-clock budget is what actually keeps
# the fan-out inside the 120s gunicorn window. Applied per channel (web / iOS /
# Android), so a member subscribed on all three has a worst case of 3× this
# before email is attempted. Anything skipped is logged, never dropped
# silently — the in-app bell row is still written, so the member sees it there.
CRUSH_PUSH_FANOUT_BUDGET_SECONDS = float(
    os.getenv("CRUSH_PUSH_FANOUT_BUDGET_SECONDS", "10")
)
# Per-send ceiling for web push. The budget above only bounds the loop if each
# send inside it is bounded too — pywebpush defaults to timeout=None, so a
# single unresponsive endpoint could otherwise hang past the budget by an
# arbitrary amount. Clamped to whatever is left of the budget at call time.
# The native channels already carry their own (httpx 10s / requests 10s).
CRUSH_PUSH_SEND_TIMEOUT_SECONDS = float(
    os.getenv("CRUSH_PUSH_SEND_TIMEOUT_SECONDS", "10")
)
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
