"""Language-neutral API URL patterns for Pixel War on power-up.lu / powerup.lu."""

from django.urls import path

from . import views

app_name = "pixel_war_api"

urlpatterns = [
    path("canvas-state/", views.get_canvas_state, name="canvas_state"),
    path(
        "canvas-state/<int:canvas_id>/",
        views.get_canvas_state,
        name="canvas_state_by_id",
    ),
    path("place-pixel/", views.place_pixel, name="place_pixel"),
    path("pixel-history/", views.get_pixel_history, name="pixel_history"),
]
