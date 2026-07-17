from django.contrib import admin

from .models import (
    ConfirmedEncounterRemovalRequest,
    EventLobbyConsent,
    EventLobbyParticipation,
    EventMeetSignal,
)


@admin.register(EventLobbyConsent)
class EventLobbyConsentAdmin(admin.ModelAdmin):
    list_display = ("user", "version", "acknowledged_at")
    readonly_fields = ("acknowledged_at",)


@admin.register(EventLobbyParticipation)
class EventLobbyParticipationAdmin(admin.ModelAdmin):
    list_display = ("event", "user", "joined_at", "eligibility_source")
    list_filter = ("eligibility_source",)
    readonly_fields = ("opaque_handle", "joined_at", "created_at")


@admin.register(EventMeetSignal)
class EventMeetSignalAdmin(admin.ModelAdmin):
    list_display = ("event", "sender", "recipient", "created_at")
    readonly_fields = ("event", "sender", "recipient", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConfirmedEncounterRemovalRequest)
class ConfirmedEncounterRemovalRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "requested_by", "reason", "status", "requested_at")
    list_filter = ("status", "reason")
    readonly_fields = (
        "encounter",
        "requested_by",
        "reason",
        "details",
        "status",
        "requested_at",
        "reviewed_at",
        "reviewed_by_coach",
        "reviewed_by_staff",
        "resolution_notes",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
