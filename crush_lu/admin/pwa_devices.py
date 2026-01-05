"""
PWA Device Installation admin for Crush.lu Coach Panel.
Admin-only visibility of user PWA installations across devices.
"""
from django.contrib import admin
from django.utils.html import format_html

from crush_lu.models import PWADeviceInstallation


class PWADeviceInstallationAdmin(admin.ModelAdmin):
    """Admin for tracking PWA installations across devices."""

    list_display = (
        'user',
        'device_category',
        'os_type',
        'form_factor',
        'browser',
        'installed_at',
        'last_used_at',
        'get_activity_status',
    )
    list_filter = (
        'os_type',
        'form_factor',
        'browser',
        'installed_at',
        'last_used_at',
    )
    search_fields = (
        'user__username',
        'user__email',
        'user__first_name',
        'user__last_name',
        'device_category',
        'browser',
    )
    readonly_fields = (
        'user',
        'device_fingerprint',
        'os_type',
        'form_factor',
        'device_category',
        'browser',
        'user_agent',
        'installed_at',
        'last_used_at',
    )
    date_hierarchy = 'installed_at'
    ordering = ['-last_used_at']

    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Device Classification', {
            'fields': ('device_category', 'os_type', 'form_factor', 'browser')
        }),
        ('Technical Details', {
            'fields': ('device_fingerprint', 'user_agent'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('installed_at', 'last_used_at')
        }),
    )

    def get_activity_status(self, obj):
        """Display color-coded activity status based on last use."""
        days = obj.days_since_last_use
        if days == 0:
            return format_html(
                '<span style="color: green; font-weight: bold;">Today</span>'
            )
        elif days < 7:
            return format_html(
                '<span style="color: green;">{} days ago</span>',
                days
            )
        elif days < 30:
            return format_html(
                '<span style="color: orange;">{} days ago</span>',
                days
            )
        else:
            return format_html(
                '<span style="color: gray;">{} days ago</span>',
                days
            )
    get_activity_status.short_description = 'Activity'
    get_activity_status.admin_order_field = 'last_used_at'

    def has_add_permission(self, request):
        """Installations are created via API only."""
        return False

    def has_change_permission(self, request, obj=None):
        """Read-only admin - no changes allowed."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Allow superusers to delete stale installations."""
        return request.user.is_superuser
