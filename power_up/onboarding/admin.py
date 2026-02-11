from django.contrib import admin

from power_up.admin import power_up_admin_site
from .models import OnboardingEmail, OnboardingSession


class OnboardingEmailInline(admin.TabularInline):
    model = OnboardingEmail
    extra = 0
    fields = ["subject", "recipient_email", "downloaded_at", "created_at"]
    readonly_fields = ["created_at"]


class OnboardingSessionAdmin(admin.ModelAdmin):
    list_display = [
        "group",
        "status",
        "language",
        "recipient",
        "contact_name",
        "sender",
        "created_by",
        "created_at",
    ]
    list_filter = ["status", "language"]
    search_fields = ["group__name", "contact_name", "contact_email"]
    list_select_related = ["group", "created_by", "sender", "recipient"]
    readonly_fields = [
        "created_at",
        "updated_at",
        "contact_name",
        "contact_email",
        "sender_name",
        "sender_email",
        "sender_phone",
        "sender_title",
    ]
    inlines = [OnboardingEmailInline]


class OnboardingEmailAdmin(admin.ModelAdmin):
    list_display = [
        "session",
        "subject",
        "recipient_email",
        "downloaded_at",
        "created_at",
    ]
    list_filter = ["downloaded_at"]
    search_fields = ["subject", "recipient_email", "session__group__name"]
    list_select_related = ["session__group"]
    readonly_fields = ["created_at"]


power_up_admin_site.register(OnboardingSession, OnboardingSessionAdmin)
power_up_admin_site.register(OnboardingEmail, OnboardingEmailAdmin)
