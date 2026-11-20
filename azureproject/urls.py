"""azureporject URL Configuration"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.http import HttpResponse  # Added for health check view
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


urlpatterns = [
    path("healthz/", health_check_view, name="healthz"),  # Added health check endpoint
    path("i18n/", include("django.conf.urls.i18n")),
    path("accounts/", include("allauth.urls")),  # Ensure this is outside i18n_patterns
    path(
        "login_complete/", entreprinder_views.login_complete, name="login_complete"
    ),  # Add this line
    path("crush/", include("crush_lu.urls")),  # Ensure crush URLs are included
    # API endpoints outside i18n_patterns — JS uses hardcoded /api/ paths
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/protected/", entreprinder_views.protected_api, name="protected_api"),
    # Admin outside i18n_patterns — accessible at /admin/ without language prefix
    path("admin/", admin.site.urls),
]

urlpatterns += i18n_patterns(
    path("", entreprinder_views.home, name="home"),
    path("", include("entreprinder.urls", namespace="entreprinder")),
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
