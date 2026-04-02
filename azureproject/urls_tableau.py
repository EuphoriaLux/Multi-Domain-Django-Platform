# azureproject/urls_tableau.py
"""
URL configuration for Tableau AI Art e-commerce site.

This is the URL config used when requests come from tableau.lu domain.
Simple e-commerce landing site - no authentication, no API, no forms.
English only (no i18n patterns).
"""

from django.http import HttpResponse
from django.urls import path, include

from .views_seo import robots_txt_tableau


def health_check(request):
    """Simple health check endpoint for Azure App Service."""
    return HttpResponse("OK", content_type="text/plain")


urlpatterns = [
    # Health check (required for Azure App Service)
    path("healthz/", health_check, name="health_check"),

    # SEO - robots.txt
    path("robots.txt", robots_txt_tableau, name="robots_txt"),

    # Tableau AI art site pages
    path("", include("tableau.urls", namespace="tableau")),
]
