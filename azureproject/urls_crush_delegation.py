# azureproject/urls_crush_delegation.py
"""
URL configuration for delegation.crush.lu subdomain.

This is the URL config used when requests come from delegation.crush.lu.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from .urls_shared import base_patterns, api_patterns
from crush_delegation.admin import crush_delegation_admin_site

urlpatterns = base_patterns + api_patterns + [
    # Custom Crush Delegation Admin Panel
    path('delegation-admin/', crush_delegation_admin_site.urls),

    # Standard Django Admin (user management)
    path('admin/', admin.site.urls),

    # Crush Delegation app URLs
    path('', include('crush_delegation.urls', namespace='crush_delegation')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)