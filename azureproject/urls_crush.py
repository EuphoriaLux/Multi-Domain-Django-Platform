# azureproject/urls_crush.py
"""
URL configuration for Crush.lu dating platform.

This is the URL config used when requests come from crush.lu domain.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap

from .urls_shared import base_patterns, api_patterns
from crush_lu.admin import crush_admin_site
from crush_lu import admin_views
from crush_lu.sitemaps import crush_sitemaps
from crush_lu.views_seo import robots_txt

urlpatterns = base_patterns + api_patterns + [
    # SEO: robots.txt and sitemap.xml
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': crush_sitemaps}, name='django.contrib.sitemaps.views.sitemap'),

    # Dedicated Crush.lu Admin Panel
    # Note: Dashboard must come BEFORE admin site to avoid path matching issues
    path('crush-admin/dashboard/', admin_views.crush_admin_dashboard, name='crush_admin_dashboard'),
    path('crush-admin/', crush_admin_site.urls),

    # Standard Django Admin (all platforms)
    path('admin/', admin.site.urls),

    # Crush.lu app URLs
    path('', include('crush_lu.urls', namespace='crush_lu')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
