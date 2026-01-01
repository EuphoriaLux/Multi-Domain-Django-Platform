"""
User preferences admin classes for Crush.lu Coach Panel.

Includes:
- UserActivityAdmin
- EmailPreferenceAdmin
"""

from django.contrib import admin
from django.contrib import messages as django_messages
from django.utils.html import format_html

from crush_lu.models import UserActivity, EmailPreference


class UserActivityAdmin(admin.ModelAdmin):
    """
    📊 USER ACTIVITY TRACKING

    Monitor user activity, online status, and PWA usage.
    """
    list_display = (
        'user', 'get_status', 'last_seen', 'get_pwa_status',
        'total_visits', 'is_active_user', 'minutes_since_last_seen'
    )
    list_filter = (
        'is_pwa_user', 'last_seen', 'first_seen'
    )
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('user', 'last_seen', 'last_pwa_visit', 'total_visits', 'first_seen')
    date_hierarchy = 'last_seen'

    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Activity Status', {
            'fields': ('last_seen', 'last_pwa_visit', 'total_visits')
        }),
        ('PWA Usage', {
            'fields': ('is_pwa_user',)
        }),
        ('Tracking Info', {
            'fields': ('first_seen',)
        }),
    )

    def get_status(self, obj):
        """Display online/offline status with icon"""
        if obj.is_online:
            return format_html('<span style="color: green;">🟢 Online</span>')
        elif obj.is_active_user:
            return format_html('<span style="color: orange;">🟡 Active ({})</span>', obj.minutes_since_last_seen)
        else:
            return format_html('<span style="color: gray;">⚫ Inactive</span>')
    get_status.short_description = 'Status'
    get_status.admin_order_field = 'last_seen'

    def get_pwa_status(self, obj):
        """Display PWA usage status"""
        if obj.uses_pwa:
            return format_html('<span style="color: purple;">📱 PWA User</span>')
        elif obj.is_pwa_user:
            return format_html('<span style="color: gray;">📱 PWA (Inactive)</span>')
        else:
            return format_html('<span style="color: gray;">🌐 Browser Only</span>')
    get_pwa_status.short_description = 'PWA Status'
    get_pwa_status.admin_order_field = 'is_pwa_user'

    def get_queryset(self, request):
        """Add computed fields for filtering"""
        qs = super().get_queryset(request)
        return qs.select_related('user')


class EmailPreferenceAdmin(admin.ModelAdmin):
    """
    📧 EMAIL PREFERENCE MANAGEMENT

    View and manage user email notification preferences.
    Track unsubscribes and email category preferences.
    """
    list_display = (
        'user', 'get_email', 'get_email_status_icons', 'unsubscribed_all',
        'email_marketing', 'updated_at'
    )
    list_filter = (
        'unsubscribed_all', 'email_marketing',
        'email_profile_updates', 'email_event_reminders',
        'email_new_connections', 'email_new_messages',
        'created_at', 'updated_at'
    )
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('unsubscribe_token', 'created_at', 'updated_at', 'get_unsubscribe_link')
    date_hierarchy = 'updated_at'

    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('📧 Email Categories', {
            'fields': (
                'email_profile_updates',
                'email_event_reminders',
                'email_new_connections',
                'email_new_messages',
            ),
            'description': 'Control which types of emails the user receives'
        }),
        ('📢 Marketing', {
            'fields': ('email_marketing',),
            'description': 'Marketing emails require explicit opt-in (GDPR)'
        }),
        ('🔕 Master Switch', {
            'fields': ('unsubscribed_all',),
            'description': 'If enabled, user receives NO emails'
        }),
        ('🔗 Unsubscribe Link', {
            'fields': ('unsubscribe_token', 'get_unsubscribe_link'),
            'classes': ('collapse',),
            'description': 'Secure one-click unsubscribe link'
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_email(self, obj):
        """Display user's email"""
        return obj.user.email
    get_email.short_description = 'Email'
    get_email.admin_order_field = 'user__email'

    def get_email_status_icons(self, obj):
        """Display enabled email categories with icons"""
        if obj.unsubscribed_all:
            return format_html('<span style="color: red;">❌ All Unsubscribed</span>')

        icons = []
        if obj.email_profile_updates:
            icons.append('👤')
        if obj.email_event_reminders:
            icons.append('📅')
        if obj.email_new_connections:
            icons.append('🔗')
        if obj.email_new_messages:
            icons.append('💬')
        if obj.email_marketing:
            icons.append('📢')

        if icons:
            return format_html('<span title="Profile, Events, Connections, Messages, Marketing">{}</span>', ' '.join(icons))
        return format_html('<span style="color: orange;">⚠️ All Off</span>')
    get_email_status_icons.short_description = 'Active Categories'

    def get_unsubscribe_link(self, obj):
        """Display the unsubscribe URL for testing"""
        if obj.unsubscribe_token:
            url = f"https://crush.lu/unsubscribe/{obj.unsubscribe_token}/"
            return format_html(
                '<div style="padding: 10px; background: #f8f9fa; border-radius: 5px;">'
                '<strong>One-Click Unsubscribe URL:</strong><br>'
                '<code style="font-size: 11px; word-break: break-all;">{}</code>'
                '</div>',
                url
            )
        return "N/A"
    get_unsubscribe_link.short_description = 'Unsubscribe Link'

    actions = ['enable_all_emails', 'disable_all_emails', 'enable_marketing', 'disable_marketing']

    def enable_all_emails(self, request, queryset):
        """Re-subscribe users to all emails"""
        updated = queryset.update(
            unsubscribed_all=False,
            email_profile_updates=True,
            email_event_reminders=True,
            email_new_connections=True,
            email_new_messages=True
        )
        self.message_user(
            request,
            f'{updated} user(s) re-subscribed to all emails (except marketing).',
            level=django_messages.SUCCESS
        )
    enable_all_emails.short_description = '✅ Re-subscribe to all emails'

    def disable_all_emails(self, request, queryset):
        """Unsubscribe users from all emails"""
        updated = queryset.update(unsubscribed_all=True)
        self.message_user(
            request,
            f'{updated} user(s) unsubscribed from all emails.',
            level=django_messages.SUCCESS
        )
    disable_all_emails.short_description = '🔕 Unsubscribe from all emails'

    def enable_marketing(self, request, queryset):
        """Opt users into marketing emails"""
        updated = queryset.update(email_marketing=True)
        self.message_user(
            request,
            f'{updated} user(s) opted into marketing emails.',
            level=django_messages.SUCCESS
        )
    enable_marketing.short_description = '📢 Opt into marketing'

    def disable_marketing(self, request, queryset):
        """Opt users out of marketing emails"""
        updated = queryset.update(email_marketing=False)
        self.message_user(
            request,
            f'{updated} user(s) opted out of marketing emails.',
            level=django_messages.SUCCESS
        )
    disable_marketing.short_description = '🔕 Opt out of marketing'
