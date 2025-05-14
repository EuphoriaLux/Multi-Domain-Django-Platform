from django.urls import path
from . import views

app_name = 'vinsdelux'

urlpatterns = [
    path('', views.home, name='home'),
    # Define your app's URLs here
    # Example: path('', views.index, name='index'),
]
