# azureproject/redirect_www_middleware.py

from django.http import HttpResponsePermanentRedirect

class RedirectWWWToRootDomainMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0]
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
