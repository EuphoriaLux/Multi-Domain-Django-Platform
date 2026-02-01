"""
Views package for Crush.lu

Organized by domain for better maintainability.
All views are re-exported here for backward compatibility.
"""

# Core views (dashboard, special welcome, LuxID mockups)
from .core import dashboard, special_welcome, luxid_mockup_view, luxid_auth_mockup_view

# Public pages
from .public import home, about, how_it_works, privacy_policy, terms_of_service, membership

# Account management
from .account import (
    account_settings, update_email_preferences, email_unsubscribe,
    set_password, disconnect_social_account, delete_account,
    data_deletion_request, data_deletion_status, facebook_data_deletion_callback,
)

# Onboarding
from .onboarding import oauth_complete, referral_redirect, signup, create_profile, profile_submitted

# Profile
from .profile import (
    save_profile_step1, save_profile_step2, save_profile_step3,
    complete_profile_submission, get_profile_progress,
    get_social_photos_api, import_social_photo,
    upload_profile_photo, delete_profile_photo,
    edit_profile,
)

# Events
from .events import (
    event_list, event_detail, event_register, event_cancel,
    event_voting_lobby, event_activity_vote, event_voting_results,
    event_presentations, submit_presentation_rating, my_presentation_scores,
    get_current_presenter_api, voting_demo,
)

# Connections
from .connections import (
    event_attendees, request_connection, request_connection_inline,
    connection_actions, respond_connection, my_connections, connection_detail,
)

# Coach
from .coach import (
    coach_dashboard, coach_mark_review_call_complete, coach_log_failed_call,
    coach_review_profile, coach_preview_email, coach_sessions, coach_edit_profile,
    coach_journey_dashboard, coach_edit_journey, coach_edit_challenge,
    coach_view_user_progress, coach_presentation_control, coach_advance_presentation,
    coach_manage_invitations,
)

# Invitations
from .invitations import invitation_landing, invitation_accept

# Journey
from .journey import (
    journey_map, journey_selector, journey_map_wonderland,
    chapter_view, challenge_view, reward_view, certificate_view,
)

# Journey gifts
from .journey_gift import (
    gift_create, gift_success, gift_landing, gift_claim, gift_list,
)

# Advent calendar
from .advent import (
    advent_calendar_view, advent_door_view, scan_qr_code,
    advent_qr_scanner, get_advent_status, open_door_api,
)

# PWA
from .pwa import (
    offline_view, service_worker_view, manifest_view,
    assetlinks_view, pwa_debug_view,
)

# Phone verification
from .phone import (
    verify_phone_page, mark_phone_verified, phone_verification_status,
)

# Media
from .media import serve_profile_photo

# OAuth
from .oauth import oauth_popup_callback, oauth_popup_error, oauth_landing, check_auth_status

# Wallet
from .wallet import apple_wallet_pass, google_wallet_jwt

# SEO
from .seo import robots_txt

# Language
from .language import set_language_with_profile

__all__ = [
    # Core
    'dashboard', 'special_welcome', 'luxid_mockup_view', 'luxid_auth_mockup_view',
    # Public
    'home', 'about', 'how_it_works', 'privacy_policy', 'terms_of_service', 'membership',
    # Account
    'account_settings', 'update_email_preferences', 'email_unsubscribe',
    'set_password', 'disconnect_social_account', 'delete_account',
    'data_deletion_request', 'data_deletion_status', 'facebook_data_deletion_callback',
    # Onboarding
    'oauth_complete', 'referral_redirect', 'signup', 'create_profile', 'profile_submitted',
    # Profile
    'save_profile_step1', 'save_profile_step2', 'save_profile_step3',
    'complete_profile_submission', 'get_profile_progress',
    'get_social_photos_api', 'import_social_photo',
    'upload_profile_photo', 'delete_profile_photo', 'edit_profile',
    # Events
    'event_list', 'event_detail', 'event_register', 'event_cancel',
    'event_voting_lobby', 'event_activity_vote', 'event_voting_results',
    'event_presentations', 'submit_presentation_rating', 'my_presentation_scores',
    'get_current_presenter_api', 'voting_demo',
    # Connections
    'event_attendees', 'request_connection', 'request_connection_inline',
    'connection_actions', 'respond_connection', 'my_connections', 'connection_detail',
    # Coach
    'coach_dashboard', 'coach_mark_review_call_complete', 'coach_log_failed_call',
    'coach_review_profile', 'coach_preview_email', 'coach_sessions', 'coach_edit_profile',
    'coach_journey_dashboard', 'coach_edit_journey', 'coach_edit_challenge',
    'coach_view_user_progress', 'coach_presentation_control', 'coach_advance_presentation',
    'coach_manage_invitations',
    # Invitations
    'invitation_landing', 'invitation_accept',
    # Journey
    'journey_map', 'journey_selector', 'journey_map_wonderland',
    'chapter_view', 'challenge_view', 'reward_view', 'certificate_view',
    # Journey gifts
    'gift_create', 'gift_success', 'gift_landing', 'gift_claim', 'gift_list',
    # Advent
    'advent_calendar_view', 'advent_door_view', 'scan_qr_code',
    'advent_qr_scanner', 'get_advent_status', 'open_door_api',
    # PWA
    'offline_view', 'service_worker_view', 'manifest_view',
    'assetlinks_view', 'pwa_debug_view',
    # Phone
    'verify_phone_page', 'mark_phone_verified', 'phone_verification_status',
    # Media
    'serve_profile_photo',
    # OAuth
    'oauth_popup_callback', 'oauth_popup_error', 'oauth_landing', 'check_auth_status',
    # Wallet
    'apple_wallet_pass', 'google_wallet_jwt',
    # SEO
    'robots_txt',
    # Language
    'set_language_with_profile',
]
