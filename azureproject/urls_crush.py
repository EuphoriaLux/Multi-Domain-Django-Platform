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

from .urls_shared import base_patterns, api_patterns
from crush_lu.admin import crush_admin_site
from crush_lu import admin_views, views, views_phone_verification
from crush_lu import api_views, api_push, api_coach_push, views_oauth_popup, api_journey
from crush_lu.sitemaps import crush_sitemaps
from crush_lu.views_seo import robots_txt

# Language-neutral patterns (no /en/, /de/, /fr/ prefix)
# These include health checks, API endpoints, authentication, SEO files, and PWA files
urlpatterns = base_patterns + api_patterns + [
    # SEO: robots.txt and sitemap.xml (must be at root, no language prefix)
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': crush_sitemaps}, name='django.contrib.sitemaps.views.sitemap'),

    # PWA: Service Worker, Manifest, and Offline page (must be at root for scope)
    # These CANNOT be inside i18n_patterns because browsers block redirected scripts
    # Note: Use {% url 'pwa_manifest' %} instead of {% url 'crush_lu:manifest' %} in templates
    path('sw-workbox.js', views.service_worker_view, name='pwa_service_worker'),
    path('manifest.json', views.manifest_view, name='pwa_manifest'),
    path('offline/', views.offline_view, name='pwa_offline'),

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
    path('api/push/unsubscribe/', api_push.unsubscribe_push, name='api_unsubscribe_push'),
    path('api/push/subscriptions/', api_push.list_subscriptions, name='api_list_subscriptions'),
    path('api/push/preferences/', api_push.update_subscription_preferences, name='api_update_push_preferences'),
    path('api/push/test/', api_push.send_test_push, name='api_send_test_push'),
    path('api/push/mark-pwa-user/', api_push.mark_pwa_user, name='api_mark_pwa_user'),
    path('api/push/pwa-status/', api_push.get_pwa_status, name='api_pwa_status'),

    # Coach Push Notifications API (called from alpine-components.js)
    path('api/coach/push/vapid-public-key/', api_coach_push.get_vapid_public_key, name='api_coach_vapid_public_key'),
    path('api/coach/push/subscribe/', api_coach_push.subscribe_push, name='api_coach_subscribe_push'),
    path('api/coach/push/unsubscribe/', api_coach_push.unsubscribe_push, name='api_coach_unsubscribe_push'),
    path('api/coach/push/subscriptions/', api_coach_push.list_subscriptions, name='api_coach_list_subscriptions'),
    path('api/coach/push/preferences/', api_coach_push.update_subscription_preferences, name='api_coach_update_push_preferences'),
    path('api/coach/push/test/', api_coach_push.send_test_push, name='api_coach_send_test_push'),

    # Auth Status API (called from oauth-popup.js, auth.html templates)
    path('api/auth/status/', views_oauth_popup.check_auth_status, name='check_auth_status'),

    # Event Voting API (called from event-voting.js)
    path('api/events/<int:event_id>/voting/status/', api_views.voting_status_api, name='voting_status_api'),
    path('api/events/<int:event_id>/voting/submit/', api_views.submit_vote_api, name='submit_vote_api'),
    path('api/events/<int:event_id>/voting/results/', api_views.voting_results_api, name='voting_results_api'),

    # Journey Reward APIs (called from photo_reveal.html with hardcoded paths)
    path('api/journey/unlock-puzzle-piece/', api_journey.unlock_puzzle_piece, name='api_unlock_puzzle_piece'),
    path('api/journey/reward-progress/<int:reward_id>/', api_journey.get_reward_progress, name='api_get_reward_progress'),

    # ============================================================================
    # ADMIN PANELS (language-neutral - always accessible without language prefix)
    # These are moved outside i18n_patterns to avoid 404 errors when admins
    # manually change the URL language prefix.
    # ============================================================================

    # Dedicated Crush.lu Admin Panel (Coach Panel)
    # Note: Dashboard must come BEFORE admin site to avoid path matching issues
    path('crush-admin/dashboard/', admin_views.crush_admin_dashboard, name='crush_admin_dashboard'),
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
