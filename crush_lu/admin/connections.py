"""
Connection-related admin classes for Crush.lu Coach Panel.

Includes:
- EventConnectionAdmin
- ConnectionMessageAdmin
"""

from django.contrib import admin
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from crush_lu.models import EventConnection, ConnectionMessage
from .filters import MutualConnectionFilter, HasMessagesFilter


class EventConnectionAdmin(admin.ModelAdmin):
    list_display = ('get_requester_display', 'get_recipient_display', 'event', 'status', 'is_mutual', 'assigned_coach', 'requested_at')
    list_filter = ('status', MutualConnectionFilter, HasMessagesFilter, 'requested_at', 'coach_approved_at')
    search_fields = (
        'requester__username', 'requester__first_name', 'requester__last_name', 'requester__email',
        'recipient__username', 'recipient__first_name', 'recipient__last_name', 'recipient__email',
        'event__title',
    )
    autocomplete_fields = ['requester', 'recipient']
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

    def get_requester_display(self, obj):
        full_name = obj.requester.get_full_name()
        if full_name:
            return format_html('{} <span style="color: #888; font-size: 11px;">({})</span>', full_name, obj.requester.username)
        return obj.requester.username
    get_requester_display.short_description = _('Requester')
    get_requester_display.admin_order_field = 'requester__first_name'

    def get_recipient_display(self, obj):
        full_name = obj.recipient.get_full_name()
        if full_name:
            return format_html('{} <span style="color: #888; font-size: 11px;">({})</span>', full_name, obj.recipient.username)
        return obj.recipient.username
    get_recipient_display.short_description = _('Recipient')
    get_recipient_display.admin_order_field = 'recipient__first_name'

    def is_mutual(self, obj):
        return obj.is_mutual
    is_mutual.boolean = True
    is_mutual.short_description = 'Mutual'


class ConnectionMessageAdmin(admin.ModelAdmin):
    list_display = ('connection', 'get_sender_display', 'is_coach_message', 'coach_approved', 'sent_at')
    list_filter = ('is_coach_message', 'coach_approved', 'sent_at')
    search_fields = ('sender__username', 'sender__first_name', 'sender__last_name', 'sender__email', 'message', 'connection__event__title')
    autocomplete_fields = ['sender']
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

    def get_sender_display(self, obj):
        full_name = obj.sender.get_full_name()
        if full_name:
            return format_html('{} <span style="color: #888; font-size: 11px;">({})</span>', full_name, obj.sender.username)
        return obj.sender.username
    get_sender_display.short_description = _('Sender')
    get_sender_display.admin_order_field = 'sender__first_name'

    def get_queryset(self, request):
        """Optimize queries with select_related for connection and sender FKs"""
        qs = super().get_queryset(request)
        return qs.select_related('connection__requester', 'connection__recipient', 'connection__event', 'sender')
