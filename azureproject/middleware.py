# azureproject/middleware.py
"""
Custom middleware for multi-domain Django application.

This module contains middleware for:
- Health check bypass (Azure App Service)
- Safe site detection (auto-creates missing Site objects)
- Domain-based URL routing
- Admin language forcing
"""
import logging
from django.utils import translation
from django.http import HttpResponse
from django.contrib.sites.models import Site

from .domains import (
    DOMAINS,
    DEV_HOSTS,
    DEV_DEFAULT,
    PRODUCTION_DEFAULT,
    get_domain_config,
)

logger = logging.getLogger(__name__)


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
                defaults={'domain': 'powerup.lu', 'name': 'PowerUP'}
            )
            logger.info("SafeCurrentSiteMiddleware: Created default Site (pk=1)")
            return site


class ForceAdminToEnglishMiddleware:
    """
    Force Django admin interface to use English language.

    This ensures a consistent admin experience regardless of user's
    language preference settings.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/admin/'):
            translation.activate('en')
            request.LANGUAGE_CODE = 'en'
        response = self.get_response(request)
        return response


class CrushAllAuthRedirectMiddleware:
    """
    Redirect Allauth /accounts/* URLs to Crush.lu-specific URLs on crush.lu domain.

    This middleware intercepts Allauth's default account URLs and redirects them
    to Crush.lu's custom authentication pages to avoid template conflicts with
    other domains (entreprinder, powerup, vinsdelux).

    Must be placed AFTER DomainURLRoutingMiddleware so request.urlconf is set.
    """
    # Mapping of Allauth URLs to Crush.lu URLs
    ALLAUTH_TO_CRUSH_REDIRECTS = {
        '/accounts/login/': '/login/',
        '/accounts/signup/': '/signup/',
        '/accounts/logout/': '/logout/',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if this is crush.lu domain
        host = request.get_host().split(':')[0].lower()
        if host.startswith('www.'):
            host = host[4:]

        is_crush = host == 'crush.lu' or host == 'localhost'

        if is_crush:
            # Check if path matches any Allauth URL that should redirect
            path = request.path
            redirect_to = self.ALLAUTH_TO_CRUSH_REDIRECTS.get(path)

            if redirect_to:
                # Preserve query string (e.g., ?next=/dashboard/)
                query_string = request.META.get('QUERY_STRING', '')
                if query_string:
                    redirect_to = f"{redirect_to}?{query_string}"

                from django.http import HttpResponseRedirect
                logger.debug(f"CrushAllAuthRedirectMiddleware: Redirecting {path} -> {redirect_to}")
                return HttpResponseRedirect(redirect_to)

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

                    with transaction.atomic():
                        # Lock the row to prevent race conditions
                        existing_state = OAuthState.objects.select_for_update(
                            nowait=False  # Wait for lock if another request has it
                        ).filter(state_id=state).first()

                        if existing_state and existing_state.used:
                            # This is a duplicate request - state already consumed
                            logger.warning(
                                f"[OAUTH-PROTECTION] Duplicate callback detected! "
                                f"State {state[:8]}... already used. Redirecting gracefully."
                            )
                            from django.http import HttpResponseRedirect

                            # If user is authenticated, send to dashboard
                            if request.user.is_authenticated:
                                logger.warning("[OAUTH-PROTECTION] User authenticated, redirecting to dashboard")
                                return HttpResponseRedirect('/dashboard/')
                            else:
                                # Check if they just got authenticated (cookie might not be set yet)
                                # Give them a chance by redirecting to home
                                logger.warning("[OAUTH-PROTECTION] User not authenticated, redirecting to home")
                                return HttpResponseRedirect('/')

                        elif existing_state:
                            logger.warning(
                                f"[OAUTH-PROTECTION] State {state[:8]}... found, not yet used. "
                                f"Proceeding with OAuth callback."
                            )
                        else:
                            logger.warning(
                                f"[OAUTH-PROTECTION] State {state[:8]}... NOT found in database! "
                                f"May be session-based OAuth or expired state."
                            )

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
