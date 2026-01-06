"""
Connection-related admin classes for Crush.lu Coach Panel.

Includes:
- EventConnectionAdmin
- ConnectionMessageAdmin
"""

from django.contrib import admin

from crush_lu.models import EventConnection, ConnectionMessage
from .filters import MutualConnectionFilter, HasMessagesFilter


class EventConnectionAdmin(admin.ModelAdmin):
    list_display = ('requester', 'recipient', 'event', 'status', 'is_mutual', 'assigned_coach', 'requested_at')
    list_filter = ('status', MutualConnectionFilter, HasMessagesFilter, 'requested_at', 'coach_approved_at')
    search_fields = ('requester__username', 'recipient__username', 'event__title')
    readonly_fields = ('requested_at', 'responded_at', 'coach_approved_at', 'shared_at', 'is_mutual')
    fieldsets = (
        ('Connection Details', {
            'fields': ('requester', 'recipient', 'event', 'status', 'is_mutual')
        }),
        ('Requester Info', {
            'fields': ('requester_note', 'requester_consents_to_share')
        }),
        ('Recipient Info', {
            'fields': ('recipient_consents_to_share',)
        }),
        ('Coach Facilitation', {
            'fields': ('assigned_coach', 'coach_notes', 'coach_introduction')
        }),
        ('Timestamps', {
            'fields': ('requested_at', 'responded_at', 'coach_approved_at', 'shared_at')
        }),
    )

    def get_queryset(self, request):
        """Optimize queries with select_related for user and event FKs"""
        qs = super().get_queryset(request)
        return qs.select_related('requester', 'recipient', 'event', 'assigned_coach__user')

    def is_mutual(self, obj):
        return obj.is_mutual
    is_mutual.boolean = True
    is_mutual.short_description = 'Mutual'


class ConnectionMessageAdmin(admin.ModelAdmin):
    list_display = ('connection', 'sender', 'is_coach_message', 'coach_approved', 'sent_at')
    list_filter = ('is_coach_message', 'coach_approved', 'sent_at')
    search_fields = ('sender__username', 'message', 'connection__event__title')
    readonly_fields = ('sent_at', 'read_at')
    fieldsets = (
        ('Message Details', {
            'fields': ('connection', 'sender', 'message')
        }),
        ('Moderation', {
            'fields': ('is_coach_message', 'coach_approved')
        }),
        ('Timestamps', {
            'fields': ('sent_at', 'read_at')
        }),
    )

    def get_queryset(self, request):
        """Optimize queries with select_related for connection and sender FKs"""
        qs = super().get_queryset(request)
        return qs.select_related('connection__requester', 'connection__recipient', 'connection__event', 'sender')
