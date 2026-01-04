# azureproject/urls_entreprinder.py
"""
URL configuration for Entreprinder business networking platform.

This is the URL config used when requests come from entreprinder.lu domain.
Tinder-style matching for entrepreneurs, investors, and business professionals.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from .urls_shared import base_patterns, api_patterns
from .views_seo import robots_txt_entreprinder
from entreprinder import views as entreprinder_views
from entreprinder.admin import entreprinder_admin_site

urlpatterns = base_patterns + api_patterns + [
    # SEO - robots.txt
    path('robots.txt', robots_txt_entreprinder, name='robots_txt'),

    # Custom Entreprinder Admin Panel
    path('entreprinder-admin/', entreprinder_admin_site.urls),

    # Standard Django Admin (user management)
    path('admin/', admin.site.urls),

    # Entreprinder home page
    path('', entreprinder_views.home, name='home'),

    # Entreprinder app URLs (includes matching, finops, and vibe_coding - all merged)
    path('', include('entreprinder.urls', namespace='entreprinder')),

    # FinOps Hub URLs - included directly for top-level namespace access
    # This allows templates to use {% url 'finops_hub:dashboard' %} without entreprinder prefix
    path('finops/', include(('entreprinder.finops.urls', 'finops_hub'))),

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
