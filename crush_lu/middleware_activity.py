"""
Crush.lu User Activity Tracking Middleware
Tracks user activity, PWA usage, and online status
"""

from django.utils import timezone
from django.core.cache import cache
from .models import UserActivity


class UserActivityMiddleware:
    """
    Tracks user activity on every request.
    Updates last_seen timestamp and detects PWA vs browser usage.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Process request
        response = self.get_response(request)

        # Only track authenticated users
        if request.user.is_authenticated:
            self.update_user_activity(request)

        return response

    def update_user_activity(self, request):
        """
        Update user activity data.
        Uses cache to avoid database writes on every request.
        """
        user = request.user
        now = timezone.now()

        # Cache key for this user
        cache_key = f'user_activity_{user.id}'

        # Only update DB every 5 minutes (reduces writes)
        last_update = cache.get(cache_key)
        if last_update and (now - last_update).seconds < 300:  # 5 minutes
            return

        # Detect if user is using PWA (standalone mode)
        is_pwa = self.detect_pwa_mode(request)

        # Get or create activity record
        from .models import UserActivity
        activity, created = UserActivity.objects.get_or_create(
            user=user,
            defaults={
                'last_seen': now,
                'is_pwa_user': is_pwa,
                'total_visits': 1
            }
        )

        if not created:
            # Update existing record
            activity.last_seen = now
            activity.total_visits += 1

            # Update PWA status if detected
            if is_pwa:
                activity.is_pwa_user = True
                activity.last_pwa_visit = now

            activity.save(update_fields=['last_seen', 'total_visits', 'is_pwa_user', 'last_pwa_visit'])

        # Update cache
        cache.set(cache_key, now, timeout=600)  # Cache for 10 minutes

    def detect_pwa_mode(self, request):
        """
        Detect if user is using the PWA (standalone mode).

        Methods:
        1. Check User-Agent header for "wv" (webview)
        2. Check custom header sent by JavaScript
        3. Check Sec-Fetch-Dest header
        """
        # Method 1: Check for standalone mode header (set by JS)
        if request.headers.get('X-Requested-With') == 'Crush-PWA':
            return True

        # Method 2: Check User-Agent for Android WebView
        user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
        if 'wv' in user_agent:  # Android WebView
            return True

        # Method 3: Check Sec-Fetch-Dest (not always reliable)
        fetch_dest = request.headers.get('Sec-Fetch-Dest', '')
        if fetch_dest == 'document' and request.headers.get('Sec-Fetch-Mode') == 'navigate':
            # Could be PWA, but not guaranteed
            pass

        return False
