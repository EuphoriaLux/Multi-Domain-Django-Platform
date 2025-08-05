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
    
    # Interactive Journey Form
    path('journey/interactive/', views.journey_interactive_form, name='journey_interactive_form'),
    path('journey/test/', views.journey_test, name='journey_test'),
    
    # API Endpoints
    path('api/adoption-plans/', views.api_adoption_plans, name='api_adoption_plans'),
    
    # Plot Selector (standalone - main plot selection page)
    path('journey/plot-selection/', views.plot_selector, name='plot_selector'),
    
    # Journey step landing pages
    # Note: journey_plot_selection removed as plot_selector now handles this
    path('journey/personalize-wine/', views.journey_step_personalize_wine, name='journey_personalize_wine'),
    path('journey/follow-production/', views.journey_step_follow_production, name='journey_follow_production'),
    path('journey/receive-taste/', views.journey_step_receive_taste, name='journey_receive_taste'),
    path('journey/create-legacy/', views.journey_step_create_legacy, name='journey_create_legacy'),
]
