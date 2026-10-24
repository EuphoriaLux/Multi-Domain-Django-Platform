"""
Admin for the Crush Connect real-person check.

Read-only oversight, superusers only. Coaches work checks from their own panel
(never from here), and the coach-only notes and checklist on these rows must
not be browsable by staff who are not superusers. The one write is the
"revoke" action, which goes through ``services.real_person_check.revoke_check``
so the badge and the check stay in step.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _


class _SuperuserReadOnlyAdmin(admin.ModelAdmin):
    """Visible to superusers only; nothing can be added, edited or deleted."""

    @staticmethod
    def _is_superuser(request):
        user = request.user
        return user.is_authenticated and user.is_active and user.is_superuser

    def has_module_permission(self, request):
        return self._is_superuser(request)

    def has_view_permission(self, request, obj=None):
        return self._is_superuser(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]


class ConnectRealPersonCheckAdmin(_SuperuserReadOnlyAdmin):
    list_display = (
        "id",
        "profile",
        "coach",
        "status",
        "medium",
        "claimed_at",
        "decided_at",
    )
    list_select_related = ("profile__user", "coach__user")
    list_filter = ("status", "medium", "claimed_at")
    search_fields = ("profile__user__username", "profile__user__email")
    date_hierarchy = "claimed_at"
    actions = ("revoke_passed_checks",)

    @admin.action(
        description=_("Revoke the selected passed checks and their badges"),
        permissions=["view"],
    )
    def revoke_passed_checks(self, request, queryset):
        from crush_lu.services.real_person_check import (
            RealPersonCheckError,
            revoke_check,
        )

        revoked = 0
        skipped = 0
        reason = f"Revoked via Crush admin by user {request.user.pk}."
        for check_id in queryset.values_list("pk", flat=True):
            try:
                revoke_check(request.user, check_id, reason)
            except RealPersonCheckError:
                skipped += 1
            else:
                revoked += 1
        if revoked:
            self.message_user(
                request,
                f"{revoked} check(s) revoked and badge(s) removed.",
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"{skipped} check(s) were not passed, so there was nothing to revoke.",
                level=messages.WARNING,
            )


class ConnectRealPersonBadgeAdmin(_SuperuserReadOnlyAdmin):
    list_display = ("profile", "medium", "granted_at", "source_check")
    list_select_related = ("profile__user", "source_check")
    list_filter = ("medium", "granted_at")
    search_fields = ("profile__user__username", "profile__user__email")
    date_hierarchy = "granted_at"
