# azureproject/urls_powerup.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from entreprinder import views as entreprinder_views

urlpatterns = [
    # Include the i18n URLs so that {% url 'set_language' %} works
    path('i18n/', include('django.conf.urls.i18n')),
    
    # You can also map set_language directly if you prefer:
    # path('set_language/', include('django.conf.urls.i18n')),
]

urlpatterns += [
    path('admin/', admin.site.urls),
    path('', entreprinder_views.home, name='home'),
    path('', include('entreprinder.urls')),
    path('accounts/', include('allauth.urls')),
    path('login_complete/', entreprinder_views.login_complete, name='login_complete'),
    path('matching/', include('matching.urls')),
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/protected/', entreprinder_views.protected_api, name='protected_api'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
