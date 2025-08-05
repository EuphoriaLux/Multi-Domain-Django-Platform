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
    
    # Test runner URL (for development/testing)
    path('test-journey/', views.journey_test_runner, name='journey_test_runner'),
    
    # Journey step landing pages
    path('journey/plot-selection/', views.journey_step_plot_selection, name='journey_plot_selection'),
    path('journey/personalize-wine/', views.journey_step_personalize_wine, name='journey_personalize_wine'),
    path('journey/follow-production/', views.journey_step_follow_production, name='journey_follow_production'),
    path('journey/receive-taste/', views.journey_step_receive_taste, name='journey_receive_taste'),
    path('journey/create-legacy/', views.journey_step_create_legacy, name='journey_create_legacy'),
]
