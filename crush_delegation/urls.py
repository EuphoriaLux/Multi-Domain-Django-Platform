# crush_delegation/urls.py
"""
URL configuration for the Crush Delegation app.

This app handles delegations.lu domain.
"""
from django.urls import path
from . import views

app_name = 'crush_delegation'

urlpatterns = [
    # Public
    path('', views.home, name='home'),

    # Authenticated user pages
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/', views.profile_view, name='profile'),

    # Status pages
    path('pending-approval/', views.pending_approval, name='pending_approval'),
    path('no-company/', views.no_company, name='no_company'),
    path('access-denied/', views.access_denied, name='access_denied'),
]
