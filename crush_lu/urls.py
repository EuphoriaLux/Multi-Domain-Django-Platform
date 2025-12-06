from django.urls import path
from . import views
from . import views_profile
from . import views_media
from . import api_views
from . import views_journey
from . import api_journey
from . import api_push
from . import views_advent

app_name = 'crush_lu'

urlpatterns = [
    # Secure media serving
    path('media/profile/<int:user_id>/<str:photo_field>/', views_media.serve_profile_photo, name='serve_profile_photo'),

    # Landing and public pages
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('how-it-works/', views.how_it_works, name='how_it_works'),

    # PWA Pages
    path('offline/', views.offline_view, name='offline'),
    path('sw-workbox.js', views.service_worker_view, name='service_worker'),

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

    # Special user experience
    path('special-welcome/', views.special_welcome, name='special_welcome'),

    # Private Invitation System (PUBLIC ACCESS)
    path('invite/<uuid:code>/', views.invitation_landing, name='invitation_landing'),
    path('invite/<uuid:code>/accept/', views.invitation_accept, name='invitation_accept'),

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

    # Coach invitation management
    path('coach/event/<int:event_id>/invitations/', views.coach_manage_invitations, name='coach_manage_invitations'),

    # NOTE: Step 1 screening call URLs have been REMOVED - screening is now part of review process
    # Old URLs (deprecated):
    # - /coach/screening/ (replaced by /coach/dashboard/ and /coach/review/)
    # - /coach/screening/<id>/complete/ (replaced by /coach/review/<id>/call-complete/)

    # Coach journey management
    path('coach/journeys/', views.coach_journey_dashboard, name='coach_journey_dashboard'),
    path('coach/journeys/<int:journey_id>/edit/', views.coach_edit_journey, name='coach_edit_journey'),
    path('coach/journeys/challenge/<int:challenge_id>/edit/', views.coach_edit_challenge, name='coach_edit_challenge'),
    path('coach/journeys/progress/<int:progress_id>/', views.coach_view_user_progress, name='coach_view_user_progress'),

    # Post-event connections
    path('events/<int:event_id>/attendees/', views.event_attendees, name='event_attendees'),
    path('events/<int:event_id>/connect/<int:user_id>/', views.request_connection, name='request_connection'),
    path('connections/', views.my_connections, name='my_connections'),
    path('connections/<int:connection_id>/', views.connection_detail, name='connection_detail'),
    path('connections/<int:connection_id>/<str:action>/', views.respond_connection, name='respond_connection'),

    # ============================================================================
    # INTERACTIVE JOURNEY SYSTEM - "The Wonderland of You"
    # ============================================================================

    # Journey Views
    path('journey/', views_journey.journey_map, name='journey_map'),  # Backwards compatible - redirects to selector
    path('journey/select/', views_journey.journey_selector, name='journey_selector'),
    path('journey/wonderland/', views_journey.journey_map_wonderland, name='journey_map_wonderland'),
    path('journey/chapter/<int:chapter_number>/', views_journey.chapter_view, name='chapter_view'),
    path('journey/chapter/<int:chapter_number>/challenge/<int:challenge_id>/', views_journey.challenge_view, name='challenge_view'),
    path('journey/reward/<int:reward_id>/', views_journey.reward_view, name='reward_view'),
    path('journey/certificate/', views_journey.certificate_view, name='certificate_view'),

    # Journey API Endpoints
    path('api/journey/submit-challenge/', api_journey.submit_challenge, name='api_submit_challenge'),
    path('api/journey/unlock-hint/', api_journey.unlock_hint, name='api_unlock_hint'),
    path('api/journey/progress/', api_journey.get_progress, name='api_get_progress'),
    path('api/journey/save-state/', api_journey.save_state, name='api_save_state'),
    path('api/journey/final-response/', api_journey.record_final_response, name='api_record_final_response'),
    path('api/journey/unlock-puzzle-piece/', api_journey.unlock_puzzle_piece, name='api_unlock_puzzle_piece'),
    path('api/journey/reward-progress/<int:reward_id>/', api_journey.get_reward_progress, name='api_get_reward_progress'),

    # ============================================================================
    # PUSH NOTIFICATIONS API
    # ============================================================================
    path('api/push/vapid-public-key/', api_push.get_vapid_public_key, name='api_vapid_public_key'),
    path('api/push/subscribe/', api_push.subscribe_push, name='api_subscribe_push'),
    path('api/push/unsubscribe/', api_push.unsubscribe_push, name='api_unsubscribe_push'),
    path('api/push/subscriptions/', api_push.list_subscriptions, name='api_list_subscriptions'),
    path('api/push/preferences/', api_push.update_subscription_preferences, name='api_update_push_preferences'),
    path('api/push/test/', api_push.send_test_push, name='api_send_test_push'),

    # ============================================================================
    # ADVENT CALENDAR SYSTEM
    # ============================================================================

    # Advent Calendar Views
    path('advent/', views_advent.advent_calendar_view, name='advent_calendar'),
    path('advent/door/<int:door_number>/', views_advent.advent_door_view, name='advent_door'),
    path('advent/qr/<uuid:token>/', views_advent.scan_qr_code, name='advent_scan_qr'),
    path('advent/qr-scanner/', views_advent.advent_qr_scanner, name='advent_qr_scanner'),

    # Advent Calendar API Endpoints
    path('api/advent/status/', views_advent.get_advent_status, name='api_advent_status'),
    path('api/advent/open-door/', views_advent.open_door_api, name='api_advent_open_door'),
]
