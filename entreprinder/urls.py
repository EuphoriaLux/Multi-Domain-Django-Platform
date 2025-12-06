from django.urls import path, include
from . import views
from . import views_matching

app_name = 'entreprinder'

urlpatterns = [
    # Core entreprinder URLs
    path('', views.home, name='home'),
    path('about/', views.about_page, name='about'),
    path('contact/', views.contact_page, name='contact'),
    path('profile/', views.profile, name='profile'),
    path('entrepreneurs/', views.entrepreneur_list, name='entrepreneur_list'),

    # Matching URLs (merged from matching app)
    path('matching/swipe/', views_matching.swipe, name='swipe'),
    path('matching/swipe/action/', views_matching.swipe_action, name='swipe_action'),
    path('matching/no-more-profiles/', views_matching.no_more_profiles, name='no_more_profiles'),
    path('matching/matches/', views_matching.matches, name='matches'),

    # FinOps Hub URLs (merged from finops_hub app)
    path('finops/', include('entreprinder.finops.urls')),

    # Vibe Coding URLs (merged from vibe_coding app)
    path('vibe-coding/', include('entreprinder.vibe.urls')),
]