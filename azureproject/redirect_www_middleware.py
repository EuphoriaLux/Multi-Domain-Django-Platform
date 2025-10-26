# azureproject/redirect_www_middleware.py

from django.http import HttpResponsePermanentRedirect
from django.core.exceptions import DisallowedHost

class AzureInternalIPMiddleware:
    """
    Middleware to handle Azure internal IPs (169.254.*) and localhost.
    Note: Host validation is now handled by custom_validate_host() in production.py
    to support OpenTelemetry middleware that runs before custom middleware.
    This middleware is kept for backwards compatibility and logging purposes.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Host validation now handled by custom_validate_host() monkey-patch
        # No need to modify ALLOWED_HOSTS dynamically
        return self.get_response(request)

class RedirectWWWToRootDomainMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip redirects for health check endpoint
        if request.path == '/healthz/' or request.path == '/healthz':
            return self.get_response(request)

        # Get host directly from META to avoid ALLOWED_HOSTS validation
        # This allows us to redirect before Django validates the host
        host = request.META.get('HTTP_HOST', '').split(':')[0].lower()

        if not host:
            return self.get_response(request)

        # Skip redirects for Azure internal IPs (health checks, monitoring)
        if host.startswith('169.254.') or host == 'localhost':
            return self.get_response(request)

        # Redirect Azure App Service hostname to powerup.lu (except health checks)
        if host.endswith('.azurewebsites.net'):
            # Preserve the path and query string
            new_url = f'https://powerup.lu{request.get_full_path()}'
            return HttpResponsePermanentRedirect(new_url)

        # Redirect www. to non-www version
        if host.startswith('www.'):
            # Construct the new URL without 'www.'
            new_url = f'https://{host[4:]}{request.get_full_path()}'
            return HttpResponsePermanentRedirect(new_url)

        return self.get_response(request)

class DomainRoutingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.META.get('HTTP_HOST', '').lower()
        if host == 'www.powerup.lu':
            # Ensure this also redirects to the non-www version if it's the primary domain
            # Or handle it according to its specific logic, for now, keeping as is
            return HttpResponsePermanentRedirect(f'https://powerup.lu{request.get_full_path()}')
        elif host in ['vinsdelux.com', 'www.vinsdelux.com']:
            request.urlconf = 'azureproject.urls_vinsdelux'
        # Add other domain routings here if necessary
        return self.get_response(request)
