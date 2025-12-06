# entreprinder/vibe/urls.py
"""
URL configuration for Vibe Coding (merged into entreprinder)

These URL patterns are included from entreprinder/urls.py under the 'vibe-coding/' prefix.
"""

from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='vibe_index'),
    path('pixel-war/', views.pixel_war, name='vibe_pixel_war'),
    path('pixel-war/optimized/', views.pixel_war_optimized, name='vibe_pixel_war_optimized'),
    path('pixel-war/pixi/', views.pixel_war_pixi, name='vibe_pixel_war_pixi'),
    path('pixel-war/ts/', views.pixel_war_ts, name='vibe_pixel_war_ts'),
    path('pixel-war/react/', views.pixel_war_react, name='vibe_pixel_war_react'),
    path('pixel-war/demo/', views.pixel_war_demo, name='vibe_pixel_war_demo'),
    path('road-trip-music-game/', views.road_trip_music_game, name='vibe_road_trip_music_game'),
    path('api/canvas-state/', views.get_canvas_state, name='vibe_canvas_state'),
    path('api/canvas-state/<int:canvas_id>/', views.get_canvas_state, name='vibe_canvas_state_by_id'),
    path('api/place-pixel/', views.place_pixel, name='vibe_place_pixel'),
    path('api/pixel-history/', views.get_pixel_history, name='vibe_pixel_history'),
]
