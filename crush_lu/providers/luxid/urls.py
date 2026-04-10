"""
LuxID URL patterns.

Generates:
    /accounts/luxid/login/
    /accounts/luxid/login/callback/
"""

from allauth.socialaccount.providers.oauth2.urls import default_urlpatterns

from .provider import LuxIDProvider

urlpatterns = default_urlpatterns(LuxIDProvider)
