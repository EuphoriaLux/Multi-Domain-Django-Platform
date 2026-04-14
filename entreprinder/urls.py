from django.urls import path
from . import views

app_name = 'entreprinder'

urlpatterns = [
    # Core entreprinder URLs
    path('', views.home, name='home'),
    path('about/', views.about_page, name='about'),
    path('contact/', views.contact_page, name='contact'),
    path('profile/', views.profile, name='profile'),
    path('entrepreneurs/', views.entrepreneur_list, name='entrepreneur_list'),

    # NOTE: FinOps Hub and Vibe Coding URLs are included at the project level
    # (in urls_powerup.py, urls.py) to avoid namespace nesting issues.
    # Do NOT add them here - they need to be top-level namespaces.
]