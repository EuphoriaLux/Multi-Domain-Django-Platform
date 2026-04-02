"""
PWA and Special Experience Views for Crush.lu

This module contains:
- PWA-related views (manifest, service worker, offline page)
- Special user experience views (VIP welcome pages)
- Debug utilities for PWA diagnostics
"""

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
import logging

logger = logging.getLogger(__name__)

from .models import SpecialUserExperience
from .decorators import crush_login_required


# ============================================================================
# Special User Experience View
# ============================================================================


@crush_login_required
def special_welcome(request):
    """
    Special welcome page for VIP users with custom animations and experience.
    Only accessible if special_experience_active is set in session.

    If a journey is configured for this user, redirect to journey_map instead.
    """
    # Check if special experience is active in session
    if not request.session.get("special_experience_active"):
        messages.warning(request, _("This page is not available."))
        # Redirect to home instead of dashboard (special users don't need profiles)
        return redirect("crush_lu:home")

    # Get special experience data from session
    special_experience_data = request.session.get("special_experience_data", {})

    # Get the full SpecialUserExperience object for complete data
    special_experience_id = request.session.get("special_experience_id")
    try:
        special_experience = SpecialUserExperience.objects.get(
            id=special_experience_id, is_active=True
        )
    except SpecialUserExperience.DoesNotExist:
        # Clear session data if experience is not found or inactive
        request.session.pop("special_experience_active", None)
        request.session.pop("special_experience_id", None)
        request.session.pop("special_experience_data", None)
        messages.warning(request, _("This special experience is no longer available."))
        return redirect("crush_lu:home")

    # ============================================================================
    # NEW: Check if journey is configured - redirect to appropriate journey type
    # ============================================================================
    from .models import JourneyConfiguration

    # First check for Wonderland journey
    wonderland_journey = JourneyConfiguration.objects.filter(
        special_experience=special_experience, journey_type="wonderland", is_active=True
    ).first()

    if wonderland_journey:
        logger.info(
            f"🎮 Redirecting {request.user.username} to Wonderland journey: {wonderland_journey.journey_name}"
        )
        return redirect("crush_lu:journey_map")

    # Check for Advent Calendar journey
    advent_journey = JourneyConfiguration.objects.filter(
        special_experience=special_experience,
        journey_type="advent_calendar",
        is_active=True,
    ).first()

    if advent_journey:
        logger.info(f"🎄 Redirecting {request.user.username} to Advent Calendar")
        return redirect("crush_lu:advent_calendar")

    # No journey configured - show simple welcome page
    # ============================================================================

    context = {
        "special_experience": special_experience,
    }

    # Mark session as viewed (only show once per login)
    request.session["special_experience_viewed"] = True

    return render(request, "crush_lu/special_welcome.html", context)


# ============================================================================
# PWA Views
# ============================================================================


def offline_view(request):
    """
    Offline fallback page for PWA
    Displayed when user is offline and tries to access unavailable content
    """
    return render(request, "crush_lu/offline.html")


def service_worker_view(request):
    """
    Serve the Workbox service worker from root path
    This is required to give the service worker access to the entire site scope
    """
    from django.http import HttpResponse
    from django.conf import settings
    import os

    # Read the service worker file from app-specific static folder
    sw_path = os.path.join(
        settings.BASE_DIR, "crush_lu", "static", "crush_lu", "sw-workbox.js"
    )

    try:
        with open(sw_path, "r", encoding="utf-8") as f:
            sw_content = f.read()

        # Return with correct MIME type
        response = HttpResponse(sw_content, content_type="application/javascript")

        # IMPORTANT: Never cache SW script as immutable - it must be revalidated
        # so updates propagate to users. The SW itself handles internal caching.
        response["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"

        return response
    except FileNotFoundError:
        return HttpResponse("Service worker not found", status=404)


def manifest_view(request):
    """
    Serve the PWA manifest.json with correct static URLs.
    Adds a version query param to force icon refresh on Android/Chrome.
    """
    from django.http import JsonResponse
    from django.templatetags.static import static
    from django.conf import settings

    # Get manifest version for cache busting
    MANIFEST_VERSION = getattr(settings, "PWA_MANIFEST_VERSION", "v1")

    def s(path: str) -> str:
        """Return static URL with version query param for cache busting."""
        return f"{static(path)}?v={MANIFEST_VERSION}"

    manifest = {
        "name": "Crush.lu - Privacy-First Dating in Luxembourg",
        "short_name": "Crush.lu",
        "description": "Event-based dating platform for Luxembourg. Meet people at real events, not endless swiping.",
        "id": "/?source=pwa",
        "start_url": "/?source=pwa",
        "display": "standalone",
        "background_color": "#9B59B6",
        "theme_color": "#9B59B6",
        "orientation": "portrait-primary",
        "scope": "/",
        "icons": [
            {
                "src": s("crush_lu/icons/android-launchericon-48-48.png"),
                "sizes": "48x48",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-72-72.png"),
                "sizes": "72x72",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-96-96.png"),
                "sizes": "96x96",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-144-144.png"),
                "sizes": "144x144",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-192-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-512-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-192-192-maskable.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-512-512-maskable.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
        "screenshots": [
            {
                "src": s("crush_lu/icons/android-launchericon-512-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "form_factor": "narrow",
                "label": "Crush.lu Mobile View",
            },
            {
                "src": s("crush_lu/icons/android-launchericon-512-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "form_factor": "wide",
                "label": "Crush.lu Desktop View",
            },
        ],
        "categories": ["social", "lifestyle"],
        "prefer_related_applications": False,
        "related_applications": [],
        "shortcuts": [
            {
                "name": "Browse Events",
                "short_name": "Events",
                "description": "Browse upcoming meetup events",
                "url": "/events/",
                "icons": [
                    {"src": s("crush_lu/icons/shortcut-events.png"), "sizes": "96x96"}
                ],
            },
            {
                "name": "My Dashboard",
                "short_name": "Dashboard",
                "description": "View your profile and registrations",
                "url": "/dashboard/",
                "icons": [
                    {
                        "src": s("crush_lu/icons/shortcut-dashboard.png"),
                        "sizes": "96x96",
                    }
                ],
            },
            {
                "name": "Connections",
                "short_name": "Connections",
                "description": "View your event connections",
                "url": "/connections/",
                "icons": [
                    {
                        "src": s("crush_lu/icons/shortcut-connections.png"),
                        "sizes": "96x96",
                    }
                ],
            },
        ],
    }

    response = JsonResponse(manifest)
    response["Content-Type"] = "application/manifest+json"
    # Prevent aggressive caching to avoid stale icon issues during updates
    response["Cache-Control"] = "no-cache"
    return response


def assetlinks_view(request):
    """
    Serve assetlinks.json for Android App Links verification.

    This enables Android to verify that Crush.lu PWA can handle all URLs
    from the crush.lu domain, enabling a better OAuth experience in installed PWAs.

    See: https://developer.android.com/training/app-links/verify-android-applinks
    """
    from django.http import JsonResponse

    assetlinks = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {"namespace": "web", "site": "https://crush.lu"},
        }
    ]

    response = JsonResponse(assetlinks, safe=False)
    response["Content-Type"] = "application/json"
    # Allow caching for 24 hours
    response["Cache-Control"] = "public, max-age=86400"
    return response


@login_required
def pwa_debug_view(request):
    """
    Superuser-only PWA debug page showing service worker state, cache info, and diagnostics.
    Useful for debugging PWA issues in production.
    """
    # Only allow Django superusers
    if not request.user.is_superuser:
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden("Superuser access required")

    return render(
        request,
        "crush_lu/pwa_debug.html",
        {
            "sw_version": "crush-v16-icon-cache-fix",  # Keep in sync with sw-workbox.js
        },
    )
