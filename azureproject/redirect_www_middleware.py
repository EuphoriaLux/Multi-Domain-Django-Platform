# azureproject/redirect_www_middleware.py

from django.http import HttpResponsePermanentRedirect

class RedirectWWWToRootDomainMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.META.get('HTTP_HOST', '')
        if host.lower() == 'www.powerup.lu':
            # Construct the new URL (including path and query string)
            return HttpResponsePermanentRedirect(f'https://powerup.lu{request.get_full_path()}')
        return self.get_response(request)
