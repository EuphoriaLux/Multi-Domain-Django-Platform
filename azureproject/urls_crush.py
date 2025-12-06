# azureproject/urls_crush.py
"""
URL configuration for Crush.lu dating platform.

This is the URL config used when requests come from crush.lu domain.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from .urls_shared import base_patterns, api_patterns
from crush_lu.admin import crush_admin_site
from crush_lu import admin_views

urlpatterns = base_patterns + api_patterns + [
    # Dedicated Crush.lu Admin Panel
    path('crush-admin/', crush_admin_site.urls),
    path('crush-admin/dashboard/', admin_views.crush_admin_dashboard, name='crush_admin_dashboard'),

    # Standard Django Admin (all platforms)
    path('admin/', admin.site.urls),

    # Crush.lu app URLs
    path('', include('crush_lu.urls', namespace='crush_lu')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
