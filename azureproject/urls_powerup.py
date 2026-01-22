# azureproject/urls_powerup.py
"""
URL configuration for Entreprinder business networking platform.

DEPRECATED: This file is kept for backwards compatibility.
- powerup.lu now serves the power_up static corporate site (via urls_power_up.py)
- entreprinder.lu serves the entreprinder app (via urls_entreprinder.py)
- Use urls_entreprinder.py for new development
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from .urls_shared import base_patterns, api_patterns
from .views_seo import robots_txt_powerup
from entreprinder import views as entreprinder_views
from entreprinder.admin import entreprinder_admin_site

urlpatterns = base_patterns + api_patterns + [
    # SEO - robots.txt
    path('robots.txt', robots_txt_powerup, name='robots_txt'),

    # Custom Entreprinder Admin Panel
    path('entreprinder-admin/', entreprinder_admin_site.urls),

    # Standard Django Admin (user management)
    path('admin/', admin.site.urls),

    # Entreprinder home page
    path('', entreprinder_views.home, name='home'),

    # Entreprinder app URLs (includes matching and vibe_coding)
    path('', include('entreprinder.urls', namespace='entreprinder')),

    # Vibe Coding URLs - included directly for top-level namespace access
    # This allows templates to use {% url 'vibe_coding:index' %} without entreprinder prefix
    path('vibe-coding/', include(('entreprinder.vibe.urls', 'vibe_coding'))),

    # LinkedIn OAuth callback
    path('login_complete/', entreprinder_views.login_complete, name='login_complete'),

    # Protected API endpoint
    path('api/protected/', entreprinder_views.protected_api, name='protected_api'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
