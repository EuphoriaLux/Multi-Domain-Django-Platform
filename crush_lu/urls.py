from django.urls import path
from django.views.generic import RedirectView
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from allauth.account.views import LoginView, LogoutView
from allauth.account.forms import LoginForm
from . import views
from . import views_static
from . import views_women_1y
from . import views_pre_screening
from . import views_crush_connect
from . import views_connect_cycle
from . import views_connect_chat
from . import views_moderation
from .forms import CrushSignupForm
from .throttling import LoginRateThrottle
from .rate_limit_utils import add_rate_limited_error, humanize_wait_seconds
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

    def get_initial(self):
        # Prefill the login field after email confirmation. The address is
        # stashed by the email_confirmed signal handler in crush_lu/signals.py.
        # We pop it so a stale value doesn't persist into a later session.
        initial = super().get_initial() or {}
        prefill = self.request.session.pop('login_prefill_email', None)
        if prefill:
            initial['login'] = prefill
        return initial

    def dispatch(self, request, *args, **kwargs):
        # Needed up-front so render_to_response() below (the rate-limit
        # branch's own template render) has self.request to work with -
        # normally View.setup()/super().dispatch() would set this for us,
        # but we need it before we know whether we're even calling super().
        self.request, self.args, self.kwargs = request, args, kwargs

        # Rate limiting for POST requests (login attempts)
        if request.method == 'POST':
            throttle = LoginRateThrottle()
            if not throttle.allow_request(request, self):
                wait = throttle.wait()
                logger.warning(f"[RATE-LIMIT] Login rate limit exceeded for IP: {throttle.get_ident(request)}")
                # UX Wave 3 · WP3 review (P2): this branch returns before
                # ever reaching super().dispatch(), so allauth's own
                # @sensitive_post_parameters_m on LoginView.dispatch never
                # runs here - mark the same fields it would, so a crash
                # while rendering this response doesn't leak the submitted
                # password into Django's error report.
                request.sensitive_post_parameters = [
                    'oldpassword',
                    'password',
                    'password1',
                    'password2',
                ]
                # UX Wave 3 · WP3 (finding 2-04): re-render the same auth.html
                # the user was on, with an inline translated error, instead of
                # a bare text/plain page with no branding or way back.
                #
                # UX Wave 3 · WP3 review (P2): bind the form to the submitted
                # POST data (not LoginForm()) so the user's typed identifier
                # survives the wait instead of forcing a retype - safe here
                # because add_rate_limited_error() seeds _errors itself and
                # never lets Form.errors trigger full_clean().
                login_form = LoginForm(request.POST)
                add_rate_limited_error(
                    login_form,
                    _('Too many login attempts. Please try again in %(wait)s.')
                    % {'wait': humanize_wait_seconds(wait)},
                )
                # UX Wave 3 · WP3 review (P2): carry the validated `next`
                # redirect target through the throttled response too, the
                # same way NextRedirectMixin.get_context_data() would have -
                # this branch bypasses it by building context by hand, so a
                # user who was sent here from a protected page and retries
                # after waiting must not land on the default destination.
                from allauth.utils import get_request_param

                redirect_field_value = get_request_param(
                    self.request, self.redirect_field_name
                )
                context = {
                    'signup_form': CrushSignupForm(),
                    'login_form': login_form,
                    'mode': 'login',
                    'redirect_field_name': self.redirect_field_name,
                    'redirect_field_value': redirect_field_value,
                }
                response = self.render_to_response(context)
                response.status_code = 429
                response['Retry-After'] = str(int(wait))
                return response

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
from . import views_journey
from . import api_journey
from . import views_advent
from . import views_journey_gift
from . import views_crush_spark
from . import views_ticket
from . import views_quiz
from . import views_coach as views_coach_module
from . import views_coach_photos
from . import views_quiz_config
from . import views_crush_cache
from . import views_changelog
from . import views_premium
from . import views_event_lobby
from . import views_payments

app_name = 'crush_lu'



def _spark_to_crush_connect(request, *args, **kwargs):
    """Funnel for soft-removed spark URLs.

    Django's RedirectView.as_view(pattern_name=...) reverses the target
    URL with whatever kwargs were captured from the source URL pattern.
    Several spark routes have ``<int:event_id>`` / ``<int:spark_id>`` /
    ``<int:user_id>`` in them; passing those to reverse() on
    ``crush_connect_teaser`` (which accepts no kwargs) raises
    NoReverseMatch and would return 500 instead of redirecting.

    This tiny view ignores any captured kwargs and reverses cleanly.
    Codex flagged the RedirectView form as P1 on PR #433.

    Left targeting the teaser rather than the hub (UX Wave 3, finding
    5-13's redirect target): retargeting spark_request/spark_send_inline/
    spark_actions is outside finding 5-13's evidence (only spark_list,
    spark_detail and matches.html were cited), and the teaser is public
    while the hub is login-gated, so switching the target here would
    change what a logged-out or unverified member hitting one of these
    routes lands on.
    """
    return redirect("crush_lu:crush_connect_teaser")


def _spark_to_crush_connect_hub(request, *args, **kwargs):
    """UX Wave 3, finding 5-13 (product answer): a permanent, unconditional
    redirect for the explicitly named member Sparks pages — /sparks/
    (spark_list), /sparks/received/ (spark_received), spark_detail and, since
    UX Wave 4 WP12, spark_create_journey — to the Crush Connect hub. Unlike
    ``_spark_to_crush_connect`` above, these are member-only pages a
    signed-in member reaches from their own dashboard/nav, so the hub's
    login gate is the right target (matching "Go to Crush Connect"
    elsewhere in this retirement). Ignores any
    captured spark_id kwarg for the same NoReverseMatch reason.
    """
    return redirect("crush_lu:crush_connect_hub", permanent=True)


_legacy_delete_redirect = RedirectView.as_view(
    pattern_name="crush_lu:delete_crushlu_profile", query_string=True
)


def _legacy_account_delete(request, *args, **kwargs):
    """Legacy /account/delete/: GET forwards, POST keeps the GDPR contract.

    Pages rendered at this URL (and cached by the service worker) POST
    deletion_type + confirm_email here. A 302 would make the browser replay
    it as a GET and drop the form, so POST goes to the GDPR view itself,
    with its own auth, method and CSRF handling unchanged.
    """
    if request.method == "POST":
        return views.gdpr_data_management(request)
    return _legacy_delete_redirect(request, *args, **kwargs)


urlpatterns = [
    # Secure media serving
    path('media/profile/<int:user_id>/<str:photo_field>/', views_media.serve_profile_photo, name='serve_profile_photo'),
    path('media/coach/<int:coach_id>/', views_media.serve_coach_photo, name='serve_coach_photo'),

    # Premium membership — member chooses their coach
    path('premium/coaches/', views_premium.premium_choose_coach, name='premium_choose_coach'),
    path('premium/coaches/<int:coach_id>/select/', views_premium.premium_select_coach, name='premium_select_coach'),
    path('premium/cancel/', views_premium.premium_cancel_membership, name='premium_cancel_membership'),

    # Landing and public pages
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    # Women's 1-year campaign landing + CTA router (crush_lu/campaign_women_1y.py)
    path('women-1-year/', views_women_1y.women_1y_landing, name='women_1y_landing'),
    path('women-1-year/go/', views_women_1y.women_1y_go, name='women_1y_go'),
    path('test-upstair/', views.test_upstair, name='test_upstair'),
    path('changelog/', views_changelog.changelog_list, name='changelog_list'),
    path('changelog/<slug:slug>/', views_changelog.changelog_detail, name='changelog_detail'),
    path('how-it-works/', views.how_it_works, name='how_it_works'),
    path('speed-dating/', views_static.speed_dating_guide, name='speed_dating_guide'),
    path('quiz-night/', views_static.quiz_night_guide, name='quiz_night_guide'),
    path('crush-coach/', views.crush_coach, name='crush_coach'),
    path('crush-connect/', views.crush_connect_teaser, name='crush_connect_teaser'),
    # Experience explainers — one member-facing landing page per Connect
    # experience (coach-pick, read-the-photo, in-the-mix).
    # See CONNECT_EXPERIENCES in views_crush_connect.py.
    path(
        'crush-connect/experiences/<slug:slug>/',
        views_crush_connect.crush_connect_experience,
        name='crush_connect_experience',
    ),
    # Crush Connect opt-in onboarding — resumable 7-step wizard. The bare
    # path is the smart-resume entry (name unchanged so existing redirects
    # keep working); the numbered path renders/saves a single step.
    path(
        'crush-connect/onboarding/',
        views_crush_connect.crush_connect_onboarding,
        name='crush_connect_onboarding',
    ),
    path(
        'crush-connect/onboarding/<int:step>/',
        views_crush_connect.crush_connect_onboarding_step,
        name='crush_connect_onboarding_step',
    ),
    # Post-onboarding editor for Connect/catalogue answers.
    path(
        'crush-connect/profile/',
        views_crush_connect.crush_connect_profile_edit,
        name='crush_connect_profile_edit',
    ),
    # Crush Connect hub — catalogue, Connect Week, Coach's Pick, and profile.
    # The dedicated nav menu and the mobile bottom-nav 'Connect' tab point here.
    path(
        'crush-connect/home/',
        views_crush_connect.crush_connect_hub,
        name='crush_connect_hub',
    ),
    path(
        'crush-connect/pause/',
        views_crush_connect.crush_connect_pause,
        name='crush_connect_pause',
    ),
    path(
        'crush-connect/reactivate/',
        views_crush_connect.crush_connect_reactivate,
        name='crush_connect_reactivate',
    ),
    # Retired Today's Drop URL: preserve old bookmarks as a hub redirect.
    path(
        'crush-connect/today/',
        views_crush_connect.crush_connect_home,
        name='crush_connect_home',
    ),
    # Retired Curiosity Spark deep links may still exist in bell notifications
    # and emails. Keep every former route as a safe redirect to the Connect hub.
    path(
        'crush-connect/spark/<int:user_id>/',
        views_crush_connect.crush_connect_legacy_spark_redirect,
        name='crush_connect_spark_compose',
    ),
    path(
        'crush-connect/sparks/',
        views_crush_connect.crush_connect_legacy_spark_redirect,
        name='crush_connect_sparks_received',
    ),
    path(
        'crush-connect/sparks/<int:spark_id>/respond/',
        views_crush_connect.crush_connect_legacy_spark_redirect,
        name='crush_connect_spark_respond',
    ),
    path(
        'crush-connect/coach-pick/',
        views_crush_connect.crush_connect_coach_pick,
        name='crush_connect_coach_pick',
    ),
    # Catalogue status and anonymous Read-the-Photo results.
    path(
        'crush-connect/catalogue/',
        views_crush_connect.crush_connect_catalogue_status,
        name='crush_connect_catalogue_status',
    ),
    # Connect Week: daily cards, 24h review, one-or-none
    # weekly request, recipient inbox. See views_connect_cycle.py.
    path(
        'crush-connect/week/',
        views_connect_cycle.connect_week_home,
        name='connect_week_home',
    ),
    path(
        'crush-connect/week/card/<int:card_id>/answer/',
        views_connect_cycle.connect_week_card_answer,
        name='connect_week_card_answer',
    ),
    path(
        'crush-connect/week/review/',
        views_connect_cycle.connect_week_review,
        name='connect_week_review',
    ),
    path(
        'crush-connect/week/review/<int:card_id>/request/',
        views_connect_cycle.connect_week_request_send,
        name='connect_week_request_send',
    ),
    path(
        'crush-connect/week/inbox/',
        views_connect_cycle.connect_week_inbox,
        name='connect_week_inbox',
    ),
    path(
        'crush-connect/week/inbox/<int:request_id>/respond/',
        views_connect_cycle.connect_week_request_respond,
        name='connect_week_request_respond',
    ),
    # Post-cycle feedback: the member's verdict on the week that just ended,
    # or their dismissal of the prompt. Takes no session id — the view
    # re-resolves the pending cycle itself (views_connect_cycle.py).
    path(
        'crush-connect/week/feedback/',
        views_connect_cycle.connect_week_feedback,
        name='connect_week_feedback',
    ),
    # Connect Cycle temp chat: send/list messages, venue picker + coffee-date
    # plan, post-meeting confirmation loop, 1-click block. See
    # views_connect_chat.py. Opened by connect_week_request_respond's accept
    # (views_connect_cycle.py) via ConnectTemporaryChat.get_or_create.
    path(
        'crush-connect/week/chats/',
        views_connect_chat.connect_week_chats,
        name='connect_week_chats',
    ),
    path(
        'crush-connect/week/chats/<int:chat_id>/',
        views_connect_chat.connect_week_chat_detail,
        name='connect_week_chat_detail',
    ),
    path('crush-connect/week/chats/<int:chat_id>/messages/', views_connect_chat.connect_chat_messages, name='connect_chat_messages'),
    path('crush-connect/week/chats/<int:chat_id>/read/', views_connect_chat.connect_chat_read, name='connect_chat_read'),
    path('crush-connect/summary/', views_connect_chat.connect_summary_json, name='connect_summary_json'),
    path(
        'crush-connect/week/chats/<int:chat_id>/send/',
        views_connect_chat.connect_week_chat_send,
        name='connect_week_chat_send',
    ),
    path(
        'crush-connect/week/chats/<int:chat_id>/venue/',
        views_connect_chat.connect_week_chat_venue,
        name='connect_week_chat_venue',
    ),
    path(
        'crush-connect/week/chats/<int:chat_id>/venue/respond/',
        views_connect_chat.connect_week_chat_venue_respond,
        name='connect_week_chat_venue_respond',
    ),
    path(
        'crush-connect/week/chats/<int:chat_id>/confirm/',
        views_connect_chat.connect_week_chat_confirm,
        name='connect_week_chat_confirm',
    ),
    path(
        'crush-connect/week/chats/<int:chat_id>/block/',
        views_connect_chat.connect_week_chat_block,
        name='connect_week_chat_block',
    ),
    # Peer safety — block / unblock / report another member.
    path(
        'members/<int:user_id>/block/',
        views_moderation.block_user,
        name='block_user',
    ),
    path(
        'members/<int:user_id>/unblock/',
        views_moderation.unblock_user,
        name='unblock_user',
    ),
    path(
        'members/<int:user_id>/report/',
        views_moderation.report_user,
        name='report_user',
    ),
    path(
        'settings/blocked/',
        views_moderation.blocked_members,
        name='blocked_members',
    ),
    # Coach Picks (M7): coach curation hub + member accept/decline.
    path(
        'coach/connect/',
        views_crush_connect.coach_connect_members,
        name='coach_connect_members',
    ),
    path(
        'coach/connect/member/<int:user_id>/',
        views_crush_connect.coach_connect_member,
        name='coach_connect_member',
    ),
    path(
        'crush-connect/pick/<int:pick_id>/respond/',
        views_crush_connect.crush_connect_pick_respond,
        name='crush_connect_pick_respond',
    ),
    # Staff-only Crush Connect Drop card preview (M3).
    path(
        'dev/connect-card/<int:user_id>/',
        views_crush_connect.dev_connect_card_preview,
        name='dev_connect_card_preview',
    ),
    path('membership/', views.membership, name='membership'),

    # Staff-only: membership segmentation concept preview (iteration tool)
    path(
        'dev/membership-concept/',
        views.membership_concept_preview,
        name='membership_concept_preview',
    ),

    # PWA Debug Page (language-prefixed is fine for debug pages)
    # Note: sw-workbox.js, manifest.json, and offline/ are now in urls_crush.py
    # as language-neutral URLs to prevent redirect errors
    path('pwa-debug/', views.pwa_debug_view, name='pwa_debug'),

    # Legal pages
    path('privacy-policy/', views.privacy_policy, name='privacy_policy'),
    path('terms-of-service/', views.terms_of_service, name='terms_of_service'),
    path('support/', views.support, name='support'),
    path('data-deletion/', views.data_deletion_request, name='data_deletion'),
    path('data-deletion/status/', views.data_deletion_status, name='data_deletion_status'),
    path('child-safety-standards/', views.child_safety_standards, name='child_safety_standards'),

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

    # Onboarding flow (7-step journey — see crush_lu/onboarding.py)
    path('signup/', views.signup, name='signup'),
    path('signup/resend-verification/', views.resend_verification_email, name='resend_verification'),
    path('onboarding/', views_profile.onboarding_entry, name='onboarding_entry'),
    path('welcome/', views_profile.welcome_view, name='welcome'),
    path('onboarding/phone/', views_profile.phone_step, name='onboarding_phone'),
    path('onboarding/coach-intro/', views_profile.coach_intro_step, name='onboarding_coach_intro'),
    path('create-profile/', views.create_profile, name='create_profile'),
    path('onboarding/meet-coach/', views_profile.meet_coach_step, name='onboarding_meet_coach'),
    path('profile-submitted/', views.profile_submitted, name='profile_submitted'),
    path('onboarding/screening-call/', views_profile.screening_call_step, name='onboarding_screening_call'),
    path('profile/rejected/', views.profile_rejected, name='profile_rejected'),

    # Pre-screening questionnaire (feature-flagged via PRE_SCREENING_ENABLED)
    path('pre-screening/', views_pre_screening.pre_screening_form, name='pre_screening'),
    path('pre-screening/section/<slug:section_id>/',
         views_pre_screening.pre_screening_save_section,
         name='pre_screening_save_section'),
    path('pre-screening/finalize/',
         views_pre_screening.pre_screening_finalize,
         name='pre_screening_finalize'),

    # Profile step-by-step saving APIs - MOVED to urls_crush.py (language-neutral)
    # These APIs are called from js/alpine/core.js with hardcoded paths:
    # - api/profile/save-step1/, save-step2/, save-step3/
    # - api/profile/complete/
    # - api/profile/progress/
    # - api/profile/social-photos/, import-social-photo/
    # - api/profile/upload-photo/<slot>/, delete-photo/<slot>/

    # Phone verification API endpoints are in urls_crush.py (language-neutral)
    # to avoid i18n prefix issues with hardcoded JavaScript API paths

    # User dashboard
    path('dashboard/', views.dashboard, name='dashboard'),
    # Redirect /profile/ to /dashboard/ (LOGIN_REDIRECT_URL points to /profile/)
    path('profile/', RedirectView.as_view(pattern_name='crush_lu:dashboard'), name='profile'),
    path('profile/edit/', views.edit_profile, name='edit_profile'),
    path('profile/preferences/', views.crush_preferences, name='crush_preferences'),
    path('matches/', RedirectView.as_view(pattern_name='crush_lu:dashboard'), name='matches_list'),

    # Account settings: the monolith is retired (8-08); the old path is a
    # nameless 301 to the edit_profile?section=account drill-down.
    path('account/settings/', views.legacy_account_settings),
    path('account/settings/whatsapp-preference/', views.update_whatsapp_preference, name='update_whatsapp_preference'),
    path('account/set-password/', views.set_password, name='set_password'),
    path('account/disconnect/<int:social_account_id>/', views.disconnect_social_account, name='disconnect_social_account'),
    path('account/link-apple/', views.apple_relay_link_prompt, name='apple_link_prompt'),

    # GDPR & Account Deletion
    # Legacy URL: GET forwards to the profile deletion page; POST keeps the GDPR form contract.
    path('account/delete/', _legacy_account_delete, name='delete_account'),
    path('account/delete-profile/', views.delete_crushlu_profile_view, name='delete_crushlu_profile'),  # Default action
    path(
        "account/take-a-break/", views.take_a_break_view, name="take_a_break"
    ),  # UX Wave 3 - WP13 reversible pause
    path(
        "account/resume-from-break/",
        views.resume_from_break_view,
        name="resume_from_break",
    ),
    path('account/gdpr/', views.gdpr_data_management, name='gdpr_data_management'),  # Full GDPR options
    path('account/gdpr/export/', views.export_user_data, name='export_user_data'),  # GDPR data export
    path('consent/confirm/', views.consent_confirm, name='consent_confirm'),  # Retroactive consent confirmation
    path('account/banned/', views.account_banned, name='account_banned'),  # Banned user info page

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
    path('my-events/', views.my_events, name='my_events'),
    path('events/<int:event_id>/', views.event_detail, name='event_detail'),
    path('events/<int:event_id>/register/', views.event_register, name='event_register'),
    path('events/<int:event_id>/cancel/', views.event_cancel, name='event_cancel'),
    path('events/<int:event_id>/feedback/', views.event_feedback, name='event_feedback'),
    path('events/<int:event_id>/calendar/', views.event_calendar_download, name='event_calendar_download'),
    path('events/<int:event_id>/ticket/', views_ticket.event_ticket, name='event_ticket'),
    path('compatibility/', views_ticket.compatibility_explainer, name='compatibility_explainer'),

    # Speed Dating TV Display (no auth required)
    path('events/<int:event_id>/tv/', views.speed_dating_tv_display, name='speed_dating_tv_display'),

    # Event Activity Voting (Phase 1)
    path('events/<int:event_id>/voting/lobby/', views.event_voting_lobby, name='event_voting_lobby'),
    path('events/<int:event_id>/voting/', views.event_activity_vote, name='event_activity_vote'),
    path('events/<int:event_id>/voting/results/', views.event_voting_results, name='event_voting_results'),

    # Presentations (Phase 2)
    path('events/<int:event_id>/presentations/', views.event_presentations, name='event_presentations'),
    path('events/<int:event_id>/presentations/rate/<int:presenter_id>/', views.submit_presentation_rating, name='submit_presentation_rating'),
    path('events/<int:event_id>/presentations/my-scores/', views.my_presentation_scores, name='my_presentation_scores'),
    # Note: Presentations API moved to urls_crush.py (language-neutral) for JS polling calls

    # Coach Presentation Controls
    path('coach/events/<int:event_id>/presentations/control/', views.coach_presentation_control, name='coach_presentation_control'),
    path('coach/events/<int:event_id>/presentations/advance/', views.coach_advance_presentation, name='coach_advance_presentation'),

    # Live Quiz (WebSocket-based)
    path('events/<int:event_id>/quiz/', views_quiz.quiz_live_view, name='quiz_live'),
    path('events/<int:event_id>/quiz/coach/', views_quiz.quiz_coach_view, name='quiz_coach'),

    # Crush Connect Event Lobby — live "I'd like to meet you" photo grid
    # (spec 2026-07-17). APIs are called from event-lobby.js via {% url %}
    # data attributes, so they can live inside the i18n-prefixed namespace.
    path('events/<int:event_id>/lobby/', views_event_lobby.event_lobby, name='event_lobby'),
    path('events/<int:event_id>/lobby/api/state/', views_event_lobby.lobby_state_api, name='event_lobby_state_api'),
    path('events/<int:event_id>/lobby/api/signal/', views_event_lobby.lobby_signal_api, name='event_lobby_signal_api'),
    path('events/<int:event_id>/lobby/api/confirm/', views_event_lobby.lobby_confirm_api, name='event_lobby_confirm_api'),
    path('events/<int:event_id>/lobby/photo/<str:handle>/', views_event_lobby.lobby_photo, name='event_lobby_photo'),
    # People I've Met — permanent collection + full-profile view (§7.8)
    path('crush-connect/people-ive-met/', views_event_lobby.people_ive_met, name='event_lobby_people'),
    path('crush-connect/people-ive-met/<int:user_id>/remove/', views_event_lobby.event_lobby_remove_person, name='event_lobby_remove_person'),
    path('crush-connect/people-ive-met/<int:user_id>/photo/', views_event_lobby.event_lobby_person_photo, name='event_lobby_person_photo'),
    path('crush-connect/people-ive-met/<int:user_id>/', views_event_lobby.event_lobby_person, name='event_lobby_person'),

    # Crush Cache — GPS + QR scavenger hunt
    path('events/<int:event_id>/cache/', views_crush_cache.cache_lobby, name='cache_lobby'),
    path('events/<int:event_id>/cache/join/', views_crush_cache.cache_join_team, name='cache_join_team'),
    path('events/<int:event_id>/cache/leave/', views_crush_cache.cache_leave_team, name='cache_leave_team'),
    path('events/<int:event_id>/cache/play/', views_crush_cache.cache_play, name='cache_play'),
    path('events/<int:event_id>/cache/scanner/', views_crush_cache.cache_scanner, name='cache_scanner'),
    path('cache/qr/<uuid:token>/', views_crush_cache.cache_qr_scan, name='cache_qr_scan'),
    path('events/<int:event_id>/cache/manual-code/', views_crush_cache.cache_manual_code, name='cache_manual_code'),
    path('events/<int:event_id>/cache/api/position/', views_crush_cache.cache_position_api, name='cache_position_api'),
    path('events/<int:event_id>/cache/api/challenge/<int:challenge_id>/answer/', views_crush_cache.cache_answer_api, name='cache_answer_api'),
    path('events/<int:event_id>/cache/api/challenge/<int:challenge_id>/hint/<int:hint_number>/', views_crush_cache.cache_hint_api, name='cache_hint_api'),
    path('events/<int:event_id>/cache/api/state/', views_crush_cache.cache_state_api, name='cache_state_api'),
    path('events/<int:event_id>/cache/coach/', views_crush_cache.cache_coach_dashboard, name='cache_coach_dashboard'),
    path('events/<int:event_id>/cache/coach/start/', views_crush_cache.cache_coach_start, name='cache_coach_start'),
    path('events/<int:event_id>/cache/coach/finish/', views_crush_cache.cache_coach_finish, name='cache_coach_finish'),
    path('events/<int:event_id>/cache/coach/auto-teams/', views_crush_cache.cache_coach_auto_teams, name='cache_coach_auto_teams'),
    path('events/<int:event_id>/cache/coach/api/state/', views_crush_cache.cache_coach_state_api, name='cache_coach_state_api'),
    path('events/<int:event_id>/cache/coach/qr-sheet/', views_crush_cache.cache_coach_qr_sheet, name='cache_coach_qr_sheet'),

    # Voting Demo/Guided Tour
    path('voting-demo/', views.voting_demo, name='voting_demo'),

    # Note: Event Voting APIs moved to urls_crush.py (language-neutral) for hardcoded JS paths
    # - api/events/<int:event_id>/voting/status/
    # - api/events/<int:event_id>/voting/submit/
    # - api/events/<int:event_id>/voting/results/

    # ============================================================================
    # CRUSH SPARK SYSTEM (soft-removed; every member-facing route redirects.
    # Coach-side spark URLs remain so coaches can clean up any in-flight
    # sparks until the data model is fully retired.)
    # ============================================================================

    # Creation paths -> Crush Connect teaser (public — these can be hit by a
    # logged-out or unverified member off an old bookmark/notification).
    # NOTE: use the _spark_to_crush_connect view (not RedirectView.as_view
    # with pattern_name) because the parameterised routes capture kwargs
    # that would be forwarded to reverse() on the teaser URL and raise
    # NoReverseMatch. See the function docstring above for context.
    path('events/<int:event_id>/spark/request/', _spark_to_crush_connect, name='spark_request'),
    path('events/<int:event_id>/spark/send/<int:user_id>/', _spark_to_crush_connect, name='spark_send_inline'),
    path('events/<int:event_id>/spark/actions/<int:user_id>/', _spark_to_crush_connect, name='spark_actions'),

    # UX Wave 3, finding 5-13 (product answer): /sparks/, /sparks/received/
    # and spark_detail — the three member Sparks pages a signed-in member
    # would actually navigate to — 301-redirect to the Crush Connect hub,
    # unconditionally, regardless of any spark still in flight. Coaches keep
    # coach_spark_list/coach_spark_assign below for in-flight cleanup, which
    # is the orphaning mitigation from Codex P1 #2/#3 on #433: the *member*
    # no longer has a page to track their spark on, but nothing they see is
    # blocked from resolving — the coach-side tooling remains. Kept as named
    # routes (not deleted) so {% url %}/reverse() call sites don't 404/500
    # (AGENTS.md).
    path('sparks/', _spark_to_crush_connect_hub, name='spark_list'),
    path('sparks/received/', _spark_to_crush_connect_hub, name='spark_received'),
    path('sparks/<int:spark_id>/', _spark_to_crush_connect_hub, name='spark_detail'),
    # UX Wave 4 (5-13 product answer): the journey-authoring page follows
    # the other member Sparks pages to the hub. The three event spark URLs
    # above keep their teaser target; coach spark tools below are unchanged.
    path('sparks/<int:spark_id>/create-journey/', _spark_to_crush_connect_hub, name='spark_create_journey'),

    # Coach spark management — left in place for in-flight cleanup.
    path('coach/sparks/', views_crush_spark.coach_spark_list, name='coach_spark_list'),
    path('coach/sparks/<int:spark_id>/assign/', views_crush_spark.coach_spark_assign, name='coach_spark_assign'),

    # Coach dashboard & profile verification
    path('coach/dashboard/', views.coach_dashboard, name='coach_dashboard'),
    path('coach/queue/', views.coach_action_queue, name='coach_action_queue'),
    path('notifications/', views.notifications_page, name='notifications'),
    path('coach/profiles/', views.coach_profiles, name='coach_profiles'),
    path('coach/unverified/', views.coach_unverified_profiles, name='coach_unverified_profiles'),
    path('coach/photo-review/', views_coach_photos.coach_photo_review_deck, name='coach_photo_review_deck'),
    path('coach/photo-review/decide/', views_coach_photos.coach_photo_review_decide, name='coach_photo_review_decide'),
    path('coach/photo-review/undo/', views_coach_photos.coach_photo_review_undo, name='coach_photo_review_undo'),
    path('coach/photo-review/more/', views_coach_photos.coach_photo_review_more, name='coach_photo_review_more'),
    path('coach/members/', views.coach_members, name='coach_members'),
    path('coach/profile/edit/', views.coach_edit_profile, name='coach_edit_profile'),
    path('coach/review/<int:submission_id>/', views.coach_review_profile, name='coach_review_profile'),
    path('coach/review/<int:submission_id>/preview/', views.coach_preview_email, name='coach_preview_email'),
    path('coach/review/<int:submission_id>/call-complete/', views.coach_mark_review_call_complete, name='coach_mark_review_call_complete'),
    path('coach/review/<int:submission_id>/call-attempt/', views.coach_log_failed_call, name='coach_log_failed_call'),
    path('coach/review/<int:submission_id>/sms-sent/', views.coach_log_sms_sent, name='coach_log_sms_sent'),
    path('coach/review/<int:submission_id>/whatsapp-sent/', views.coach_log_whatsapp_sent, name='coach_log_whatsapp_sent'),
    path('coach/review/<int:submission_id>/pre-screening-reminder/', views.coach_send_pre_screening_reminder, name='coach_send_pre_screening_reminder'),
    path('coach/review/<int:submission_id>/offer-booking/', views.coach_offer_self_booking, name='coach_offer_self_booking'),
    path('coach/review/<int:submission_id>/screening-mode/', views.coach_set_screening_mode, name='coach_set_screening_mode'),
    path('coach/sessions/', views.coach_sessions, name='coach_sessions'),
    path('coach/verifications/', views.coach_verification_history, name='coach_verification_history'),
    path('coach/team-stats/', views.coach_team_stats, name='coach_team_stats'),
    path('coach/channel/', views.coach_verification_channel, name='coach_verification_channel'),

    # Hybrid Coach Review System — coach preferences (Phase 2)
    path('coach/settings/', views.coach_settings, name='coach_settings'),
    path(
        'coach/settings/availability/add/',
        views.coach_settings_availability_add,
        name='coach_settings_availability_add',
    ),
    path(
        'coach/settings/availability/<int:index>/remove/',
        views.coach_settings_availability_remove,
        name='coach_settings_availability_remove',
    ),

    # Hybrid Coach Review System — self-booking (Phase 5)
    # Token is the credential; views are unauthenticated.
    path(
        'book/<uuid:booking_token>/',
        views.book_screening,
        name='book_screening',
    ),
    path(
        'book/<uuid:booking_token>/confirm/',
        views.confirm_booking,
        name='confirm_booking',
    ),
    path(
        'book/<uuid:booking_token>/cancel/',
        views.cancel_booking,
        name='cancel_booking',
    ),
    path(
        'book/<uuid:booking_token>/ics/',
        views.download_booking_ics,
        name='download_booking_ics',
    ),

    # Coach invitation management
    path('coach/event/<int:event_id>/invitations/', views.coach_manage_invitations, name='coach_manage_invitations'),

    # NOTE: Step 1 screening call URLs have been REMOVED - screening is now part of review process
    # Old URLs (deprecated):
    # - /coach/screening/ (replaced by /coach/dashboard/ and /coach/review/)
    # - /coach/screening/<id>/complete/ (replaced by /coach/review/<id>/call-complete/)

    # Coach event management
    path('coach/events/', views.coach_event_list, name='coach_event_list'),
    path('coach/events/<int:event_id>/', views.coach_event_detail, name='coach_event_detail'),
    path('coach/events/<int:event_id>/checkin/', views_coach_module.coach_event_checkin, name='coach_event_checkin'),
    path('coach/events/<int:event_id>/sms-invite/', views_coach_module.coach_event_sms_invite, name='coach_event_sms_invite'),
    path('coach/events/<int:event_id>/sms-invite/<int:submission_id>/log/', views_coach_module.coach_log_event_sms_sent, name='coach_log_event_sms_sent'),
    path('coach/events/<int:event_id>/sms-invite/profile/<int:profile_id>/log/', views_coach_module.coach_log_event_sms_sent_by_profile, name='coach_log_event_sms_sent_by_profile'),

    # Coach quiz configuration
    path('coach/events/<int:event_id>/quiz/config/', views_quiz_config.coach_quiz_config, name='coach_quiz_config'),
    path('coach/events/<int:event_id>/quiz/config/create/', views_quiz_config.coach_quiz_create, name='coach_quiz_create'),
    path('coach/events/<int:event_id>/quiz/config/tables/', views_quiz_config.coach_quiz_update_tables, name='coach_quiz_update_tables'),
    path('coach/events/<int:event_id>/quiz/config/round/add/', views_quiz_config.coach_quiz_round_add, name='coach_quiz_round_add'),
    path('coach/events/<int:event_id>/quiz/config/round/<int:round_id>/edit/', views_quiz_config.coach_quiz_round_edit, name='coach_quiz_round_edit'),
    path('coach/events/<int:event_id>/quiz/config/round/<int:round_id>/delete/', views_quiz_config.coach_quiz_round_delete, name='coach_quiz_round_delete'),
    path('coach/events/<int:event_id>/quiz/config/round/<int:round_id>/question/add/', views_quiz_config.coach_quiz_question_add, name='coach_quiz_question_add'),
    path('coach/events/<int:event_id>/quiz/config/question/<int:question_id>/edit/', views_quiz_config.coach_quiz_question_edit, name='coach_quiz_question_edit'),
    path('coach/events/<int:event_id>/quiz/config/question/<int:question_id>/delete/', views_quiz_config.coach_quiz_question_delete, name='coach_quiz_question_delete'),

    # Coach connection management
    path('coach/connections/', views.coach_connections, name='coach_connections'),
    path('coach/connections/<int:connection_id>/', views.coach_connection_review, name='coach_connection_review'),
    # Recipient-side co-coach task: its own constrained surface, because the
    # co-coach must never open the lead itself (spec §5).
    path('coach/crush-outreach/<int:connection_id>/', views.coach_crush_outreach_task, name='coach_crush_outreach_task'),

    # Coach member overview & assignment
    path('coach/member/<int:user_id>/', views.coach_member_overview, name='coach_member_overview'),
    path('coach/member/<int:user_id>/matches/', views.coach_member_matches, name='coach_member_matches'),
    path('coach/member/<int:user_id>/verify/', views.coach_verify_member, name='coach_verify_member'),
    path('coach/match-pairs/', views.coach_match_pairs, name='coach_match_pairs'),
    path('coach/submission/<int:submission_id>/reassign/', views.coach_reassign_submission, name='coach_reassign_submission'),

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
    # Must precede the <str:action> catch-all below or "messages" is routed there.
    path('connections/<int:connection_id>/messages/', views.connection_messages, name='connection_messages'),
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
    path('journey/gift/<str:gift_code>/report/', views_journey_gift.gift_report, name='gift_report'),
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
    # EVENT POLLS
    # ============================================================================

    path('polls/', views.poll_list, name='poll_list'),
    path('polls/<int:poll_id>/', views.poll_detail, name='poll_detail'),
    path('polls/<int:poll_id>/suggest/', views.poll_suggest, name='poll_suggest'),
    # Public theme-night ballot: newest active public poll
    path('themes/', views.theme_board, name='theme_board'),

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

    # Payments (SumUp)
    path('payments/sumup/create-event-checkout/<int:registration_id>/', views_payments.create_sumup_event_checkout, name='sumup_create_event_checkout'),
    path('payments/sumup/create-premium-checkout/<int:membership_id>/', views_payments.create_sumup_premium_checkout, name='sumup_create_premium_checkout'),
    path('payments/sumup/create-donation-checkout/', views_payments.create_sumup_donation_checkout, name='sumup_create_donation_checkout'),
    path('payments/sumup/widget/<str:checkout_id>/', views_payments.sumup_widget_view, name='sumup_widget'),
    path('payments/sumup/widget/<str:checkout_id>/status/', views_payments.sumup_widget_status, name='sumup_widget_status'),
    path('payments/sumup/widget/<str:checkout_id>/failed/', views_payments.report_sumup_widget_failure, name='sumup_widget_failure'),
    path('payments/sumup/return/', views_payments.sumup_payment_return, name='sumup_payment_return'),
    path('payments/sumup/webhook/', views_payments.sumup_webhook, name='sumup_webhook'),
]


from django.conf import settings
if settings.DEBUG:
    from .views_account import dev_simulate_luxid_connect
    urlpatterns += [
        path('dev/luxid-connect/', dev_simulate_luxid_connect, name='dev_simulate_luxid_connect'),
    ]
