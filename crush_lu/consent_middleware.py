"""
Middleware to enforce Crush.lu consent requirements.

Ensures all authenticated users have given Crush.lu consent before accessing
protected features. Users without consent are redirected to a consent page.

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

    If a user is authenticated but hasn't given Crush.lu consent, redirect them
    to a consent confirmation page before allowing access to protected features.

    Exemptions:
    - Public pages (login, signup, landing pages)
    - Consent confirmation page itself
    - API endpoints
    - Static files
    - Admin panel
    """

    # Paths that don't require consent
    EXEMPT_PATHS = [
        '/login/',
        '/signup/',
        '/logout/',
        '/consent/confirm/',  # The consent confirmation page
        '/account/banned/',  # Banned user info page
        '/api/',
        '/static/',
        '/media/',
        '/admin/',
        '/crush-admin/',
        '/accounts/',  # Allauth endpoints
        '/healthz/',
        '/robots.txt',
        '/sitemap.xml',
        '/favicon.ico',
        '/',  # Landing page
        '/fr/',
        '/de/',
        '/en/',
    ]

    # Paths that require consent (profile features)
    PROTECTED_PATHS = [
        '/dashboard/',
        '/profile/',
        '/events/',
        '/connections/',
        '/messages/',
        '/create-profile/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check ban status before anything else (for authenticated Crush.lu users)
        if self.is_on_crush_domain(request) and request.user.is_authenticated:
            if self.is_banned(request.user):
                path = request.path
                # Allow access to banned page, logout, and static assets
                if not any(path.startswith(p) for p in ['/account/banned/', '/logout/', '/static/', '/media/', '/healthz/']):
                    logger.info(f"Banned user {request.user.id} redirected from {path} to banned page")
                    return redirect('crush_lu:account_banned')

        # Check if consent is required
        if self.requires_consent_check(request):
            # Check if user has consent
            if not self.has_crushlu_consent(request.user):
                logger.info(f"User {request.user.id} attempted to access {request.path} without Crush.lu consent")
                return redirect('crush_lu:consent_confirm')

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

        Returns True if:
        - Request is on the Crush.lu domain
        - User is authenticated
        - Path is not exempt
        - Path is protected
        """
        # Only apply on Crush.lu domain
        if getattr(request, 'urlconf', None) != CRUSH_URLCONF:
            return False

        # Not authenticated - no check needed
        if not request.user.is_authenticated:
            return False

        # Check if path is explicitly exempt
        path = request.path
        for exempt_path in self.EXEMPT_PATHS:
            if path.startswith(exempt_path):
                return False

        # Check if path is protected
        for protected_path in self.PROTECTED_PATHS:
            if path.startswith(protected_path) or f'/en{protected_path}' in path or f'/fr{protected_path}' in path or f'/de{protected_path}' in path:
                return True

        return False

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
