"""
PassKit Device Registration admin classes for Crush.lu Coach Panel.

Provides admin interface for managing Apple Wallet device registrations
for push notification updates.
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone

from crush_lu.models import PasskitDeviceRegistration


class PasskitDeviceRegistrationAdmin(admin.ModelAdmin):
    """
    Admin for Apple PassKit device registrations.

    Tracks devices that have added Crush.lu passes to Apple Wallet.
    Used for sending push notifications to update passes.
    """
    list_display = (
        'get_device_short',
        'pass_type_identifier',
        'serial_number',
        'get_push_token_short',
        'created_at',
        'updated_at',
        'get_days_since_update',
    )
    list_filter = ('pass_type_identifier', 'created_at', 'updated_at')
    search_fields = ('device_library_identifier', 'serial_number', 'push_token')
    readonly_fields = (
        'device_library_identifier',
        'pass_type_identifier',
        'serial_number',
        'push_token',
        'created_at',
        'updated_at',
    )
    ordering = ['-updated_at']
    date_hierarchy = 'created_at'
    actions = ['trigger_push_update']

    fieldsets = (
        ('Device Information', {
            'fields': ('device_library_identifier', 'pass_type_identifier'),
        }),
        ('Pass Identification', {
            'fields': ('serial_number',),
        }),
        ('Push Notification', {
            'fields': ('push_token',),
            'description': 'APNS token for sending pass update notifications',
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def get_device_short(self, obj):
        """Display truncated device identifier"""
        device_id = obj.device_library_identifier
        if len(device_id) > 20:
            short = device_id[:10] + '...' + device_id[-6:]
        else:
            short = device_id
        return format_html(
            '<code title="{}">{}</code>',
            device_id,
            short
        )
    get_device_short.short_description = 'Device ID'
    get_device_short.admin_order_field = 'device_library_identifier'

    def get_push_token_short(self, obj):
        """Display truncated push token"""
        token = obj.push_token
        if len(token) > 24:
            short = token[:12] + '...' + token[-8:]
        else:
            short = token
        return format_html(
            '<code title="{}" style="font-size: 11px;">{}</code>',
            token,
            short
        )
    get_push_token_short.short_description = 'Push Token'

    def get_days_since_update(self, obj):
        """Display days since last update"""
        if not obj.updated_at:
            return format_html('<span style="color: #999;">—</span>')

        days = (timezone.now() - obj.updated_at).days

        if days == 0:
            return format_html('<span style="color: #28a745;">Today</span>')
        elif days == 1:
            return format_html('<span style="color: #28a745;">Yesterday</span>')
        elif days < 7:
            return format_html('<span style="color: #17a2b8;">{}d ago</span>', days)
        elif days < 30:
            return format_html('<span style="color: #ffc107;">{}d ago</span>', days)
        else:
            return format_html('<span style="color: #dc3545;">{}d ago</span>', days)
    get_days_since_update.short_description = 'Last Update'
    get_days_since_update.admin_order_field = 'updated_at'

    def has_add_permission(self, request):
        """Disable adding - registrations come from Apple Wallet"""
        return False

    def has_change_permission(self, request, obj=None):
        """Disable editing - registrations should not be modified"""
        return False

    @admin.action(description='🔔 Trigger push update for selected devices')
    def trigger_push_update(self, request, queryset):
        """
        Trigger APNS push notification to update passes on selected devices.

        Note: This requires proper APNS configuration to work.
        """
        count = queryset.count()

        # For now, just log that we would send push notifications
        # Actual APNS integration would go here
        self.message_user(
            request,
            f'Would trigger push update for {count} device(s). '
            f'APNS integration required for actual delivery.',
            level='info'
        )
