from django.contrib import admin

from .models import (
    HubProfile,
    HubRequest,
    HubResource,
    HubTimelineEvent,
    WhatsAppMessage,
)


@admin.register(HubProfile)
class HubProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "primary_contact", "phone", "updated_at")
    search_fields = ("user__email", "user__username", "organization", "primary_contact")


@admin.register(HubRequest)
class HubRequestAdmin(admin.ModelAdmin):
    list_display = ("subject", "user", "category", "status", "priority", "created_at")
    list_filter = ("status", "priority", "category")
    search_fields = ("subject", "summary", "user__email", "user__username")
    date_hierarchy = "created_at"


@admin.register(HubResource)
class HubResourceAdmin(admin.ModelAdmin):
    list_display = ("title", "type", "is_public", "updated_at")
    list_filter = ("type", "is_public")
    search_fields = ("title", "summary", "url")
    filter_horizontal = ("audience",)


@admin.register(HubTimelineEvent)
class HubTimelineEventAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "kind", "occurred_at")
    list_filter = ("kind",)
    search_fields = ("title", "body", "user__email", "user__username")
    date_hierarchy = "occurred_at"


@admin.register(WhatsAppMessage)
class WhatsAppMessageAdmin(admin.ModelAdmin):
    list_display = (
        "template_name",
        "recipient",
        "user",
        "status",
        "wa_message_id",
        "created_at",
    )
    list_filter = ("status",)
    search_fields = ("template_name", "recipient", "wa_message_id", "user__email")
    date_hierarchy = "created_at"
    readonly_fields = ("wa_message_id", "status_history", "created_at", "updated_at")
