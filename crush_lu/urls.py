from django.urls import path
from . import views
from . import views_profile

app_name = 'crush_lu'

urlpatterns = [
    # Landing and public pages
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('how-it-works/', views.how_it_works, name='how_it_works'),

    # Authentication
    path('login/', views.crush_login, name='login'),
    path('logout/', views.crush_logout, name='logout'),

    # Onboarding flow
    path('signup/', views.signup, name='signup'),
    path('create-profile/', views.create_profile, name='create_profile'),
    path('profile-submitted/', views.profile_submitted, name='profile_submitted'),

    # Profile step-by-step saving (AJAX endpoints)
    path('api/profile/save-step1/', views_profile.save_profile_step1, name='save_profile_step1'),
    path('api/profile/save-step2/', views_profile.save_profile_step2, name='save_profile_step2'),
    path('api/profile/save-step3/', views_profile.save_profile_step3, name='save_profile_step3'),
    path('api/profile/complete/', views_profile.complete_profile_submission, name='complete_profile_submission'),
    path('api/profile/progress/', views_profile.get_profile_progress, name='get_profile_progress'),

    # User dashboard
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/edit/', views.edit_profile, name='edit_profile'),

    # Events
    path('events/', views.event_list, name='event_list'),
    path('events/<int:event_id>/', views.event_detail, name='event_detail'),
    path('events/<int:event_id>/register/', views.event_register, name='event_register'),
    path('events/<int:event_id>/cancel/', views.event_cancel, name='event_cancel'),

    # Coach dashboard
    path('coach/dashboard/', views.coach_dashboard, name='coach_dashboard'),
    path('coach/review/<int:submission_id>/', views.coach_review_profile, name='coach_review_profile'),
    path('coach/sessions/', views.coach_sessions, name='coach_sessions'),

    # Coach screening calls
    path('coach/screening/', views_profile.coach_screening_dashboard, name='coach_screening_dashboard'),
    path('coach/screening/<int:profile_id>/complete/', views_profile.coach_mark_screening_complete, name='coach_mark_screening_complete'),

    # Post-event connections
    path('events/<int:event_id>/attendees/', views.event_attendees, name='event_attendees'),
    path('events/<int:event_id>/connect/<int:user_id>/', views.request_connection, name='request_connection'),
    path('connections/', views.my_connections, name='my_connections'),
    path('connections/<int:connection_id>/', views.connection_detail, name='connection_detail'),
    path('connections/<int:connection_id>/<str:action>/', views.respond_connection, name='respond_connection'),
]
