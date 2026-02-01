"""
PWA infrastructure views for Crush.lu

Service worker, manifest, offline pages, and PWA diagnostics.
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
from django.templatetags.static import static
from django.conf import settings
import os


def offline_view(request):
    """
    Offline fallback page for PWA
    Displayed when user is offline and tries to access unavailable content
    """
    return render(request, 'crush_lu/pwa/offline.html')


def service_worker_view(request):
    """
    Serve the Workbox service worker from root path
    This is required to give the service worker access to the entire site scope
    """
    # Read the service worker file from app-specific static folder
    sw_path = os.path.join(settings.BASE_DIR, 'crush_lu', 'static', 'crush_lu', 'sw-workbox.js')

    try:
        with open(sw_path, 'r', encoding='utf-8') as f:
            sw_content = f.read()

        # Return with correct MIME type
        response = HttpResponse(sw_content, content_type='application/javascript')

        # IMPORTANT: Never cache SW script as immutable - it must be revalidated
        # so updates propagate to users. The SW itself handles internal caching.
        response['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'

        return response
    except FileNotFoundError:
        return HttpResponse('Service worker not found', status=404)


def manifest_view(request):
    """
    Serve the PWA manifest.json with correct static URLs.
    Adds a version query param to force icon refresh on Android/Chrome.
    """
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
                "src": s('crush_lu/icons/android-launchericon-48-48.png'),
                "sizes": "48x48",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-72-72.png'),
                "sizes": "72x72",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-96-96.png'),
                "sizes": "96x96",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-144-144.png'),
                "sizes": "144x144",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-192-192.png'),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-512-512.png'),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-192-192-maskable.png'),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "maskable"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-512-512-maskable.png'),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable"
            }
        ],
        "screenshots": [
            {
                "src": s('crush_lu/icons/android-launchericon-512-512.png'),
                "sizes": "512x512",
                "type": "image/png",
                "form_factor": "narrow",
                "label": "Crush.lu Mobile View"
            },
            {
                "src": s('crush_lu/icons/android-launchericon-512-512.png'),
                "sizes": "512x512",
                "type": "image/png",
                "form_factor": "wide",
                "label": "Crush.lu Desktop View"
            }
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
                    {
                        "src": s('crush_lu/icons/shortcut-events.png'),
                        "sizes": "96x96"
                    }
                ]
            },
            {
                "name": "My Dashboard",
                "short_name": "Dashboard",
                "description": "View your profile and registrations",
                "url": "/dashboard/",
                "icons": [
                    {
                        "src": s('crush_lu/icons/shortcut-dashboard.png'),
                        "sizes": "96x96"
                    }
                ]
            },
            {
                "name": "Connections",
                "short_name": "Connections",
                "description": "View your event connections",
                "url": "/connections/",
                "icons": [
                    {
                        "src": s('crush_lu/icons/shortcut-connections.png'),
                        "sizes": "96x96"
                    }
                ]
            }
        ]
    }

    response = JsonResponse(manifest)
    response['Content-Type'] = 'application/manifest+json'
    # Prevent aggressive caching to avoid stale icon issues during updates
    response['Cache-Control'] = 'no-cache'
    return response


def assetlinks_view(request):
    """
    Serve assetlinks.json for Android App Links verification.

    This enables Android to verify that Crush.lu PWA can handle all URLs
    from the crush.lu domain, enabling a better OAuth experience in installed PWAs.

    See: https://developer.android.com/training/app-links/verify-android-applinks
    """
    assetlinks = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "web",
                "site": "https://crush.lu"
            }
        }
    ]

    response = JsonResponse(assetlinks, safe=False)
    response['Content-Type'] = 'application/json'
    # Allow caching for 24 hours
    response['Cache-Control'] = 'public, max-age=86400'
    return response


@login_required
def pwa_debug_view(request):
    """
    Superuser-only PWA debug page showing service worker state, cache info, and diagnostics.
    Useful for debugging PWA issues in production.
    """
    # Only allow Django superusers
    if not request.user.is_superuser:
        return HttpResponseForbidden('Superuser access required')

    return render(request, 'crush_lu/pwa/pwa_debug.html')
