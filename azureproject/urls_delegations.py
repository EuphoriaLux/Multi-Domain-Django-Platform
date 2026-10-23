# azureproject/urls_delegations.py
"""
URL configuration for delegations.lu domain.

Supports language-prefixed marketing & SEO routes (/en/, /fr/, /de/) as well
as language-neutral portal, OAuth, sitemap.xml, and robots.txt endpoints.
"""

from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from delegations import views as delegation_views
from delegations.admin import delegations_admin_site
from delegations.sitemaps import DelegationsStaticViewSitemap

from .urls_shared import api_patterns, base_patterns
from .views_seo import robots_txt_delegations

sitemaps = {
    "static": DelegationsStaticViewSitemap,
}

urlpatterns = (
    base_patterns
    + api_patterns
    + [
        # SEO - robots.txt and multilingual sitemap.xml
        path("robots.txt", robots_txt_delegations, name="robots_txt"),
        path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
        # Custom Delegations Admin Panel
        path("delegation-admin/", delegations_admin_site.urls),
        # Standard Django Admin (user management)
        path("admin/", admin.site.urls),
        # Delegations app URLs (includes root '/' and portal status routes)
        path("", include("delegations.urls", namespace="delegations")),
    ]
)

# Language-prefixed marketing routes (/en/, /de/, /fr/)
urlpatterns += i18n_patterns(
    path("", delegation_views.home, name="delegations_home_i18n"),
    prefix_default_language=True,
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
