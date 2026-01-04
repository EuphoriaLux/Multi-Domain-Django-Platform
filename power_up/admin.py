from django.contrib import admin


# ============================================================================
# CUSTOM ADMIN SITE - Power-Up Administration
# ============================================================================

class PowerUpAdminSite(admin.AdminSite):
    """
    Custom admin site for Power-Up corporate/investor site.

    This admin panel manages the power-up.lu and powerup.lu domains.
    Currently a static site, but this admin provides a consistent
    experience across all platform admin panels.
    """
    site_header = 'Power-Up Administration'
    site_title = 'Power-Up Admin'
    index_title = 'Corporate Site Management'

    def get_app_list(self, request, app_label=None):
        """
        Override to customize the admin index page.
        Power-Up is currently a static site with no models,
        but this can be extended in the future.
        """
        app_list = super().get_app_list(request, app_label)
        return app_list


# Instantiate the custom admin site
power_up_admin_site = PowerUpAdminSite(name='power_up_admin')


# ============================================================================
# IMPORT SUBMODULE ADMIN REGISTRATIONS
# ============================================================================
# Import finops admin to register models with power_up_admin_site
# This must be done after power_up_admin_site is created
from power_up.finops import admin as finops_admin  # noqa: F401, E402
