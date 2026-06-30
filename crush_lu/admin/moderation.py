"""
Admin for the peer-safety models.

``UserReportAdmin`` is the moderation queue: the ``status`` filter is the live
work list (mirrors ``CuriositySparkAdmin``'s "accepted = coach queue" idiom). The
"Exclude reported user" action reuses the existing coach panic button
(``CrushConnectMembership.excluded_by_coach``) rather than inventing a parallel
exclusion mechanism. ``UserBlockAdmin`` is read-only oversight.
"""

from django.contrib import admin, messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext


class UserReportAdmin(admin.ModelAdmin):
    list_display = (
        "reporter",
        "reported_user",
        "reason",
        "status",
        "source",
        "created_at",
        "handled_by",
    )
    list_select_related = ["reporter", "reported_user", "handled_by"]
    list_filter = ("status", "reason", "source", "created_at")
    search_fields = (
        "reporter__username",
        "reporter__email",
        "reported_user__username",
        "reported_user__email",
        "details",
        "resolution_notes",
    )
    raw_id_fields = ("reporter", "reported_user", "handled_by")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"
    actions = (
        "mark_reviewing",
        "mark_dismissed",
        "exclude_reported_users",
    )
    fieldsets = (
        (None, {"fields": ("reporter", "reported_user", "reason", "details")}),
        (_("Context"), {"fields": ("source", "source_id")}),
        (
            _("Resolution"),
            {"fields": ("status", "handled_by", "handled_at", "resolution_notes")},
        ),
        (_("Meta"), {"fields": ("created_at",)}),
    )

    def _stamp_handled(self, request, queryset, status):
        return queryset.update(
            status=status, handled_by=request.user, handled_at=timezone.now()
        )

    @admin.action(description=_("Mark as reviewing"))
    def mark_reviewing(self, request, queryset):
        updated = self._stamp_handled(request, queryset, "reviewing")
        self.message_user(
            request,
            ngettext("%d report marked reviewing.", "%d reports marked reviewing.", updated)
            % updated,
        )

    @admin.action(description=_("Dismiss selected reports"))
    def mark_dismissed(self, request, queryset):
        updated = self._stamp_handled(request, queryset, "dismissed")
        self.message_user(
            request,
            ngettext("%d report dismissed.", "%d reports dismissed.", updated) % updated,
        )

    @admin.action(description=_("Exclude reported user from Crush Connect (panic button)"))
    def exclude_reported_users(self, request, queryset):
        """Flip the reported user's coach panic button, then mark the report actioned.

        Reuses ``CrushConnectMembership.excluded_by_coach`` — the same lever a
        coach uses manually — so the reported user drops out of every Crush
        Connect pool immediately.
        """
        from crush_lu.models import CrushConnectMembership
        from crush_lu.services.blocking import purge_user_from_connect_queues

        excluded = 0
        for report in queryset.select_related("reported_user"):
            membership, _created = CrushConnectMembership.objects.get_or_create(
                user=report.reported_user
            )
            if not membership.excluded_by_coach:
                membership.excluded_by_coach = True
                membership.excluded_at = timezone.now()
                if hasattr(request.user, "crushcoach"):
                    membership.excluded_by = request.user.crushcoach
                membership.exclusion_reason = (
                    f"Report #{report.pk}: {report.get_reason_display()}"
                )
                membership.save(
                    update_fields=[
                        "excluded_by_coach",
                        "excluded_at",
                        "excluded_by",
                        "exclusion_reason",
                    ]
                )
                excluded += 1
            # Excluding from future pools isn't enough — clear any live Spark or
            # coach pick so the excluded user can't linger in the coach queues.
            purge_user_from_connect_queues(report.reported_user)
            report.status = "actioned"
            report.handled_by = request.user
            report.handled_at = timezone.now()
            report.save(update_fields=["status", "handled_by", "handled_at"])

        self.message_user(
            request,
            _("%(n)d user(s) excluded; %(r)d report(s) marked actioned.")
            % {"n": excluded, "r": queryset.count()},
            level=messages.WARNING,
        )


class UserBlockAdmin(admin.ModelAdmin):
    """Read-only oversight of peer blocks."""

    list_display = ("blocker", "blocked", "reason", "created_at")
    list_select_related = ["blocker", "blocked"]
    list_filter = ("reason", "created_at")
    search_fields = (
        "blocker__username",
        "blocker__email",
        "blocked__username",
        "blocked__email",
    )
    raw_id_fields = ("blocker", "blocked")
    readonly_fields = ("blocker", "blocked", "reason", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False
