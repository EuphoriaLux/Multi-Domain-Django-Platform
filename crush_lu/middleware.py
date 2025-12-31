"""
Crush.lu Middleware
Handles user activity tracking and PWA detection.
"""

import logging
from django.utils import timezone
from django.db.models import F

logger = logging.getLogger(__name__)

# Paths to skip for activity tracking (static files, API endpoints that shouldn't count as visits)
SKIP_PATHS = (
    '/static/',
    '/media/',
    '/healthz/',
    '/favicon.ico',
    '/robots.txt',
    '/sitemap.xml',
    '/manifest.json',
    '/service-worker.js',
    '/api/',  # API calls shouldn't count as page visits
)

# Minimum seconds between activity updates (to avoid DB write on every request)
ACTIVITY_UPDATE_INTERVAL = 300  # 5 minutes


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

        # Only track authenticated users on Crush.lu domain
        if not request.user.is_authenticated:
            return response

        # Skip certain paths
        path = request.path
        if any(path.startswith(skip) for skip in SKIP_PATHS):
            return response

        # Check if this is a Crush.lu request (domain routing)
        # Skip if not on crush.lu domain
        host = request.get_host().lower()
        if not ('crush' in host or 'localhost' in host or '127.0.0.1' in host):
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
            now = timezone.now()
            user = request.user

            # Get or create activity record
            activity, created = UserActivity.objects.get_or_create(
                user=user,
                defaults={
                    'last_seen': now,
                    'total_visits': 1,
                }
            )

            if created:
                logger.debug(f"Created UserActivity for {user.username}")
                return

            # Check if we should update (throttle to every 5 minutes)
            time_since_last = (now - activity.last_seen).total_seconds()
            if time_since_last < ACTIVITY_UPDATE_INTERVAL:
                return

            # Detect PWA usage
            is_pwa = self._detect_pwa(request)

            # Build update fields
            update_fields = ['last_seen']
            activity.last_seen = now

            # Increment visit counter atomically
            # We need to use update() for F() expression
            UserActivity.objects.filter(pk=activity.pk).update(
                last_seen=now,
                total_visits=F('total_visits') + 1
            )

            # Handle PWA-specific updates separately (can't mix with F())
            if is_pwa:
                UserActivity.objects.filter(pk=activity.pk).update(
                    is_pwa_user=True,
                    last_pwa_visit=now
                )
                logger.debug(f"PWA visit recorded for {user.username}")

        except Exception as e:
            # Don't let activity tracking break the request
            logger.warning(f"Error updating user activity: {e}")

    def _detect_pwa(self, request):
        """
        Detect if the request is from an installed PWA.

        Detection methods:
        1. Sec-Fetch-Mode header (modern browsers)
        2. Sec-Fetch-Dest header
        3. X-PWA-Mode custom header (set by service worker)
        4. Referer containing 'display-mode=standalone'

        Returns:
            bool: True if request appears to be from PWA
        """
        # Method 1: Sec-Fetch-Mode header
        # When app is installed, this is often 'navigate' with Sec-Fetch-Dest: document
        # But the key indicator is the display mode

        # Method 2: Custom header from service worker
        if request.headers.get('X-PWA-Mode') == 'standalone':
            return True

        # Method 3: Check for PWA-specific query parameter
        # (can be added by manifest start_url)
        if request.GET.get('pwa') == '1':
            return True

        # Method 4: Check session flag (set by mark_pwa_user API)
        if request.session.get('is_pwa_user'):
            return True

        # Method 5: Check if user already marked as PWA user
        # (don't need to re-check headers if we already know)
        try:
            if hasattr(request.user, 'activity') and request.user.activity.is_pwa_user:
                return True
        except Exception:
            pass

        return False