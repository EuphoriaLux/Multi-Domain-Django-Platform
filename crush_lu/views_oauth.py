"""
OAuth callback views for Crush.lu with PWA duplicate request handling.

PWA mode can cause duplicate OAuth callback requests because:
1. Service worker may pass request to network AND browser makes its own request
2. Navigation preload in some browsers
3. PWA shell reload behavior

This module provides idempotent OAuth callback views that handle duplicates gracefully
by using Django's cache framework for cross-request synchronization.

NOTE: Session-based tracking doesn't work because duplicate requests arrive
simultaneously (before either request's session changes are persisted).
"""
import logging
import time

from django.shortcuts import redirect
from django.core.cache import cache
from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView

logger = logging.getLogger(__name__)


class IdempotentOAuth2CallbackView(OAuth2CallbackView):
    """
    OAuth2 callback view that handles duplicate requests gracefully.

    OAuth authorization codes are one-time use. In PWA mode, duplicate requests
    can occur within milliseconds, causing the second request to fail with an
    "already used" error even though the user was successfully authenticated
    by the first request.

    This view uses Django's cache to implement a distributed lock:
    1. First request acquires lock on the OAuth state
    2. Second request sees lock exists, waits briefly for auth to complete
    3. If user becomes authenticated, redirect to success URL
    4. If timeout, show error page (which has its own recovery logic)
    """

    # Override in subclass for provider-specific success URLs
    success_url = '/dashboard/'
    login_url = '/login/'

    # How long to hold the lock (seconds)
    LOCK_TIMEOUT = 30

    # How long to wait for first request to complete auth (seconds)
    WAIT_FOR_AUTH_TIMEOUT = 3

    def dispatch(self, request, *args, **kwargs):
        """Handle the OAuth callback with cache-based duplicate detection."""
        state = request.GET.get('state', '')
        code = request.GET.get('code', '')

        if not state:
            # No state parameter - let allauth handle the error
            return super().dispatch(request, *args, **kwargs)

        # Cache key for this OAuth state
        lock_key = f"oauth_lock_{state}"
        result_key = f"oauth_result_{state}"

        # Try to acquire lock (set if not exists)
        # Returns True if we set the value (we got the lock), False if already exists
        acquired = cache.add(lock_key, 'processing', self.LOCK_TIMEOUT)

        if not acquired:
            # Another request is already processing this OAuth callback
            logger.info(
                f"Duplicate OAuth callback detected for state={state[:8]}... "
                f"Waiting for first request to complete authentication"
            )

            # Wait briefly for the first request to complete authentication
            # Check periodically if user has been authenticated
            start_time = time.time()
            while time.time() - start_time < self.WAIT_FOR_AUTH_TIMEOUT:
                # Check if the first request stored a successful result
                result = cache.get(result_key)
                if result:
                    user_id = result.get('user_id')
                    if user_id:
                        logger.info(
                            f"First request authenticated user {user_id}, "
                            f"redirecting duplicate to {self.success_url}"
                        )
                        return redirect(self.success_url)

                # Also check if current request's session now shows authenticated
                # (session may have been updated by first request)
                if request.user.is_authenticated:
                    logger.info(
                        f"User now authenticated via session, "
                        f"redirecting to {self.success_url}"
                    )
                    return redirect(self.success_url)

                # Sleep briefly before checking again
                time.sleep(0.1)

            # Timeout - first request may have failed or is still processing
            # Let this request fall through to show error page
            # The error page has JS that will check auth status and recover
            logger.warning(
                f"Timeout waiting for first OAuth request to complete for state={state[:8]}... "
                f"Falling through to error handling"
            )
            # Don't process - let allauth show error (our custom template will handle recovery)
            return super().dispatch(request, *args, **kwargs)

        # We acquired the lock - this is the first request, process normally
        logger.info(f"Processing OAuth callback for state={state[:8]}... (lock acquired)")

        try:
            response = super().dispatch(request, *args, **kwargs)
        except Exception as e:
            logger.error(f"OAuth callback error: {e}", exc_info=True)
            # Release lock on error
            cache.delete(lock_key)
            raise

        # If successful (302 redirect means auth completed), store result for duplicate requests
        if response.status_code == 302:
            # Store successful auth result
            user_id = request.user.id if request.user.is_authenticated else None
            cache.set(result_key, {'user_id': user_id, 'success': True}, self.LOCK_TIMEOUT)
            logger.info(
                f"OAuth callback successful for state={state[:8]}... "
                f"User ID: {user_id}, stored result in cache"
            )
        else:
            # Non-redirect response (error) - release lock
            cache.delete(lock_key)

        return response


class IdempotentFacebookCallbackView(IdempotentOAuth2CallbackView):
    """
    Facebook-specific OAuth callback with duplicate handling.

    Inherits all duplicate detection logic from IdempotentOAuth2CallbackView.
    """
    success_url = '/dashboard/'
    login_url = '/login/'


class IdempotentMicrosoftCallbackView(IdempotentOAuth2CallbackView):
    """
    Microsoft-specific OAuth callback with duplicate handling.

    Used for Crush Delegation Microsoft login.
    """
    success_url = '/delegation/dashboard/'
    login_url = '/delegation/'
