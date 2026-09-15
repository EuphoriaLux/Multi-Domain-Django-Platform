from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .forms import ArboristBookingAdminForm
from .models import ArboristBooking

# ============================================================================
# CUSTOM ADMIN SITE - Arborist Administration
# ============================================================================


class ArboristAdminSite(admin.AdminSite):
    """
    Custom admin site for Arborist site (arborist.lu).
    """

    site_header = "Arborist Administration - Tom Aakrann"
    site_title = "Arborist Admin"
    index_title = "Tree Care & Booking Management"


arborist_admin_site = ArboristAdminSite(name="arborist_admin")


@admin.register(ArboristBooking, site=arborist_admin_site)
class ArboristBookingAdmin(admin.ModelAdmin):
    """Admin configuration for tree service bookings."""

    list_display = (
        "booking_reference",
        "name",
        "service_type",
        "postal_code",
        "city_or_commune",
        "zone_badge",
        "rush_badge",
        "prepayment_display",
        "status",
        "payment_status",
        "created_at",
    )
    list_filter = (
        "status",
        "payment_status",
        "is_rush",
        "zone",
        "service_type",
        "created_at",
    )
    search_fields = (
        "booking_reference",
        "name",
        "email",
        "phone",
        "street_address",
        "postal_code",
        "city_or_commune",
    )
    readonly_fields = (
        "booking_reference",
        "zone",
        "distance_km",
        "prepayment_amount",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            _("Reference & Client"),
            {
                "fields": (
                    "booking_reference",
                    "name",
                    "email",
                    "phone",
                )
            },
        ),
        (
            _("Service Address"),
            {
                "fields": (
                    "street_address",
                    "postal_code",
                    "city_or_commune",
                )
            },
        ),
        (
            _("Service & Timing"),
            {
                "fields": (
                    "service_type",
                    "number_of_trees",
                    "is_rush",
                    "preferred_date",
                    "preferred_time_slot",
                    "notes",
                )
            },
        ),
        (
            _("Zone & Prepayment"),
            {
                "fields": (
                    "zone",
                    "distance_km",
                    "prepayment_amount",
                    "payment_status",
                )
            },
        ),
        (
            _("Management & Notes"),
            {
                "fields": (
                    "status",
                    "admin_notes",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    actions = ["mark_as_confirmed", "mark_prepayment_paid", "mark_as_completed"]

    form = ArboristBookingAdminForm

    # What the quote is computed from. zone, distance_km and prepayment_amount
    # are read-only above, so staff never type a price: adding a booking, or an
    # edit that touches one of these, re-prices it instead.
    PRICING_INPUT_FIELDS = {"postal_code", "is_rush"}

    def save_model(self, request, obj, form, change):
        if not change or self.PRICING_INPUT_FIELDS & set(form.changed_data):
            obj.apply_quote()
        super().save_model(request, obj, form, change)

    @admin.display(description=_("Zone"))
    def zone_badge(self, obj):
        color = "#15803d" if obj.zone == 1 else "#b45309"
        text = f"Zone {obj.zone} ({obj.distance_km} km)"
        return format_html(
            '<span style="background:{}; color:white; padding:3px 8px; border-radius:12px; font-weight:600; font-size:11px;">{}</span>',
            color,
            text,
        )

    @admin.display(description=_("Urgency"))
    def rush_badge(self, obj):
        if obj.is_urgent:
            return format_html(
                '<span style="background:#dc2626; color:white; padding:3px 8px; border-radius:12px; font-weight:700; font-size:11px;">🚨 RUSH</span>'
            )
        return format_html(
            '<span style="color:#6b7280; font-size:11px;">Standard</span>'
        )

    @admin.display(description=_("Prepayment"))
    def prepayment_display(self, obj):
        return f"{obj.prepayment_amount:.2f} €"

    # QuerySet.update() skips auto_now, so the bulk actions stamp updated_at.
    @admin.action(description=_("Mark selected bookings as Confirmed"))
    def mark_as_confirmed(self, request, queryset):
        count = queryset.update(status="confirmed", updated_at=timezone.now())
        self.message_user(request, _(f"{count} booking(s) marked as Confirmed."))

    @admin.action(description=_("Mark selected bookings as Prepayment Paid"))
    def mark_prepayment_paid(self, request, queryset):
        count = queryset.update(payment_status="paid", updated_at=timezone.now())
        self.message_user(request, _(f"{count} booking(s) marked as Prepayment Paid."))

    @admin.action(description=_("Mark selected bookings as Completed"))
    def mark_as_completed(self, request, queryset):
        count = queryset.update(status="completed", updated_at=timezone.now())
        self.message_user(request, _(f"{count} booking(s) marked as Completed."))
