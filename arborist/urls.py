"""
URL patterns for Arborist informational site app.

These are included by azureproject/urls_arborist.py for the arborist.lu domain.
"""

from django.urls import path

from . import views

app_name = "arborist"

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("services/", views.services, name="services"),
    path("contact/", views.contact, name="contact"),
]
