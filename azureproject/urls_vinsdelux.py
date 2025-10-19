from django.urls import path, include # Add include
from django.contrib import admin # Add admin import
# Remove entreprinder_views import if no longer used directly here
# from entreprinder import views as entreprinder_views
from vibe_coding import views as vibe_views # Keep if vibe_views is still used
from azureproject.urls import health_check_view # Import health check view

urlpatterns = [
    path('healthz/', health_check_view, name='healthz_vinsdelux'), # Add health check endpoint
    path('admin/', admin.site.urls), # Add admin URLs
    # Add Crush.lu URLs for local development
    path('crush/', include('crush_lu.urls', namespace='crush_lu')),
    path('', include('vinsdelux.urls')), # Route root to vinsdelux app's URLs
    path('vibe/', vibe_views.index, name='vinsdelux_vibe'), # Keep other specific routes
    # Add other vinsdelux.com specific routes here
]
