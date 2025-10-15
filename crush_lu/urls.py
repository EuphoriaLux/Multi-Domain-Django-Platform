from django.urls import path
from . import views
from . import views_profile
from . import views_media
from . import api_views

app_name = 'crush_lu'

urlpatterns = [
    # Secure media serving
    path('media/profile/<int:user_id>/<str:photo_field>/', views_media.serve_profile_photo, name='serve_profile_photo'),

    # Landing and public pages
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('how-it-works/', views.how_it_works, name='how_it_works'),

    # Legal pages
    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),
    path('terms-of-service/', views.terms_of_service, name='terms_of_service'),
    path('data-deletion/', views.data_deletion_request, name='data_deletion'),

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
    path('profile/edit-simple/', views.edit_profile_simple, name='edit_profile_simple'),

    # Events
    path('events/', views.event_list, name='event_list'),
    path('events/<int:event_id>/', views.event_detail, name='event_detail'),
    path('events/<int:event_id>/register/', views.event_register, name='event_register'),
    path('events/<int:event_id>/cancel/', views.event_cancel, name='event_cancel'),

    # Event Activity Voting (Phase 1)
    path('events/<int:event_id>/voting/lobby/', views.event_voting_lobby, name='event_voting_lobby'),
    path('events/<int:event_id>/voting/', views.event_activity_vote, name='event_activity_vote'),
    path('events/<int:event_id>/voting/results/', views.event_voting_results, name='event_voting_results'),

    # Presentations (Phase 2)
    path('events/<int:event_id>/presentations/', views.event_presentations, name='event_presentations'),
    path('events/<int:event_id>/presentations/rate/<int:presenter_id>/', views.submit_presentation_rating, name='submit_presentation_rating'),
    path('events/<int:event_id>/presentations/my-scores/', views.my_presentation_scores, name='my_presentation_scores'),
    path('api/events/<int:event_id>/presentations/current/', views.get_current_presenter_api, name='get_current_presenter_api'),

    # Coach Presentation Controls
    path('coach/events/<int:event_id>/presentations/control/', views.coach_presentation_control, name='coach_presentation_control'),
    path('coach/events/<int:event_id>/presentations/advance/', views.coach_advance_presentation, name='coach_advance_presentation'),

    # Voting Demo/Guided Tour
    path('voting-demo/', views.voting_demo, name='voting_demo'),

    # Event Activity Voting API
    path('api/events/<int:event_id>/voting/status/', api_views.voting_status_api, name='voting_status_api'),
    path('api/events/<int:event_id>/voting/submit/', api_views.submit_vote_api, name='submit_vote_api'),
    path('api/events/<int:event_id>/voting/results/', api_views.voting_results_api, name='voting_results_api'),

    # Coach dashboard
    path('coach/dashboard/', views.coach_dashboard, name='coach_dashboard'),
    path('coach/review/<int:submission_id>/', views.coach_review_profile, name='coach_review_profile'),
    path('coach/review/<int:submission_id>/call-complete/', views.coach_mark_review_call_complete, name='coach_mark_review_call_complete'),
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
