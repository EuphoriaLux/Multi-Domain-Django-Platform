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

from .adapter import get_i18n_redirect_url
from .models import CrushProfile

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
        try:
            request.user.crushprofile
            has_profile = True
        except CrushProfile.DoesNotExist:
            has_profile = False
        user_name = request.user.first_name or request.user.username

    oauth_result = {
        "success": is_authenticated,
        "hasProfile": has_profile,
        "userName": user_name,
    }

    # Determine redirect destination for parent window (with language prefix)
    if is_authenticated:
        if has_profile:
            redirect_url = get_i18n_redirect_url(
                request, "crush_lu:dashboard", request.user
            )
        else:
            redirect_url = get_i18n_redirect_url(request, "crush_lu:create_profile")
    else:
        redirect_url = get_i18n_redirect_url(request, "crush_lu:login")

    # Clear popup mode flag from session
    request.session.pop("oauth_popup_mode", None)

    # Build the allowed origin for postMessage security
    origin = request.build_absolute_uri("/").rstrip("/")

    return render(
        request,
        "crush_lu/oauth_popup_callback.html",
        {
            "oauth_result": oauth_result,
            "redirect_url": redirect_url,
            "origin": origin,
            "is_authenticated": is_authenticated,
            "user_name": user_name,
            "has_profile": has_profile,
        },
    )


@require_http_methods(["GET"])
def oauth_popup_error(request):
    """
    Handle OAuth errors in popup window.

    Renders error message and communicates failure to parent window.
    This is called when OAuth fails (user cancels, permissions denied, etc.)
    """
    error_code = request.GET.get("error", "unknown")
    error_description = request.GET.get(
        "error_description", "Authentication failed. Please try again."
    )

    # Clear popup mode flag from session
    request.session.pop("oauth_popup_mode", None)

    # Build the allowed origin for postMessage security
    origin = request.build_absolute_uri("/").rstrip("/")

    return render(
        request,
        "crush_lu/oauth_popup_error.html",
        {
            "error_code": error_code,
            "error_description": error_description,
            "origin": origin,
        },
    )


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
    # Recovery can arrive a polling tick after the landing navigation when
    # the session cookie is delayed. Apply the same proof gate on every retry.
    state_id = request.GET.get("state", "")
    if state_id:
        from .oauth_recovery import recover_callback_login

        try:
            recover_callback_login(request, state_id)
        except Exception:
            logger.exception("[OAUTH-STATUS] Callback recovery failed")

    if request.user.is_authenticated:
        try:
            request.user.crushprofile
            has_profile = True
        except CrushProfile.DoesNotExist:
            has_profile = False
        if has_profile:
            redirect_url = get_i18n_redirect_url(
                request, "crush_lu:dashboard", request.user
            )
        else:
            redirect_url = get_i18n_redirect_url(request, "crush_lu:create_profile")
        from .mobile_auth import peek_mobile_handoff_url

        redirect_url = peek_mobile_handoff_url(request) or redirect_url
        response = JsonResponse(
            {
                "authenticated": True,
                "has_profile": has_profile,
                "user_name": request.user.first_name or request.user.username,
                "redirect_url": redirect_url,
            }
        )
    else:
        response = JsonResponse(
            {
                "authenticated": False,
                "redirect_url": get_i18n_redirect_url(request, "crush_lu:login"),
            }
        )

    # Aggressive no-cache headers - critical for cookie commit polling
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    response["Referrer-Policy"] = "no-referrer"

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

    Recovery requires a short-lived HttpOnly credential issued only to the
    browser that completed provider authentication. The public OAuth state ID
    alone never authenticates a visitor, including after a cross-browser flow.

    Solution:
    1. Atomically consume callback proof before recovering a lost session
    2. Return 200 OK with aggressive no-cache headers
    3. Use JavaScript polling with 400ms initial delay
    4. Poll /api/auth/status/ until authenticated (max 3 seconds)
    5. This allows Android Chrome to commit cookies before redirecting

    This view is the target for ALL OAuth completions on Crush.lu, replacing
    direct 302 redirects to /dashboard/ or /create-profile/.
    """
    # Debug logging for request metadata (enable via Django logging config if needed)
    logger.debug(
        f"[OAUTH] Landing page: sec-fetch-mode={request.META.get('HTTP_SEC_FETCH_MODE', 'unknown')}"
    )

    # Get state parameter (passed by middleware for duplicate request handling)
    state_id = request.GET.get("state", "")

    # State is correlation data. Only callback-issued proof (or the matching
    # authenticated user) can consume fresh, single-use recovery metadata.
    recovered_state = None
    if state_id:
        from .oauth_recovery import recover_callback_login

        try:
            recovered_state = recover_callback_login(request, state_id)
        except Exception:
            logger.exception("[OAUTH-LANDING] Callback recovery failed")

    is_popup = request.session.pop("oauth_popup_mode", False)
    if recovered_state:
        is_popup = is_popup or recovered_state.is_popup

    # Clear OAuth provider flag
    request.session.pop("oauth_provider", None)

    # Native-app auth sheet: resume the handoff instead of landing on the
    # website. This view bypasses allauth's post_login (where the handoff is
    # normally honoured), so it has to make the decision itself — including for
    # the database-recovery login above, whose session is brand new and
    # therefore carries no handoff flag. Prefer the OAuth state (survives the
    # cross-browser/replay paths) and fall back to the session flag.
    if request.user.is_authenticated:
        from .mobile_auth import is_mobile_handoff_path, peek_mobile_handoff_url

        _handoff_url = None
        if recovered_state:
            try:
                _next = (json.loads(recovered_state.state_data) or {}).get("next")
                if is_mobile_handoff_path(_next):
                    _handoff_url = _next
            except (ValueError, TypeError, AttributeError):
                logger.warning("[OAUTH-LANDING] Invalid handoff metadata")

        if not _handoff_url:
            _handoff_url = peek_mobile_handoff_url(request)

        if _handoff_url:
            logger.info("[OAUTH-LANDING] Resuming native-app auth handoff")
            response = redirect(_handoff_url)
            response["Referrer-Policy"] = "no-referrer"
            return response

    # Determine authentication state and destination (with language prefix)
    is_authenticated = request.user.is_authenticated
    has_profile = False
    user_name = ""
    redirect_url = get_i18n_redirect_url(request, "crush_lu:login")

    if is_authenticated:
        try:
            request.user.crushprofile
            has_profile = True
        except CrushProfile.DoesNotExist:
            has_profile = False
        user_name = request.user.first_name or request.user.username

        # Check for Apple "Hide My Email" linking prompt
        if request.session.pop("apple_relay_needs_linking", False):
            redirect_url = get_i18n_redirect_url(request, "crush_lu:apple_link_prompt")
        elif has_profile:
            redirect_url = get_i18n_redirect_url(
                request, "crush_lu:dashboard", request.user
            )
        else:
            redirect_url = get_i18n_redirect_url(request, "crush_lu:create_profile")

    logger.info(
        f"OAuth landing: authenticated={is_authenticated}, "
        f"user={request.user.username if is_authenticated else 'anonymous'}, "
        f"popup={is_popup}, redirect={redirect_url}, state={state_id[:8] + '...' if state_id else 'none'}"
    )

    response = render(
        request,
        "crush_lu/oauth_landing.html",
        {
            "redirect_url": redirect_url,
            "is_popup": is_popup,
            "has_profile": has_profile,
            "user_name": user_name,
            "is_authenticated": is_authenticated,
            "state_id": state_id,  # Pass state to template for JS polling fallback
        },
    )

    # Aggressive no-cache headers to prevent SW and browser caching
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    response["Referrer-Policy"] = "no-referrer"

    return response
