from django.contrib import admin
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .models import LeadEvent, LeadPhoto
from .services.leads import notify_lead


class OverdueFilter(admin.SimpleListFilter):
    title = _("Follow-up")
    parameter_name = "overdue"

    def lookups(self, request, model_admin):
        return [("yes", _("Overdue"))]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(follow_up_at__lt=timezone.now()).exclude(
                status="closed"
            )


class PhotoInline(admin.TabularInline):
    model = LeadPhoto
    fields = ("tree_label", "category", "private_link", "created_at")
    readonly_fields = fields
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Photo"))
    def private_link(self, obj):
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            reverse("arborist:lead_photo", kwargs={"photo_id": obj.pk}),
            _("View private photo"),
        )


class EventInline(admin.TabularInline):
    model = LeadEvent
    fields = ("created_at", "actor", "description")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class ArboristLeadAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "service",
        "postal_code",
        "is_urgent",
        "status",
        "owner",
        "next_action",
        "follow_up_at",
        "overdue",
        "staff_delivery",
        "customer_delivery",
        "created_at",
    )
    list_filter = (
        "status",
        "is_urgent",
        "source",
        "owner",
        OverdueFilter,
        "staff_delivery",
        "customer_delivery",
    )
    search_fields = ("name", "email", "phone", "postal_code")
    readonly_fields = (
        "id",
        "submission_id",
        "first_attribution",
        "last_attribution",
        "staff_delivery",
        "customer_delivery",
        "notification_attempted_at",
        "created_at",
        "updated_at",
    )
    inlines = (PhotoInline, EventInline)
    actions = ("retry_notifications",)
    list_select_related = ("owner",)

    @admin.display(boolean=True, description=_("Overdue"))
    def overdue(self, obj):
        return bool(
            obj.follow_up_at
            and obj.follow_up_at < timezone.now()
            and obj.status != "closed"
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        LeadEvent.objects.create(
            lead=obj,
            actor=request.user,
            description=(
                ("Updated: " + ", ".join(form.changed_data))[:500]
                if change
                else "Created by staff"
            ),
        )

    @admin.action(
        description=_("Retry unsent notifications (one enquiry at a time)"),
        permissions=["change"],
    )
    def retry_notifications(self, request, queryset):
        for pk in queryset.order_by("created_at").values_list("pk", flat=True)[:1]:
            transaction.on_commit(lambda lead_id=pk: notify_lead(lead_id), robust=True)
        self.message_user(
            request,
            _(
                "Unsent notifications scheduled for retry. Check delivery status before retrying again."
            ),
        )
