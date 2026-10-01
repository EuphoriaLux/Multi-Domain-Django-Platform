import math
import time
import uuid
from functools import wraps
from django.shortcuts import redirect, render
from django.urls import reverse
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.utils.translation import gettext as _

from crush_lu.oauth_statekit import get_client_ip
from crush_lu.rate_limit_utils import humanize_wait_seconds


def crush_login_required(function):
    """
    Custom login_required decorator that redirects to Crush.lu's login page
    instead of the default Django/Allauth login page.
    """
    @wraps(function)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            # Redirect to Crush.lu login with validated next parameter
            from django.contrib.auth.views import redirect_to_login
            login_url = reverse('crush_lu:login')
            # Use Django's redirect_to_login which safely handles the next parameter
            return redirect_to_login(request.get_full_path(), login_url)
        return function(request, *args, **kwargs)
    return wrapper


def coach_required(function):
    """
    Decorator for coach-only views. Checks that the user is an active coach
    and attaches `request.coach` for convenience.

    Redirects non-coaches to the dashboard with an error message.
    """
    @wraps(function)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            login_url = reverse('crush_lu:login')
            return redirect_to_login(request.get_full_path(), login_url)

        from crush_lu.models import CrushCoach
        try:
            coach = CrushCoach.objects.get(user=request.user)
            if not coach.is_active:
                messages.error(
                    request,
                    'Your coach account has been deactivated. Please contact an administrator.',
                )
                return redirect('crush_lu:dashboard')
        except CrushCoach.DoesNotExist:
            messages.error(request, 'You do not have coach access.')
            return redirect('crush_lu:dashboard')

        request.coach = coach
        return function(request, *args, **kwargs)
    return wrapper


def ratelimit(
    key='ip', rate='5/15m', method='POST', block=True, rate_limited_template=None,
    count_if=None,
):
    """
    Simple rate limiting decorator using Django's cache framework.

    Args:
        key: 'ip', 'user', or callable that returns a string
        rate: '<count>/<period>' where period is 's', 'm', 'h', 'd'
              Examples: '5/15m' = 5 requests per 15 minutes
                       '10/h' = 10 requests per hour
        method: 'GET', 'POST', 'ALL' - which HTTP methods to rate limit
        block: If True, block the request with 429. If False, just set request.limited = True
        rate_limited_template: When block triggers on a plain browser request
              (not JSON/XHR), render this template with a translated
              {{ wait_message }} instead of the bare text/plain fallback.
              Leave unset for API-style endpoints that should keep the
              existing plain-text/JSON contract.
        count_if: Optional callable(response) -> bool. When set, a request
              reserves a slot atomically before the view runs and keeps it
              only if count_if(response) is true (e.g. only successful
              creations); otherwise, or if the view raises, the slot is
              released. Concurrent requests therefore can never exceed the
              limit together.

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
                limit, period = rate.split('/')
                limit = int(limit)
            except (ValueError, AttributeError):
                # Invalid rate format, skip rate limiting
                return func(request, *args, **kwargs)

            # Convert period to seconds
            period_seconds = _parse_period(period)

            # Get cache key
            cache_key = _get_cache_key(request, key, func.__name__)

            # Count this request (gracefully handle cache errors). With
            # count_if this is a reservation, released below unless the
            # response qualifies; a peek-then-count let a concurrent burst
            # all pass on one read.
            try:
                count, generation = _count_request(cache_key, period_seconds)
            except Exception:
                count = None
                generation = None
            if not isinstance(count, int):
                # Cache unavailable - allow request to proceed. django_redis
                # with IGNORE_EXCEPTIONS returns None instead of raising.
                return func(request, *args, **kwargs)

            if count > limit:
                # Rate limit exceeded
                request.limited = True
                request.limited_retry_after = _remaining_window_seconds(
                    cache_key, period_seconds
                )
                if block and count_if is not None:
                    # A refused request never counts toward a success-only
                    # cap; the counter stays at or above the limit.
                    _release_request(cache_key, generation)
                if block:
                    # Return JSON for API/AJAX requests, plain text for browser requests
                    if (
                        request.headers.get("X-Requested-With") == "XMLHttpRequest"
                        or "application/json" in request.headers.get("Accept", "")
                        or request.content_type == "application/json"
                    ):
                        return JsonResponse(
                            {"error": _("Too many attempts. Please try again later."), "error_code": "rate_limited"},
                            status=429,
                        )
                    if rate_limited_template:
                        response = render(
                            request,
                            rate_limited_template,
                            {
                                'wait_message': humanize_wait_seconds(
                                    request.limited_retry_after
                                )
                            },
                            status=429,
                        )
                        response['Retry-After'] = str(request.limited_retry_after)
                        return response
                    messages.error(
                        request,
                        _('Too many attempts. Please try again later.')
                    )
                    return HttpResponse(
                        _('Rate limit exceeded. Please try again later.'),
                        status=429
                    )

            if count_if is None:
                return func(request, *args, **kwargs)
            try:
                response = func(request, *args, **kwargs)
            except BaseException:
                _release_request(cache_key, generation)
                raise
            if not count_if(response):
                _release_request(cache_key, generation)
            return response

        return wrapper
    return decorator


def _window_deadline_key(cache_key):
    return f"{cache_key}:deadline"


def _generation_key(cache_key):
    return f"{cache_key}:generation"


def _record_window_deadline(cache_key, period_seconds):
    """Track expiry for cache backends without a native TTL method.

    Also stamps the new window with a fresh generation token, so a release
    can tell whether it still belongs to the window it reserved in. Returns
    the token so the request that created the window knows it for certain."""
    token = uuid.uuid4().hex
    cache.set(_generation_key(cache_key), token, period_seconds)
    cache.set(
        _window_deadline_key(cache_key),
        time.time() + period_seconds,
        period_seconds,
    )
    return token


def _remaining_window_seconds(cache_key, period_seconds):
    """Return the counter's remaining fixed-window lifetime, rounded up."""
    ttl = getattr(cache, "ttl", None)
    if callable(ttl):
        try:
            remaining = ttl(cache_key)
            if isinstance(remaining, (int, float)) and remaining >= 0:
                return max(1, min(period_seconds, math.ceil(remaining)))
        except Exception:
            pass

    try:
        deadline = cache.get(_window_deadline_key(cache_key))
    except Exception:
        deadline = None
    if isinstance(deadline, (int, float)):
        return max(1, min(period_seconds, math.ceil(deadline - time.time())))
    # A concurrent first request may increment before the deadline key is
    # written; the full period is accurate at the start of that window.
    return period_seconds


def _count_request(cache_key, period_seconds):
    """
    Count one request in the key's window; return ``(total, generation)``.

    ``generation`` is the token of the window the increment landed in, or
    None when that cannot be established (see below), in which case the slot
    is never released.

    add() creates the counter, with the window's expiry, only if it is absent,
    and incr() returns the incremented value, so concurrent requests each get
    a distinct count (atomic INCR on django_redis, lock-held on LocMem). A
    get-then-set let a burst all read the same value and pass together.
    Requests over the limit are counted too; incr() keeps the expiry that
    add() set, so they never extend the window.
    """
    before = cache.get(_generation_key(cache_key))
    created = None
    if cache.add(cache_key, 0, period_seconds):
        created = _record_window_deadline(cache_key, period_seconds)
    try:
        count = cache.incr(cache_key)
    except ValueError:
        # Evicted between add() and incr(): start a fresh window with this
        # request, unless a concurrent request already did.
        if cache.add(cache_key, 1, period_seconds):
            return 1, _record_window_deadline(cache_key, period_seconds)
        created = None
        count = cache.incr(cache_key)
    if created is not None:
        return count, created
    # Joined an existing window. Reading the token on both sides of the
    # increment proves which window counted it: a window rollover in between
    # changes the token, and then the owner is unknown. An unknown owner is
    # never released, so the race can only cost a slot, never grant one.
    after = cache.get(_generation_key(cache_key))
    return count, (before if before is not None and before == after else None)


def _release_request(cache_key, generation=None):
    """
    Give back a slot reserved by _count_request().

    ``generation`` is the window token the reservation was counted in (see
    _count_request); None means unknown, and nothing is released. If the window
    has since expired and a new one started, the slot is already gone with the
    old counter: releasing would take a count from the new window (#1111).

    decr() is atomic and keeps the expiry add() set; on a missing key it
    raises rather than creating one, so a release never extends the window
    or leaves a counter without a timeout. A release that lands in a newer
    window than its reservation could drive the counter below zero; undo it.
    """
    try:
        if generation is None or cache.get(_generation_key(cache_key)) != generation:
            return
        if cache.decr(cache_key) < 0:
            cache.incr(cache_key)
        elif cache.get(_generation_key(cache_key)) != generation:
            # The window rolled over between the check and the decrement, so
            # the decrement landed in the new window: give it back.
            cache.incr(cache_key)
    except Exception:
        # Evicted or expired (nothing left to release) or cache unavailable.
        pass


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


def _client_ip(request):
    """
    Client address without the port. Azure's X-Forwarded-For is IP:PORT and
    the port changes with every TCP connection, so keying on the raw header
    gave each new connection a fresh counter.
    """
    return get_client_ip(request) or 'unknown'


def _get_cache_key(request, key, view_name=''):
    """
    Generate cache key based on key type.
    """
    if callable(key):
        key_value = key(request)
    elif key == 'ip':
        key_value = _client_ip(request)
    elif key == 'user':
        if request.user.is_authenticated:
            key_value = f'user_{request.user.id}'
        else:
            # Fall back to IP for anonymous users
            key_value = _client_ip(request)
    else:
        key_value = str(key)

    # Sanitize key (cache keys can't have spaces or dots)
    key_value = key_value.replace(' ', '_').replace('.', '_').replace(':', '_')

    return f'ratelimit:{view_name}:{key_value}'
