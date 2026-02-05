from django.contrib import admin
from django.utils.translation import gettext_lazy as _


class CrushSiteConfigAdmin(admin.ModelAdmin):
    """Admin for singleton site configuration. No add/delete - always edit pk=1."""

    fieldsets = (
        (
            _("WhatsApp Business"),
            {
                "fields": (
                    "whatsapp_enabled",
                    "whatsapp_number",
                    "whatsapp_default_message",
                ),
                "description": _(
                    "Configure the floating WhatsApp contact button. "
                    "Enter the phone number without + or spaces (e.g. 352621XXXXXX)."
                ),
            },
        ),
    )
    readonly_fields = ("updated_at",)

    def has_add_permission(self, request):
        # Only allow one instance
        return not self.model.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Invalidate the context processor cache immediately
        from crush_lu.context_processors import _site_config_cache

        _site_config_cache["config"] = None
        _site_config_cache["expires"] = 0
