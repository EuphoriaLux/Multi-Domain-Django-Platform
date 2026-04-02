from django.contrib import admin
from django.utils.translation import gettext_lazy as _


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
