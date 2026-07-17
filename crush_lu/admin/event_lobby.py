"""
Admin for the Event Lobby models — read-only oversight (spec §17 Phase A).

Lobby rows are immutable member actions (participations and irrevocable meet
signals), so staff get list/search visibility for support and abuse
investigation but no add/edit/delete. Even in admin, sender identities of
one-sided signals are support-only data — they must never be relayed to the
recipient (spec §13).
"""

from django.contrib import admin


class EventLobbyParticipationAdmin(admin.ModelAdmin):
    list_display = ("event", "user", "eligibility_source", "joined_at")
    list_select_related = ["event", "user"]
    list_filter = ("eligibility_source", "joined_at")
    search_fields = ("user__username", "user__email", "event__title")
    raw_id_fields = ("event_registration", "event", "user")
    readonly_fields = (
        "event_registration",
        "event",
        "user",
        "handle",
        "joined_at",
        "eligibility_source",
        "created_at",
    )
    date_hierarchy = "joined_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EventMeetSignalAdmin(admin.ModelAdmin):
    list_display = ("event", "sender", "recipient", "created_at", "mutual_revealed_at")
    list_select_related = ["event", "sender", "recipient"]
    list_filter = ("created_at",)
    search_fields = (
        "sender__username",
        "sender__email",
        "recipient__username",
        "recipient__email",
        "event__title",
    )
    raw_id_fields = ("event", "sender", "recipient")
    readonly_fields = (
        "event",
        "sender",
        "recipient",
        "created_at",
        "mutual_revealed_at",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
