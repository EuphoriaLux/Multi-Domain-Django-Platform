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
            except _RateWindowBusy:
                # Known stale/contended state is not a cache outage. Refuse or
                # flag it conservatively rather than admit an unmetered view.
                count = limit + 1
                generation = None
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
                    cache_key, period_seconds, generation
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


class _RateWindowBusy(Exception):
    """Known window transitions exhausted their bounded retry budget."""


def _generation_key(cache_key):
    # Separate the new protocol from counters/tokens held by an older worker.
    return f"{cache_key}:window:v2"


def _counter_selector_key(cache_key, window):
    return f"{cache_key}:window:{window[1]}:counter"


def _counter_key(cache_key, window, nonce):
    return f"{_counter_selector_key(cache_key, window)}:{nonce}"


def _window_token(value):
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], str)
    )


def _remaining_window_seconds(cache_key, period_seconds, generation=None):
    """Remaining anchored window lifetime, never a fresh counter/link timeout."""
    ttl = getattr(cache, "ttl", None)
    if callable(ttl):
        try:
            remaining = ttl(_generation_key(cache_key))
            if isinstance(remaining, (int, float)) and remaining >= 0:
                return max(1, min(period_seconds, math.ceil(remaining)))
        except Exception:
            pass
    try:
        window = cache.get(_generation_key(cache_key))
    except Exception:
        window = None
    deadline = (
        generation[0]
        if generation is not None
        else (window[0] if _window_token(window) else None)
    )
    if isinstance(deadline, (int, float)):
        return max(1, min(period_seconds, math.ceil(deadline - time.time())))
    return period_seconds


def _counter_generation(cache_key, window, selector):
    """Elect a pre-created physical counter with add(), never set/delete.

    A published counter key is never re-created. Losing its data elects a new
    nonce at its immutable :next selector, so old reservations stay fenced.
    """
    nonce = cache.get(selector)
    if nonce is not None:
        if not isinstance(nonce, str):
            raise _RateWindowBusy
        return nonce
    remaining = window[0] - time.time()
    if remaining <= 0:
        raise _RateWindowBusy
    nonce = uuid.uuid4().hex
    created = cache.add(_counter_key(cache_key, window, nonce), 0, remaining)
    if created is None:
        return None  # django_redis IGNORE_EXCEPTIONS outage
    if not created:
        raise _RateWindowBusy  # Never reuse even an unpublished collision.
    remaining = window[0] - time.time()
    if remaining <= 0:
        raise _RateWindowBusy
    published = cache.add(selector, nonce, remaining)
    if published is None:
        return None
    if published:
        return nonce
    winner = cache.get(selector)
    if winner is None:
        raise _RateWindowBusy  # Expiry during publication, not an outage proof.
    if not isinstance(winner, str):
        raise _RateWindowBusy
    return winner


def _count_request(cache_key, period_seconds):
    """Reserve atomically; return (count, (deadline, immutable physical key)).

    The first request publishes a fixed window via atomic add(). Its counters
    and immutable successor selectors receive only that window's remaining
    lifetime. All increments/decrements use nonce-specific physical keys.
    A stalled creator cannot replace a winner or re-create a published key.
    Counter loss rotates to a fresh nonce without deleting a shared selector.

    Rollover/eviction contention retries are bounded and fail closed. Only a
    cache exception or a swallowed failed add/incr uses the decorator's usual
    outage fail-open behavior. Cache loss can still lose historical counts;
    this cache-based limiter does not offer durable quotas.
    """
    for _window_attempt in range(3):
        window = cache.get(_generation_key(cache_key))
        if window is None:
            candidate = (time.time() + period_seconds, uuid.uuid4().hex)
            published = cache.add(_generation_key(cache_key), candidate, period_seconds)
            if published is None:
                return None, None
            window = candidate if published else cache.get(_generation_key(cache_key))
        if not _window_token(window) or window[0] <= time.time():
            continue
        selector = _counter_selector_key(cache_key, window)
        path = []
        try:
            for _counter_attempt in range(8):
                nonce = _counter_generation(cache_key, window, selector)
                if nonce is None:
                    return None, None
                path.append((selector, nonce))
                physical_key = _counter_key(cache_key, window, nonce)
                successor_key = f"{physical_key}:next"
                try:
                    count = cache.incr(physical_key)
                except ValueError:
                    # A counter can disappear before its selector is visible,
                    # or be evicted later. Never revive that physical identity.
                    selector = successor_key
                    continue
                if not isinstance(count, int):
                    return None, None
                reservation = (window[0], physical_key)
                successor = cache.get(successor_key)
                connected = all(cache.get(key) == value for key, value in path)
                current = cache.get(_generation_key(cache_key))
                if not connected or current != window or window[0] <= time.time():
                    _release_request(cache_key, reservation)
                    break
                if successor is not None:
                    _release_request(cache_key, reservation)
                    selector = successor_key
                    continue
                return count, reservation
        except _RateWindowBusy:
            continue
    raise _RateWindowBusy


def _release_request(cache_key, generation=None):
    """Release only the exact counter returned by _count_request().

    Its key is immutable and never re-created after publication. Expiry or
    eviction therefore makes a release a no-op, rather than borrowing from a
    newer window/counter and compensating after another request used the hole.
    decr() never creates a key or extends its TTL.
    """
    if generation is None:
        return
    try:
        cache.decr(generation[1])
    except Exception:
        pass  # Missing/expired counter or cache outage: no slot to return.


def _parse_period(period_str):
    """
    Convert period string to seconds.
    Examples: 'm' -> 60, 'h' -> 3600, '15m' -> 900, '1d' -> 86400
    """
    if not period_str:
        return 900  # Default 15 minutes

    # A rate such as '10/h' omits the multiplier, meaning one hour.
    if period_str in {"s", "m", "h", "d"}:
        period_str = f"1{period_str}"

    # Extract number and unit
    num_str = period_str[:-1] if period_str[-1].isalpha() else period_str
    unit = period_str[-1] if period_str[-1].isalpha() else "m"

    try:
        num = int(num_str)
    except ValueError:
        num = 15
        unit = "m"

    multipliers = {
        "s": 1,  # seconds
        "m": 60,  # minutes
        "h": 3600,  # hours
        "d": 86400,  # days
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
