from django.urls import path

from . import views

app_name = "crm"

urlpatterns = [
    path("", views.groups_overview, name="groups_overview"),
    path("<uuid:pk>/", views.group_detail, name="group_detail"),
    path("tickets/", views.ticket_list, name="ticket_list"),
    path("tickets/new/", views.ticket_create, name="ticket_create"),
    path(
        "tickets/requester-options/",
        views.ticket_requester_options,
        name="ticket_requester_options",
    ),
    path("tickets/<uuid:pk>/", views.ticket_detail, name="ticket_detail"),
    path("tickets/<uuid:pk>/update/", views.ticket_update, name="ticket_update"),
    path(
        "tickets/<uuid:pk>/comment/",
        views.ticket_comment_add,
        name="ticket_comment_add",
    ),
]
