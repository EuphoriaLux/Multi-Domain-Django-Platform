# azureproject/urls_power_up.py
"""
URL configuration for Power-Up corporate/investor site.

This is the URL config used when requests come from power-up.lu and powerup.lu domains.
Supports internationalization with language-prefixed URLs (/en/, /de/, /fr/).
"""

from django.contrib import admin
from django.urls import path, include
from django.conf.urls.i18n import i18n_patterns

from power_up.admin import power_up_admin_site
from .views_seo import robots_txt_power_up
from .urls_shared import base_patterns


# Language-neutral patterns (no /en/, /de/, /fr/ prefix)
urlpatterns = base_patterns + [
    # SEO - robots.txt
    path("robots.txt", robots_txt_power_up, name="robots_txt"),

    # Power-Up custom admin panel (language-neutral)
    path("power-admin/", power_up_admin_site.urls),

    # Standard Django admin (language-neutral)
    path("admin/", admin.site.urls),

    # FinOps Hub - Azure cost management dashboard (language-neutral)
    path("finops/", include(("power_up.finops.urls", "finops_hub"))),

    # CRM - Customer groups dashboard (language-neutral)
    path("crm/", include("power_up.crm.urls")),

    # Onboarding - Customer onboarding email builder (language-neutral)
    path("onboarding/", include("power_up.onboarding.urls")),
]

# Language-prefixed patterns (user-facing pages)
# URLs will be: /en/, /de/, /fr/, /en/about/, /de/about/, etc.
urlpatterns += i18n_patterns(
    # Power-Up corporate site pages
    path("", include("power_up.urls", namespace="power_up")),

    # Include /en/ prefix even for default language
    prefix_default_language=True,
)
