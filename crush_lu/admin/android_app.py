from django.contrib import admin

from ..models import AndroidAppDevice


class AndroidAppDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "device_name",
        "app_version",
        "app_build",
        "enabled",
        "last_seen_at",
        "last_push_at",
        "failure_count",
    )
    list_filter = ("enabled", "app_version", "created_at", "last_seen_at")
    search_fields = ("user__username", "user__email", "device_id", "registration_token", "device_name")
    readonly_fields = ("created_at", "updated_at", "last_seen_at", "last_push_at")
