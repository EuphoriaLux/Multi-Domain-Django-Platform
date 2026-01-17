# azureproject/redirect_www_middleware.py
"""
Middleware for handling domain redirects and Azure internal IPs.

This module handles:
- WWW to non-WWW redirects (www.crush.lu -> crush.lu)
- Azure hostname redirects (*.azurewebsites.net -> powerup.lu)
- Azure internal IP handling for health checks
- Staging subdomain (test.*) noindex headers
"""
from django.http import HttpResponsePermanentRedirect

from .domains import DOMAINS, PRODUCTION_DEFAULT


def is_staging_subdomain(host):
    """Check if the host is a staging test subdomain (test.*)."""
    return host.startswith('test.')


class StagingNoIndexMiddleware:
    """
    Add X-Robots-Tag: noindex header for staging subdomains (test.*).

    This prevents search engines from indexing staging/test environments.
    The test.* subdomains point to the Azure staging slot for pre-production testing.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Get host directly from META
        host = request.META.get('HTTP_HOST', '').split(':')[0].lower()

        # Add noindex header for staging subdomains
        if is_staging_subdomain(host):
            response['X-Robots-Tag'] = 'noindex, nofollow'

        return response


class AzureInternalIPMiddleware:
    """
    Middleware to handle Azure internal IPs (169.254.*) and localhost.

    Note: Host validation is now handled by custom_validate_host() in production.py
    to support OpenTelemetry middleware that runs before custom middleware.
    This middleware is kept for backwards compatibility.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Host validation now handled by custom_validate_host() monkey-patch
        return self.get_response(request)


class RedirectWWWToRootDomainMiddleware:
    """
    Redirect WWW subdomains to root domains and Azure hostnames to production domain.

    Redirects:
    - www.crush.lu -> crush.lu
    - www.vinsdelux.com -> vinsdelux.com
    - www.entreprinder.lu -> entreprinder.lu
    - www.power-up.lu -> power-up.lu
    - *.azurewebsites.net -> entreprinder.lu (PRODUCTION_DEFAULT)

    All redirects are HTTP 301 (permanent) for SEO purposes.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip redirects for health check endpoint
        if request.path in ['/healthz/', '/healthz']:
            return self.get_response(request)

        # Get host directly from META to avoid ALLOWED_HOSTS validation
        host = request.META.get('HTTP_HOST', '').split(':')[0].lower()

        if not host:
            return self.get_response(request)

        # Skip redirects for Azure internal IPs (health checks, monitoring)
        if host.startswith('169.254.') or host == 'localhost':
            return self.get_response(request)

        # Skip redirects for staging subdomains (test.*)
        # These should stay on the staging slot, not redirect to production
        if is_staging_subdomain(host):
            return self.get_response(request)

        # Redirect Azure App Service hostname to production default
        if host.endswith('.azurewebsites.net'):
            new_url = f'https://{PRODUCTION_DEFAULT}{request.get_full_path()}'
            return HttpResponsePermanentRedirect(new_url)

        # Redirect www. to non-www version (only for configured domains)
        if host.startswith('www.'):
            root_domain = host[4:]  # Remove 'www.'
            if root_domain in DOMAINS:
                new_url = f'https://{root_domain}{request.get_full_path()}'
                return HttpResponsePermanentRedirect(new_url)

        return self.get_response(request)
