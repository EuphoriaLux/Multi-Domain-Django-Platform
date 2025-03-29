from django.urls import path
from . import views

app_name = 'vibe_coding'

urlpatterns = [
    path('', views.index, name='index'),
]
