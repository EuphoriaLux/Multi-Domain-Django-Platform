"""
Custom rate limiting / throttling classes for Crush.lu authentication endpoints.

These throttles are designed to prevent brute-force attacks on sensitive endpoints
like login, signup, and phone verification while allowing legitimate users to proceed.

Usage:
    Apply to views using the @throttle_classes decorator or throttle_classes attribute:

    from crush_lu.throttling import LoginRateThrottle

    @api_view(['POST'])
    @throttle_classes([LoginRateThrottle])
    def login_view(request):
        ...

Rates are configured in settings.py under REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
"""
from rest_framework.throttling import SimpleRateThrottle


class LoginRateThrottle(SimpleRateThrottle):
    """
    Throttle for login attempts.
    Uses IP address as the cache key to prevent brute-force attacks.
    Default: 5 attempts per minute (configured in settings.py)
    """
    scope = 'login'

    def get_cache_key(self, request, view):
        # Use IP address for anonymous login attempts
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request)
        }


class SignupRateThrottle(SimpleRateThrottle):
    """
    Throttle for signup/registration attempts.
    Stricter than login to prevent mass account creation.
    Default: 3 attempts per minute (configured in settings.py)
    """
    scope = 'signup'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request)
        }


class PhoneVerificationRateThrottle(SimpleRateThrottle):
    """
    Throttle for phone verification attempts.
    Prevents abuse of SMS sending functionality.
    Default: 3 attempts per minute (configured in settings.py)
    """
    scope = 'phone_verify'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request)
        }


class PasswordResetRateThrottle(SimpleRateThrottle):
    """
    Throttle for password reset requests.
    Prevents email enumeration and spam via password reset emails.
    Default: 3 attempts per hour (configured in settings.py)
    """
    scope = 'password_reset'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request)
        }


def ratelimit_view(throttle_classes):
    """
    Decorator to apply rate limiting to Django views (not DRF views).

    This wraps a standard Django view with DRF throttling logic.

    Usage:
        @ratelimit_view([LoginRateThrottle])
        def my_view(request):
            ...

    Returns 429 Too Many Requests if rate limit exceeded.
    """
    from functools import wraps
    from django.http import HttpResponse

    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            # Check each throttle
            for throttle_class in throttle_classes:
                throttle = throttle_class()
                if not throttle.allow_request(request, None):
                    wait = throttle.wait()
                    response = HttpResponse(
                        f'Rate limit exceeded. Try again in {int(wait)} seconds.',
                        status=429,
                        content_type='text/plain'
                    )
                    response['Retry-After'] = str(int(wait))
                    return response
            return view_func(request, *args, **kwargs)
        return wrapped_view
    return decorator
