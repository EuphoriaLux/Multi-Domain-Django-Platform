# azureproject/middleware.py

class DomainURLRoutingMiddleware:
    """
    Middleware that sets request.urlconf based on the HTTP host.
    - For travelinstyle.lu (or www.travelinstyle.lu), use the travelinstyle URL configuration.
    - For powerup.lu (or www.powerup.lu), use the powerup URL configuration.
    - Otherwise, fallback to powerup (default).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0].lower()  # Remove any port and lower-case the host
        if host in ['travelinstyle.lu', 'www.travelinstyle.lu']:
            request.urlconf = 'azureproject.urls_travelinstyle'
        elif host in ['powerup.lu', 'www.powerup.lu']:
            request.urlconf = 'azureproject.urls_powerup'
        elif host in ['vinsdelux.com', 'www.vinsdelux.com']:
            request.urlconf = 'azureproject.urls_vinsdelux'
        else:
            # Fallback to powerup if no match
            request.urlconf = 'azureproject.urls_powerup'
        return self.get_response(request)
