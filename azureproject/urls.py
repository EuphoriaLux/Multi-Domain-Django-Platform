
"""azureporject URL Configuration"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.http import HttpResponse # Added for health check view
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from entreprinder import views as entreprinder_views

# Simple health check view
def health_check_view(request):
    return HttpResponse("OK", status=200)

from vinsdelux import views as vinsdelux_views

from vibe_coding import views as vibe_coding_views

urlpatterns = [
    path('healthz/', health_check_view, name='healthz'), # Added health check endpoint
    path('i18n/', include('django.conf.urls.i18n')),
    path('accounts/', include('allauth.urls')),  # Ensure this is outside i18n_patterns
    path('login_complete/', entreprinder_views.login_complete, name='login_complete'),  # Add this line
    # Add journey URLs and API outside i18n_patterns for direct access
    path('journey/plot-selection/', vinsdelux_views.plot_selector, name='plot_selector_direct'),
    path('journey/enhanced-plot-selection/', vinsdelux_views.enhanced_plot_selector, name='enhanced_plot_selector_direct'),
    path('vinsdelux/api/adoption-plans/', vinsdelux_views.api_adoption_plans, name='api_adoption_plans_direct'),
    # Add vibe_coding API URLs outside i18n_patterns
    path('vibe-coding/api/canvas-state/', vibe_coding_views.get_canvas_state, name='canvas_state_api'),
    path('vibe-coding/api/canvas-state/<int:canvas_id>/', vibe_coding_views.get_canvas_state, name='canvas_state_by_id_api'),
    path('vibe-coding/api/place-pixel/', vibe_coding_views.place_pixel, name='place_pixel_api'),
    path('vibe-coding/api/pixel-history/', vibe_coding_views.get_pixel_history, name='pixel_history_api'),
    # Add direct access to road trip music game
    path('road-trip-music/', vibe_coding_views.road_trip_music_game, name='road_trip_music_game_direct'),
]

urlpatterns += i18n_patterns(
    path('admin/', admin.site.urls),
    path('', entreprinder_views.home, name='home'),
    path('', include('entreprinder.urls')), # Assuming entreprinder.urls defines app_name='entreprinder'
    path('vinsdelux/', include('vinsdelux.urls', namespace='vinsdelux')), # Switched from Entreprinder to Vinsdelux
    path('matching/', include('matching.urls', namespace='matching')), # Explicit namespace
    path('vibe-coding/', include('vibe_coding.urls', namespace='vibe_coding')), # Explicit namespace
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/protected/', entreprinder_views.protected_api, name='protected_api'),
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
