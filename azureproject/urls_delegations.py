# azureproject/urls_delegations.py
"""
URL configuration for delegations.lu domain.

This is the URL config used when requests come from delegations.lu.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from .urls_shared import base_patterns, api_patterns
from delegations.admin import delegations_admin_site

urlpatterns = base_patterns + api_patterns + [
    # Custom Delegations Admin Panel
    path('delegation-admin/', delegations_admin_site.urls),

    # Standard Django Admin (user management)
    path('admin/', admin.site.urls),

    # Delegations app URLs
    path('', include('delegations.urls', namespace='delegations')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
