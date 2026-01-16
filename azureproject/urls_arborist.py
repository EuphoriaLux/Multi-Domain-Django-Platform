# azureproject/urls_arborist.py
"""
URL configuration for Arborist informational site.

This is the URL config used when requests come from arborist.lu domain.
Supports internationalization with language-prefixed URLs (/en/, /de/, /fr/).
"""

from django.contrib import admin
from django.urls import path, include
from django.conf.urls.i18n import i18n_patterns

from arborist.admin import arborist_admin_site
from .views_seo import robots_txt_arborist
from .urls_shared import base_patterns


# Language-neutral patterns (no /en/, /de/, /fr/ prefix)
urlpatterns = base_patterns + [
    # SEO - robots.txt
    path("robots.txt", robots_txt_arborist, name="robots_txt"),

    # Arborist custom admin panel (language-neutral)
    path("arborist-admin/", arborist_admin_site.urls),

    # Standard Django admin (language-neutral)
    path("admin/", admin.site.urls),
]

# Language-prefixed patterns (user-facing pages)
# URLs will be: /en/, /de/, /fr/, /en/about/, /de/about/, etc.
urlpatterns += i18n_patterns(
    # Arborist informational site pages
    path("", include("arborist.urls", namespace="arborist")),

    # Include /en/ prefix even for default language
    prefix_default_language=True,
)
