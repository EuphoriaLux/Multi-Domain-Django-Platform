# azureproject/urls_travelinstyle.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

# Make sure you have created a 'travelinstyle' app (e.g., using `python manage.py startapp travelinstyle`)
from travelinstyle import views as travelinstyle_views

urlpatterns = [
    path('admin/', admin.site.urls),
    # Travel in Style app for travelinstyle.lu
    path('', travelinstyle_views.home, name='travelinstyle_home'),
    path('accounts/', include('allauth.urls')),
    # Add other travelinstyle-specific routes here as needed.
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
