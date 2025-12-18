"""
Popup OAuth views for Crush.lu.

This module implements popup-based OAuth authentication that works seamlessly
with PWA installations. Instead of redirecting the main window to Facebook,
we open a popup window that handles the OAuth flow and communicates back
to the parent window via postMessage.

CRITICAL FIX (Android PWA Cookie Timing):
On Android Chrome WebView, 302 redirects fire BEFORE session cookies are
committed to storage. This causes the session to be lost after OAuth.
Solution: Return 200 OK with a JavaScript-delayed redirect (400ms) to allow
Chrome to commit cookies before navigating.
"""

import json
import logging
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from django.http import JsonResponse

logger = logging.getLogger(__name__)


@require_http_methods(["GET"])
def oauth_popup_callback(request):
    """
    OAuth callback that renders a template with postMessage to parent.

    This view is loaded in the popup window after OAuth completes (success or failure).
    It renders a minimal HTML page that:
    1. Checks authentication status
    2. Posts result to parent window via postMessage
    3. Closes itself automatically

    The actual OAuth token exchange is handled by Allauth's callback view.
    This view is only called AFTER Allauth redirects here upon completion.
    """
    # Determine success/failure based on authentication state
    is_authenticated = request.user.is_authenticated
    has_profile = False
    user_name = None

    if is_authenticated:
        has_profile = hasattr(request.user, 'crushprofile')
        user_name = request.user.first_name or request.user.username

    oauth_result = {
        'success': is_authenticated,
        'hasProfile': has_profile,
        'userName': user_name,
    }

    # Determine redirect destination for parent window
    if is_authenticated:
        if has_profile:
            redirect_url = '/dashboard/'
        else:
            redirect_url = '/create-profile/'
    else:
        redirect_url = '/login/'

    # Clear popup mode flag from session
    request.session.pop('oauth_popup_mode', None)

    # Build the allowed origin for postMessage security
    origin = request.build_absolute_uri('/').rstrip('/')

    return render(request, 'crush_lu/oauth_popup_callback.html', {
        'oauth_result': json.dumps(oauth_result),
        'redirect_url': redirect_url,
        'origin': origin,
        'is_authenticated': is_authenticated,
        'user_name': user_name,
        'has_profile': has_profile,
    })


@require_http_methods(["GET"])
def oauth_popup_error(request):
    """
    Handle OAuth errors in popup window.

    Renders error message and communicates failure to parent window.
    This is called when OAuth fails (user cancels, permissions denied, etc.)
    """
    error_code = request.GET.get('error', 'unknown')
    error_description = request.GET.get('error_description', 'Authentication failed. Please try again.')

    # Clear popup mode flag from session
    request.session.pop('oauth_popup_mode', None)

    # Build the allowed origin for postMessage security
    origin = request.build_absolute_uri('/').rstrip('/')

    return render(request, 'crush_lu/oauth_popup_error.html', {
        'error_code': error_code,
        'error_description': error_description,
        'origin': origin,
    })


@never_cache
@require_http_methods(["GET"])
def check_auth_status(request):
    """
    AJAX endpoint to check authentication status.

    Called by:
    1. Parent window after popup closes to verify login succeeded
    2. OAuth landing page polling to wait for cookie commit

    This provides a reliable way to check if the session cookie is readable.

    Returns JSON with:
    - authenticated: boolean
    - has_profile: boolean (if authenticated)
    - user_name: string (if authenticated)
    - redirect_url: string - where to redirect the user
    """
    if request.user.is_authenticated:
        has_profile = hasattr(request.user, 'crushprofile')
        response = JsonResponse({
            'authenticated': True,
            'has_profile': has_profile,
            'user_name': request.user.first_name or request.user.username,
            'redirect_url': '/dashboard/' if has_profile else '/create-profile/',
        })
    else:
        response = JsonResponse({
            'authenticated': False,
            'redirect_url': '/login/',
        })

    # Aggressive no-cache headers - critical for cookie commit polling
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'

    return response


@never_cache
@require_http_methods(["GET"])
def oauth_landing(request):
    """
    OAuth landing page with polling-based cookie commit buffer.

    CRITICAL FIX FOR ANDROID PWA:
    On Android Chrome WebView (PWA), 302 redirects fire BEFORE the session
    cookie is committed to storage. This causes the session to be lost.

    ALSO: Workbox service worker navigation replay can cause duplicate callbacks.

    Solution:
    1. Return 200 OK with aggressive no-cache headers
    2. Use JavaScript polling with 400ms initial delay
    3. Poll /api/auth/status/ until authenticated (max 3 seconds)
    4. This allows Android Chrome to commit cookies before redirecting

    This view is the target for ALL OAuth completions on Crush.lu, replacing
    direct 302 redirects to /dashboard/ or /create-profile/.
    """
    # Check if this is popup mode (do this before any early returns)
    is_popup = request.session.pop('oauth_popup_mode', False)

    # Clear OAuth provider flag
    request.session.pop('oauth_provider', None)

    # Determine authentication state and destination
    is_authenticated = request.user.is_authenticated
    has_profile = False
    user_name = ''
    redirect_url = '/login/'

    if is_authenticated:
        has_profile = hasattr(request.user, 'crushprofile')
        user_name = request.user.first_name or request.user.username
        redirect_url = '/dashboard/' if has_profile else '/create-profile/'

    logger.info(f"OAuth landing: authenticated={is_authenticated}, user={request.user.username if is_authenticated else 'anonymous'}, popup={is_popup}, redirect={redirect_url}")

    response = render(request, 'crush_lu/oauth_landing.html', {
        'redirect_url': redirect_url,
        'is_popup': is_popup,
        'has_profile': has_profile,
        'user_name': user_name,
        'is_authenticated': is_authenticated,
    })

    # Aggressive no-cache headers to prevent SW and browser caching
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'

    return response
