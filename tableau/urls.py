# tableau/urls.py
"""
URL patterns for Tableau AI Art e-commerce site.

Simple URL structure for the landing site.
"""
from django.urls import path

from . import views

app_name = "tableau"

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
]
