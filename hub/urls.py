from django.urls import path

from . import views

app_name = "hub"

urlpatterns = [
    path("me", views.MeView.as_view(), name="me"),
    path("me/", views.MeView.as_view()),
    path("requests", views.RequestsView.as_view(), name="requests"),
    path("requests/", views.RequestsView.as_view()),
    path("resources", views.ResourcesView.as_view(), name="resources"),
    path("resources/", views.ResourcesView.as_view()),
    path("timeline", views.TimelineView.as_view(), name="timeline"),
    path("timeline/", views.TimelineView.as_view()),
]
