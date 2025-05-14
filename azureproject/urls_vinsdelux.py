from django.urls import path, include # Add include
# Remove entreprinder_views import if no longer used directly here
# from entreprinder import views as entreprinder_views
from vibe_coding import views as vibe_views # Keep if vibe_views is still used

urlpatterns = [
    path('', include('vinsdelux.urls')), # Route root to vinsdelux app's URLs
    path('vibe/', vibe_views.index, name='vinsdelux_vibe'), # Keep other specific routes
    # Add other vinsdelux.com specific routes here
]
