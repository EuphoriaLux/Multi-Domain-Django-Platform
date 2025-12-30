"""
Shared URL patterns used across all domain configurations.

Import these in domain-specific URL files to avoid duplication.

Usage:
    from .urls_shared import base_patterns, api_patterns

    urlpatterns = base_patterns + api_patterns + [
        # Domain-specific patterns here
    ]
"""
from django.urls import path, include
from django.http import HttpResponse
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .views import csp_report
from crush_lu.views_language import set_language_with_profile


def health_check(request):
    """
    Health check endpoint for Azure App Service.

    Returns HTTP 200 "OK" for health probes.
    This endpoint must be accessible without authentication.
    """
    return HttpResponse("OK")


# Base patterns - included by ALL domains
# These provide essential functionality that every domain needs
base_patterns = [
    path('healthz/', health_check, name='health_check'),
    path('csp-report/', csp_report, name='csp_report'),  # CSP violation reports
    # Custom set_language view that also updates CrushProfile.preferred_language
    # This replaces Django's default i18n/setlang/ with our extended version
    path('i18n/setlang/', set_language_with_profile, name='set_language'),
    path('accounts/', include('allauth.urls')),
    path('cookies/', include('cookie_consent.urls')),  # GDPR cookie consent
]

# JWT API patterns - included by domains that need API authentication
# Used by Crush.lu and PowerUP for mobile app / API access
api_patterns = [
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]
