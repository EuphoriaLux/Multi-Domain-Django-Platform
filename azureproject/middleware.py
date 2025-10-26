# azureproject/middleware.py
import logging
from django.utils import translation
from django.http import HttpResponse

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
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/admin/'):
            translation.activate('en')
            request.LANGUAGE_CODE = 'en'
        response = self.get_response(request)
        # Deactivate to avoid affecting other parts or subsequent requests if needed,
        # though for admin it might be fine to leave it activated for the request's lifetime.
        # translation.deactivate() 
        return response

class DomainURLRoutingMiddleware:
    """
    Middleware that sets request.urlconf based on the HTTP host.
    - For powerup.lu (or www.powerup.lu), use the powerup URL configuration.
    - For vinsdelux.com (or www.vinsdelux.com), use the vinsdelux URL configuration.
    - For crush.lu (or www.crush.lu), use the crush URL configuration.
    - Otherwise, fallback to powerup (default).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0].lower()  # Remove any port and lower-case the host
        logger.info(f"DomainURLRoutingMiddleware: Detected host: {host}") # Log the detected host

        if host in ['powerup.lu', 'www.powerup.lu']:
            request.urlconf = 'azureproject.urls_powerup'
            logger.info(f"DomainURLRoutingMiddleware: Routing to urls_powerup for host: {host}")
        elif host in ['vinsdelux.com', 'www.vinsdelux.com']:
            request.urlconf = 'azureproject.urls_vinsdelux'
            logger.info(f"DomainURLRoutingMiddleware: Routing to urls_vinsdelux for host: {host}")
        elif host in ['crush.lu', 'www.crush.lu']:
            # Route crush.lu domain to Crush URLs
            request.urlconf = 'azureproject.urls_crush'
            logger.info(f"DomainURLRoutingMiddleware: Routing to urls_crush for host: {host}")
        elif host in ['localhost', '127.0.0.1', '192.168.178.184']:
            # Route localhost and local IP to Crush.lu (for testing)
            request.urlconf = 'azureproject.urls_crush'
            logger.info(f"DomainURLRoutingMiddleware: Routing to urls_crush for localhost development: {host}")
        elif host.endswith('.azurewebsites.net'):
            # Azure App Service hostname - use powerup URLs (will be redirected by RedirectWWWToRootDomainMiddleware)
            request.urlconf = 'azureproject.urls_powerup'
            logger.info(f"DomainURLRoutingMiddleware: Azure hostname detected, routing to urls_powerup for host: {host}")
        else:
            # Fallback to powerup if no match, or choose a different default if appropriate
            request.urlconf = 'azureproject.urls_powerup' # Fallback
            logger.warning(f"DomainURLRoutingMiddleware: Falling back to urls_powerup for unrecognized host: {host}")
        return self.get_response(request)
