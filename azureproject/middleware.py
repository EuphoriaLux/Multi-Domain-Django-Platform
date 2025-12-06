# azureproject/middleware.py
"""
Custom middleware for multi-domain Django application.

This module contains middleware for:
- Health check bypass (Azure App Service)
- Domain-based URL routing
- Admin language forcing
"""
import logging
from django.utils import translation
from django.http import HttpResponse

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

        elif host.endswith('.azurewebsites.net'):
            # Azure App Service hostname
            prod_config = DOMAINS[PRODUCTION_DEFAULT]
            request.urlconf = prod_config['urlconf']
            logger.debug(f"DomainURLRoutingMiddleware: Azure hostname {host} -> {prod_config['urlconf']}")

        else:
            # Fallback to production default
            prod_config = DOMAINS[PRODUCTION_DEFAULT]
            request.urlconf = prod_config['urlconf']
            logger.warning(f"DomainURLRoutingMiddleware: Unknown host {host} -> {prod_config['urlconf']} (fallback)")

        return self.get_response(request)
