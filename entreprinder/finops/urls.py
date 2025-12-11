# entreprinder/finops/urls.py
"""
URL configuration for FinOps Hub (merged into entreprinder)

These URL patterns are included from entreprinder/urls.py under the 'finops/' prefix.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views, api_views, views_webhook

# App namespace for template URL tags ({% url 'finops_hub:dashboard' %})
app_name = 'finops_hub'

# API router
router = DefaultRouter()
router.register(r'exports', api_views.CostExportViewSet, basename='export')
router.register(r'records', api_views.CostRecordViewSet, basename='record')
router.register(r'aggregations', api_views.CostAggregationViewSet, basename='aggregation')

urlpatterns = [
    # Dashboard views
    path('', views.dashboard, name='dashboard'),
    path('subscriptions/', views.subscription_view, name='subscriptions'),
    path('services/', views.service_breakdown, name='services'),
    path('resources/', views.resource_explorer, name='resources'),
    path('import/', views.trigger_import, name='import'),
    path('import/<int:export_id>/update-subscription/', views.update_subscription_id, name='update_subscription_id'),
    path('faq/', views.faq, name='faq'),

    # API endpoints
    path('api/', include(router.urls)),
    path('api/costs/summary/', api_views.cost_summary, name='api_cost_summary'),
    path('api/costs/by-subscription/', api_views.costs_by_subscription, name='api_costs_by_subscription'),
    path('api/costs/by-service/', api_views.costs_by_service, name='api_costs_by_service'),
    path('api/costs/by-resource-group/', api_views.costs_by_resource_group, name='api_costs_by_resource_group'),
    path('api/costs/trend/', api_views.cost_trend, name='api_cost_trend'),
    path('api/costs/export-csv/', api_views.export_costs_csv, name='api_export_csv'),
    path('api/exports/status/', api_views.export_status, name='api_export_status'),

    # Webhook endpoints for automated sync
    path('api/sync/', views_webhook.trigger_cost_sync, name='webhook_sync'),
    path('api/sync/status/', views_webhook.sync_status, name='webhook_status'),
]
