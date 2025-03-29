
"""azureporject URL Configuration"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from entreprinder import views as entreprinder_views

urlpatterns = [
    path('i18n/', include('django.conf.urls.i18n')),
    path('accounts/', include('allauth.urls')),  # Ensure this is outside i18n_patterns
    path('login_complete/', entreprinder_views.login_complete, name='login_complete'),  # Add this line
]

urlpatterns += i18n_patterns(
    path('admin/', admin.site.urls),
    path('', entreprinder_views.home, name='home'),
    path('', include('entreprinder.urls')),
    path('matching/', include('matching.urls')),
    path('vibe-coding/', include('vibe_coding.urls')),
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/protected/', entreprinder_views.protected_api, name='protected_api'),
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
