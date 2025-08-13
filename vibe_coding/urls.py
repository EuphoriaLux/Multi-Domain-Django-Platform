from django.urls import path
from . import views

app_name = 'vibe_coding'

urlpatterns = [
    path('', views.index, name='index'),
    path('pixel-war/', views.pixel_war, name='pixel_war'),
    path('api/canvas-state/', views.get_canvas_state, name='canvas_state'),
    path('api/canvas-state/<int:canvas_id>/', views.get_canvas_state, name='canvas_state_by_id'),
    path('api/place-pixel/', views.place_pixel, name='place_pixel'),
    path('api/pixel-history/', views.get_pixel_history, name='pixel_history'),
]
