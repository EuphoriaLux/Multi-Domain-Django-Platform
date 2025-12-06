# entreprinder/vibe/admin.py
"""
Vibe Coding Admin Configuration
"""

from django.contrib import admin
from .models import PixelCanvas, Pixel, PixelHistory, UserPixelCooldown, UserPixelStats


@admin.register(PixelCanvas)
class PixelCanvasAdmin(admin.ModelAdmin):
    list_display = ['name', 'width', 'height', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name']


@admin.register(Pixel)
class PixelAdmin(admin.ModelAdmin):
    list_display = ['canvas', 'x', 'y', 'color', 'placed_by', 'placed_at']
    list_filter = ['canvas', 'placed_at']
    search_fields = ['placed_by__username']


@admin.register(PixelHistory)
class PixelHistoryAdmin(admin.ModelAdmin):
    list_display = ['canvas', 'x', 'y', 'color', 'placed_by', 'placed_at']
    list_filter = ['canvas', 'placed_at']
    search_fields = ['placed_by__username']
    date_hierarchy = 'placed_at'


@admin.register(UserPixelCooldown)
class UserPixelCooldownAdmin(admin.ModelAdmin):
    list_display = ['user', 'canvas', 'last_placed', 'pixels_placed_last_minute']
    list_filter = ['canvas']
    search_fields = ['user__username', 'session_key']


@admin.register(UserPixelStats)
class UserPixelStatsAdmin(admin.ModelAdmin):
    list_display = ['user', 'canvas', 'total_pixels_placed', 'last_pixel_placed']
    list_filter = ['canvas']
    search_fields = ['user__username']
