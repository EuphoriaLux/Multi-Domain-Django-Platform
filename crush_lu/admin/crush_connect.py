from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext_lazy as _, ngettext


class CrushConnectMembershipAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "is_onboarded_display",
        "excluded_by_coach",
        "onboarded_at",
        "excluded_at",
        "updated_at",
    ]
    list_filter = ["excluded_by_coach", "onboarded_at"]
    list_editable = ["excluded_by_coach"]
    search_fields = [
        "user__email",
        "user__first_name",
        "user__last_name",
        "user__username",
        "exclusion_reason",
    ]
    raw_id_fields = ["user", "excluded_by"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("user", "onboarded_at")}),
        (
            _("Coach exclusion (panic button)"),
            {
                "fields": (
                    "excluded_by_coach",
                    "excluded_by",
                    "excluded_at",
                    "exclusion_reason",
                ),
                "description": _(
                    "Flipping ``excluded_by_coach`` removes this user from every other user's "
                    "Crush Connect pool immediately. Add a reason for the audit trail."
                ),
            },
        ),
        (_("Audit"), {"fields": ("created_at", "updated_at")}),
    )

    def is_onboarded_display(self, obj):
        return obj.is_onboarded

    is_onboarded_display.boolean = True
    is_onboarded_display.short_description = _("Onboarded")

    def save_model(self, request, obj, form, change):
        # Stamp the exclusion audit fields automatically when the flag is flipped.
        if change and "excluded_by_coach" in form.changed_data:
            if obj.excluded_by_coach:
                obj.excluded_at = obj.excluded_at or timezone.now()
                if not obj.excluded_by and hasattr(request.user, "crushcoach"):
                    obj.excluded_by = request.user.crushcoach
            else:
                obj.excluded_at = None
                obj.excluded_by = None
                obj.exclusion_reason = ""
        super().save_model(request, obj, form, change)


class ConnectDailyDropAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "drop_date",
        "recipient_count",
        "created_at",
    ]
    list_filter = ["drop_date"]
    search_fields = [
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
        "recipients__email",
        "recipients__username",
    ]
    raw_id_fields = ["user"]
    filter_horizontal = ["recipients"]
    readonly_fields = ["created_at"]
    date_hierarchy = "drop_date"
    actions = ["preview_today_for_selected_user", "preview_tomorrow_for_selected_user"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_recipient_count=Count("recipients"))

    def recipient_count(self, obj):
        return obj._recipient_count

    recipient_count.short_description = _("Cards")

    @admin.action(description=_("Preview today's Drop (idempotent)"))
    def preview_today_for_selected_user(self, request, queryset):
        self._preview(request, queryset, timezone.localdate())

    @admin.action(description=_("Preview tomorrow's Drop (idempotent)"))
    def preview_tomorrow_for_selected_user(self, request, queryset):
        self._preview(request, queryset, timezone.localdate() + timedelta(days=1))

    def _preview(self, request, queryset, target_date):
        """Compute (or fetch) the drop for the selected drops' users."""
        from crush_lu.services import get_or_create_daily_drop

        users = {drop.user for drop in queryset.select_related("user")}
        if not users:
            self.message_user(
                request,
                _("Select at least one Drop row to preview a user."),
                level=messages.WARNING,
            )
            return

        created = 0
        for user in users:
            drop = get_or_create_daily_drop(user, drop_date=target_date)
            if drop is not None:
                created += 1

        self.message_user(
            request,
            ngettext(
                "Previewed Drop for %(n)d user on %(date)s.",
                "Previewed Drops for %(n)d users on %(date)s.",
                created,
            )
            % {"n": created, "date": target_date.isoformat()},
            level=messages.SUCCESS,
        )


class CrushConnectWaitlistAdmin(admin.ModelAdmin):
    list_display = ["user", "joined_at", "notification_preference", "is_eligible"]
    list_filter = ["joined_at", "notification_preference"]
    search_fields = ["user__email", "user__first_name", "user__last_name", "user__username"]
    raw_id_fields = ["user"]
    readonly_fields = ["joined_at"]

    def is_eligible(self, obj):
        return obj.is_eligible

    is_eligible.boolean = True
    is_eligible.short_description = _("Eligible")


class SparkPromptAdmin(admin.ModelAdmin):
    list_display = ["text", "is_active", "weight", "updated_at"]
    list_filter = ["is_active"]
    list_editable = ["is_active", "weight"]
    search_fields = ["text", "text_en", "text_de", "text_fr"]
    readonly_fields = ["created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("is_active", "weight")}),
        (
            _("Prompt text (translations)"),
            {
                "fields": ("text", "text_en", "text_de", "text_fr"),
                "description": _(
                    "Edit each language version. ``text`` is the fallback for users in unmatched locales."
                ),
            },
        ),
        (_("Audit"), {"fields": ("created_at", "updated_at")}),
    )
