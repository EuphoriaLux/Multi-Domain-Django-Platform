from django.urls import path
from . import views

app_name = 'vinsdelux'

urlpatterns = [
    path('', views.home, name='home'),
    
    # Product URLs
    path('coffrets/', views.coffret_list, name='coffret_list'),
    path('coffrets/<slug:slug>/', views.coffret_detail, name='coffret_detail'),
    path('adoption-plans/<slug:slug>/', views.adoption_plan_detail, name='adoption_plan_detail'),
    
    # Producer URLs
    path('producers/', views.producer_list, name='producer_list'),
    path('producers/<slug:slug>/', views.producer_detail, name='producer_detail'),
]
