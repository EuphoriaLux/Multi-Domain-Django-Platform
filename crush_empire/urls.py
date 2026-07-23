from django.urls import path

from . import views_game

app_name = "crush_empire"

urlpatterns = [
    path("", views_game.teaser, name="teaser"),
    path("play/", views_game.play, name="play"),
]
