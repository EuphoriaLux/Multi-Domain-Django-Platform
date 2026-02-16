from django.contrib import admin
from django.utils.translation import gettext_lazy as _


class CrushSparkAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "sender",
        "recipient",
        "event",
        "status",
        "is_sender_revealed",
        "created_at",
    ]
    list_filter = ["status", "is_sender_revealed", "event"]
    search_fields = [
        "sender__username",
        "sender__first_name",
        "recipient__username",
        "recipient__first_name",
        "sender_description",
    ]
    raw_id_fields = ["sender", "recipient", "assigned_coach", "journey", "special_experience"]
    readonly_fields = ["created_at", "coach_assigned_at", "journey_created_at", "delivered_at", "completed_at", "revealed_at"]
    list_select_related = ["sender", "recipient", "event"]

    fieldsets = (
        (None, {
            "fields": ("event", "sender", "recipient", "status"),
        }),
        (_("Coach Mediation"), {
            "fields": ("assigned_coach", "sender_description", "coach_notes"),
        }),
        (_("Journey"), {
            "fields": ("journey", "special_experience", "sender_message"),
        }),
        (_("Anonymity"), {
            "fields": ("is_sender_revealed", "revealed_at"),
        }),
        (_("Media"), {
            "fields": (
                "chapter1_image",
                "chapter3_image_1", "chapter3_image_2", "chapter3_image_3",
                "chapter3_image_4", "chapter3_image_5",
                "chapter4_video", "chapter5_letter_music",
            ),
            "classes": ("collapse",),
        }),
        (_("Timestamps"), {
            "fields": (
                "created_at", "coach_assigned_at", "journey_created_at",
                "delivered_at", "completed_at", "expires_at",
            ),
        }),
    )
