"""
OAuth State admin classes for Crush.lu Coach Panel.

Provides admin interface for debugging OAuth authentication issues,
particularly the Android PWA cross-browser problem.
"""

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from crush_lu.models import OAuthState


class OAuthStateAdmin(admin.ModelAdmin):
    """
    Admin for OAuth State records.

    Useful for debugging OAuth authentication issues:
    - View pending/used states
    - Check for expired states
    - Monitor Android PWA OAuth flow
    - Clean up old states
    """
    list_display = (
        'short_state_id',
        'provider',
        'get_status_badge',
        'is_popup',
        'auth_completed',
        'created_at',
        'expires_at',
        'get_time_remaining',
        'ip_address',
    )
    list_filter = ('provider', 'used', 'auth_completed', 'is_popup', 'created_at')
    search_fields = ('state_id', 'ip_address', 'user_agent')
    readonly_fields = (
        'state_id', 'state_data', 'created_at', 'expires_at', 'used',
        'provider', 'user_agent', 'ip_address', 'is_popup',
        'auth_completed', 'auth_user_id', 'auth_redirect_url', 'last_callback_at',
        'get_state_data_formatted',
    )
    ordering = ['-created_at']
    date_hierarchy = 'created_at'
    actions = ['cleanup_expired_states', 'cleanup_old_used_states']

    fieldsets = (
        ('State Identification', {
            'fields': ('state_id', 'provider'),
        }),
        ('Status', {
            'fields': ('used', 'is_popup', 'auth_completed'),
        }),
        ('Timing', {
            'fields': ('created_at', 'expires_at'),
        }),
        ('Authentication Result', {
            'fields': ('auth_user_id', 'auth_redirect_url', 'last_callback_at'),
            'classes': ('collapse',),
            'description': 'Information stored after successful OAuth completion',
        }),
        ('Request Information', {
            'fields': ('ip_address', 'user_agent'),
            'classes': ('collapse',),
        }),
        ('State Data', {
            'fields': ('get_state_data_formatted',),
            'classes': ('collapse',),
            'description': 'Raw JSON state data from Allauth',
        }),
    )

    def short_state_id(self, obj):
        """Display truncated state ID"""
        return format_html(
            '<code title="{}">{}</code>',
            obj.state_id,
            obj.state_id[:12] + '...'
        )
    short_state_id.short_description = 'State ID'
    short_state_id.admin_order_field = 'state_id'

    def get_status_badge(self, obj):
        """Display status with visual badge"""
        now = timezone.now()

        if obj.used:
            if obj.auth_completed:
                return format_html(
                    '<span style="background: #28a745; color: white; padding: 3px 8px; '
                    'border-radius: 12px; font-size: 11px;">✅ Completed</span>'
                )
            return format_html(
                '<span style="background: #6c757d; color: white; padding: 3px 8px; '
                'border-radius: 12px; font-size: 11px;">⚫ Used</span>'
            )
        elif now > obj.expires_at:
            return format_html(
                '<span style="background: #dc3545; color: white; padding: 3px 8px; '
                'border-radius: 12px; font-size: 11px;">⏰ Expired</span>'
            )
        else:
            return format_html(
                '<span style="background: #17a2b8; color: white; padding: 3px 8px; '
                'border-radius: 12px; font-size: 11px;">🔄 Active</span>'
            )
    get_status_badge.short_description = 'Status'

    def get_time_remaining(self, obj):
        """Display time remaining or expired"""
        now = timezone.now()

        if obj.used:
            return format_html('<span style="color: #999;">—</span>')

        if now > obj.expires_at:
            delta = now - obj.expires_at
            minutes = int(delta.total_seconds() / 60)
            return format_html(
                '<span style="color: #dc3545;">Expired {}m ago</span>',
                minutes
            )
        else:
            delta = obj.expires_at - now
            minutes = int(delta.total_seconds() / 60)
            seconds = int(delta.total_seconds() % 60)
            return format_html(
                '<span style="color: #28a745;">{}m {}s</span>',
                minutes, seconds
            )
    get_time_remaining.short_description = 'Time Left'

    def get_state_data_formatted(self, obj):
        """Display formatted JSON state data"""
        import json
        try:
            data = json.loads(obj.state_data)
            formatted = json.dumps(data, indent=2)
            return format_html(
                '<pre style="background: #f8f9fa; padding: 10px; border-radius: 4px; '
                'overflow-x: auto; max-width: 600px; font-size: 12px;">{}</pre>',
                formatted
            )
        except (json.JSONDecodeError, TypeError):
            return obj.state_data
    get_state_data_formatted.short_description = 'State Data (JSON)'

    def has_add_permission(self, request):
        """Disable adding - states are created programmatically"""
        return False

    def has_change_permission(self, request, obj=None):
        """Disable editing - states should not be modified"""
        return False

    @admin.action(description='🗑️ Clean up expired OAuth states')
    def cleanup_expired_states(self, request, queryset):
        """Delete expired OAuth states"""
        deleted = OAuthState.cleanup_expired()
        self.message_user(
            request,
            f'Deleted {deleted} expired OAuth state(s)',
            level='success' if deleted > 0 else 'info'
        )

    @admin.action(description='🗑️ Clean up old used OAuth states (>24h)')
    def cleanup_old_used_states(self, request, queryset):
        """Delete old used OAuth states"""
        deleted = OAuthState.cleanup_old_used(hours=24)
        self.message_user(
            request,
            f'Deleted {deleted} old used OAuth state(s)',
            level='success' if deleted > 0 else 'info'
        )
