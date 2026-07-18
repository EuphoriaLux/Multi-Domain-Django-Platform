"""
Admin for the Event Lobby models — read-only oversight (spec §17 Phase A).

Lobby rows are immutable member actions (participations and irrevocable meet
signals), so staff get list/search visibility for support and abuse
investigation but no add/edit/delete. Even in admin, sender identities of
one-sided signals are support-only data — they must never be relayed to the
recipient (spec §13).
"""

from django.contrib import admin, messages


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


class EventMeetingConfirmationAdmin(admin.ModelAdmin):
    list_display = ("event", "confirmer", "other_user", "created_at")
    list_select_related = ["event", "confirmer", "other_user"]
    list_filter = ("created_at",)
    search_fields = (
        "confirmer__username",
        "confirmer__email",
        "other_user__username",
        "other_user__email",
        "event__title",
    )
    raw_id_fields = ("event", "confirmer", "other_user")
    readonly_fields = ("event", "confirmer", "other_user", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ConfirmedEncounterAdmin(admin.ModelAdmin):
    """Read/oversight only — status transitions belong to the (future) removal
    review workflow (§8.1), not manual admin edits."""

    list_display = (
        "user_low",
        "user_high",
        "status",
        "created_at",
        "created_from_event",
    )
    list_select_related = ["user_low", "user_high", "created_from_event"]
    list_filter = ("status", "created_at")
    search_fields = (
        "user_low__username",
        "user_low__email",
        "user_high__username",
        "user_high__email",
    )
    raw_id_fields = ("user_low", "user_high", "created_from_event")
    readonly_fields = (
        "user_low",
        "user_high",
        "created_from_event",
        "status",
        "created_at",
        "hidden_at",
        "removed_at",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ConfirmedEncounterRemovalRequestAdmin(admin.ModelAdmin):
    """Private staff queue; outcomes go through the audited service workflow."""

    list_display = (
        "id",
        "encounter",
        "requested_by",
        "reason",
        "status",
        "requested_at",
        "reviewed_at",
    )
    list_select_related = (
        "encounter",
        "requested_by",
        "reviewed_by_coach",
        "reviewed_by_staff",
    )
    list_filter = ("status", "reason", "requested_at")
    search_fields = (
        "requested_by__username",
        "requested_by__email",
        "encounter__user_low__username",
        "encounter__user_high__username",
    )
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
    date_hierarchy = "requested_at"
    actions = (
        "approve_removals",
        "keep_removals_hidden",
        "restore_encounters",
    )

    @staticmethod
    def _can_review(user):
        if not user.is_authenticated or not user.is_staff:
            return False
        return user.is_superuser or not hasattr(user, "crushcoach")

    def get_queryset(self, request):
        from crush_lu.services.event_lobby import reviewable_removal_requests

        queryset = super().get_queryset(request)
        allowed_ids = reviewable_removal_requests(request.user).values("pk")
        return queryset.filter(pk__in=allowed_ids)

    def has_module_permission(self, request):
        return self._can_review(request.user)

    def has_view_permission(self, request, obj=None):
        return self._can_review(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _review_selected(self, request, queryset, decision, label):
        from crush_lu.services.event_lobby import (
            LobbyAccessError,
            review_encounter_removal_request,
        )

        reviewed = 0
        skipped = 0
        notes = f"{label} via Crush admin by staff user {request.user.pk}."
        for removal_request_id in queryset.values_list("pk", flat=True):
            try:
                review_encounter_removal_request(
                    request.user,
                    removal_request_id,
                    decision,
                    notes,
                )
            except LobbyAccessError:
                skipped += 1
            else:
                reviewed += 1
        if reviewed:
            self.message_user(
                request,
                f"{reviewed} removal request(s) reviewed.",
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} request(s) were already reviewed or unavailable.",
                level=messages.WARNING,
            )

    @admin.action(description="Approve removal", permissions=["view"])
    def approve_removals(self, request, queryset):
        self._review_selected(request, queryset, "approve", "Removal approved")

    @admin.action(description="Resolve and keep hidden", permissions=["view"])
    def keep_removals_hidden(self, request, queryset):
        self._review_selected(request, queryset, "keep_hidden", "Kept hidden")

    @admin.action(description="Restore encounter visibility", permissions=["view"])
    def restore_encounters(self, request, queryset):
        self._review_selected(request, queryset, "restore", "Visibility restored")
