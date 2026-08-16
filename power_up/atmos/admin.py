"""Venue/table/menu CRUD for the pilot — Django admin, per spec §6.2.

Registered on both the default site and `power_up_admin_site`, matching the
crm/onboarding convention (`power_up/admin.py` imports this module for its
side effect of calling `power_up_admin_site.register(...)`).
"""

from __future__ import annotations

from django.contrib import admin

from power_up.admin import power_up_admin_site

from .models import Guest, MenuCategory, MenuItem, Order, OrderItem, Table, Tab, Venue


class TableInline(admin.TabularInline):
    model = Table
    extra = 1
    fields = ("label", "seats", "is_active", "qr_token")
    readonly_fields = ("qr_token",)


class MenuCategoryInline(admin.TabularInline):
    model = MenuCategory
    extra = 1
    fields = ("name", "sort_order", "is_visible")


class VenueAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "service_open", "currency", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [TableInline, MenuCategoryInline]


class MenuItemInline(admin.TabularInline):
    model = MenuItem
    extra = 1
    fields = ("name", "price", "is_available", "contains_alcohol", "sort_order")


class MenuCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "venue", "sort_order", "is_visible")
    list_filter = ("venue",)
    inlines = [MenuItemInline]


class MenuItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "is_available", "contains_alcohol")
    list_filter = ("category__venue", "is_available", "contains_alcohol")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ("menu_item", "name_snapshot", "unit_price_snapshot", "quantity", "line_total")
    can_delete = False


class OrderAdmin(admin.ModelAdmin):
    list_display = ("short_code", "alias_snapshot", "venue", "status", "total_amount", "placed_at")
    list_filter = ("venue", "status")
    readonly_fields = (
        "id", "guest", "tab", "venue", "short_code", "alias_snapshot",
        "total_amount", "vignette", "vignette_source", "placed_at",
    )
    inlines = [OrderItemInline]


class GuestAdmin(admin.ModelAdmin):
    list_display = ("display", "venue", "status", "joined_at")
    list_filter = ("venue", "status")


# Only power_up_admin_site, matching crm/onboarding — not the default
# admin.site (see power_up/admin.py's import-for-side-effect convention).
power_up_admin_site.register(Venue, VenueAdmin)
power_up_admin_site.register(Table)
power_up_admin_site.register(MenuCategory, MenuCategoryAdmin)
power_up_admin_site.register(MenuItem, MenuItemAdmin)
power_up_admin_site.register(Tab)
power_up_admin_site.register(Guest, GuestAdmin)
power_up_admin_site.register(Order, OrderAdmin)
