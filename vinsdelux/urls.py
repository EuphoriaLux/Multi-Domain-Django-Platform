# vinsdelux/urls.py
"""VinsDelux URL configuration — concept-explainer site."""

from django.urls import path
from . import views

app_name = 'vinsdelux'

urlpatterns = [
    path('', views.home, name='home'),
]
