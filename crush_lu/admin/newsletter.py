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
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import translation
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from crush_lu.models.events import MeetupEvent
from crush_lu.models.newsletter import Newsletter, NewsletterRecipient
from crush_lu.newsletter_service import (
    get_newsletter_recipients,
    render_event_announcement,
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


def _get_event_choices():
    """Build choices for the event dropdown (published, future events)."""
    from django.utils import timezone as tz

    choices = [('', '— None —')]
    events = MeetupEvent.objects.filter(
        is_published=True,
        is_cancelled=False,
        date_time__gte=tz.now(),
    ).order_by('date_time')
    for event in events:
        date_str = event.date_time.strftime('%b %d')
        label = f"{event.title} ({date_str})"
        choices.append((event.pk, label))
    return choices


class NewsletterAdminForm(forms.ModelForm):
    """Custom form with segment validation and event selection."""

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

        event_field = self.fields.get('event')
        if event_field:
            event_field.widget = forms.Select(choices=_get_event_choices())
            event_field.help_text = 'Auto-generates content. Registered users excluded.'

        # Make body_html not required (auto-generated for event newsletters)
        if 'body_html' in self.fields:
            self.fields['body_html'].required = False

    def clean(self):
        cleaned_data = super().clean()
        audience = cleaned_data.get('audience')
        segment_key = cleaned_data.get('segment_key')
        event = cleaned_data.get('event')

        if audience == 'segment' and not segment_key:
            self.add_error(
                'segment_key',
                'You must select a segment when audience is '
                '"Specific user segment".',
            )
        elif audience != 'segment':
            # Clear segment_key when not using segment audience
            cleaned_data['segment_key'] = ''

        # When event is selected, auto-populate subject if empty
        if event and not cleaned_data.get('subject'):
            cleaned_data['subject'] = f"New Event: {event.title}"

        # body_html is required only for non-event newsletters
        if not event and not cleaned_data.get('body_html'):
            self.add_error(
                'body_html',
                'Body HTML is required for standard newsletters '
                '(without an event selected).',
            )

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
        'subject', 'get_status_badge', 'get_event_badge', 'audience',
        'language', 'total_sent', 'total_failed', 'total_skipped',
        'created_at', 'sent_at',
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
        css = {'all': ('crush_lu/admin/css/newsletter_admin.css',)}
        js = ('crush_lu/admin/js/newsletter_admin.js',)

    fieldsets = (
        ('Event', {
            'fields': ('event',),
        }),
        ('Content', {
            'fields': ('subject', 'body_html', 'body_text'),
        }),
        ('Email Preview', {
            'fields': ('email_preview',),
        }),
        ('Targeting', {
            'fields': (
                'audience', 'segment_key', 'language',
                'estimated_recipients',
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
            count = 0
            # For new newsletters, still try to estimate from defaults
            temp = Newsletter(audience='all_users', language='all')
            try:
                count = get_newsletter_recipients(temp).count()
            except Exception:
                pass
        else:
            count = get_newsletter_recipients(obj).count()
        return format_html(
            '<span id="estimated-recipients-badge" '
            'style="background:#3b82f6; color:white; padding:2px 10px; '
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

        languages = [('en', 'English'), ('de', 'Deutsch'), ('fr', 'Français')]

        # Build tab buttons
        tab_buttons = ''
        for i, (code, label) in enumerate(languages):
            active = ' background:#9B59B6; color:white;' if i == 0 else ''
            tab_buttons += (
                f'<button type="button" onclick="showPreviewTab(\'{code}\')" '
                f'id="preview-tab-{code}" '
                f'style="padding:6px 16px; border:1px solid #ddd; border-bottom:none; '
                f'border-radius:6px 6px 0 0; cursor:pointer; font-size:13px; '
                f'font-weight:600;{active}" '
                f'class="preview-lang-tab">{label}</button> '
            )

        # Build iframe panels for each language
        panels = ''
        for i, (code, label) in enumerate(languages):
            rendered_html = self._render_preview(obj, lang=code)
            escaped_html = (
                rendered_html.replace('&', '&amp;').replace('"', '&quot;')
            )
            display = 'block' if i == 0 else 'none'
            panels += (
                f'<iframe id="preview-panel-{code}" srcdoc="{escaped_html}" '
                f'style="width:100%; height:700px; border:none; background:#fff; '
                f'display:{display};"></iframe>'
            )

        # JS to switch tabs
        script = (
            '<script>'
            'function showPreviewTab(lang) {'
            '  ["en","de","fr"].forEach(function(c) {'
            '    var panel = document.getElementById("preview-panel-" + c);'
            '    var tab = document.getElementById("preview-tab-" + c);'
            '    if (panel) panel.style.display = c === lang ? "block" : "none";'
            '    if (tab) {'
            '      tab.style.background = c === lang ? "#9B59B6" : "";'
            '      tab.style.color = c === lang ? "white" : "";'
            '    }'
            '  });'
            '}'
            '</script>'
        )

        header = format_html(
            '<div style="border:1px solid #ddd; border-radius:8px; overflow:hidden;">'
            '  <div style="background:#f0f0f0; padding:8px 12px; font-size:12px; '
            '       color:#555; border-bottom:1px solid #ddd;">'
            '    From: <strong>love@crush.lu</strong> &nbsp;|&nbsp; '
            '    Subject: <strong>{subject}</strong> &nbsp;|&nbsp; '
            '    <a href="{url}" target="_blank" style="color:#9B59B6;">Open in new tab</a>'
            '  </div>'
            '  <div style="padding:8px 12px 0; background:#fafafa; '
            '       border-bottom:1px solid #ddd;">',
            subject=obj.subject,
            url=preview_url,
        )
        # tab_buttons, panels, and script contain pre-escaped HTML with CSS
        # curly braces that would break format_html(), so use mark_safe.
        return mark_safe(
            header
            + tab_buttons
            + '</div>'
            + panels
            + script
            + '</div>'
        )
    email_preview.short_description = 'Email Preview'

    def _render_preview(self, newsletter, first_name='Preview', lang='en'):
        """Render the newsletter template with placeholder context."""
        if newsletter.event_id:
            return self._render_event_preview(newsletter, first_name, lang)

        context = {
            'first_name': first_name,
            'body_html': newsletter.body_html,
            'unsubscribe_url': '#unsubscribe',
            'home_url': 'https://crush.lu',
            'about_url': 'https://crush.lu/about/',
            'events_url': 'https://crush.lu/events/',
            'settings_url': 'https://crush.lu/account/settings/',
            'LANGUAGE_CODE': lang,
        }
        with translation.override(lang):
            return render_to_string('crush_lu/emails/newsletter.html', context)

    def _render_event_preview(self, newsletter, first_name='Preview', lang='en'):
        """Render the event announcement template for admin preview."""
        event = newsletter.event

        event_image_url = None
        if event.image:
            try:
                event_image_url = event.image.url
            except Exception:
                pass

        with translation.override(lang):
            context = {
                'first_name': first_name,
                'event': event,
                'event_title': event.title,
                'event_description': event.description,
                'event_image_url': event_image_url,
                'event_url': f'https://crush.lu/{lang}/events/{event.pk}/',
                'spots_remaining': event.spots_remaining,
                'unsubscribe_url': '#unsubscribe',
                'home_url': 'https://crush.lu',
                'about_url': 'https://crush.lu/about/',
                'events_url': 'https://crush.lu/events/',
                'settings_url': 'https://crush.lu/account/settings/',
                'LANGUAGE_CODE': lang,
            }
            return render_to_string(
                'crush_lu/emails/event_announcement.html', context
            )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'estimate-recipients/',
                self.admin_site.admin_view(self.estimate_recipients_view),
                name='crush_lu_newsletter_estimate_recipients',
            ),
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

    def estimate_recipients_view(self, request):
        """AJAX endpoint: estimate recipient count based on form field values."""
        audience = request.GET.get('audience', 'all_users')
        segment_key = request.GET.get('segment_key', '')
        language = request.GET.get('language', 'all')
        event_id = request.GET.get('event', '')

        # Build a temporary Newsletter-like object for get_newsletter_recipients
        newsletter = Newsletter(
            audience=audience,
            segment_key=segment_key,
            language=language,
        )
        if event_id:
            try:
                newsletter.event_id = int(event_id)
            except (ValueError, TypeError):
                pass

        try:
            count = get_newsletter_recipients(newsletter).count()
        except Exception:
            count = 0

        return JsonResponse({'count': count})

    def preview_view(self, request, pk):
        """Render the newsletter email template as a standalone HTML page."""
        newsletter = Newsletter.objects.get(pk=pk)
        first_name = request.user.first_name or 'Preview'
        lang = request.GET.get('lang', 'en')
        if lang not in ('en', 'de', 'fr'):
            lang = 'en'
        html = self._render_preview(newsletter, first_name=first_name, lang=lang)
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

    def get_event_badge(self, obj):
        if obj.event_id:
            return format_html(
                '<span style="background:#9B59B6; color:white; padding:2px 8px; '
                'border-radius:10px; font-size:11px;">Event</span>'
            )
        return ''
    get_event_badge.short_description = 'Type'

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
