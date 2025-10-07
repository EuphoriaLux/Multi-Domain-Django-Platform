from django.contrib.auth.decorators import login_required as django_login_required
from functools import wraps
from django.shortcuts import redirect
from django.urls import reverse


def crush_login_required(function):
    """
    Custom login_required decorator that redirects to Crush.lu's login page
    instead of the default Django/Allauth login page.
    """
    @wraps(function)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            # Redirect to Crush.lu login with next parameter
            login_url = reverse('crush_lu:login')
            next_url = request.get_full_path()
            return redirect(f'{login_url}?next={next_url}')
        return function(request, *args, **kwargs)
    return wrapper
