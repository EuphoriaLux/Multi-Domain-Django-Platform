"""
URL patterns for Power-Up corporate site app.

These are included by azureproject/urls_power_up.py for the power-up.lu domain.
"""

from django.urls import path

from . import views

app_name = "power_up"

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("platforms/", views.platforms, name="platforms"),
    path("investors/", views.investors, name="investors"),
    path("contact/", views.contact, name="contact"),
]
