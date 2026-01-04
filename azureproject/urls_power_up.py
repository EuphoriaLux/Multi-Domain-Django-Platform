# azureproject/urls_power_up.py
"""
URL configuration for Power-Up corporate/investor site.

This is the URL config used when requests come from power-up.lu and powerup.lu domains.
Static corporate site - no authentication, no API, no forms.
"""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import path, include

from power_up.admin import power_up_admin_site
from .views_seo import robots_txt_power_up


def health_check(request):
    """Simple health check endpoint for Azure App Service."""
    return HttpResponse("OK", content_type="text/plain")


urlpatterns = [
    # Health check (required for Azure App Service)
    path("healthz/", health_check, name="health_check"),

    # SEO - robots.txt
    path("robots.txt", robots_txt_power_up, name="robots_txt"),

    # Power-Up custom admin panel
    path("power-admin/", power_up_admin_site.urls),

    # Standard Django admin (for superusers)
    path("admin/", admin.site.urls),

    # Power-Up corporate site pages
    path("", include("power_up.urls", namespace="power_up")),
]
