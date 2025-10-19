# azureproject/urls_crush.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from azureproject.urls import health_check_view
from crush_lu.admin import crush_admin_site
from crush_lu import admin_views

urlpatterns = [
    path('healthz/', health_check_view, name='healthz_crush'),
    path('i18n/', include('django.conf.urls.i18n')),
    path('accounts/', include('allauth.urls')),
]

urlpatterns += [
    # Dedicated Crush.lu Admin Panel
    path('crush-admin/', crush_admin_site.urls),
    path('crush-admin/dashboard/', admin_views.crush_admin_dashboard, name='crush_admin_dashboard'),

    # Standard Django Admin (all platforms)
    path('admin/', admin.site.urls),

    # Crush.lu app URLs
    path('', include('crush_lu.urls', namespace='crush_lu')),

    # API endpoints
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
