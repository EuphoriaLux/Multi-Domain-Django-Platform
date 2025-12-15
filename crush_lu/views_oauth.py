"""
OAuth callback views for Crush.lu with PWA duplicate request handling.

PWA mode can cause duplicate OAuth callback requests because:
1. Service worker may pass request to network AND browser makes its own request
2. Navigation preload in some browsers
3. PWA shell reload behavior

This module provides idempotent OAuth callback views that handle duplicates gracefully
by tracking processed callbacks in the session.
"""
import logging

from django.shortcuts import redirect
from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView

logger = logging.getLogger(__name__)


class IdempotentOAuth2CallbackView(OAuth2CallbackView):
    """
    OAuth2 callback view that handles duplicate requests gracefully.

    OAuth authorization codes are one-time use. In PWA mode, duplicate requests
    can occur within milliseconds, causing the second request to fail with an
    "already used" error even though the user was successfully authenticated
    by the first request.

    This view:
    1. Checks if the callback state was already processed (stored in session)
    2. If duplicate: redirects to dashboard (if authenticated) or login page
    3. If new: processes normally and marks state as processed on success
    """

    # Override in subclass for provider-specific success URLs
    success_url = '/dashboard/'
    login_url = '/login/'

    def dispatch(self, request, *args, **kwargs):
        """Handle the OAuth callback with duplicate detection."""
        state = request.GET.get('state', '')
        code = request.GET.get('code', '')

        # Generate a unique key for this callback attempt
        # Using state is sufficient as it's unique per OAuth flow
        callback_key = f"oauth_callback_processed_{state}"

        # Check if this exact callback was already processed
        if state and request.session.get(callback_key):
            logger.info(
                f"Duplicate OAuth callback detected for state={state[:8]}... "
                f"User authenticated: {request.user.is_authenticated}"
            )

            if request.user.is_authenticated:
                # User was successfully authenticated by first request
                # Redirect to success URL
                logger.info(f"Redirecting authenticated user to {self.success_url}")
                return redirect(self.success_url)
            else:
                # State was consumed but user not logged in
                # This shouldn't happen normally, redirect to login
                logger.warning(
                    f"OAuth state consumed but user not authenticated. "
                    f"Redirecting to {self.login_url}"
                )
                return redirect(self.login_url)

        # Not a duplicate - process normally
        logger.debug(f"Processing OAuth callback for state={state[:8] if state else 'none'}...")

        try:
            response = super().dispatch(request, *args, **kwargs)
        except Exception as e:
            logger.error(f"OAuth callback error: {e}", exc_info=True)
            raise

        # If successful (302 redirect means auth completed), mark this callback as processed
        if response.status_code == 302 and state:
            request.session[callback_key] = True
            # Ensure session is saved
            request.session.modified = True
            logger.info(
                f"OAuth callback successful for state={state[:8]}... "
                f"Marked as processed in session"
            )

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
