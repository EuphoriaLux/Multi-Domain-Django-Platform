from django.contrib.auth.decorators import login_required as django_login_required
from functools import wraps
from django.shortcuts import redirect
from django.urls import reverse
from django.core.cache import cache
from django.http import HttpResponse
from django.contrib import messages
from urllib.parse import quote


def crush_login_required(function):
    """
    Custom login_required decorator that redirects to Crush.lu's login page
    instead of the default Django/Allauth login page.
    """
    @wraps(function)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            # Redirect to Crush.lu login with next parameter
            # URL-encode the next parameter to prevent injection
            login_url = reverse('crush_lu:login')
            next_url = request.get_full_path()
            # Use quote to safely encode the URL
            safe_next = quote(next_url, safe='/')
            return redirect(f'{login_url}?next={safe_next}')
        return function(request, *args, **kwargs)
    return wrapper


def ratelimit(key='ip', rate='5/15m', method='POST', block=True):
    """
    Simple rate limiting decorator using Django's cache framework.

    Args:
        key: 'ip', 'user', or callable that returns a string
        rate: '<count>/<period>' where period is 's', 'm', 'h', 'd'
              Examples: '5/15m' = 5 requests per 15 minutes
                       '10/h' = 10 requests per hour
        method: 'GET', 'POST', 'ALL' - which HTTP methods to rate limit
        block: If True, block the request with 429. If False, just set request.limited = True

    Example:
        @ratelimit(key='ip', rate='5/15m', method='POST')
        def login(request):
            # This view will be rate limited to 5 POST requests per 15 minutes per IP
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            # Check if method matches
            if method != 'ALL' and request.method != method:
                return func(request, *args, **kwargs)

            # Parse rate limit
            try:
                count, period = rate.split('/')
                count = int(count)
            except (ValueError, AttributeError):
                # Invalid rate format, skip rate limiting
                return func(request, *args, **kwargs)

            # Convert period to seconds
            period_seconds = _parse_period(period)

            # Get cache key
            cache_key = _get_cache_key(request, key, func.__name__)

            # Get current count from cache (gracefully handle cache errors)
            try:
                current = cache.get(cache_key, 0)
            except Exception:
                # Cache unavailable - allow request to proceed
                return func(request, *args, **kwargs)

            if current >= count:
                # Rate limit exceeded
                request.limited = True
                if block:
                    messages.error(
                        request,
                        f'Too many attempts. Please try again later.'
                    )
                    return HttpResponse(
                        'Rate limit exceeded. Please try again later.',
                        status=429
                    )
            else:
                # Increment counter (gracefully handle cache errors)
                try:
                    if current == 0:
                        # First request - set with expiry
                        cache.set(cache_key, 1, period_seconds)
                    else:
                        # Increment existing counter
                        try:
                            cache.incr(cache_key)
                        except ValueError:
                            # Key doesn't exist, recreate it
                            cache.set(cache_key, 1, period_seconds)
                except Exception:
                    # Cache unavailable - continue without rate limiting
                    pass

            return func(request, *args, **kwargs)

        return wrapper
    return decorator


def _parse_period(period_str):
    """
    Convert period string to seconds.
    Examples: '15m' -> 900, '1h' -> 3600, '1d' -> 86400
    """
    if not period_str:
        return 900  # Default 15 minutes

    # Extract number and unit
    num_str = period_str[:-1] if period_str[-1].isalpha() else period_str
    unit = period_str[-1] if period_str[-1].isalpha() else 'm'

    try:
        num = int(num_str)
    except ValueError:
        num = 15
        unit = 'm'

    multipliers = {
        's': 1,        # seconds
        'm': 60,       # minutes
        'h': 3600,     # hours
        'd': 86400,    # days
    }

    return num * multipliers.get(unit, 60)


def _get_cache_key(request, key, view_name=''):
    """
    Generate cache key based on key type.
    """
    if callable(key):
        key_value = key(request)
    elif key == 'ip':
        # Get IP address (handle proxy headers)
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            key_value = x_forwarded_for.split(',')[0].strip()
        else:
            key_value = request.META.get('REMOTE_ADDR', 'unknown')
    elif key == 'user':
        if request.user.is_authenticated:
            key_value = f'user_{request.user.id}'
        else:
            # Fall back to IP for anonymous users
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                key_value = x_forwarded_for.split(',')[0].strip()
            else:
                key_value = request.META.get('REMOTE_ADDR', 'unknown')
    else:
        key_value = str(key)

    # Sanitize key (cache keys can't have spaces or dots)
    key_value = key_value.replace(' ', '_').replace('.', '_').replace(':', '_')

    return f'ratelimit:{view_name}:{key_value}'
