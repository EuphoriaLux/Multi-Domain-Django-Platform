from django import forms
from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from modeltranslation.admin import TranslationAdmin

from azureproject.admin_translation_mixin import AutoTranslateMixin

from crush_lu.models.profiles import ProfileSubmission


class CrushSiteConfigForm(forms.ModelForm):
    """Custom form to render banner_target_statuses as checkboxes."""

    banner_target_statuses = forms.MultipleChoiceField(
        choices=ProfileSubmission.STATUS_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text=_(
            "Show banner only to users with these submission statuses. "
            "Leave all unchecked to show to ALL authenticated users."
        ),
    )


class CrushSiteConfigAdmin(AutoTranslateMixin, TranslationAdmin):
    """Admin for singleton site configuration. No add/delete - always edit pk=1."""

    form = CrushSiteConfigForm

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
        (
            _("Social Media Links"),
            {
                "fields": (
                    "social_instagram_url",
                    "social_facebook_url",
                    "social_linkedin_url",
                    "social_google_business_url",
                    "social_reddit_url",
                ),
                "description": _(
                    "Add social media profile URLs. "
                    "Leave blank to hide the icon in the footer."
                ),
            },
        ),
        (
            _("Status Banner"),
            {
                "fields": (
                    "banner_enabled",
                    "banner_message",
                    "banner_link_text",
                    "banner_link_url",
                    "banner_style",
                    "banner_target_statuses",
                ),
                "description": _(
                    "Show a persistent banner to users based on their profile status. "
                    "Banner message and link text support EN/DE/FR translations."
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
