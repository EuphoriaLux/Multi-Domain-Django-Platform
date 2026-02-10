"""
Newsletter admin classes for Crush.lu Coach Panel.

Includes:
- NewsletterAdmin (with live HTML preview, send button, async sending)
- NewsletterRecipientAdmin
"""
import logging
import threading

from django import forms
from django.contrib import admin, messages
from django.db import close_old_connections
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from crush_lu.models.newsletter import Newsletter, NewsletterRecipient
from crush_lu.newsletter_service import (
    get_newsletter_recipients,
    send_newsletter,
)

logger = logging.getLogger(__name__)


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


class NewsletterAdminForm(forms.ModelForm):
    """Custom form with segment validation."""

    class Meta:
        model = Newsletter
        exclude = (
            'status', 'total_recipients', 'total_sent', 'total_failed',
            'total_skipped', 'sent_at', 'created_by', 'created_at',
            'updated_at',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        segment_field = self.fields.get('segment_key')
        if segment_field:
            segment_field.widget = forms.Select(choices=_get_segment_choices())
            segment_field.help_text = (
                'Only used when audience is "Specific user segment".'
            )

    def clean(self):
        cleaned_data = super().clean()
        audience = cleaned_data.get('audience')
        segment_key = cleaned_data.get('segment_key')

        if audience == 'segment' and not segment_key:
            self.add_error(
                'segment_key',
                'You must select a segment when audience is '
                '"Specific user segment".',
            )
        elif audience != 'segment':
            # Clear segment_key when not using segment audience
            cleaned_data['segment_key'] = ''

        return cleaned_data


def _send_newsletter_in_thread(newsletter):
    """Wrapper to run send_newsletter in a daemon thread with DB cleanup."""
    try:
        close_old_connections()
        send_newsletter(newsletter)
    except Exception:
        logger.exception(
            "Background newsletter send failed for #%s", newsletter.pk
        )
    finally:
        close_old_connections()


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

    form = NewsletterAdminForm
    change_form_template = 'admin/crush_lu/newsletter/change_form.html'

    list_display = (
        'subject', 'get_status_badge', 'audience', 'language', 'total_sent',
        'total_failed', 'total_skipped', 'created_at', 'sent_at',
    )
    list_filter = ('status', 'audience', 'language', 'created_at')
    search_fields = ('subject',)
    readonly_fields = (
        'email_preview', 'estimated_recipients',
        'status', 'total_recipients', 'total_sent', 'total_failed',
        'total_skipped', 'sent_at', 'created_by', 'created_at', 'updated_at',
    )
    date_hierarchy = 'created_at'
    inlines = [NewsletterRecipientInline]

    class Media:
        js = ('crush_lu/admin/js/newsletter_admin.js',)

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
            'fields': (
                'audience', 'segment_key', 'language',
                'estimated_recipients',
            ),
            'description': (
                'Choose who receives this newsletter. '
                'Select "Specific user segment" and pick a segment from '
                'the dropdown.'
            ),
        }),
        ('Status & Statistics', {
            'fields': (
                'status', 'created_by', 'sent_at',
                'total_recipients', 'total_sent', 'total_failed',
                'total_skipped',
            ),
            'classes': ('collapse',),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def estimated_recipients(self, obj):
        if not obj.pk:
            return format_html(
                '<span style="color:#999;">Save the newsletter first to '
                'see estimated recipients.</span>'
            )
        count = get_newsletter_recipients(obj).count()
        return format_html(
            '<span style="background:#3b82f6; color:white; padding:2px 10px; '
            'border-radius:10px; font-size:12px; font-weight:600;">'
            '{} recipient{}</span>',
            count,
            's' if count != 1 else '',
        )
    estimated_recipients.short_description = 'Estimated Recipients'

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
            path(
                '<int:pk>/send/',
                self.admin_site.admin_view(self.send_view),
                name='crush_lu_newsletter_send',
            ),
        ]
        return custom_urls + urls

    def preview_view(self, request, pk):
        """Render the newsletter email template as a standalone HTML page."""
        newsletter = Newsletter.objects.get(pk=pk)
        first_name = request.user.first_name or 'Preview'
        html = self._render_preview(newsletter, first_name=first_name)
        return HttpResponse(html)

    def send_view(self, request, pk):
        """Two-step send: GET shows confirmation, POST launches async send."""
        newsletter = get_object_or_404(Newsletter, pk=pk)

        if newsletter.status != 'draft':
            self.message_user(
                request,
                f"Cannot send: newsletter status is '{newsletter.get_status_display()}'.",
                level=messages.WARNING,
            )
            return redirect(
                reverse('crush_admin:crush_lu_newsletter_change', args=[pk])
            )

        recipient_count = get_newsletter_recipients(newsletter).count()

        if request.method == 'POST':
            if recipient_count == 0:
                self.message_user(
                    request,
                    "No recipients match the current targeting criteria.",
                    level=messages.WARNING,
                )
                return redirect(
                    reverse(
                        'crush_admin:crush_lu_newsletter_change', args=[pk]
                    )
                )

            # Launch async send
            thread = threading.Thread(
                target=_send_newsletter_in_thread,
                args=(newsletter,),
                daemon=True,
            )
            thread.start()

            self.message_user(
                request,
                f"Sending started for '{newsletter.subject}' to "
                f"{recipient_count} recipients. Refresh to check status.",
                level=messages.SUCCESS,
            )
            return redirect(
                reverse('crush_admin:crush_lu_newsletter_change', args=[pk])
            )

        # GET: show confirmation page
        context = {
            **self.admin_site.each_context(request),
            'newsletter': newsletter,
            'recipient_count': recipient_count,
            'opts': self.model._meta,
            'title': f'Send Newsletter: {newsletter.subject}',
        }
        return TemplateResponse(
            request,
            'admin/crush_lu/newsletter/send_confirmation.html',
            context,
        )

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        try:
            obj = Newsletter.objects.get(pk=object_id)
            extra_context['show_send_button'] = obj.status == 'draft'
            extra_context['send_url'] = reverse(
                'crush_admin:crush_lu_newsletter_send', args=[object_id]
            )
        except Newsletter.DoesNotExist:
            pass
        return super().change_view(
            request, object_id, form_url, extra_context=extra_context
        )

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
        """Send selected draft newsletters (async)."""
        drafts = queryset.filter(status='draft')
        if not drafts.exists():
            self.message_user(
                request,
                "No draft newsletters selected. Only drafts can be sent.",
                level=messages.WARNING,
            )
            return

        for newsletter in drafts:
            thread = threading.Thread(
                target=_send_newsletter_in_thread,
                args=(newsletter,),
                daemon=True,
            )
            thread.start()
            self.message_user(
                request,
                f"Sending started for '{newsletter.subject}'. "
                f"Refresh to check status.",
                level=messages.SUCCESS,
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
