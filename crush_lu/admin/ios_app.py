from django.contrib import admin



class IOSAppDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "device_name",
        "environment",
        "app_version",
        "app_build",
        "enabled",
        "last_seen_at",
        "last_push_at",
        "failure_count",
    )
    list_filter = ("enabled", "environment", "app_version", "created_at", "last_seen_at")
    search_fields = ("user__username", "user__email", "device_id", "device_token", "device_name")
    readonly_fields = ("created_at", "updated_at", "last_seen_at", "last_push_at")


class IOSNativeAuthCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "redirect_uri", "expires_at", "consumed_at", "created_at")
    list_filter = ("created_at", "expires_at", "consumed_at")
    search_fields = ("user__username", "user__email", "redirect_uri")
    readonly_fields = ("code_hash", "created_at", "consumed_at")
