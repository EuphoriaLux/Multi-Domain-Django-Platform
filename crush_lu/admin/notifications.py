"""
Push notification admin classes for Crush.lu Coach Panel.

Includes:
- PushSubscriptionAdmin
- CoachPushSubscriptionAdmin
"""

from django.contrib import admin
from django.contrib import messages as django_messages

from crush_lu.models import PushSubscription, CoachPushSubscription


class PushSubscriptionAdmin(admin.ModelAdmin):
    """
    🔔 PUSH SUBSCRIPTION MANAGEMENT

    View and manage user push notification subscriptions.
    Each user can have multiple subscriptions (different devices).
    """
    list_display = (
        'user', 'device_name', 'enabled', 'created_at',
        'last_used_at', 'failure_count', 'get_preferences'
    )
    list_filter = (
        'enabled', 'created_at', 'failure_count',
        'notify_new_messages', 'notify_event_reminders',
        'notify_new_connections', 'notify_profile_updates'
    )
    search_fields = ('user__username', 'user__email', 'device_name', 'endpoint')
    readonly_fields = (
        'endpoint', 'p256dh_key', 'auth_key', 'user_agent',
        'created_at', 'updated_at', 'last_used_at', 'failure_count'
    )
    date_hierarchy = 'created_at'

    fieldsets = (
        ('User & Device', {
            'fields': ('user', 'device_name', 'user_agent')
        }),
        ('Subscription Details', {
            'fields': ('endpoint', 'p256dh_key', 'auth_key'),
            'classes': ('collapse',)
        }),
        ('Notification Preferences', {
            'fields': (
                'enabled',
                'notify_new_messages',
                'notify_event_reminders',
                'notify_new_connections',
                'notify_profile_updates',
            )
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at', 'last_used_at', 'failure_count')
        }),
    )

    def get_preferences(self, obj):
        """Display active notification types"""
        prefs = []
        if obj.notify_new_messages:
            prefs.append('Messages')
        if obj.notify_event_reminders:
            prefs.append('Events')
        if obj.notify_new_connections:
            prefs.append('Connections')
        if obj.notify_profile_updates:
            prefs.append('Profile')
        return ', '.join(prefs) if prefs else 'None'
    get_preferences.short_description = 'Active Notifications'

    actions = ['enable_subscriptions', 'disable_subscriptions', 'send_test_notification']

    def enable_subscriptions(self, request, queryset):
        """Enable selected subscriptions"""
        updated = queryset.update(enabled=True)
        self.message_user(
            request,
            f'{updated} subscription(s) enabled.',
            level=django_messages.SUCCESS
        )
    enable_subscriptions.short_description = '✅ Enable selected subscriptions'

    def disable_subscriptions(self, request, queryset):
        """Disable selected subscriptions"""
        updated = queryset.update(enabled=False)
        self.message_user(
            request,
            f'{updated} subscription(s) disabled.',
            level=django_messages.SUCCESS
        )
    disable_subscriptions.short_description = '🔕 Disable selected subscriptions'

    def send_test_notification(self, request, queryset):
        """Send test notification to selected subscriptions (specific devices only)"""
        from crush_lu.push_notifications import send_push_to_subscription
        from django.utils.translation import gettext as _

        success = 0
        failed = 0
        for subscription in queryset:
            result = send_push_to_subscription(
                subscription=subscription,
                title=_("Test Notification"),
                body=_("Push notifications are working! You'll receive updates about events, messages, and connections."),
                url='/dashboard/',
                tag='user-test-notification'
            )
            if result.get('success'):
                success += 1
            else:
                failed += 1

        total = success + failed
        self.message_user(
            request,
            f'Sent test notifications: {success}/{total} successful.',
            level=django_messages.SUCCESS if success > 0 else django_messages.WARNING
        )
    send_test_notification.short_description = '📤 Send test notification'


class CoachPushSubscriptionAdmin(admin.ModelAdmin):
    """
    🔔 COACH PUSH SUBSCRIPTION MANAGEMENT

    View and manage coach push notification subscriptions.
    Each coach can have multiple subscriptions (different devices).
    Separate from user push subscriptions.
    """
    list_display = (
        'coach', 'device_name', 'enabled', 'created_at',
        'last_used_at', 'failure_count', 'get_preferences'
    )
    list_filter = (
        'enabled', 'created_at', 'failure_count',
        'notify_new_submissions', 'notify_screening_reminders',
        'notify_user_responses', 'notify_system_alerts'
    )
    search_fields = ('coach__user__username', 'coach__user__email', 'device_name', 'endpoint')
    readonly_fields = (
        'endpoint', 'p256dh_key', 'auth_key', 'user_agent',
        'created_at', 'updated_at', 'last_used_at', 'failure_count'
    )
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Coach & Device', {
            'fields': ('coach', 'device_name', 'user_agent')
        }),
        ('Subscription Details', {
            'fields': ('endpoint', 'p256dh_key', 'auth_key'),
            'classes': ('collapse',)
        }),
        ('Notification Preferences', {
            'fields': (
                'enabled',
                'notify_new_submissions',
                'notify_screening_reminders',
                'notify_user_responses',
                'notify_system_alerts',
            )
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at', 'last_used_at', 'failure_count')
        }),
    )

    def get_preferences(self, obj):
        """Display active notification types"""
        prefs = []
        if obj.notify_new_submissions:
            prefs.append('Submissions')
        if obj.notify_screening_reminders:
            prefs.append('Screening')
        if obj.notify_user_responses:
            prefs.append('Responses')
        if obj.notify_system_alerts:
            prefs.append('Alerts')
        return ', '.join(prefs) if prefs else 'None'
    get_preferences.short_description = 'Active Notifications'

    actions = ['enable_subscriptions', 'disable_subscriptions', 'send_test_notification']

    def enable_subscriptions(self, request, queryset):
        """Enable selected subscriptions"""
        updated = queryset.update(enabled=True)
        self.message_user(
            request,
            f'{updated} coach subscription(s) enabled.',
            level=django_messages.SUCCESS
        )
    enable_subscriptions.short_description = '✅ Enable selected subscriptions'

    def disable_subscriptions(self, request, queryset):
        """Disable selected subscriptions"""
        updated = queryset.update(enabled=False)
        self.message_user(
            request,
            f'{updated} coach subscription(s) disabled.',
            level=django_messages.SUCCESS
        )
    disable_subscriptions.short_description = '🔕 Disable selected subscriptions'

    def send_test_notification(self, request, queryset):
        """Send test notification to selected coach subscriptions (specific devices only)"""
        from crush_lu.coach_notifications import send_coach_push_to_subscription
        from django.utils.translation import gettext as _

        success = 0
        failed = 0
        for subscription in queryset:
            result = send_coach_push_to_subscription(
                subscription=subscription,
                title=_("Test Notification"),
                body=_("Your coach notifications are working correctly!"),
                url='/coach/dashboard/',
                tag='coach-test-notification'
            )
            if result.get('success'):
                success += 1
            else:
                failed += 1

        total = success + failed
        self.message_user(
            request,
            f'Sent test notifications: {success}/{total} successful.',
            level=django_messages.SUCCESS if success > 0 else django_messages.WARNING
        )
    send_test_notification.short_description = '📤 Send test notification'
