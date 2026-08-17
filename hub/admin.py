from django.contrib import admin

from .models import (
    HubProfile,
    HubRequest,
    HubResource,
    HubTimelineEvent,
    Location,
    LocationContact,
    PaymentIn,
    PaymentOut,
    Payroll,
    Refund,
    WhatsAppInboundMessage,
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


class LocationContactInline(admin.StackedInline):
    model = LocationContact
    extra = 0
    max_num = 1


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "city",
        "partnership_stage",
        "max_capacity",
        "account_manager",
        "last_contact_date",
    )
    list_filter = (
        "partnership_stage",
        "country",
        "has_outdoor_space",
        "has_kitchen",
        "has_private_room",
        "has_sound_system",
    )
    search_fields = ("name", "address", "city", "account_manager", "notes")
    date_hierarchy = "last_contact_date"
    inlines = (LocationContactInline,)


@admin.register(LocationContact)
class LocationContactAdmin(admin.ModelAdmin):
    list_display = ("name", "location", "role", "email", "phone")
    search_fields = ("name", "location__name", "role", "email", "phone")
    list_select_related = ("location",)


@admin.register(PaymentIn)
class PaymentInAdmin(admin.ModelAdmin):
    list_display = ("date", "source", "client_name", "amount", "status", "reference")
    list_filter = ("status", "payment_method")
    search_fields = ("source", "client_name", "reference")
    date_hierarchy = "date"


@admin.register(PaymentOut)
class PaymentOutAdmin(admin.ModelAdmin):
    list_display = ("date", "payee", "category", "amount", "status", "location")
    list_filter = ("status", "category", "payment_method", "deposit_status")
    search_fields = ("payee", "description", "location__name")
    date_hierarchy = "date"
    list_select_related = ("location",)
    autocomplete_fields = ("location",)


@admin.register(Payroll)
class PayrollAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "employee_name",
        "category",
        "amount",
        "gross_salary",
        "employer_charges",
        "status",
    )
    list_filter = ("status", "category", "payment_method")
    search_fields = ("employee_name", "description")
    date_hierarchy = "date"


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("date", "participant_name", "event_name", "amount", "status")
    list_filter = ("status",)
    search_fields = ("participant_name", "event_name", "reason")
    date_hierarchy = "date"


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


@admin.register(WhatsAppInboundMessage)
class WhatsAppInboundMessageAdmin(admin.ModelAdmin):
    list_display = (
        "from_number",
        "contact_name",
        "message_type",
        "is_read",
        "received_at",
    )
    list_filter = ("is_read", "message_type")
    search_fields = ("from_number", "contact_name", "text", "wa_message_id")
    date_hierarchy = "received_at"
    readonly_fields = ("wa_message_id", "payload", "received_at", "created_at")
