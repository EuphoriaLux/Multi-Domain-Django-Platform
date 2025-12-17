"""
Popup OAuth views for Crush.lu.

This module implements popup-based OAuth authentication that works seamlessly
with PWA installations. Instead of redirecting the main window to Facebook,
we open a popup window that handles the OAuth flow and communicates back
to the parent window via postMessage.
"""

import json
import logging
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
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


@require_http_methods(["GET"])
def check_auth_status(request):
    """
    AJAX endpoint to check authentication status.

    Called by parent window after popup closes to verify login succeeded.
    This provides a fallback mechanism if postMessage fails.

    Returns JSON with:
    - authenticated: boolean
    - has_profile: boolean (if authenticated)
    - user_name: string (if authenticated)
    - redirect_url: string - where to redirect the user
    """
    if request.user.is_authenticated:
        has_profile = hasattr(request.user, 'crushprofile')
        return JsonResponse({
            'authenticated': True,
            'has_profile': has_profile,
            'user_name': request.user.first_name or request.user.username,
            'redirect_url': '/dashboard/' if has_profile else '/create-profile/',
        })

    return JsonResponse({
        'authenticated': False,
        'redirect_url': '/login/',
    })
