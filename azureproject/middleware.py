# azureproject/middleware.py
"""
Custom middleware for multi-domain Django application.

This module contains middleware for:
- Health check bypass (Azure App Service)
- Safe site detection (auto-creates missing Site objects)
- Domain-based URL routing
- Admin language forcing
- Custom CSRF failure handling
"""
import logging
from django.utils import translation
from django.http import HttpResponse, HttpResponseForbidden
from django.contrib.sites.models import Site

from .domains import (
    DOMAINS,
    DEV_HOSTS,
    DEV_DEFAULT,
    PRODUCTION_DEFAULT,
    get_domain_config,
)

logger = logging.getLogger(__name__)


class LoginPostDebugMiddleware:
    """
    Debug middleware to log ALL POST requests to /login/ BEFORE CSRF processing.

    This helps diagnose if 403 errors come from CSRF middleware or elsewhere.
    MUST be placed BEFORE CsrfViewMiddleware in MIDDLEWARE list.

    SECURITY: Only active in DEBUG mode to prevent information disclosure in production.
    """
    def __init__(self, get_response):
        self.get_response = get_response
        # Cache DEBUG setting at init time
        from django.conf import settings
        self.debug_enabled = settings.DEBUG

    def __call__(self, request):
        # Skip all logging in production (DEBUG=False)
        if not self.debug_enabled:
            return self.get_response(request)

        # Log ALL POSTs to /login/ before any processing
        if request.method == 'POST' and request.path == '/login/':
            logger.warning(
                f"[PRE-CSRF-DEBUG] POST /login/ ENTERING - "
                f"has_csrf_cookie={'csrftoken' in request.COOKIES}, "
                f"origin={request.META.get('HTTP_ORIGIN', 'None')}, "
                f"host={request.get_host()}"
            )

        response = self.get_response(request)

        # Log response AFTER processing
        if request.method == 'POST' and request.path == '/login/':
            logger.warning(
                f"[PRE-CSRF-DEBUG] POST /login/ RESPONSE - "
                f"status={response.status_code}, "
                f"content_length={len(response.content) if hasattr(response, 'content') else 'N/A'}"
            )

        return response


def csrf_failure_view(request, reason=""):
    """
    Custom CSRF failure view with detailed logging.

    This helps diagnose CSRF issues by logging:
    - Request path and method
    - Origin and Referer headers
    - CSRF cookie presence
    - Session cookie presence
    - User agent
    """
    logger.error(
        f"[CSRF FAILURE] path={request.path}, method={request.method}, "
        f"reason={reason}, "
        f"origin={request.META.get('HTTP_ORIGIN', 'None')}, "
        f"referer={request.META.get('HTTP_REFERER', 'None')}, "
        f"has_csrf_cookie={'csrftoken' in request.COOKIES}, "
        f"has_session={'sessionid' in request.COOKIES}, "
        f"host={request.get_host()}, "
        f"user_agent={request.META.get('HTTP_USER_AGENT', 'None')[:100]}"
    )

    return HttpResponseForbidden(
        f"CSRF verification failed. Reason: {reason}. "
        f"Please refresh the page and try again.",
        content_type="text/plain"
    )


class HealthCheckMiddleware:
    """
    Bypass all middleware and Sites framework for health check endpoint.

    This prevents Azure health checks from failing due to missing Site objects.
    MUST be placed FIRST in MIDDLEWARE list.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Immediately return OK for health checks, bypassing all other middleware
        if request.path in ['/healthz/', '/healthz']:
            return HttpResponse("OK", status=200, content_type="text/plain")
        return self.get_response(request)


class AuthRateLimitMiddleware:
    """
    Rate limiting middleware for sensitive authentication endpoints.

    Applies rate limiting to:
    - Password reset requests (allauth's /accounts/password/reset/)

    This middleware runs early to block abuse before hitting the view.
    Uses DRF throttle classes for consistent rate limiting behavior.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only apply to POST requests on specific paths
        if request.method == 'POST':
            if '/accounts/password/reset/' in request.path:
                return self._check_password_reset_limit(request)

        return self.get_response(request)

    def _check_password_reset_limit(self, request):
        """Check rate limit for password reset requests."""
        try:
            from crush_lu.throttling import PasswordResetRateThrottle

            throttle = PasswordResetRateThrottle()
            if not throttle.allow_request(request, None):
                wait = throttle.wait()
                logger.warning(
                    f"[RATE-LIMIT] Password reset rate limit exceeded for IP: "
                    f"{throttle.get_ident(request)}"
                )
                return HttpResponse(
                    f'Too many password reset requests. Please try again in {int(wait / 60)} minutes.',
                    status=429,
                    content_type='text/plain',
                    headers={'Retry-After': str(int(wait))}
                )
        except ImportError:
            # crush_lu not available, skip rate limiting
            pass

        return self.get_response(request)


class SafeCurrentSiteMiddleware:
    """
    Safe replacement for django.contrib.sites.middleware.CurrentSiteMiddleware.

    Instead of raising Site.DoesNotExist, this middleware:
    1. Tries to find a matching Site by domain
    2. Auto-creates a Site entry if none exists
    3. Falls back to SITE_ID=1 if auto-creation fails

    This prevents 500 errors from Azure health checks and unknown hosts.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Set request.site safely
        request.site = self._get_site(request)
        return self.get_response(request)

    def _get_site(self, request):
        """Get or create Site for the current request host."""
        from django.conf import settings

        host = request.get_host().split(':')[0].lower()

        # Skip internal Azure IPs - use default site
        if host.startswith('169.254.'):
            return self._get_default_site()

        try:
            # Try exact domain match first
            return Site.objects.get(domain__iexact=host)
        except Site.DoesNotExist:
            pass

        # Check if this is a known domain from our config
        config = get_domain_config(host)
        if config:
            # Auto-create Site for known domains
            site, created = Site.objects.get_or_create(
                domain=host,
                defaults={'name': config.get('name', host.title())}
            )
            if created:
                logger.info(f"SafeCurrentSiteMiddleware: Auto-created Site for {host}")
            return site

        # For Azure hostnames, dev hosts, and unknown hosts - use default
        return self._get_default_site()

    def _get_default_site(self):
        """Get or create a default Site (ID=1)."""
        try:
            return Site.objects.get(pk=1)
        except Site.DoesNotExist:
            # Create default site
            site, _ = Site.objects.get_or_create(
                pk=1,
                defaults={'domain': 'entreprinder.lu', 'name': 'Entreprinder'}
            )
            logger.info("SafeCurrentSiteMiddleware: Created default Site (pk=1)")
            return site


class ForceAdminToEnglishMiddleware:
    """
    Force all admin interfaces to use English language.

    This ensures a consistent admin experience regardless of user's
    language preference settings. Covers standard Django admin and
    all custom platform admin panels.
    """
    ADMIN_PATHS = (
        '/admin/',
        '/crush-admin/',
        '/entreprinder-admin/',
        '/vinsdelux-admin/',
        '/power-admin/',
        '/delegation-admin/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if any(request.path.startswith(p) for p in self.ADMIN_PATHS):
            translation.activate('en')
            request.LANGUAGE_CODE = 'en'
        return self.get_response(request)


class AdminLanguagePrefixRedirectMiddleware:
    """
    Redirect language-prefixed admin URLs to language-neutral versions.

    E.g., /fr/admin/ -> /admin/, /de/crush-admin/ -> /crush-admin/

    This handles bookmarked URLs and incorrect navigation attempts.
    Admin panels are defined outside i18n_patterns() and must be accessed
    without language prefixes.
    """
    ADMIN_PATHS = ('admin/', 'crush-admin/', 'entreprinder-admin/', 'vinsdelux-admin/', 'power-admin/', 'delegation-admin/')
    LANG_CODES = ('en', 'de', 'fr')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.http import HttpResponsePermanentRedirect

        path = request.path
        # Check for pattern like /fr/admin/ or /de/crush-admin/
        for lang in self.LANG_CODES:
            for admin_path in self.ADMIN_PATHS:
                prefix = f'/{lang}/{admin_path}'
                if path.startswith(prefix):
                    # Redirect to language-neutral path
                    # Strip the language prefix: /fr/admin/... -> /admin/...
                    new_path = '/' + path[len(f'/{lang}/'):]
                    logger.debug(f"AdminLanguagePrefixRedirectMiddleware: Redirecting {path} -> {new_path}")
                    return HttpResponsePermanentRedirect(new_path)
        return self.get_response(request)


class OAuthCallbackProtectionMiddleware:
    """
    Prevent duplicate OAuth callback requests that cause "Third-Party Login Failure".

    On Android PWAs, the browser sometimes replays OAuth callback URLs which causes
    errors because OAuth authorization codes can only be used once. This middleware:

    1. Checks the OAuth state parameter against database (not session!)
    2. If state is already used (duplicate request), redirects gracefully
    3. Uses database storage which works across browser contexts (PWA -> system browser)
    4. Ensures OAuth statekit patch is applied for database-backed state storage
    5. For duplicate requests, passes state param to oauth_landing for DB-based auth recovery

    Must be placed AFTER SessionMiddleware but BEFORE any Allauth processing.

    IMPORTANT: This uses DATABASE storage instead of session storage because
    Android PWA opens OAuth in system browser (different session context).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Ensure OAuth patch is applied on any OAuth-related request
        if '/accounts/' in request.path:
            try:
                from crush_lu.oauth_statekit import ensure_patched
                ensure_patched()
            except ImportError:
                pass  # crush_lu not installed

        # Only check OAuth callback URLs
        if '/accounts/' in request.path and '/login/callback' in request.path:
            state = request.GET.get('state', '')

            # Diagnostic logging (DEBUG level - enable via Django logging config if needed)
            if state:
                logger.debug(
                    f"[OAUTH] Callback: state={state[:8]}... "
                    f"sec-fetch-mode={request.META.get('HTTP_SEC_FETCH_MODE', 'unknown')}"
                )

            if state:
                # Check if this state has already been used in DATABASE (not session!)
                # This works across browser contexts (PWA -> system browser)
                #
                # IMPORTANT: We use select_for_update() to create a database lock.
                # This ensures that if two requests arrive simultaneously:
                # - Request 1 acquires lock, checks used=False, proceeds
                # - Request 2 waits for lock, then sees used=True (set by Request 1's view)
                try:
                    from crush_lu.models import OAuthState
                    from django.db import transaction
                    from django.utils import timezone

                    with transaction.atomic():
                        # Lock the row to prevent race conditions
                        existing_state = OAuthState.objects.select_for_update(
                            nowait=False  # Wait for lock if another request has it
                        ).filter(state_id=state).first()

                        if existing_state and existing_state.used:
                            # Duplicate request - state already consumed by first callback
                            # Redirect to landing with state param for DB-based auth recovery
                            from django.http import HttpResponseRedirect

                            logger.info(
                                f"[OAUTH] Duplicate callback for state {state[:8]}... "
                                f"(user_id={existing_state.auth_user_id or 'pending'}), "
                                f"redirecting to landing"
                            )

                            return HttpResponseRedirect(f'/oauth/landing/?state={state}')

                        elif existing_state:
                            # First callback - update timestamp and proceed
                            existing_state.last_callback_at = timezone.now()
                            existing_state.save(update_fields=['last_callback_at'])
                            logger.debug(f"[OAUTH] First callback for state {state[:8]}..., proceeding")
                        else:
                            # State not in DB - may be session-based OAuth or expired
                            logger.debug(f"[OAUTH] State {state[:8]}... not in database")

                except ImportError:
                    logger.debug("[OAUTH-PROTECTION] crush_lu.models not available, skipping DB check")
                except Exception as e:
                    logger.error(f"[OAUTH-PROTECTION] Error checking state in database: {e}")

        response = self.get_response(request)
        return response


class DomainURLRoutingMiddleware:
    """
    Middleware that sets request.urlconf based on the HTTP host.

    Domain routing is configured in azureproject/domains.py.
    To test a different site locally, change DEV_DEFAULT in domains.py.

    Routing logic:
    1. Check if host matches a configured domain or its aliases
    2. For development hosts (localhost), use DEV_DEFAULT domain
    3. For Azure hostnames (*.azurewebsites.net), use PRODUCTION_DEFAULT
    4. Fallback to PRODUCTION_DEFAULT for unknown hosts
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0].lower()

        # Try to get domain config (checks both primary domains and aliases)
        config = get_domain_config(host)

        if config:
            request.urlconf = config['urlconf']
            logger.debug(f"DomainURLRoutingMiddleware: Routing to {config['urlconf']} for host: {host}")

        elif host in DEV_HOSTS:
            # Development: use configurable default
            dev_config = DOMAINS[DEV_DEFAULT]
            request.urlconf = dev_config['urlconf']
            logger.debug(f"DomainURLRoutingMiddleware: Dev host {host} -> {dev_config['urlconf']} (DEV_DEFAULT={DEV_DEFAULT})")

        elif host.endswith('.azurewebsites.net') or host.startswith('169.254.'):
            # Azure App Service hostname or Azure internal IP (health probes, load balancer)
            prod_config = DOMAINS[PRODUCTION_DEFAULT]
            request.urlconf = prod_config['urlconf']
            logger.debug(f"DomainURLRoutingMiddleware: Azure host {host} -> {prod_config['urlconf']}")

        else:
            # Fallback to production default
            prod_config = DOMAINS[PRODUCTION_DEFAULT]
            request.urlconf = prod_config['urlconf']
            logger.warning(f"DomainURLRoutingMiddleware: Unknown host {host} -> {prod_config['urlconf']} (fallback)")

        return self.get_response(request)
