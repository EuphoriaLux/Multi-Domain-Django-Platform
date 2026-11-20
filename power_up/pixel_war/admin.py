"""Pixel War admin registrations for the Power-Up admin site."""

from django.contrib import admin

from power_up.admin import power_up_admin_site
from power_up.pixel_war.models import (
    Pixel,
    PixelCanvas,
    PixelHistory,
    UserPixelCooldown,
    UserPixelStats,
)


class PixelCanvasAdmin(admin.ModelAdmin):
    list_display = ["name", "width", "height", "is_active", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name"]


class PixelAdmin(admin.ModelAdmin):
    list_display = ["canvas", "x", "y", "color", "placed_by", "placed_at"]
    list_filter = ["canvas", "placed_at"]
    list_select_related = ["canvas", "placed_by"]
    raw_id_fields = ["placed_by"]
    search_fields = ["placed_by__username", "color"]


class PixelHistoryAdmin(admin.ModelAdmin):
    list_display = ["canvas", "x", "y", "color", "placed_by", "placed_at"]
    list_filter = ["canvas", "placed_at"]
    list_select_related = ["canvas", "placed_by"]
    raw_id_fields = ["placed_by"]
    search_fields = ["placed_by__username", "color"]
    date_hierarchy = "placed_at"


class UserPixelCooldownAdmin(admin.ModelAdmin):
    list_display = ["user", "canvas", "last_placed", "pixels_placed_last_minute"]
    list_filter = ["canvas"]
    list_select_related = ["user", "canvas"]
    raw_id_fields = ["user"]
    search_fields = ["user__username", "session_key"]


class UserPixelStatsAdmin(admin.ModelAdmin):
    list_display = ["user", "canvas", "total_pixels_placed", "last_pixel_placed"]
    list_filter = ["canvas"]
    list_select_related = ["user", "canvas"]
    raw_id_fields = ["user"]
    search_fields = ["user__username"]


power_up_admin_site.register(PixelCanvas, PixelCanvasAdmin)
power_up_admin_site.register(Pixel, PixelAdmin)
power_up_admin_site.register(PixelHistory, PixelHistoryAdmin)
power_up_admin_site.register(UserPixelCooldown, UserPixelCooldownAdmin)
power_up_admin_site.register(UserPixelStats, UserPixelStatsAdmin)
