"""
Context processors for global admin navigation across all platforms.

This module provides the admin navigation context processor that enables
superusers to switch between different platform admin panels.
"""
from django.conf import settings


# Platform configurations for admin navigation
# Each platform has its own custom admin panel at a dedicated path
ADMIN_PLATFORMS = [
    {
        'name': 'Django Admin',
        'icon': '',
        'domain': None,  # Uses current domain with relative path
        'path': '/admin/',
        'key': 'default',
        'description': 'Core user management',
    },
    {
        'name': 'Crush.lu',
        'icon': '',
        'domain': 'crush.lu',
        'path': '/crush-admin/',
        'key': 'crush',
        'description': 'Dating platform coach panel',
    },
    {
        'name': 'PowerUP',
        'icon': '',
        'domain': 'powerup.lu',
        'path': '/powerup-admin/',
        'key': 'powerup',
        'description': 'Business networking admin',
    },
    {
        'name': 'VinsDelux',
        'icon': '',
        'domain': 'vinsdelux.com',
        'path': '/vinsdelux-admin/',
        'key': 'vinsdelux',
        'description': 'Wine e-commerce admin',
    },
    {
        'name': 'Delegation',
        'icon': '',
        'domain': 'delegation.crush.lu',
        'path': '/delegation-admin/',
        'key': 'delegation',
        'description': 'Company access management',
    },
]

# Development domain mappings - maps production domains to localhost paths
DEV_DOMAIN_PATHS = {
    'crush.lu': '/crush-admin/',
    'powerup.lu': '/powerup-admin/',
    'vinsdelux.com': '/vinsdelux-admin/',
    'delegation.crush.lu': '/delegation-admin/',
}


def _get_current_platform_key(request):
    """
    Determine which platform admin the user is currently viewing.

    Returns the platform key based on the current URL path.
    Each platform has its own dedicated admin path, so we only need
    to check the path - not the host.
    """
    path = request.path

    # Check URL path for custom admin panels
    # Order matters: check specific paths before generic /admin/
    if '/crush-admin/' in path:
        return 'crush'
    if '/powerup-admin/' in path:
        return 'powerup'
    if '/vinsdelux-admin/' in path:
        return 'vinsdelux'
    if '/delegation-admin/' in path:
        return 'delegation'

    # Standard Django Admin at /admin/ is always 'default'
    # regardless of which domain we're on
    if '/admin/' in path:
        return 'default'

    return 'default'


def _build_admin_url(platform, request, is_development):
    """
    Build the full URL for a platform's admin panel.

    In development mode, uses localhost with the appropriate path.
    In production, uses absolute URLs with full domain names.
    """
    from django.utils import translation

    domain = platform['domain']
    path = platform['path']

    # Get current language prefix for i18n URLs
    current_language = translation.get_language() or 'en'

    # Django Admin uses relative path (same domain) but needs language prefix
    if domain is None:
        return f'/{current_language}{path}'

    # Build language-prefixed path for i18n URLs
    lang_path = f'/{current_language}{path}'

    if is_development:
        # In development, all admins are on localhost
        # Use the path directly since domain routing is based on path
        host = request.get_host()
        protocol = 'https' if request.is_secure() else 'http'
        return f"{protocol}://{host}{lang_path}"
    else:
        # Production: use absolute URLs with HTTPS and language prefix
        return f"https://{domain}{lang_path}"


def admin_navigation(request):
    """
    Context processor that provides admin navigation links for superusers.

    This allows superusers to quickly switch between different platform
    admin panels (Crush.lu, PowerUP, VinsDelux, Delegation).

    The navigation is only visible to superusers and appears in the
    admin header area of all admin panels.

    Context variables:
        - admin_platforms: List of platform navigation items with URLs
        - current_admin_platform: Key of the currently active platform
        - show_admin_navigation: Boolean indicating if nav should be shown
    """
    # Only provide navigation data for authenticated superusers
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return {
            'admin_platforms': [],
            'current_admin_platform': None,
            'show_admin_navigation': False,
        }

    if not request.user.is_superuser:
        return {
            'admin_platforms': [],
            'current_admin_platform': None,
            'show_admin_navigation': False,
        }

    # Determine if we're in development mode
    is_development = getattr(settings, 'DEBUG', False)

    # Get current platform
    current_platform = _get_current_platform_key(request)

    # Build navigation items with URLs
    platforms = []
    for platform in ADMIN_PLATFORMS:
        url = _build_admin_url(platform, request, is_development)
        platforms.append({
            'name': platform['name'],
            'icon': platform['icon'],
            'url': url,
            'key': platform['key'],
            'description': platform['description'],
            'is_current': platform['key'] == current_platform,
        })

    return {
        'admin_platforms': platforms,
        'current_admin_platform': current_platform,
        'show_admin_navigation': True,
    }
