# azureproject/urls_crush.py
"""
URL configuration for Crush.lu dating platform.

This is the URL config used when requests come from crush.lu domain.
Supports internationalization with language-prefixed URLs (/en/, /de/, /fr/).
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.conf.urls.i18n import i18n_patterns
from django.contrib.sitemaps.views import sitemap
from django.shortcuts import redirect
from django.utils.translation import get_language
from django.views.i18n import JavaScriptCatalog

from .urls_shared import base_patterns, api_patterns
from crush_lu.admin import crush_admin_site
from crush_lu.admin.user_segments import user_segments_dashboard, segment_detail
from crush_lu.admin.profile_reminders import profile_reminders_panel
from crush_lu import admin_views, views, views_phone_verification, views_profile, views_profile_draft
from crush_lu.admin_views import signup_trend_api, verification_trend_api, cumulative_growth_api
from crush_lu.admin_views import (
    email_template_manager,
    email_template_user_search,
    email_template_preview,
    email_template_send,
    email_template_create_draft,
    email_template_load_events,
    email_template_load_connections,
    email_template_load_invitations,
    email_template_load_gifts,
)
from crush_lu import api_views, api_push, api_coach_push, api_pwa, views_oauth_popup, api_journey, views_wallet, api_referral, api_admin_sync, views_crush_spark, views_checkin
from crush_lu.wallet import passkit_service, google_callback
from crush_lu.sitemaps import crush_sitemaps
from crush_lu.views_seo import robots_txt


def redirect_profile_to_dashboard(request):
    """
    Redirect /profile/ to /{lang}/dashboard/.

    LOGIN_REDIRECT_URL is set globally to /profile/ but Crush.lu uses /dashboard/.
    This redirect handles the non-prefixed URL and sends users to the localized dashboard.
    """
    lang = get_language() or 'en'
    return redirect(f'/{lang}/dashboard/')


# Language-neutral patterns (no /en/, /de/, /fr/ prefix)
# These include health checks, API endpoints, authentication, SEO files, and PWA files
urlpatterns = base_patterns + api_patterns + [
    # SEO: robots.txt and sitemap.xml (must be at root, no language prefix)
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': crush_sitemaps}, name='django.contrib.sitemaps.views.sitemap'),

    # JavaScript i18n catalog (must be language-neutral for JavaScript to access)
    path('jsi18n/', JavaScriptCatalog.as_view(packages=['crush_lu']), name='javascript-catalog'),

    # PWA: Service Worker, Manifest, and Offline page (must be at root for scope)
    # These CANNOT be inside i18n_patterns because browsers block redirected scripts
    # Note: Use {% url 'pwa_manifest' %} instead of {% url 'crush_lu:manifest' %} in templates
    path('sw-workbox.js', views.service_worker_view, name='pwa_service_worker'),
    path('manifest.json', views.manifest_view, name='pwa_manifest'),
    path('offline/', views.offline_view, name='pwa_offline'),
    # Android App Links verification for PWA
    path('.well-known/assetlinks.json', views.assetlinks_view, name='assetlinks'),

    # Phone verification API (language-neutral - called by JavaScript with hardcoded paths)
    path('api/phone/mark-verified/', views_phone_verification.mark_phone_verified, name='api_phone_mark_verified'),
    path('api/phone/status/', views_phone_verification.phone_verification_status, name='api_phone_status'),

    # ============================================================================
    # LANGUAGE-NEUTRAL API ENDPOINTS
    # These APIs are called from external JavaScript files with hardcoded paths.
    # They must NOT be inside i18n_patterns to avoid 404 errors for non-English users.
    # ============================================================================

    # Push Notifications API (called from push-notifications.js, pwa-detector.js)
    path('api/push/vapid-public-key/', api_push.get_vapid_public_key, name='api_vapid_public_key'),
    path('api/push/subscribe/', api_push.subscribe_push, name='api_subscribe_push'),
    path('api/push/refresh-subscription/', api_push.refresh_subscription, name='api_refresh_push_subscription'),
    path('api/push/validate-subscription/', api_push.validate_subscription, name='api_validate_push_subscription'),
    path('api/push/unsubscribe/', api_push.unsubscribe_push, name='api_unsubscribe_push'),
    path('api/push/delete-subscription/', api_push.delete_push_subscription, name='api_delete_push_subscription'),
    path('api/push/subscriptions/', api_push.list_subscriptions, name='api_list_subscriptions'),
    path('api/push/preferences/', api_push.update_subscription_preferences, name='api_update_push_preferences'),
    path('api/push/test/', api_push.send_test_push, name='api_send_test_push'),
    path('api/push/mark-pwa-user/', api_push.mark_pwa_user, name='api_mark_pwa_user'),
    path('api/push/pwa-status/', api_push.get_pwa_status, name='api_pwa_status'),
    path('api/push/health-check/', api_push.run_subscription_health_check, name='api_push_health_check'),

    # PWA Device Registration API (called from pwa-detector.js)
    path('api/pwa/register-installation/', api_pwa.register_pwa_installation, name='api_pwa_register_installation'),

    # Coach Push Notifications API (called from alpine-components.js)
    path('api/coach/push/vapid-public-key/', api_coach_push.get_vapid_public_key, name='api_coach_vapid_public_key'),
    path('api/coach/push/subscribe/', api_coach_push.subscribe_push, name='api_coach_subscribe_push'),
    path('api/coach/push/unsubscribe/', api_coach_push.unsubscribe_push, name='api_coach_unsubscribe_push'),
    path('api/coach/push/delete-subscription/', api_coach_push.delete_push_subscription, name='api_coach_delete_push_subscription'),
    path('api/coach/push/subscriptions/', api_coach_push.list_subscriptions, name='api_coach_list_subscriptions'),
    path('api/coach/push/preferences/', api_coach_push.update_subscription_preferences, name='api_coach_update_push_preferences'),
    path('api/coach/push/test/', api_coach_push.send_test_push, name='api_coach_send_test_push'),

    # Auth Status API (called from oauth-popup.js, auth.html templates)
    path('api/auth/status/', views_oauth_popup.check_auth_status, name='check_auth_status'),

    # Event Voting API (called from event-voting.js)
    path('api/events/<int:event_id>/voting/status/', api_views.voting_status_api, name='voting_status_api'),
    path('api/events/<int:event_id>/voting/submit/', api_views.submit_vote_api, name='submit_vote_api'),
    path('api/events/<int:event_id>/voting/results/', api_views.voting_results_api, name='voting_results_api'),

    # Crush Spark API (language-neutral for JS polling)
    path('api/sparks/<int:spark_id>/status/', views_crush_spark.api_spark_status, name='api_spark_status'),

    # Journey Reward APIs (called from photo_reveal.html with hardcoded paths)
    path('api/journey/unlock-puzzle-piece/', api_journey.unlock_puzzle_piece, name='api_unlock_puzzle_piece'),
    path('api/journey/reward-progress/<int:reward_id>/', api_journey.get_reward_progress, name='api_get_reward_progress'),

    # PassKit Web Service (Apple Wallet)
    path(
        'wallet/v1/devices/<str:device_library_identifier>/registrations/<str:pass_type_identifier>/<str:serial_number>',
        passkit_service.device_registration,
        name='passkit_device_registration',
    ),
    path(
        'wallet/v1/devices/<str:device_library_identifier>/registrations/<str:pass_type_identifier>',
        passkit_service.list_device_registrations,
        name='passkit_list_registrations',
    ),
    path(
        'wallet/v1/passes/<str:pass_type_identifier>/<str:serial_number>',
        passkit_service.get_latest_pass,
        name='passkit_get_pass',
    ),
    path(
        'wallet/v1/log',
        passkit_service.log_endpoint,
        name='passkit_log',
    ),

    # Profile Step-by-Step Saving APIs (called from alpine-components.js with hardcoded paths)
    path('api/profile/save-step1/', views_profile.save_profile_step1, name='api_save_profile_step1'),
    path('api/profile/save-step2/', views_profile.save_profile_step2, name='api_save_profile_step2'),
    path('api/profile/save-step3/', views_profile.save_profile_step3, name='api_save_profile_step3'),
    path('api/profile/progress/', views_profile.get_profile_progress, name='api_get_profile_progress'),

    # Profile Draft APIs (auto-save and draft recovery)
    path('api/profile/draft/save/', views_profile_draft.save_draft, name='api_save_draft'),
    path('api/profile/draft/get/', views_profile_draft.get_draft, name='api_get_draft'),
    path('api/profile/draft/clear/', views_profile_draft.clear_draft, name='api_clear_draft'),
    path('api/profile/draft/upload-photo/', views_profile.upload_photo_draft, name='api_upload_photo_draft'),

    # Social Photo Import APIs (called from alpine-components.js with hardcoded paths)
    path('api/profile/social-photos/', views_profile.get_social_photos_api, name='api_get_social_photos'),
    path('api/profile/import-social-photo/', views_profile.import_social_photo, name='api_import_social_photo'),

    # Profile Photo Upload/Delete APIs (called from alpine-components.js with hardcoded paths)
    path('api/profile/upload-photo/<int:slot>/', views_profile.upload_profile_photo, name='api_upload_profile_photo'),
    path('api/profile/delete-photo/<int:slot>/', views_profile.delete_profile_photo, name='api_delete_profile_photo'),

    # Profile Completion API (called from alpine-components.js with hardcoded paths)
    path('api/profile/complete/', views_profile.complete_profile_submission, name='api_complete_profile_submission'),

    # Event Check-In API (language-neutral - called from QR codes and scanner)
    path('api/events/checkin/<int:registration_id>/<str:token>/', views_checkin.event_checkin_api, name='event_checkin_api'),

    # Wallet passes (language-neutral for platform-specific clients)
    path('wallet/apple/pass/', views_wallet.apple_wallet_pass, name='wallet_apple_pass'),
    path('wallet/google/jwt/', views_wallet.google_wallet_jwt, name='wallet_google_jwt'),
    path('wallet/google/event-ticket/<int:registration_id>/jwt/', views_wallet.google_event_ticket_jwt, name='event_ticket_jwt'),

    # Google Wallet callback (called by Google when users save/delete passes)
    path('wallet/google/callback/', google_callback.google_wallet_callback, name='wallet_google_callback'),

    # Referral API (called from dashboard with hardcoded paths)
    path('api/referral/me/', api_referral.referral_me, name='api_referral_me'),
    path('api/referral/redeem/', api_referral.redeem_points, name='api_referral_redeem'),

    # Admin Sync API (called by Azure Functions for scheduled tasks)
    path('api/admin/sync-contacts/', api_admin_sync.sync_contacts_endpoint, name='api_admin_sync_contacts'),
    path('api/admin/sync-contacts/delete-all/', api_admin_sync.delete_all_contacts_endpoint, name='api_admin_delete_all_contacts'),
    path('api/admin/sync-contacts/health/', api_admin_sync.sync_contacts_health, name='api_admin_sync_contacts_health'),

    # Referral redirect (language-neutral for wallet passes and sharing)
    # This allows https://crush.lu/r/CODE/ to work without language prefix
    # Users will be redirected to the home page in their browser's preferred language
    path('r/<str:code>/', views.referral_redirect, name='referral_redirect_neutral'),

    # ============================================================================
    # LOGIN REDIRECT COMPATIBILITY
    # LOGIN_REDIRECT_URL is set globally to /profile/ but Crush.lu uses /dashboard/.
    # This redirect handles the non-prefixed URL from login and sends users to the
    # localized dashboard. Without this, users would get a 404 after login.
    # ============================================================================
    path('profile/', redirect_profile_to_dashboard, name='profile_redirect'),

    # ============================================================================
    # ADMIN PANELS (language-neutral - always accessible without language prefix)
    # These are moved outside i18n_patterns to avoid 404 errors when admins
    # manually change the URL language prefix.
    # ============================================================================

    # Dedicated Crush.lu Admin Panel (Coach Panel)
    # Note: Dashboard must come BEFORE admin site to avoid path matching issues
    path('crush-admin/dashboard/', admin_views.crush_admin_dashboard, name='crush_admin_dashboard'),
    path('crush-admin/api/signup-trend/', signup_trend_api, name='crush_admin_signup_trend'),
    path('crush-admin/api/verification-trend/', verification_trend_api, name='crush_admin_verification_trend'),
    path('crush-admin/api/cumulative-growth/', cumulative_growth_api, name='crush_admin_cumulative_growth'),
    path('crush-admin/user-segments/', user_segments_dashboard, name='user_segments_dashboard'),
    path('crush-admin/user-segments/<str:segment_key>/', segment_detail, name='segment_detail'),
    path('crush-admin/profile-reminders/', profile_reminders_panel, name='profile_reminders_panel'),

    # Email Template Manager
    path('crush-admin/email-templates/', email_template_manager, name='email_template_manager'),
    path('crush-admin/email-templates/search-users/', email_template_user_search, name='email_template_user_search'),
    path('crush-admin/email-templates/preview/', email_template_preview, name='email_template_preview'),
    path('crush-admin/email-templates/send/', email_template_send, name='email_template_send'),
    path('crush-admin/email-templates/create-draft/', email_template_create_draft, name='email_template_create_draft'),
    path('crush-admin/email-templates/load-events/', email_template_load_events, name='email_template_load_events'),
    path('crush-admin/email-templates/load-connections/', email_template_load_connections, name='email_template_load_connections'),
    path('crush-admin/email-templates/load-invitations/', email_template_load_invitations, name='email_template_load_invitations'),
    path('crush-admin/email-templates/load-gifts/', email_template_load_gifts, name='email_template_load_gifts'),

    path('crush-admin/', crush_admin_site.urls),

    # Standard Django Admin (all platforms)
    path('admin/', admin.site.urls),
]

# Language-prefixed patterns (all user-facing pages)
# URLs will be: /en/events/, /de/events/, /fr/events/, etc.
urlpatterns += i18n_patterns(
    # Crush.lu app URLs
    path('', include('crush_lu.urls', namespace='crush_lu')),

    # Include /en/ prefix even for default language
    prefix_default_language=True,
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
