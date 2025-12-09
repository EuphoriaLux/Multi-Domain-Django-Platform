# crush_delegation/urls.py
"""
URL configuration for the Crush Delegation app.

This app handles delegation.crush.lu subdomain.
"""
from django.urls import path
from . import views

app_name = 'crush_delegation'

urlpatterns = [
    path('', views.home, name='home'),
]