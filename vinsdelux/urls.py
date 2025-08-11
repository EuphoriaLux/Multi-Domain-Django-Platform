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
    
    # Legacy API Endpoints (backward compatibility)
    path('api/adoption-plans/', views.api_adoption_plans, name='api_adoption_plans'),
    
    # Plot Selection URLs
    path('journey/plot-selection/', views.plot_selector, name='plot_selector'),
    path('journey/enhanced-plot-selection/', views.EnhancedPlotSelectionView.as_view(), name='enhanced_plot_selector'),
    
    # Plot API Endpoints
    path('api/plots/', views.PlotListAPIView.as_view(), name='api_plot_list'),
    path('api/plots/<int:id>/', views.PlotDetailAPIView.as_view(), name='api_plot_detail'),
    path('api/plots/availability/', views.PlotAvailabilityAPIView.as_view(), name='api_plot_availability'),
    path('api/plots/reserve/', views.PlotReservationAPIView.as_view(), name='api_plot_reserve'),
    path('api/plots/selection/', views.PlotSelectionAPIView.as_view(), name='api_plot_selection'),
    
    # Journey step landing pages
    path('journey/personalize-wine/', views.journey_step_personalize_wine, name='journey_personalize_wine'),
    path('journey/follow-production/', views.journey_step_follow_production, name='journey_follow_production'),
    path('journey/receive-taste/', views.journey_step_receive_taste, name='journey_receive_taste'),
    path('journey/create-legacy/', views.journey_step_create_legacy, name='journey_create_legacy'),
]
