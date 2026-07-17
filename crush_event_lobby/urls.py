from django.urls import path

from . import views

app_name = "crush_event_lobby"

urlpatterns = [
    path("consent/", views.consent, name="consent"),
    path("<int:event_id>/", views.lobby, name="lobby"),
    path("<int:event_id>/state/", views.state_api, name="state"),
    path(
        "<int:event_id>/participants/",
        views.participants_api,
        name="participants",
    ),
    path(
        "<int:event_id>/participants/<uuid:handle>/photo/",
        views.participant_photo,
        name="participant_photo",
    ),
    path(
        "<int:event_id>/signals/<uuid:handle>/confirm/",
        views.confirm_signal,
        name="confirm_signal",
    ),
    path(
        "<int:event_id>/signals/<uuid:handle>/",
        views.signal,
        name="signal",
    ),
]

