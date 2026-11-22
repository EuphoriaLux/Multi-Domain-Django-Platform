"""URL patterns for language-prefixed Pixel War pages on power-up.lu / powerup.lu."""

from django.urls import path

from . import views

app_name = "pixel_war"

urlpatterns = [
    path("", views.pixel_war, name="canvas"),
]
