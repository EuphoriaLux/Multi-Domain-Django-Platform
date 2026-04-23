"""
URL configuration for api.crush.lu — dedicated API subdomain.

This urlconf is pure JSON API: no i18n, no HTML templates, no admin, no allauth
pages. It exposes JWT auth endpoints and the /hub/* surface consumed by the
Next.js SPA hosted at hub.crush.lu.
"""
from django.http import HttpResponse
from django.urls import include, path
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
    TokenVerifyView,
)


def health_check(request):
    return HttpResponse("OK")


urlpatterns = [
    path("healthz/", health_check, name="health_check_api"),
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("api/token/logout/", TokenBlacklistView.as_view(), name="token_logout"),
    path("hub/", include("hub.urls")),
]
