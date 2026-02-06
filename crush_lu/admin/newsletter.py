"""
Newsletter admin classes for Crush.lu Coach Panel.

Includes:
- NewsletterAdmin (with live HTML preview)
- NewsletterRecipientAdmin
"""

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.utils.html import format_html

from crush_lu.models.newsletter import Newsletter, NewsletterRecipient
from crush_lu.newsletter_service import send_newsletter


def _get_segment_choices():
    """Build grouped choices for the segment_key dropdown."""
    try:
        from crush_lu.admin.user_segments import get_segment_definitions
        segments = get_segment_definitions()
    except Exception:
        return [('', '— Select a segment —')]

    choices = [('', '— Select a segment —')]
    for group in segments.values():
        group_label = group.get('title', 'Other')
        group_choices = []
        for seg in group.get('segments', []):
            label = f"{seg['name']} ({seg['count']} users)"
            group_choices.append((seg['key'], label))
        if group_choices:
            choices.append((group_label, group_choices))
    return choices


class NewsletterRecipientInline(admin.TabularInline):
    model = NewsletterRecipient
    extra = 0
    readonly_fields = ('user', 'email', 'status', 'sent_at', 'error_message')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class NewsletterAdmin(admin.ModelAdmin):
    """
    Newsletter management for Crush.lu with live email preview.
    """

    list_display = (
        'subject', 'get_status_badge', 'audience', 'total_sent',
        'total_failed', 'total_skipped', 'created_at', 'sent_at',
    )
    list_filter = ('status', 'audience', 'created_at')
    search_fields = ('subject',)
    readonly_fields = (
        'email_preview',
        'status', 'total_recipients', 'total_sent', 'total_failed',
        'total_skipped', 'sent_at', 'created_by', 'created_at', 'updated_at',
    )
    date_hierarchy = 'created_at'
    inlines = [NewsletterRecipientInline]

    fieldsets = (
        ('Content', {
            'fields': ('subject', 'body_html', 'body_text'),
        }),
        ('Email Preview', {
            'fields': ('email_preview',),
            'description': 'Save the newsletter first to see the preview. '
                           'The preview shows exactly what recipients will receive.',
        }),
        ('Targeting', {
            'fields': ('audience', 'segment_key'),
            'description': (
                'Choose who receives this newsletter. '
                'Select "Specific user segment" and pick a segment from the dropdown.'
            ),
        }),
        ('Status & Statistics', {
            'fields': (
                'status', 'created_by', 'sent_at',
                'total_recipients', 'total_sent', 'total_failed', 'total_skipped',
            ),
            'classes': ('collapse',),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        segment_field = form.base_fields.get('segment_key')
        if segment_field:
            segment_field.widget = forms.Select(choices=_get_segment_choices())
            segment_field.help_text = 'Only used when audience is "Specific user segment".'
        return form

    def email_preview(self, obj):
        if not obj.pk:
            return format_html(
                '<div style="padding:20px; background:#f8f9fa; border:1px dashed #ccc; '
                'border-radius:8px; text-align:center; color:#666;">'
                'Save the newsletter first to see the email preview.'
                '</div>'
            )

        preview_url = reverse(
            'crush_admin:crush_lu_newsletter_preview', args=[obj.pk]
        )

        # Render the email HTML inline for srcdoc (avoids cross-origin iframe issues)
        rendered_html = self._render_preview(obj)
        # Escape for srcdoc attribute: quotes and ampersands
        escaped_html = rendered_html.replace('&', '&amp;').replace('"', '&quot;')

        return format_html(
            '<div style="border:1px solid #ddd; border-radius:8px; overflow:hidden;">'
            '  <div style="background:#f0f0f0; padding:8px 12px; font-size:12px; '
            '       color:#555; border-bottom:1px solid #ddd;">'
            '    From: <strong>love@crush.lu</strong> &nbsp;|&nbsp; '
            '    Subject: <strong>{subject}</strong> &nbsp;|&nbsp; '
            '    <a href="{url}" target="_blank" style="color:#9B59B6;">Open in new tab</a>'
            '  </div>'
            '  <iframe srcdoc="{srcdoc}" style="width:100%; height:700px; border:none; background:#fff;">'
            '  </iframe>'
            '</div>',
            subject=obj.subject,
            url=preview_url,
            srcdoc=escaped_html,
        )
    email_preview.short_description = 'Email Preview'

    def _render_preview(self, newsletter, first_name='Preview'):
        """Render the newsletter template with placeholder context."""
        context = {
            'first_name': first_name,
            'body_html': newsletter.body_html,
            'unsubscribe_url': '#unsubscribe',
            'home_url': 'https://crush.lu',
            'about_url': 'https://crush.lu/about/',
            'events_url': 'https://crush.lu/events/',
            'settings_url': 'https://crush.lu/account/settings/',
            'LANGUAGE_CODE': 'en',
        }
        return render_to_string('crush_lu/emails/newsletter.html', context)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:pk>/preview/',
                self.admin_site.admin_view(self.preview_view),
                name='crush_lu_newsletter_preview',
            ),
        ]
        return custom_urls + urls

    def preview_view(self, request, pk):
        """Render the newsletter email template as a standalone HTML page."""
        newsletter = Newsletter.objects.get(pk=pk)
        first_name = request.user.first_name or 'Preview'
        html = self._render_preview(newsletter, first_name=first_name)
        return HttpResponse(html)

    def get_status_badge(self, obj):
        colors = {
            'draft': '#6b7280',
            'sending': '#f59e0b',
            'sent': '#10b981',
            'failed': '#ef4444',
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; '
            'border-radius:10px; font-size:11px;">{}</span>',
            color, obj.get_status_display(),
        )
    get_status_badge.short_description = 'Status'
    get_status_badge.admin_order_field = 'status'

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    actions = ['send_selected_newsletters']

    def send_selected_newsletters(self, request, queryset):
        """Send selected draft newsletters."""
        drafts = queryset.filter(status='draft')
        if not drafts.exists():
            self.message_user(
                request,
                "No draft newsletters selected. Only drafts can be sent.",
                level=messages.WARNING,
            )
            return

        for newsletter in drafts:
            try:
                results = send_newsletter(newsletter)
                self.message_user(
                    request,
                    f"Newsletter '{newsletter.subject}': "
                    f"Sent {results['sent']}, Failed {results['failed']}, "
                    f"Skipped {results['skipped']}",
                    level=messages.SUCCESS,
                )
            except Exception as e:
                self.message_user(
                    request,
                    f"Error sending '{newsletter.subject}': {e}",
                    level=messages.ERROR,
                )
    send_selected_newsletters.short_description = "Send selected draft newsletters"


class NewsletterRecipientAdmin(admin.ModelAdmin):
    """
    Newsletter recipient tracking (read-only view).
    """

    list_display = ('newsletter', 'email', 'status', 'sent_at', 'short_error')
    list_filter = ('status', 'newsletter')
    search_fields = ('email', 'user__username', 'user__first_name')
    readonly_fields = (
        'newsletter', 'user', 'email', 'status', 'sent_at', 'error_message',
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
