from django.urls import path

from . import views

app_name = "crush_event_lobby"

urlpatterns = [
    path("consent/", views.consent, name="consent"),
    path("people-met/", views.people_met, name="people_met"),
    path(
        "people-met/<uuid:handle>/",
        views.people_met_member,
        name="people_met_profile",
    ),
    path(
        "people-met/<uuid:handle>/photo/<int:slot>/",
        views.people_met_photo,
        name="people_met_photo",
    ),
    path("<int:event_id>/", views.lobby, name="lobby"),
    path("<int:event_id>/recap/", views.recap, name="recap"),
    path("<int:event_id>/state/", views.state_api, name="state"),
    path(
        "<int:event_id>/participants/",
        views.participants_api,
        name="participants",
    ),
    path(
        "<int:event_id>/recap/participants/",
        views.recap_participants_api,
        name="recap_participants",
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
    path(
        "<int:event_id>/recap/<uuid:handle>/confirm/",
        views.confirm_meeting,
        name="confirm_meeting",
    ),
    path(
        "<int:event_id>/recap/<uuid:handle>/",
        views.meeting_confirmation,
        name="meeting_confirmation",
    ),
]
