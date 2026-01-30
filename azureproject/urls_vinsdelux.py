# azureproject/urls_vinsdelux.py
"""
URL configuration for VinsDelux wine adoption portfolio showcase.

This is the URL config used when requests come from vinsdelux.com domain.
Simplified to portfolio-only (no e-commerce functionality).
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from .urls_shared import base_patterns
from .views_seo import robots_txt_vinsdelux
from entreprinder.vibe import views as vibe_views
from vinsdelux.admin import vinsdelux_admin_site

urlpatterns = base_patterns + [
    # SEO - robots.txt
    path('robots.txt', robots_txt_vinsdelux, name='robots_txt'),

    # Custom VinsDelux Admin Panel
    path('vinsdelux-admin/', vinsdelux_admin_site.urls),

    # Standard Django Admin (user management)
    path('admin/', admin.site.urls),

    # Crush.lu URLs (for local development convenience)
    path('crush/', include('crush_lu.urls', namespace='crush_lu')),

    # VinsDelux app URLs (root)
    path('', include('vinsdelux.urls', namespace='vinsdelux')),

    # Vibe Coding game (merged into entreprinder)
    path('vibe/', vibe_views.index, name='vinsdelux_vibe'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
