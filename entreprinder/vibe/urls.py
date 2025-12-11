# entreprinder/vibe/urls.py
"""
URL configuration for Vibe Coding (merged into entreprinder)

These URL patterns are included from entreprinder/urls.py under the 'vibe-coding/' prefix.
"""

from django.urls import path
from . import views

# App namespace for template URL tags ({% url 'vibe_coding:pixel_war' %})
app_name = 'vibe_coding'

urlpatterns = [
    path('', views.index, name='index'),
    path('pixel-war/', views.pixel_war, name='pixel_war'),
    path('pixel-war/optimized/', views.pixel_war_optimized, name='pixel_war_optimized'),
    path('pixel-war/pixi/', views.pixel_war_pixi, name='pixel_war_pixi'),
    path('pixel-war/ts/', views.pixel_war_ts, name='pixel_war_ts'),
    path('pixel-war/react/', views.pixel_war_react, name='pixel_war_react'),
    path('pixel-war/demo/', views.pixel_war_demo, name='pixel_war_demo'),
    path('road-trip-music-game/', views.road_trip_music_game, name='road_trip_music_game'),
    path('api/canvas-state/', views.get_canvas_state, name='canvas_state'),
    path('api/canvas-state/<int:canvas_id>/', views.get_canvas_state, name='canvas_state_by_id'),
    path('api/place-pixel/', views.place_pixel, name='place_pixel'),
    path('api/pixel-history/', views.get_pixel_history, name='pixel_history'),
]
