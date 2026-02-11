"""URL configuration for portal.powerup.lu — CRM admin portal.

Access model:
    /              — Customer portal UI (Phase 3, Entra SSO)
    /power-admin/  — Agent CRM/Ticketing UI (Phase 2, custom views)
    /admin/        — Django admin for superusers (Users, Groups, Permissions)
"""

from django.contrib import admin
from django.urls import include, path

from power_up.admin import power_up_admin_site
from .urls_shared import base_patterns

urlpatterns = base_patterns + [
    path("crm/", include("power_up.crm.urls")),
    path("power-admin/", power_up_admin_site.urls),
    path("admin/", admin.site.urls),
]
