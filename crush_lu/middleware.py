"""
Crush.lu Middleware
Handles user activity tracking and PWA detection.
"""

import logging
from django.core.cache import cache
from django.utils import timezone
from django.db.models import F

logger = logging.getLogger(__name__)

# Paths to skip for activity tracking (static files, API endpoints that shouldn't count as visits)
SKIP_PATHS = (
    "/static/",
    "/media/",
    "/healthz/",
    "/favicon.ico",
    "/robots.txt",
    "/sitemap.xml",
    "/manifest.json",
    "/service-worker.js",
    "/api/",  # API calls shouldn't count as page visits
)

# Minimum seconds between activity updates (to avoid DB write on every request)
ACTIVITY_UPDATE_INTERVAL = 300  # 5 minutes


def _cache_get_safe(key):
    """Cache read that treats any backend error as a miss.

    django_redis already swallows exceptions via IGNORE_EXCEPTIONS on
    production, but this keeps the middleware resilient against any
    backend that doesn't — a cache outage must never silently stop
    activity tracking; worst case we fall through to the DB path.
    """
    try:
        return cache.get(key)
    except Exception as e:  # noqa: BLE001 — defensive breadth
        logger.debug("activity cache read failed (%s); falling back to DB path", e)
        return None


def _cache_set_safe(key, value, ttl):
    try:
        cache.set(key, value, ttl)
    except Exception as e:  # noqa: BLE001
        logger.debug("activity cache write failed (%s); ignoring", e)


class UserActivityMiddleware:
    """
    Middleware to track user activity for Crush.lu.

    Features:
    - Updates last_seen timestamp (throttled to every 5 minutes)
    - Increments total_visits counter
    - Detects PWA usage via Sec-Fetch-Mode header or display-mode media query
    - Updates last_pwa_visit for PWA users

    Performance considerations:
    - Only processes authenticated users
    - Skips static files, API endpoints, and health checks
    - Throttles DB updates to every 5 minutes per user
    - Uses F() expression for atomic counter increment
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Process response first
        response = self.get_response(request)

        # Skip certain paths BEFORE checking auth - avoids unnecessary DB connections
        path = request.path
        if any(path.startswith(skip) for skip in SKIP_PATHS):
            return response

        # Only track authenticated users on Crush.lu domain
        if not request.user.is_authenticated:
            return response

        # Check if this is a Crush.lu request (domain routing)
        # Skip if not on crush.lu domain
        host = request.get_host().lower()
        if not ("crush" in host or "localhost" in host or "127.0.0.1" in host):
            return response

        # Update user activity
        self._update_activity(request)

        return response

    def _update_activity(self, request):
        """
        Update user activity record.
        Throttled to avoid excessive DB writes.
        """
        from .models import UserActivity

        try:
            user = request.user
            now = timezone.now()

            # Per-day activity rollup — recorded independently of the last_seen
            # throttle below, so the first request of each new calendar day is
            # always captured (even mid-session across midnight). Drives a stable
            # WAU in the weekly KPI snapshot regardless of when the job runs.
            self._record_daily_activity(request, user, now)

            # Cache-gated throttle: if we updated this user within the interval,
            # skip the DB roundtrip entirely. Previously get_or_create() ran on
            # every request even when no write was needed.
            cache_key = f"user_activity:last_seen:{user.pk}"
            if _cache_get_safe(cache_key):
                return

            activity, created = UserActivity.objects.get_or_create(
                user=user,
                defaults={
                    "last_seen": now,
                    "total_visits": 1,
                },
            )

            if created:
                _cache_set_safe(cache_key, 1, ACTIVITY_UPDATE_INTERVAL)
                logger.debug(f"Created UserActivity for {user.username}")
                return

            # Double-check against the DB timestamp in case cache was cold but
            # another worker updated recently.
            time_since_last = (now - activity.last_seen).total_seconds()
            if time_since_last < ACTIVITY_UPDATE_INTERVAL:
                _cache_set_safe(
                    cache_key,
                    1,
                    ACTIVITY_UPDATE_INTERVAL - int(time_since_last),
                )
                return

            # Detect PWA usage
            is_pwa = self._detect_pwa(request)

            activity.last_seen = now

            # Increment visit counter atomically
            # We need to use update() for F() expression
            UserActivity.objects.filter(pk=activity.pk).update(
                last_seen=now, total_visits=F("total_visits") + 1
            )

            # Handle PWA-specific updates separately (can't mix with F())
            if is_pwa:
                UserActivity.objects.filter(pk=activity.pk).update(
                    is_pwa_user=True, last_pwa_visit=now
                )
                logger.debug(f"PWA visit recorded for {user.username}")

            _cache_set_safe(cache_key, 1, ACTIVITY_UPDATE_INTERVAL)

        except Exception as e:
            # Don't let activity tracking break the request
            logger.warning(f"Error updating user activity: {e}")

    def _record_daily_activity(self, request, user, now):
        """Record one ``DailyUserActivity`` row per user per local day (issue #523).

        Cache-gated on its own per-day key so the DB is touched at most once per
        user per day — *except* to upgrade ``was_pwa`` the first time a PWA
        request lands after an earlier browser hit that same day. The cached
        value tracks whether the day's row is already PWA-flagged (``"pwa"``)
        or only browser-seen (``"seen"``) so a later PWA visit isn't skipped.

        Uses ``localdate()`` (Europe/Luxembourg) so the row's date matches the
        ``__date`` window the weekly KPI snapshot filters on. PWA detection here
        uses the DB-free request signals only (not the sticky "ever installed"
        flag) so ``was_pwa`` means "actually used the PWA that day".
        """
        from .models import DailyUserActivity

        try:
            today = timezone.localdate(now)
            daily_key = f"user_activity:daily:{user.pk}:{today.isoformat()}"
            cached = _cache_get_safe(daily_key)
            pwa_now = self._is_pwa_request(request)

            # Already fully recorded (incl. PWA), or recorded and this isn't a
            # PWA request that would add anything — nothing to do.
            if cached == "pwa" or (cached == "seen" and not pwa_now):
                return

            row, _created = DailyUserActivity.objects.get_or_create(
                user=user,
                activity_date=today,
                defaults={"was_pwa": pwa_now},
            )
            if pwa_now and not row.was_pwa:
                DailyUserActivity.objects.filter(pk=row.pk).update(was_pwa=True)

            # Hold the key until local midnight so we don't re-query all day; the
            # key is date-scoped, so the next day always misses and writes afresh.
            local_now = timezone.localtime(now)
            end_of_day = local_now.replace(hour=23, minute=59, second=59, microsecond=0)
            ttl = max(60, int((end_of_day - local_now).total_seconds()))
            _cache_set_safe(
                daily_key, "pwa" if (pwa_now or row.was_pwa) else "seen", ttl
            )

        except Exception as e:  # noqa: BLE001 — never break the request
            logger.warning(f"Error recording daily activity: {e}")

    def _is_pwa_request(self, request):
        """DB-free signals that THIS request came from the installed PWA.

        Recognizes the manifest launch marker (``start_url`` is ``/?source=pwa``
        — see ``views_pwa.py``), the ``X-PWA-Mode`` header a service worker can
        add to same-origin fetches, the legacy ``?pwa=1`` param, and a session
        flag if one is set. None touch the database, so this is cheap to call on
        every request. The ``/api/push/mark-pwa-user/`` call that sets the sticky
        ``is_pwa_user`` flag is under ``SKIP_PATHS``, so it can't be the signal
        here — the launch marker is what a same-day PWA open carries.
        """
        if request.headers.get("X-PWA-Mode") == "standalone":
            return True
        if request.GET.get("source") == "pwa" or request.GET.get("pwa") == "1":
            return True
        if request.session.get("is_pwa_user"):
            return True
        return False

    def _detect_pwa(self, request):
        """
        Detect if the request is from an installed PWA.

        Combines the DB-free per-request signals (``_is_pwa_request``) with the
        sticky "user has ever used the installed PWA" flag on UserActivity.

        Returns:
            bool: True if request appears to be from PWA
        """
        if self._is_pwa_request(request):
            return True

        # Sticky fallback: user already known as a PWA user.
        try:
            if hasattr(request.user, "activity") and request.user.activity.is_pwa_user:
                return True
        except Exception:
            pass

        return False
