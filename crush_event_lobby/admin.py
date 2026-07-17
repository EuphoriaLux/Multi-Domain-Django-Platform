from django.contrib import admin

from .models import EventLobbyConsent, EventLobbyParticipation, EventMeetSignal


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

