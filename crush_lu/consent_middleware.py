"""
Middleware to enforce Crush.lu consent requirements.

Uses a deny-by-default approach: all authenticated Crush.lu requests require
consent unless the path is explicitly exempt. Users without consent are
redirected to the consent confirmation page.

Only active on the Crush.lu domain (checks request.urlconf).
"""
import logging
from django.shortcuts import redirect
from django.urls import reverse
from django.contrib import messages
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)

CRUSH_URLCONF = 'azureproject.urls_crush'


class CrushConsentMiddleware:
    """
    Middleware to check if authenticated users have given Crush.lu consent.

    Uses a deny-by-default approach: all authenticated requests on the Crush.lu
    domain require consent UNLESS the path is in EXEMPT_PATHS. This ensures new
    routes are automatically protected without needing to update an allowlist.

    Exempt paths include auth flows, public pages, infrastructure endpoints,
    and the consent page itself (to avoid redirect loops).
    """

    # Paths that DON'T require consent - everything else does.
    # Uses prefix matching: '/about/' matches '/about/', '/about/team/', etc.
    EXEMPT_PATHS = [
        # Auth
        '/login/',
        '/signup/',
        '/logout/',
        '/oauth-complete/',
        '/oauth/',
        '/accounts/',  # Allauth endpoints

        # Consent & ban flow (must be exempt to avoid redirect loops)
        '/consent/confirm/',
        '/account/banned/',

        # Public/landing pages
        '/about/',
        '/how-it-works/',
        '/membership/',
        '/privacy-policy/',
        '/terms-of-service/',
        '/data-deletion/',
        '/test-ghost-story/',
        '/test-upstair/',

        # Public landing pages
        '/r/',  # Referral redirect
        '/invite/',  # Invitation landing
        '/unsubscribe/',
        '/facebook/',  # Data deletion callback
        '/voting-demo/',

        # LuxID mockups
        '/mockup/',

        # Infrastructure
        '/api/',
        '/static/',
        '/media/',
        '/admin/',
        '/crush-admin/',
        '/healthz/',
        '/robots.txt',
        '/sitemap.xml',
        '/favicon.ico',
        '/pwa-debug/',
        '/sw-workbox.js',
        '/manifest.json',
        '/offline/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check ban status before anything else (for authenticated Crush.lu users)
        if self.is_on_crush_domain(request) and request.user.is_authenticated:
            if self.is_banned(request.user):
                path = request.path
                # Allow access to banned page, logout, and static assets
                if not any(path.startswith(p) for p in ['/account/banned/', '/logout/', '/static/', '/media/', '/healthz/']) and '/account/banned/' not in path:
                    logger.info(f"Banned user {request.user.id} redirected from {path} to banned page")
                    return redirect(reverse('crush_lu:account_banned', urlconf=CRUSH_URLCONF))

        # Check if consent is required
        if self.requires_consent_check(request):
            # Check if user has consent
            if not self.has_crushlu_consent(request.user):
                logger.info(f"User {request.user.id} attempted to access {request.path} without Crush.lu consent")
                return redirect(reverse('crush_lu:consent_confirm', urlconf=CRUSH_URLCONF))

        response = self.get_response(request)
        return response

    def is_on_crush_domain(self, request):
        """Check if request is on the Crush.lu domain."""
        return getattr(request, 'urlconf', None) == CRUSH_URLCONF

    def is_banned(self, user):
        """Check if user is banned from Crush.lu."""
        if not hasattr(user, 'data_consent'):
            return False
        return user.data_consent.crushlu_banned

    def requires_consent_check(self, request):
        """
        Determine if this request requires consent checking.

        Uses a deny-by-default approach: all paths require consent unless
        explicitly listed in EXEMPT_PATHS. Language prefixes (/en/, /fr/, /de/)
        are stripped before matching.

        Returns True if:
        - Request is on the Crush.lu domain
        - User is authenticated
        - Path is not in EXEMPT_PATHS
        """
        # Only apply on Crush.lu domain
        if getattr(request, 'urlconf', None) != CRUSH_URLCONF:
            return False

        # Not authenticated - no check needed
        if not request.user.is_authenticated:
            return False

        path = request.path

        # Strip language prefix for consistent matching
        for lang_prefix in ['/en/', '/fr/', '/de/']:
            if path.startswith(lang_prefix):
                path = '/' + path[len(lang_prefix):]
                break

        # Check if path is exempt (exact match or prefix match)
        for exempt_path in self.EXEMPT_PATHS:
            if path == exempt_path or path.startswith(exempt_path):
                return False

        # The bare root path '/' is always exempt (landing page)
        if path == '/':
            return False

        # All non-exempt paths require consent for authenticated users
        return True

    def has_crushlu_consent(self, user):
        """
        Check if user has given Crush.lu consent.

        Returns True if:
        - User has UserDataConsent record with crushlu_consent_given=True
        """
        if not hasattr(user, 'data_consent'):
            # No consent record - should not happen with signals, but be safe
            logger.warning(f"User {user.id} has no data_consent record")
            return False

        return user.data_consent.crushlu_consent_given
