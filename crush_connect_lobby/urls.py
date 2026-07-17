from django.urls import path
from crush_connect_lobby import views

app_name = "crush_connect_lobby"

urlpatterns = [
    path("lobby/<int:event_id>/", views.event_lobby_home, name="lobby_home"),
    path("lobby/<int:event_id>/state/", views.api_lobby_state, name="api_lobby_state"),
    path("lobby/<int:event_id>/participants/", views.api_list_participants, name="api_list_participants"),
    path("lobby/<int:event_id>/signal/", views.api_send_signal, name="api_send_signal"),
    path("lobby/<int:event_id>/confirm/", views.api_confirm_meeting, name="api_confirm_meeting"),
    path("lobby/<int:event_id>/photo/<str:handle>/", views.serve_participant_photo, name="serve_participant_photo"),
    path("lobby/profile/<int:member_id>/", views.view_member_profile, name="view_member_profile"),
    path("people-ive-met/", views.people_ive_met_view, name="people_ive_met"),
    path("people-ive-met/remove/<int:encounter_id>/", views.api_request_removal, name="api_request_removal"),
]
