"""
Coach Panel admin for multi-channel campaigns.

Operational views live in the campaign dashboard
(``crush_lu/admin/campaign_dashboard.py``); these ModelAdmins provide the
record-level fallback (inspection, cancellation, recipient drill-down).
"""
import logging

from django.contrib import admin, messages
from modeltranslation.admin import TranslationAdmin

from azureproject.admin_translation_mixin import AutoTranslateMixin

logger = logging.getLogger(__name__)


class CampaignAdmin(AutoTranslateMixin, TranslationAdmin):
    """Read-only campaign records (inspection + cancellation only).

    Campaigns are created exclusively through the dashboard composer
    (``create_campaign()``), which also creates the linked Newsletter for the
    email leg and captures the audience snapshot. A hand-made admin record
    would skip both — e.g. an email campaign with no newsletter dispatches
    as instantly "sent" without contacting anyone — so add/change stay off.
    """

    list_display = (
        'name', 'status', 'channel_list', 'audience', 'segment_key',
        'scheduled_at', 'stats_summary', 'created_at',
    )
    list_filter = ('status', 'audience', 'language')
    search_fields = ('name', 'slug', 'segment_key')
    date_hierarchy = 'created_at'
    actions = ['cancel_campaigns']

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        # Every field read-only (change permission stays on so the
        # cancel_campaigns changelist action remains available).
        return [field.name for field in self.model._meta.fields]

    def channel_list(self, obj):
        return ', '.join(obj.channels) or '—'
    channel_list.short_description = 'Channels'

    def stats_summary(self, obj):
        totals = obj.stats['totals']
        return (
            f"{totals['sent']} sent / {totals['failed']} failed / "
            f"{totals['skipped']} skipped"
        )
    stats_summary.short_description = 'Results'

    @admin.action(description='Cancel selected campaigns')
    def cancel_campaigns(self, request, queryset):
        cancelled = 0
        for campaign in queryset:
            if campaign.cancel():
                cancelled += 1
        if cancelled:
            self.message_user(
                request, f"Cancelled {cancelled} campaign(s).",
                level=messages.SUCCESS,
            )
        skipped = queryset.count() - cancelled
        if skipped:
            self.message_user(
                request,
                f"{skipped} campaign(s) were already completed or cancelled.",
                level=messages.WARNING,
            )


class CampaignRecipientAdmin(admin.ModelAdmin):
    """Per-recipient campaign send tracking (read-only view)."""

    list_display = (
        'campaign', 'channel', 'user', 'status', 'sent_at', 'short_error',
    )
    list_per_page = 50
    list_select_related = ['campaign', 'user']
    list_filter = ('channel', 'status', 'campaign')
    search_fields = ('user__email', 'user__username', 'user__first_name')
    readonly_fields = (
        'campaign', 'channel', 'user', 'status', 'sent_at', 'error_message',
        'whatsapp_message',
    )

    def short_error(self, obj):
        if obj.error_message:
            truncated = obj.error_message[:80]
            if len(obj.error_message) > 80:
                truncated += '...'
            return truncated
        return ''
    short_error.short_description = 'Error'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
