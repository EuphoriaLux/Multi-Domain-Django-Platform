from django.contrib import admin


# ============================================================================
# CUSTOM ADMIN SITE - Arborist Administration
# ============================================================================


class ArboristAdminSite(admin.AdminSite):
    """
    Custom admin site for Arborist informational site.

    This admin panel manages the arborist.lu domain.
    Currently a static site, but this admin provides a consistent
    experience across all platform admin panels.
    """

    site_header = "Arborist Administration"
    site_title = "Arborist Admin"
    index_title = "Site Management"

    def get_app_list(self, request, app_label=None):
        """
        Override to customize the admin index page.
        Arborist is currently a static site with no models,
        but this can be extended in the future.
        """
        app_list = super().get_app_list(request, app_label)
        return app_list


# Instantiate the custom admin site
arborist_admin_site = ArboristAdminSite(name="arborist_admin")
