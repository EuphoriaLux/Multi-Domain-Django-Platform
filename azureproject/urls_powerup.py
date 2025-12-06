# azureproject/urls_powerup.py
"""
URL configuration for PowerUP / Entreprinder business networking platform.

This is the URL config used when requests come from powerup.lu domain.
Also serves as the fallback for unknown domains and Azure hostnames.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from .urls_shared import base_patterns, api_patterns
from entreprinder import views as entreprinder_views

urlpatterns = base_patterns + api_patterns + [
    # Django Admin
    path('admin/', admin.site.urls),

    # Entreprinder home page
    path('', entreprinder_views.home, name='home'),

    # Entreprinder app URLs (includes matching, finops, and vibe_coding - all merged)
    path('', include('entreprinder.urls', namespace='entreprinder')),

    # LinkedIn OAuth callback
    path('login_complete/', entreprinder_views.login_complete, name='login_complete'),

    # Protected API endpoint
    path('api/protected/', entreprinder_views.protected_api, name='protected_api'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
