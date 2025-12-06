from django.urls import path
from . import views

app_name = 'matching'

urlpatterns = [
    path('swipe/', views.swipe, name='swipe'),
    path('swipe/action/', views.swipe_action, name='swipe_action'),
    path('no-more-profiles/', views.no_more_profiles, name='no_more_profiles'),
    path('matches/', views.matches, name='matches'),
]