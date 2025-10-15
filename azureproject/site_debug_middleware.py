import logging
from django.contrib.sites.models import Site
from django.conf import settings

logger = logging.getLogger(__name__)


class SiteDebugMiddleware:
    """
    Debug middleware to log Site detection and ensure request.site is always set.
    Place this AFTER CurrentSiteMiddleware to verify it's working correctly.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host()

        # Check if CurrentSiteMiddleware set request.site
        if hasattr(request, 'site'):
            logger.info(f"Site detected: {request.site.domain} (ID: {request.site.id}) for host: {host}")
        else:
            logger.warning(f"NO SITE SET for host: {host}")
            # Try to manually set site based on host
            try:
                # Remove port if present
                host_no_port = host.split(':')[0].lower()
                site = Site.objects.get(domain=host_no_port)
                request.site = site
                logger.info(f"Manually set site: {site.domain} (ID: {site.id})")
            except Site.DoesNotExist:
                logger.error(f"Could not find Site for host: {host_no_port}")
                # Fallback to first site to prevent crashes
                try:
                    request.site = Site.objects.first()
                    logger.warning(f"Using fallback site: {request.site.domain}")
                except Exception as e:
                    logger.error(f"Failed to set fallback site: {e}")

        response = self.get_response(request)
        return response
