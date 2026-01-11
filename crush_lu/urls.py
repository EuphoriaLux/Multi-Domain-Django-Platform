from django.urls import path
from django.views.decorators.cache import never_cache
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import HttpResponse
from allauth.account.views import LoginView, LogoutView
from allauth.account.forms import LoginForm
from . import views
from .forms import CrushSignupForm
from .throttling import LoginRateThrottle
import logging

logger = logging.getLogger(__name__)


# Unified Auth View - combines login and signup in tabbed interface
class UnifiedAuthView(LoginView):
    """
    Unified authentication view with login/signup tabs.
    Extends LoginView to handle login form processing.
    """
    template_name = 'crush_lu/auth.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add signup form for the signup tab
        context['signup_form'] = CrushSignupForm()
        context['login_form'] = context.get('form')  # Allauth's login form
        context['mode'] = 'login'
        return context

    def dispatch(self, request, *args, **kwargs):
        # Rate limiting for POST requests (login attempts)
        if request.method == 'POST':
            throttle = LoginRateThrottle()
            if not throttle.allow_request(request, self):
                wait = throttle.wait()
                logger.warning(f"[RATE-LIMIT] Login rate limit exceeded for IP: {throttle.get_ident(request)}")
                return HttpResponse(
                    f'Too many login attempts. Please try again in {int(wait)} seconds.',
                    status=429,
                    content_type='text/plain',
                    headers={'Retry-After': str(int(wait))}
                )

        # Diagnostic logging for 403 debugging
        if request.method == 'POST':
            # Check SOCIALACCOUNT_ONLY setting - this could be the cause of 403!
            from allauth import app_settings as allauth_app_settings
            from django.conf import settings
            logger.warning(
                f"[LOGIN-DEBUG] POST to /login/ - "
                f"SOCIALACCOUNT_ONLY={allauth_app_settings.SOCIALACCOUNT_ONLY}, "
                f"settings.SOCIALACCOUNT_ONLY={getattr(settings, 'SOCIALACCOUNT_ONLY', 'NOT_SET')}, "
                f"has_csrf_cookie={'csrftoken' in request.COOKIES}, "
                f"csrf_token_in_post={'csrfmiddlewaretoken' in request.POST}, "
                f"origin={request.META.get('HTTP_ORIGIN', 'None')}, "
                f"referer={request.META.get('HTTP_REFERER', 'None')[:80] if request.META.get('HTTP_REFERER') else 'None'}, "
                f"host={request.get_host()}, "
                f"content_type={request.content_type}"
            )

        # Call parent's dispatch - wrap to catch any exceptions for logging
        try:
            response = super().dispatch(request, *args, **kwargs)
        except Exception as e:
            logger.error(f"[LOGIN-DEBUG] Exception in dispatch: {type(e).__name__}: {e}")
            raise
        # Log response status for debugging
        if request.method == 'POST':
            # Note: Don't access response.content on TemplateResponse before it's rendered
            logger.warning(
                f"[LOGIN-DEBUG] Response status={response.status_code}, "
                f"response_type={type(response).__name__}"
            )
        # Aggressive no-cache headers - critical for Android PWA
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response
from . import views_profile
from . import views_media
from . import views_oauth_popup
from . import views_phone_verification
from . import api_views
from . import views_journey
from . import api_journey
from . import api_push
from . import api_coach_push
from . import views_advent
from . import views_journey_gift

app_name = 'crush_lu'

urlpatterns = [
    # Secure media serving
    path('media/profile/<int:user_id>/<str:photo_field>/', views_media.serve_profile_photo, name='serve_profile_photo'),

    # Wallet passes
    path('wallet/apple/pass/', views.wallet_apple_pass, name='wallet_apple_pass'),
    path('wallet/google/save/', views.wallet_google_save, name='wallet_google_save'),

    # Landing and public pages
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('how-it-works/', views.how_it_works, name='how_it_works'),
    path('membership/', views.membership, name='membership'),

    # PWA Debug Page (language-prefixed is fine for debug pages)
    # Note: sw-workbox.js, manifest.json, and offline/ are now in urls_crush.py
    # as language-neutral URLs to prevent redirect errors
    path('pwa-debug/', views.pwa_debug_view, name='pwa_debug'),

    # Legal pages
    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),
    path('terms-of-service/', views.terms_of_service, name='terms_of_service'),
    path('data-deletion/', views.data_deletion_request, name='data_deletion'),
    path('data-deletion/status/', views.data_deletion_status, name='data_deletion_status'),

    # Facebook Data Deletion Callback (required by Facebook)
    path('facebook/data-deletion/', views.facebook_data_deletion_callback, name='facebook_data_deletion'),

    # Authentication - Unified auth view with login/signup tabs
    # UnifiedAuthView combines login and signup into a single tabbed experience
    path('login/', UnifiedAuthView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('oauth-complete/', views.oauth_complete, name='oauth_complete'),

    # OAuth Popup Flow (for better PWA experience)
    path('oauth/popup-callback/', views_oauth_popup.oauth_popup_callback, name='oauth_popup_callback'),
    path('oauth/popup-error/', views_oauth_popup.oauth_popup_error, name='oauth_popup_error'),
    path('oauth/landing/', views_oauth_popup.oauth_landing, name='oauth_landing'),
    # Note: api/auth/status/ moved to urls_crush.py (language-neutral) for hardcoded JS paths

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

    # Social photo import API
    path('api/profile/social-photos/', views_profile.get_social_photos_api, name='get_social_photos_api'),
    path('api/profile/import-social-photo/', views_profile.import_social_photo, name='import_social_photo'),

    # HTMX photo upload/delete endpoints
    path('api/profile/upload-photo/<int:slot>/', views_profile.upload_profile_photo, name='upload_profile_photo'),
    path('api/profile/delete-photo/<int:slot>/', views_profile.delete_profile_photo, name='delete_profile_photo'),

    # Phone verification API endpoints are in urls_crush.py (language-neutral)
    # to avoid i18n prefix issues with hardcoded JavaScript API paths

    # Phone verification page (for existing users)
    path('verify-phone/', views_phone_verification.verify_phone_page, name='verify_phone'),

    # User dashboard
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/edit/', views.edit_profile, name='edit_profile'),

    # Account settings
    path('account/settings/', views.account_settings, name='account_settings'),
    path('account/settings/email-preferences/', views.update_email_preferences, name='update_email_preferences'),
    path('account/set-password/', views.set_password, name='set_password'),
    path('account/disconnect/<int:social_account_id>/', views.disconnect_social_account, name='disconnect_social_account'),
    path('account/delete/', views.delete_account, name='delete_account'),

    # Email unsubscribe (public access with token)
    path('unsubscribe/<uuid:token>/', views.email_unsubscribe, name='email_unsubscribe'),

    # Special user experience
    path('special-welcome/', views.special_welcome, name='special_welcome'),

    # Referral landing (public access)
    path('r/<str:code>/', views.referral_redirect, name='referral_redirect'),

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

    # Note: Event Voting APIs moved to urls_crush.py (language-neutral) for hardcoded JS paths
    # - api/events/<int:event_id>/voting/status/
    # - api/events/<int:event_id>/voting/submit/
    # - api/events/<int:event_id>/voting/results/

    # Coach dashboard
    path('coach/dashboard/', views.coach_dashboard, name='coach_dashboard'),
    path('coach/profile/edit/', views.coach_edit_profile, name='coach_edit_profile'),
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
    path('events/<int:event_id>/connect-inline/<int:user_id>/', views.request_connection_inline, name='request_connection_inline'),
    path('events/<int:event_id>/connection-actions/<int:user_id>/', views.connection_actions, name='connection_actions'),
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

    # Journey Gift System
    path('journey/gift/create/', views_journey_gift.gift_create, name='gift_create'),
    path('journey/gift/success/<str:gift_code>/', views_journey_gift.gift_success, name='gift_success'),
    path('journey/gift/<str:gift_code>/', views_journey_gift.gift_landing, name='gift_landing'),
    path('journey/gift/<str:gift_code>/claim/', views_journey_gift.gift_claim, name='gift_claim'),
    path('journey/gifts/', views_journey_gift.gift_list, name='gift_list'),

    # Journey API Endpoints (these use {% url %} template tags so can stay in i18n_patterns)
    path('api/journey/submit-challenge/', api_journey.submit_challenge, name='api_submit_challenge'),
    path('api/journey/unlock-hint/', api_journey.unlock_hint, name='api_unlock_hint'),
    path('api/journey/progress/', api_journey.get_progress, name='api_get_progress'),
    path('api/journey/save-state/', api_journey.save_state, name='api_save_state'),
    path('api/journey/final-response/', api_journey.record_final_response, name='api_record_final_response'),
    # Note: unlock-puzzle-piece and reward-progress moved to urls_crush.py (language-neutral)
    # for hardcoded JS paths in photo_reveal.html

    # ============================================================================
    # PUSH NOTIFICATIONS API - MOVED TO urls_crush.py (language-neutral)
    # All push notification APIs have been moved to azureproject/urls_crush.py
    # because they are called from external JS files with hardcoded paths.
    # ============================================================================

    # ============================================================================
    # COACH PUSH NOTIFICATIONS API - MOVED TO urls_crush.py (language-neutral)
    # All coach push notification APIs have been moved to azureproject/urls_crush.py
    # because they are called from external JS files with hardcoded paths.
    # ============================================================================

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
