"""
Crush.lu views - Dashboard, profile management, and core views.

Split into modules for maintainability:
- views_static.py: Public/static pages (home, about, privacy, etc.)
- views_account.py: Authentication, account settings, GDPR, deletion
- views_events.py: Event listing, registration, cancellation
- views_connections.py: Post-event connections and messaging
- views_coach.py: Coach dashboard, profile review, journey management
- views_voting.py: Event activity voting and presentations
- views_invitations.py: Private invitation system
- views_pwa.py: PWA, service worker, manifest, special experiences
"""

from datetime import timedelta

from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import logging
import json

logger = logging.getLogger(__name__)

AUTOSAVE_ABOUT_FIELDS = {
    "bio",
    "interests",
    "phone_number",
    "location",
    "event_languages",
    "qualities_ids",
    "defects_ids",
    "gender",
    "date_of_birth",
}
AUTOSAVE_PRIVACY_FIELDS = {
    "show_full_name",
    "show_exact_age",
}

from .models import (
    CrushProfile,
    ProfileSubmission,
    MeetupEvent,
    EventRegistration,
    EventConnection,
    CoachPushSubscription,
)
from .forms import (
    CrushProfileForm,
    CrushProfileContactForm,
    CrushProfileEventIdentityForm,
)
from .decorators import crush_login_required, ratelimit
from . import onboarding
from .email_helpers import (
    send_profile_submission_notifications,
)
from .coach_notifications import (
    broadcast_new_submission_to_channel,
)
from .utils.i18n import is_valid_language

# =============================================================================
# Re-export all views from split modules so urls.py continues to work
# with `views.function_name` references unchanged.
# =============================================================================

# Static pages
from .views_static import (  # noqa: F401
    home,
    test_upstair,
    about,
    how_it_works,
    privacy_policy,
    terms_of_service,
    support,
    data_deletion_request,
    child_safety_standards,
    crush_coach,
    crush_connect_teaser,
    membership_concept_preview,
)

# Account & auth
from .views_account import (  # noqa: F401
    oauth_complete,
    facebook_data_deletion_callback,
    parse_facebook_signed_request,
    delete_crushlu_profile_only,
    delete_full_account,
    data_deletion_status,
    account_settings,
    update_email_preferences,
    update_whatsapp_preference,
    api_update_email_preference,
    email_unsubscribe,
    set_password,
    disconnect_social_account,
    delete_crushlu_profile_view,
    gdpr_data_management,
    consent_confirm,
    account_banned,
    referral_redirect,
    signup,
    resend_verification_email,
    export_user_data,
    apple_relay_link_prompt,
    _luxid_connect_url,
)

# Events
from .views_events import (  # noqa: F401
    event_list,
    event_detail,
    event_calendar_download,
    event_register,
    event_cancel,
    my_events,
    event_feedback,
)

# Connections
from .views_connections import (  # noqa: F401
    event_attendees,
    request_connection,
    request_connection_inline,
    connection_actions,
    respond_connection,
    my_connections,
    connection_detail,
    connection_messages,
)

# Coach
from .views_notifications import notifications_page  # noqa: F401

from .views_coach import (  # noqa: F401
    coach_dashboard,
    coach_action_queue,
    coach_mark_review_call_complete,
    coach_log_failed_call,
    coach_log_sms_sent,
    coach_log_whatsapp_sent,
    coach_review_profile,
    coach_send_pre_screening_reminder,
    coach_offer_self_booking,
    coach_set_screening_mode,
    coach_preview_email,
    coach_sessions,
    coach_edit_profile,
    coach_journey_dashboard,
    coach_edit_journey,
    coach_edit_challenge,
    coach_view_user_progress,
    coach_event_list,
    coach_event_detail,
    coach_member_overview,
    coach_reassign_submission,
    coach_verification_history,
    coach_connections,
    coach_connection_review,
    coach_profiles,
    coach_team_stats,
    coach_verification_channel,
    api_coach_claim_submission,
    coach_members,
    coach_member_matches,
    coach_match_pairs,
    # Hybrid Coach Review System (Phase 2)
    coach_settings,
    coach_settings_availability_add,
    coach_settings_availability_remove,
)

# Hybrid Coach Review System — Phase 5 (self-booking flow)
from .views_booking import (  # noqa: F401
    book_screening,
    confirm_booking,
    cancel_booking,
)

# Voting & presentations
from .views_voting import (  # noqa: F401
    event_voting_lobby,
    event_activity_vote,
    event_voting_results,
    event_presentations,
    submit_presentation_rating,
    coach_presentation_control,
    coach_advance_presentation,
    my_presentation_scores,
    get_current_presenter_api,
    voting_demo,
    speed_dating_tv_display,
    speed_dating_tv_display_data,
)

# Invitations
from .views_invitations import (  # noqa: F401
    invitation_landing,
    invitation_accept,
    coach_manage_invitations,
)

# Event Polls
from .views_event_polls import (  # noqa: F401
    poll_list,
    poll_detail,
)

# PWA & special experiences
from .views_pwa import (  # noqa: F401
    special_welcome,
    offline_view,
    service_worker_view,
    manifest_view,
    assetlinks_view,
    apple_app_site_association_view,
    pwa_debug_view,
)

# =============================================================================
# Dashboard, profile management, and remaining core views
# =============================================================================


def _coach_card(coach, subtitle):
    """Build a display dict for a real CrushCoach shown on the dashboard."""
    first = coach.user.first_name or _("Your coach")
    return {
        "kind": "coach",
        "coach_id": coach.id,
        "name": first,
        "subtitle": subtitle,
        "detail": coach.specializations,
        "photo_url": (
            reverse("crush_lu:serve_coach_photo", args=[coach.id])
            if coach.photo
            else None
        ),
        "initial": (first[:1] or "?").upper(),
        "languages": coach.get_spoken_languages_display,
    }


def _build_dashboard_verifier(profile, review_coach, is_premium):
    """Return the adaptive verifier card for the dashboard, or None.

    - Premium members → their real personal (assigned) coach.
    - LuxID-verified → a *virtual* LuxID coach (never a real assigned_coach,
      which would wrongly flip them to premium).
    - Coach-verified (in person) → the verifying/reviewing coach if known.
    - Not yet verified → None. Pending users already get the "Get verified to
      join" hero (_verification_journey.html) plus the pending banner, so the
      verifier card stays out of the way until there's a verifier to highlight.
    """
    if profile.verification_status != "verified":
        # The verifier card highlights *who* verified you — there's nothing to
        # show until verification completes. The pending CTA is owned by the
        # verification hero, so returning a card here just duplicated it.
        return None

    if is_premium and profile.assigned_coach_id:
        return _coach_card(profile.assigned_coach, _("Your personal coach"))

    if profile.verification_method == "luxid":
        return {
            "kind": "luxid",
            "name": "LuxID",
            "subtitle": _("Your digital verifier"),
            "detail": _("Government-grade identity verification"),
        }

    if profile.verification_method in ("coach_event", "premium_coach") and review_coach:
        return _coach_card(review_coach, _("Verified by your coach"))

    return None


def _verification_path_context(profile, user):
    """Detect which verification path a *pending* user has chosen.

    Lets the pending-state UI speak to the user's actual choice instead of a
    generic "verify now". The path is inferred (no extra schema):
      - ``premium`` ⇐ a pending ``PremiumMembership`` exists for the user.
      - ``event``   ⇐ the user holds an active registration (confirmed /
        waitlist) for an upcoming, non-cancelled event — a real commitment.
      - ``""``      ⇐ no path chosen yet (generic hero).

    Returns a context dict (empty-ish unless pending) with ``premium_pending``,
    ``chosen_path`` and ``path_locked`` (premium can only be one coach at a
    time, so it locks the options grid against accidental re-selection).
    """
    from .models import PremiumMembership

    if profile.verification_status != "pending":
        return {"chosen_path": "", "premium_pending": None, "path_locked": False}

    premium_pending = (
        PremiumMembership.objects.filter(user=user, status="pending")
        .select_related("coach__user")
        .first()
    )
    if premium_pending:
        chosen_path = "premium"
    else:
        now = timezone.now()
        candidate_registrations = EventRegistration.objects.filter(
            user=user,
            status__in=("confirmed", "waitlist"),
            event__is_cancelled=False,
            event__date_time__gte=MeetupEvent.live_lookback_cutoff(now),
        ).select_related("event")
        chosen_path = (
            "event"
            if any(
                registration.event.end_time >= now
                for registration in candidate_registrations
            )
            else ""
        )

    return {
        "chosen_path": chosen_path,
        "premium_pending": premium_pending,
        "path_locked": bool(premium_pending),
    }


@crush_login_required
def dashboard(request):
    """User dashboard - always shows the dating profile dashboard.
    Coaches access their coach dashboard via the dedicated Coach tab.
    """
    # Regular user dashboard
    try:
        profile = CrushProfile.objects.select_related("assigned_coach__user").get(
            user=request.user
        )
        # Get latest submission status. Expired submissions are closed-out
        # pre-pivot reviews — the user verifies self-serve, so render them
        # like a user with no submission at all (even when older non-expired
        # rows exist — no falling back to legacy messaging).
        latest_submission = ProfileSubmission.latest_for_profile(profile)

        # Get user's event registrations
        registrations = (
            EventRegistration.objects.filter(user=request.user)
            .select_related("event")
            .order_by("-event__date_time")
        )

        # Get connection count (exclude blocked counterparts so a blocked
        # `shared` pair doesn't keep inflating the dashboard badge).
        from django.db.models import Q as _Q

        from .services.blocking import blocked_user_ids

        _blocked_ids = blocked_user_ids(request.user)
        connection_count = (
            EventConnection.objects.active_for_user(request.user)
            .exclude(
                _Q(requester_id__in=_blocked_ids) | _Q(recipient_id__in=_blocked_ids)
            )
            .count()
        )

        # Get or create referral code for this user's profile
        from .models import ReferralCode
        from .referrals import build_referral_url

        referral_code = ReferralCode.get_or_create_for_profile(profile)
        referral_url = build_referral_url(referral_code.code, request=request)

        coach = latest_submission.coach if latest_submission else None

        # Whether user has attended at least one event (event-history display)
        has_attended_event = EventRegistration.objects.filter(
            user=request.user, status="attended"
        ).exists()

        # Premium = active PremiumMembership (paid or comped). Premium unlocks
        # RECEIVING Crush Connect Drops; LuxID unlocks APPEARING in the
        # catalogue. A coach assigned without a membership (backfill,
        # attendance auto-assign) is a service relationship, not Premium.
        is_premium = profile.has_active_premium

        # LuxID upgrade prompt: verified members without LuxID can link it
        # to enter the Crush Connect catalogue (asymmetric model).
        has_luxid_connected = profile.has_luxid_connected
        luxid_connect_url = None
        if not has_luxid_connected:
            try:
                from allauth.socialaccount.models import SocialApp
                from django.contrib.sites.models import Site

                _site = Site.objects.get_current(request)
                _providers = set(
                    SocialApp.objects.filter(sites=_site).values_list(
                        "provider", flat=True
                    )
                )
                _oidc = SocialApp.objects.filter(
                    provider="openid_connect", provider_id="luxid", sites=_site
                ).first()
                luxid_connect_url = _luxid_connect_url(_providers, oidc_app=_oidc)
            except Exception:
                luxid_connect_url = None

        # Adaptive "verifier" highlight for the dashboard. We never put LuxID in
        # assigned_coach (that is the premium flag) — instead we render it as a
        # virtual coach for display only. Premium shows the real coach; LuxID
        # shows a virtual LuxID coach; coach-verified shows the verifying coach.
        verifier = _build_dashboard_verifier(profile, coach, is_premium)
        # The "Your Crush Coach" section below renders `coach` in full (bio,
        # languages, contact CTA) — drop the verifier card when it would show
        # the same person right above it.
        if verifier and coach and verifier.get("coach_id") == coach.id:
            verifier = None

        # Event Lobby CTA per registration card. The gate checks cost queries,
        # so only evaluate attended registrations still in a live/recap phase
        # (at most one or two rows); every other card renders no lobby CTA.
        from .services.event_lobby import (
            PHASE_CLOSED,
            event_lobby_phase,
            lobby_cta,
        )

        _now = timezone.now()
        for _reg in registrations:
            _reg.can_cancel = bool(
                _reg.event
                and _reg.event.date_time > _now
                and _reg.status in ("confirmed", "waitlist")
            )
            if (
                _reg.status == "attended"
                and _reg.event
                and event_lobby_phase(_reg.event, _now) != PHASE_CLOSED
            ):
                _reg.lobby_cta = lobby_cta(
                    request.user, _reg.event, registration=_reg, now=_now
                )
            else:
                _reg.lobby_cta = None

        # Next current or upcoming published event (drives "attend to unlock" CTA).
        next_event_candidates = MeetupEvent.objects.filter(
            is_published=True,
            is_cancelled=False,
            date_time__gte=MeetupEvent.live_lookback_cutoff(_now),
        ).order_by("date_time")
        next_event = next(
            (event for event in next_event_candidates if event.end_time >= _now),
            None,
        )

        # Time-based greeting (use localtime for correct Luxembourg timezone)
        hour = timezone.localtime().hour
        name = profile.display_name
        if 5 <= hour < 12:
            greeting = _("Good morning, %(name)s") % {"name": name}
        elif 18 <= hour < 24:
            greeting = _("Good evening, %(name)s") % {"name": name}
        else:
            greeting = _("Hey, %(name)s") % {"name": name}

        # Crush Connect opt-in state for the non-premium status strip (same
        # expression as edit_profile). Exclusion is tracked separately so the
        # strip renders nothing for coach-excluded members instead of a "join"
        # CTA that the onboarding gate would silently bounce. Photo-share
        # consent is the last gate between "opted in" and actually discoverable
        # (see services.crush_connect.is_catalogue_eligible) — without it the
        # strip nudges to re-enable sharing instead of claiming "in the Mix".
        connect_membership = getattr(request.user, "crush_connect_membership", None)

        from .views_wallet import _is_apple_wallet_configured

        context = {
            "profile": profile,
            "submission": latest_submission,
            "coach": coach,
            "registrations": registrations,
            "connection_count": connection_count,
            "referral_url": referral_url,
            "has_attended_event": has_attended_event,
            "is_premium": is_premium,
            "has_luxid_connected": has_luxid_connected,
            "luxid_connect_url": luxid_connect_url,
            "connect_onboarded": bool(
                connect_membership and connect_membership.is_onboarded
            ),
            "connect_excluded": bool(
                connect_membership and connect_membership.excluded_by_coach
            ),
            "connect_photo_consent": bool(
                connect_membership and connect_membership.photo_share_consent
            ),
            "verifier": verifier,
            "next_event": next_event,
            "greeting": greeting,
            "apple_wallet_enabled": _is_apple_wallet_configured(),
            **_verification_path_context(profile, request.user),
        }
    except CrushProfile.DoesNotExist:
        messages.warning(request, _("Please complete your profile first."))
        return redirect("crush_lu:create_profile")

    return render(request, "crush_lu/dashboard.html", context)


def _render_create_profile(request, context):
    """Render create_profile.html with the 7-step journey stepper injected.

    The current step is derived from the user's actual profile state so the
    outer rail stays honest. A user landing here without a verified phone
    sees the stepper on step 2, not step 4.
    """
    context.setdefault("profile", None)
    profile = context.get("profile")
    current = onboarding.get_current_step(profile)
    context.update(onboarding.stepper_context(current=current))
    # Gender choices powering the Preferences sub-step (step 4 of the inner
    # wizard). Passed through so the checkbox list can render labels server-
    # side without hard-coding them in the template.
    context.setdefault(
        "profile_gender_choices",
        CrushProfile.GENDER_CHOICES,
    )

    # Event Identity (wizard step 2, 2026 redesign): interest taxonomy, vibe
    # chips and event-vibe choices. Selections come from the profile when it
    # already exists (resume / revision), else empty.
    from django.db.models import Q
    from .models import Trait, Interest

    interests_qs = Interest.objects.filter(is_active=True)
    selected_interest_ids = []
    ask_me_about_ids = []
    selected_event_vibe = ""
    selected_qualities = []
    selected_defects = []
    if profile is not None and profile.pk:
        current_ids = list(profile.interests_new.values_list("pk", flat=True))
        if current_ids:
            interests_qs = Interest.objects.filter(
                Q(is_active=True) | Q(pk__in=current_ids)
            ).distinct()
        selected_interest_ids = current_ids
        ask_me_about_ids = list(profile.ask_me_about or [])
        selected_event_vibe = profile.event_vibe or ""
        selected_qualities = list(profile.qualities.values_list("pk", flat=True))
        selected_defects = list(profile.defects.values_list("pk", flat=True))

    context.setdefault("event_identity_interests", interests_qs)
    context.setdefault("event_identity_selected_ids", selected_interest_ids)
    context.setdefault("event_identity_ask_me_about", ask_me_about_ids)
    context.setdefault("event_identity_vibe", selected_event_vibe)
    context.setdefault("event_vibe_choices", CrushProfile.EVENT_VIBE_CHOICES)
    context.setdefault(
        "qualities_grouped",
        _group_traits_by_category(list(Trait.objects.filter(trait_type="quality"))),
    )
    context.setdefault(
        "defects_grouped",
        _group_traits_by_category(list(Trait.objects.filter(trait_type="defect"))),
    )
    context.setdefault("selected_qualities_json", json.dumps(selected_qualities))
    context.setdefault("selected_defects_json", json.dumps(selected_defects))

    return render(request, "crush_lu/create_profile.html", context)


@crush_login_required
@ratelimit(key="user", rate="10/15m", method="POST", block=True)
def create_profile(request):
    """Profile creation - coaches can also create dating profiles"""

    # Check if user is banned from Crush.lu
    if (
        hasattr(request.user, "data_consent")
        and request.user.data_consent.crushlu_banned
    ):
        messages.error(
            request,
            _(
                "You cannot create a new Crush.lu profile. Your previous profile was permanently deleted."
            ),
        )
        return redirect("crush_lu:account_settings")

    # Journey guard on GET: a user arriving at /create-profile/ without
    # having finished steps 1–3 (direct URL, stale bookmark, old email link)
    # gets bounced into the smart-resume entry so they land on their actual
    # current step. POSTs are left alone — the form-submit path already
    # short-circuits on phone_verified / coach_intro_seen_at further down.
    if request.method == "GET":
        _existing = CrushProfile.objects.filter(user=request.user).first()
        if _existing and onboarding.get_current_step(_existing) < 4:
            return redirect("crush_lu:onboarding_entry")

    # If it's a POST request, process the form submission first
    if request.method == "POST":
        # Get existing profile if it exists (from Steps 1-2 AJAX saves)
        existing_profile = None
        try:
            existing_profile = CrushProfile.objects.get(user=request.user)
            form = CrushProfileForm(
                request.POST, request.FILES, instance=existing_profile
            )
        except CrushProfile.DoesNotExist:
            form = CrushProfileForm(request.POST, request.FILES)

        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user

            # Enforce phone verification before allowing submission. Phone
            # verification lives at step 2 of the onboarding journey, so a
            # form POST without phone_verified is an edge case (JS disabled,
            # direct URL, stale session). Send them to the step-2 page
            # rather than re-rendering the create_profile fallback widget.
            phone_check_profile = existing_profile or profile
            if not phone_check_profile.phone_verified:
                messages.error(
                    request,
                    _(
                        "Please verify your phone number before submitting your profile."
                    ),
                )
                return redirect("crush_lu:onboarding_phone")

            # Journey guard: mirrors the AJAX submit path at
            # views_profile.complete_profile_submission. The JS-disabled form
            # POST must not bypass the step-3 coach-intro acknowledgement.
            if existing_profile and not existing_profile.coach_intro_seen_at:
                logger.info(
                    f"Form-POST submission blocked without coach intro ack: {request.user.email}"
                )
                return redirect("crush_lu:onboarding_coach_intro")

            # Enforce email verification before allowing submission. The
            # journey stepper shows a soft reminder banner from signup
            # onwards; this gate makes sure no profile is submitted without
            # a verified email address. Bot signups never get past this
            # point, and users with typoed emails learn about it before
            # the coach review starts. Social-login users are exempt
            # because their providers verify the email upfront via
            # SOCIALACCOUNT_EMAIL_VERIFIED_PROVIDERS.
            #
            # Fail-closed: any DB/import error here bubbles up rather than
            # silently letting an unverified user through.
            from allauth.account.models import EmailAddress

            email_ok = EmailAddress.objects.filter(
                user=request.user, verified=True
            ).exists()
            if not email_ok:
                messages.error(
                    request,
                    _(
                        "Please verify your email address before submitting your profile. "
                        "Check your inbox for the confirmation link, or resend it from your account settings."
                    ),
                )
                return redirect("account_email")

            # Check if this is first submission or resubmission
            is_first_submission = profile.verification_status not in (
                "pending",
                "verified",
            )

            # Set preferred language from current request language on first submission
            if is_first_submission and hasattr(request, "LANGUAGE_CODE"):
                current_lang = request.LANGUAGE_CODE
                if is_valid_language(current_lang):
                    profile.preferred_language = current_lang
                    logger.debug(
                        f"Set preferred_language to '{current_lang}' for {request.user.email}"
                    )

            # Mark profile as submitted — waiting for LuxId verification
            profile.completion_status = (
                "submitted"  # legacy; remove after migration cleanup
            )
            profile.verification_status = "pending"

            if is_first_submission:
                logger.info(
                    f"First submission - screening call will be done during coach review for {request.user.email}"
                )
            else:
                logger.info(f"Resubmission detected for {request.user.email}")

            try:
                with transaction.atomic():
                    profile.save()
                    logger.info(f"Profile submitted for review: {request.user.email}")

                    # Create profile submission for coach review (PREVENT DUPLICATES)
                    existing_submission = (
                        ProfileSubmission.objects.select_for_update()
                        .filter(profile=profile, status="pending")
                        .first()
                    )

                    # Block resubmission if profile was rejected
                    rejected_submission = (
                        ProfileSubmission.objects.select_for_update()
                        .filter(profile=profile, status="rejected")
                        .first()
                    )
                    if rejected_submission:
                        messages.error(
                            request,
                            _(
                                "Your profile has been rejected and cannot be resubmitted. Please contact support@crush.lu."
                            ),
                        )
                        return redirect("crush_lu:profile_rejected")

                    # An expired latest row means the pivot cleanup closed this
                    # user's coach-review story — never requeue an older legacy
                    # revision row for them; they verify self-serve instead.
                    revision_submission = None
                    if ProfileSubmission.latest_for_profile(profile) is not None:
                        revision_submission = (
                            ProfileSubmission.objects.select_for_update()
                            .filter(profile=profile, status="revision")
                            .first()
                        )

                    is_revision = False
                    submission = None
                    if existing_submission:
                        submission = existing_submission
                        created = False
                        logger.warning(
                            f"⚠️ Existing pending submission found for {request.user.email}"
                        )
                    elif revision_submission:
                        # Revision re-submit: user is in a paid coach flow and
                        # was asked to update their profile. Put it back in the
                        # channel so their coach (or a new one) can re-review.
                        submission = revision_submission
                        submission.status = "pending"
                        submission.coach = None
                        submission.submitted_at = timezone.now()
                        submission.assigned_at = None
                        submission.sla_deadline = None
                        submission.escalated_at = None
                        submission.nudge_sent_at = None
                        submission.fallback_offered_at = None
                        submission.save()
                        created = False
                        is_revision = True
                        logger.info(
                            f"Revision submission updated to pending for {request.user.email}"
                        )
                    else:
                        # Fresh submission — no ProfileSubmission created.
                        # The user verifies via LuxId (free) or purchases a
                        # coach review (paid). Neither path needs a submission
                        # at this point.
                        created = False
                        logger.info(
                            f"Profile submitted (pending LuxId/paid coach) for {request.user.email}"
                        )

            except Exception as e:
                logger.error(
                    f"❌ Transaction failed for {request.user.email}: {e}",
                    exc_info=True,
                )
                messages.error(
                    request,
                    _(
                        "An error occurred while submitting your profile. Please try again."
                    ),
                )
                from .social_photos import get_all_social_photos

                context = {
                    "form": form,
                    "profile": profile,
                    "current_step": "step3",
                    "social_photos": get_all_social_photos(request.user),
                }
                return _render_create_profile(request, context)

            # Broadcast to the verification channel only when a paid coach
            # submission actually exists (revision re-submits).
            if submission and is_revision:
                try:
                    broadcast_new_submission_to_channel(submission)
                    logger.info(
                        f"Channel broadcast sent for submission {submission.id}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to broadcast channel notification: {e}")

            # No "Profile submitted" toast: the profile_submitted page already
            # explains the verification next step, and on the LuxID path this
            # message double-stacked with the "identity verified" banner.
            return redirect("crush_lu:profile_submitted")
        else:
            logger.error(
                f"❌ Profile form validation failed for user {request.user.email}"
            )
            logger.error(f"❌ Form errors: {form.errors.as_json()}")

            for field, errors in form.errors.items():
                for error in errors:
                    if field == "__all__":
                        messages.error(
                            request, _("Form error: %(error)s") % {"error": error}
                        )
                    else:
                        messages.error(
                            request,
                            _("%(field)s: %(error)s")
                            % {
                                "field": field.replace("_", " ").title(),
                                "error": error,
                            },
                        )

            from .social_photos import get_all_social_photos

            context = {
                "form": form,
                # Without the profile, _render_create_profile falls back to
                # None: the Event Identity selections (sourced from the
                # profile, not the bound form — they save through
                # save-step2) come back empty and the journey stepper resets
                # to step 1. A user who trips a validation error on the final
                # submit would see their interests and vibe wiped from the
                # wizard, and re-walking step 2 would then save that empty
                # selection over the real one.
                "profile": existing_profile,
                "current_step": "step3",
                "social_photos": get_all_social_photos(request.user),
            }
            return _render_create_profile(request, context)

    # GET request - check if profile already exists and redirect accordingly
    try:
        profile = CrushProfile.objects.get(user=request.user)

        if profile.verification_status in ("pending", "verified"):
            latest_submission = (
                ProfileSubmission.objects.filter(profile=profile)
                .order_by("-submitted_at")
                .first()
            )

            if latest_submission and latest_submission.status == "rejected":
                return redirect("crush_lu:profile_rejected")

            if latest_submission and latest_submission.status == "recontact_coach":
                return redirect("crush_lu:profile_submitted")

            if latest_submission and latest_submission.status == "revision":
                from .social_photos import get_all_social_photos

                form = CrushProfileForm(instance=profile)
                context = {
                    "form": form,
                    "profile": profile,
                    "current_step": "step1",
                    "social_photos": get_all_social_photos(request.user),
                    "submission": latest_submission,
                    "is_revision": True,
                }
                return _render_create_profile(request, context)

            messages.info(
                request, _("Your profile has been submitted. Check the status below.")
            )
            return redirect("crush_lu:profile_submitted")
        elif profile.verification_status == "incomplete":
            from .social_photos import get_all_social_photos

            form = CrushProfileForm(instance=profile)
            # wizard_step returns an Alpine sub-step number (1/3), or None
            # when every required field is filled — resume those users on
            # the Review step (4), not back at Basic Info.
            step = profile.wizard_step or 4
            return _render_create_profile(
                request,
                {
                    "form": form,
                    "profile": profile,
                    "current_step": step,
                    "social_photos": get_all_social_photos(request.user),
                },
            )
        else:
            return redirect("crush_lu:edit_profile")
    except CrushProfile.DoesNotExist:
        from .social_photos import get_all_social_photos

        form = CrushProfileForm()
        return _render_create_profile(
            request,
            {
                "form": form,
                "profile": None,
                "social_photos": get_all_social_photos(request.user),
            },
        )


def _render_edit_profile_form(request):
    """Internal: Render card-based profile editing for approved profiles.

    Supports section-based navigation:
    - No section param → card list (section overview)
    - ?section=photos|about|privacy|account → section detail
    - HTMX request + section → return section partial only
    - Non-HTMX + section → full page with section pre-expanded
    """
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, _("You need to create a profile first."))
        return redirect("crush_lu:create_profile")

    if not profile.is_approved:
        messages.warning(request, _("Your profile must be approved before editing."))
        return redirect("crush_lu:create_profile")

    section = request.GET.get("section", "")
    valid_sections = (
        "photos",
        "event_identity",
        "privacy",
        "account",
        "about_crushlu",
        "contact",
    )

    # --- Section: Photos ---
    if section == "photos":
        return _edit_section_photos(request, profile)

    # --- Section: Your Event Identity (vibe, interests, ask-me-about,
    #     event vibe, event languages) — merges the retired About You /
    #     Your Personality / Event Preferences cards (spec §5.5). ---
    if section == "event_identity":
        return _edit_section_event_identity(request, profile)

    # --- Section: Contact & Location ---
    if section == "contact":
        return _edit_section_contact(request, profile)

    # The "Ideal Crush" preferences section has moved to the opt-in Crush
    # Connect onboarding (crush_lu:crush_connect_profile_edit) so members who
    # never opt in are not asked to complete it. A stale ?section=preferences
    # link falls through to the default card list below.

    # --- Section: Privacy ---
    if section == "privacy":
        return _edit_section_privacy(request, profile)

    # --- Section: Account (language, theme, notifications, logout) ---
    if section == "account":
        return _edit_section_account(request, profile)

    # --- Section: About Crush.lu (mobile footer content) ---
    if section == "about_crushlu":
        return _edit_section_about_crushlu(request, profile)

    # --- Default: Card-based section list ---
    # Crush Connect card state: unlocked once Connect onboarding is complete,
    # locked (upsell into the teaser) otherwise. Visibility of the card itself
    # is gated in the template via the `crush_connect_visible` filter.
    membership = getattr(request.user, "crush_connect_membership", None)
    context = {
        "profile": profile,
        "section": section,
        "connect_onboarded": bool(membership and membership.is_onboarded),
    }
    return render(request, "crush_lu/edit_profile.html", context)


def _edit_section_photos(request, profile):
    """Handle photos section editing."""
    from .social_photos import get_all_social_photos

    if request.method == "POST":
        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            if request.htmx:
                return render(
                    request,
                    "crush_lu/edit_profile.html#edit_success",
                    {"profile": profile},
                )
            messages.success(request, _("Photos updated!"))
            return redirect("crush_lu:edit_profile")

    form = CrushProfileForm(instance=profile)
    context = {
        "form": form,
        "profile": profile,
        "social_photos": get_all_social_photos(request.user),
        "section": "photos",
    }
    template = "crush_lu/partials/edit_photos.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


def _normalize_form_errors(errors):
    """Return a plain dict of field -> [messages] for JSON responses."""
    normalized = {}
    for field, field_errors in errors.items():
        normalized[field] = [str(error) for error in field_errors]
    return normalized


def _get_profile_form_initial_data(profile):
    # bio/interests are intentionally absent: the free-text write path was
    # retired by the Event Identity redesign (spec §6.2). The structured
    # replacements below round-trip through the "event_identity" autosave section.
    return {
        "phone_number": profile.phone_number or "",
        "date_of_birth": (
            profile.date_of_birth.isoformat() if profile.date_of_birth else ""
        ),
        "gender": profile.gender or "",
        "location": profile.location or "",
        "event_languages": list(profile.event_languages or []),
        "qualities_ids": ",".join(
            str(pk) for pk in profile.qualities.values_list("pk", flat=True)
        ),
        "defects_ids": ",".join(
            str(pk) for pk in profile.defects.values_list("pk", flat=True)
        ),
        "interests_new": [
            str(pk) for pk in profile.interests_new.values_list("pk", flat=True)
        ],
        "ask_me_about": [str(x) for x in (profile.ask_me_about or [])],
        "event_vibe": profile.event_vibe or "",
        "show_full_name": bool(profile.show_full_name),
        "show_exact_age": bool(profile.show_exact_age),
    }


def _merge_autosave_payload(base_data, payload, allowed_fields):
    from django.http import QueryDict

    merged = dict(base_data)
    for field in allowed_fields:
        if field in payload:
            merged[field] = payload[field]
    # Convert to QueryDict so MultipleChoiceField / multi-value fields work
    qd = QueryDict(mutable=True)
    for key, value in merged.items():
        if isinstance(value, list):
            qd.setlist(key, value)
        else:
            qd[key] = value if value is not None else ""
    return qd


def _render_event_identity_section(request, profile, form=None):
    """Context for the merged "Your Event Identity" edit card (spec §5.5)."""
    from .models import Trait

    qualities_grouped = _group_traits_by_category(
        list(Trait.objects.filter(trait_type="quality"))
    )
    defects_grouped = _group_traits_by_category(
        list(Trait.objects.filter(trait_type="defect"))
    )
    selected_qualities = list(profile.qualities.values_list("pk", flat=True))
    selected_defects = list(profile.defects.values_list("pk", flat=True))

    if form is None:
        form = CrushProfileEventIdentityForm(instance=profile)

    return {
        "form": form,
        "profile": profile,
        "section": "event_identity",
        "qualities_grouped": qualities_grouped,
        "defects_grouped": defects_grouped,
        "selected_qualities_json": json.dumps(selected_qualities),
        "selected_defects_json": json.dumps(selected_defects),
        "selected_interest_ids": list(
            profile.interests_new.values_list("pk", flat=True)
        ),
        "ask_me_about_ids": list(profile.ask_me_about or []),
        "selected_event_vibe": profile.event_vibe or "",
    }


def _edit_section_event_identity(request, profile):
    """Handle the merged "Your Event Identity" section (vibe / interests /
    ask-me-about / event vibe / event languages)."""
    if request.method == "POST":
        form = CrushProfileEventIdentityForm(request.POST, instance=profile)
        if form.is_valid():
            updated_profile = form.save()
            from .matching import update_match_scores_for_user

            transaction.on_commit(lambda: update_match_scores_for_user(request.user))
            if request.htmx:
                context = _render_event_identity_section(request, updated_profile)
                return render(
                    request, "crush_lu/partials/edit_event_identity.html", context
                )
            messages.success(request, _("Your Event Identity was updated!"))
            return redirect("crush_lu:edit_profile")
        else:
            if request.htmx:
                context = _render_event_identity_section(request, profile, form=form)
                context["has_errors"] = True
                return render(
                    request, "crush_lu/partials/edit_event_identity.html", context
                )
    else:
        form = CrushProfileEventIdentityForm(instance=profile)

    context = _render_event_identity_section(request, profile, form=form)
    template = "crush_lu/partials/edit_event_identity.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


def _render_contact_section(request, profile, form=None):
    if form is None:
        form = CrushProfileContactForm(instance=profile)

    return {
        "form": form,
        "profile": profile,
        "section": "contact",
    }


def _edit_section_contact(request, profile):
    """Handle contact and location editing."""
    if request.method == "POST":
        form = CrushProfileContactForm(request.POST, instance=profile)
        if form.is_valid():
            updated_profile = form.save()
            from .matching import update_match_scores_for_user
            transaction.on_commit(lambda: update_match_scores_for_user(request.user))
            if request.htmx:
                context = _render_contact_section(request, updated_profile)
                return render(request, "crush_lu/partials/edit_contact.html", context)
            messages.success(request, _("Contact and location details updated!"))
            return redirect("crush_lu:edit_profile")
        else:
            if request.htmx:
                context = _render_contact_section(request, profile, form=form)
                context["has_errors"] = True
                return render(request, "crush_lu/partials/edit_contact.html", context)
    else:
        form = CrushProfileContactForm(instance=profile)

    context = _render_contact_section(request, profile, form=form)
    template = "crush_lu/partials/edit_contact.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


def _render_privacy_section(profile, form=None):
    if form is None:
        form = CrushProfileForm(instance=profile)

    return {
        "form": form,
        "profile": profile,
        "section": "privacy",
    }


def _edit_section_privacy(request, profile):
    """Handle privacy settings section."""
    if request.method == "POST":
        form = CrushProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            if request.htmx:
                context = _render_privacy_section(profile)
                return render(request, "crush_lu/partials/edit_privacy.html", context)
            messages.success(request, _("Privacy settings updated!"))
            return redirect("crush_lu:edit_profile")
    else:
        form = CrushProfileForm(instance=profile)

    context = _render_privacy_section(profile, form=form)
    template = "crush_lu/partials/edit_privacy.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


def _edit_section_account(request, profile):
    """Handle account settings section with sub-sections."""
    sub = request.GET.get("sub", "")

    if sub == "settings":
        return _edit_sub_account_settings(request, profile)
    if sub == "notifications":
        return _edit_sub_account_notifications(request, profile)
    if sub == "danger":
        return _edit_sub_account_danger(request, profile)

    # Default: show card list
    context = {
        "profile": profile,
        "section": "account",
    }
    template = "crush_lu/partials/edit_account.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


def _edit_section_about_crushlu(request, profile):
    """Render the About Crush.lu section (mobile footer content)."""
    context = {
        "profile": profile,
        "section": "about_crushlu",
    }
    template = "crush_lu/partials/edit_about_crushlu.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


def _edit_sub_account_settings(request, profile):
    """Handle account settings sub-section (account info, linked accounts, password)."""
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site
    from .social_photos import get_all_social_photos

    CRUSH_SOCIAL_PROVIDERS = [
        "google",
        "facebook",
        "microsoft",
        "apple",
        "luxid",
        "openid_connect",
    ]

    connected_providers = set(
        request.user.socialaccount_set.values_list("provider", flat=True)
    )
    crush_social_accounts = request.user.socialaccount_set.filter(
        provider__in=CRUSH_SOCIAL_PROVIDERS
    )

    # Annotate display email for each social account
    for account in crush_social_accounts:
        if account.provider == "microsoft":
            account.display_email = (
                account.extra_data.get("mail")
                or account.extra_data.get("userPrincipalName")
                or account.extra_data.get("email")
                or ""
            )
        elif account.provider in ("luxid", "openid_connect"):
            _claims = (
                account.extra_data.get("userinfo")
                or account.extra_data.get("id_token")
                or account.extra_data
            )
            account.display_email = (
                _claims.get("email", "") if isinstance(_claims, dict) else ""
            )
        else:
            account.display_email = account.extra_data.get("email", "")

    social_photos = get_all_social_photos(request.user)

    try:
        current_site = Site.objects.get_current(request)
        available_providers = set(
            SocialApp.objects.filter(sites=current_site).values_list(
                "provider", flat=True
            )
        )
    except Exception:
        available_providers = set()

    oidc_app = None
    if "openid_connect" in available_providers:
        try:
            oidc_app = SocialApp.objects.filter(
                provider="openid_connect", provider_id="luxid", sites=current_site
            ).first()
        except Exception:
            pass

    # Scope the openid_connect connected check to the LuxID-specific OIDC app.
    # SocialAccount has no app FK in allauth 65.x; route through SocialToken which does.
    _luxid_oidc_acct_ids: set = set()
    if oidc_app is not None and "openid_connect" in connected_providers:
        try:
            from allauth.socialaccount.models import SocialToken

            _luxid_oidc_acct_ids = set(
                SocialToken.objects.filter(
                    account__user=request.user,
                    account__provider="openid_connect",
                    app=oidc_app,
                ).values_list("account_id", flat=True)
            )
        except Exception:
            pass
    luxid_connected = "luxid" in connected_providers or bool(_luxid_oidc_acct_ids)

    # Annotate is_luxid on each account so templates can brand correctly without
    # treating every openid_connect account as LuxID.
    for account in crush_social_accounts:
        account.is_luxid = (
            account.provider == "luxid" or account.pk in _luxid_oidc_acct_ids
        )

    context = {
        "profile": profile,
        "section": "account",
        "google_connected": "google" in connected_providers,
        "facebook_connected": "facebook" in connected_providers,
        "microsoft_connected": "microsoft" in connected_providers,
        "apple_connected": "apple" in connected_providers,
        "luxid_connected": luxid_connected,
        "google_available": "google" in available_providers,
        "facebook_available": "facebook" in available_providers,
        "microsoft_available": "microsoft" in available_providers,
        "apple_available": "apple" in available_providers,
        "luxid_available": "luxid" in available_providers or oidc_app is not None,
        "luxid_connect_url": _luxid_connect_url(available_providers, oidc_app=oidc_app),
        "crush_social_accounts": crush_social_accounts,
        "social_photos": social_photos,
        "is_apple_relay_user": bool(
            request.user.email
            and request.user.email.endswith("@privaterelay.appleid.com")
        ),
        "show_apple_link_banner": request.GET.get("apple_link") == "1",
    }
    template = "crush_lu/partials/edit_account_settings.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


def _edit_sub_account_notifications(request, profile):
    """Handle notifications sub-section (email prefs, push, coach push).
    Email preferences are saved via the JSON API endpoint (api_update_email_preference).
    """
    import json
    from .models import EmailPreference, PushSubscription

    def get_device_type(device_name):
        mobile_devices = ["Android Chrome", "iPhone Safari"]
        return "mobile" if device_name in mobile_devices else "desktop"

    email_prefs = EmailPreference.get_or_create_for_user(request.user)

    push_subscriptions = []
    push_subscriptions_json = "[]"
    try:
        subs = PushSubscription.objects.filter(user=request.user, enabled=True)
        for sub in subs:
            push_subscriptions.append(
                {
                    "id": sub.id,
                    "endpoint": sub.endpoint,
                    "device_fingerprint": sub.device_fingerprint or "",
                    "device_name": sub.device_name or "Unknown Device",
                    "device_type": get_device_type(sub.device_name or ""),
                    "last_used_at": sub.last_used_at,
                    "notify_new_messages": sub.notify_new_messages,
                    "notify_event_reminders": sub.notify_event_reminders,
                    "notify_new_connections": sub.notify_new_connections,
                    "notify_profile_updates": sub.notify_profile_updates,
                }
            )
        push_subscriptions_json = json.dumps(push_subscriptions, default=str)
    except Exception:
        logger.warning(
            "Failed to fetch push subscriptions for user %s",
            request.user.id,
            exc_info=True,
        )

    is_coach = False
    coach_push_subscriptions = []
    coach_push_subscriptions_json = "[]"
    try:
        if hasattr(request.user, "crushcoach") and request.user.crushcoach.is_active:
            is_coach = True
            coach = request.user.crushcoach
            coach_subs = CoachPushSubscription.objects.filter(coach=coach, enabled=True)
            for sub in coach_subs:
                coach_push_subscriptions.append(
                    {
                        "id": sub.id,
                        "endpoint": sub.endpoint,
                        "device_fingerprint": sub.device_fingerprint or "",
                        "device_name": sub.device_name or "Unknown Device",
                        "device_type": get_device_type(sub.device_name or ""),
                        "last_used_at": sub.last_used_at,
                        "notify_new_submissions": sub.notify_new_submissions,
                        "notify_screening_reminders": sub.notify_screening_reminders,
                        "notify_user_responses": sub.notify_user_responses,
                        "notify_system_alerts": sub.notify_system_alerts,
                    }
                )
        coach_push_subscriptions_json = json.dumps(
            coach_push_subscriptions, default=str
        )
    except Exception:
        logger.warning(
            "Failed to fetch coach push subscriptions for user %s",
            request.user.id,
            exc_info=True,
        )

    context = {
        "profile": profile,
        "section": "account",
        "email_prefs": email_prefs,
        "push_subscriptions": push_subscriptions,
        "push_subscriptions_json": push_subscriptions_json,
        "is_coach": is_coach,
        "coach_push_subscriptions": coach_push_subscriptions,
        "coach_push_subscriptions_json": coach_push_subscriptions_json,
    }
    template = "crush_lu/partials/edit_account_notifications.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


def _edit_sub_account_danger(request, profile):
    """Handle danger zone sub-section (privacy, delete, logout)."""
    context = {
        "profile": profile,
        "section": "account",
    }
    template = "crush_lu/partials/edit_account_danger.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


def _profile_autosave_values(profile):
    """Build a serializable snapshot of editable profile fields for autosave
    responses. bio/interests are intentionally absent — the free-text write path
    was retired by the Event Identity redesign (spec §6.2)."""
    return {
        "phone_number": profile.phone_number or "",
        "location": profile.location or "",
        "event_languages": list(profile.event_languages or []),
        "qualities_ids": list(profile.qualities.values_list("pk", flat=True)),
        "defects_ids": list(profile.defects.values_list("pk", flat=True)),
        "interests_new": list(profile.interests_new.values_list("pk", flat=True)),
        "ask_me_about": list(profile.ask_me_about or []),
        "event_vibe": profile.event_vibe or "",
    }


@crush_login_required
@require_http_methods(["POST"])
def api_profile_settings_autosave(request):
    """Auto-save approved profile settings sections via JSON."""
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "Profile not found."}, status=404
        )

    if not profile.is_approved:
        return JsonResponse(
            {
                "success": False,
                "error": "Auto-save is only available for approved profiles.",
            },
            status=403,
        )

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON."}, status=400)

    section = payload.get("section", "")

    # --- Map each section to its specialised form + allowed fields ---
    # The "about" (bio/interests), "traits", and "event_languages" sections were
    # merged into "event_identity" by the 2026 redesign (spec §6.2/§8.3). A
    # direct {"section": "about", ...} POST now falls through to "Invalid
    # section." below and writes nothing.
    _section_config = {
        "event_identity": (
            CrushProfileEventIdentityForm,
            {
                "interests_new",
                "ask_me_about",
                "event_vibe",
                "qualities_ids",
                "defects_ids",
                "event_languages",
            },
        ),
        "contact": (
            CrushProfileContactForm,
            {"phone_number", "date_of_birth", "gender", "location"},
        ),
    }

    if section in _section_config:
        form_class, allowed_fields = _section_config[section]
        data = _merge_autosave_payload(
            _get_profile_form_initial_data(profile),
            payload,
            allowed_fields,
        )
        form = form_class(data, instance=profile)
        if form.is_valid():
            updated_profile = form.save()
            from .matching import update_match_scores_for_user

            transaction.on_commit(lambda: update_match_scores_for_user(request.user))
            return JsonResponse(
                {
                    "success": True,
                    "saved_fields": sorted(allowed_fields & set(payload.keys())),
                    "values": _profile_autosave_values(updated_profile),
                }
            )

        return JsonResponse(
            {
                "success": False,
                "errors": _normalize_form_errors(form.errors),
                "non_field_errors": [str(error) for error in form.non_field_errors()],
            },
            status=400,
        )

    if section == "privacy":
        data = _merge_autosave_payload(
            _get_profile_form_initial_data(profile),
            payload,
            AUTOSAVE_PRIVACY_FIELDS,
        )
        form = CrushProfileForm(data, instance=profile)
        if form.is_valid():
            updated_profile = form.save()
            return JsonResponse(
                {
                    "success": True,
                    "saved_fields": sorted(
                        AUTOSAVE_PRIVACY_FIELDS & set(payload.keys())
                    ),
                    "values": {
                        "show_full_name": bool(updated_profile.show_full_name),
                        "show_exact_age": bool(updated_profile.show_exact_age),
                    },
                }
            )

        return JsonResponse(
            {
                "success": False,
                "errors": _normalize_form_errors(form.errors),
                "non_field_errors": [str(error) for error in form.non_field_errors()],
            },
            status=400,
        )

    return JsonResponse({"success": False, "error": "Invalid section."}, status=400)


@crush_login_required
def edit_profile(request):
    """Edit existing profile - routes to appropriate edit flow"""
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, _("You need to create a profile first."))
        return redirect("crush_lu:create_profile")

    # 1. If profile is verified → use simple single-page edit
    if profile.verification_status == "verified":
        return _render_edit_profile_form(request)

    # 2. If profile is pending (submitted, awaiting verification) → send to the
    #    get-verified page. New users have no ProfileSubmission; only legacy
    #    in-flight submissions still carry revision/rejected/recontact states.
    if profile.verification_status == "pending":
        submission = (
            ProfileSubmission.objects.filter(profile=profile)
            .order_by("-submitted_at")
            .first()
        )
        if submission and submission.status == "rejected":
            return redirect("crush_lu:profile_rejected")
        if submission and submission.status == "revision":
            messages.warning(
                request,
                _(
                    "Your profile needs updates. Please review the coach feedback below."
                ),
            )
            return redirect("crush_lu:create_profile")
        # Generic pending (new flow) or legacy pending/recontact → status page.
        return redirect("crush_lu:profile_submitted")

    # 3. Profile is incomplete → redirect to create_profile
    if profile.verification_status == "incomplete":
        messages.info(request, _("Please complete your profile to continue."))
        return redirect("crush_lu:create_profile")

    # 4. Profile is rejected → terminal. Do not let the fallback form below
    #    flip it back to "pending" (which would also re-open the pending-only
    #    LuxID self-verify path). Send the user to the rejection page.
    if profile.verification_status == "rejected":
        return redirect("crush_lu:profile_rejected")

    # 5. Default: Use multi-step form for any other edge cases
    if request.method == "POST":
        # Mirror the email-verification gate from create_profile and
        # complete_profile_submission so this edge-case resubmission path
        # can't bypass the "verified email before submission" policy.
        # Fail-closed: any DB/import error bubbles up rather than letting
        # an unverified user through.
        from allauth.account.models import EmailAddress

        email_ok = EmailAddress.objects.filter(
            user=request.user, verified=True
        ).exists()
        if not email_ok:
            messages.error(
                request,
                _(
                    "Please verify your email address before submitting your profile. "
                    "Check your inbox for the confirmation link, or resend it from your account settings."
                ),
            )
            return redirect("account_email")

        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.completion_status = (
                "submitted"  # legacy; remove after migration cleanup
            )
            profile.verification_status = "pending"
            profile.save()

            # Look through expired (closed-out pre-pivot) submissions so a
            # returning user gets a fresh pending review instead of the
            # expired row being silently reused.
            submission, created = ProfileSubmission.objects.exclude(
                status="expired"
            ).get_or_create(
                profile=profile, defaults={"status": "pending", "coach": None}
            )
            if created:
                try:
                    broadcast_new_submission_to_channel(submission)
                    logger.info(
                        f"Channel broadcast sent for submission {submission.id}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to broadcast channel notification: {e}")

                send_profile_submission_notifications(
                    submission,
                    request,
                    add_message_func=lambda msg: messages.warning(request, msg),
                )

            messages.success(request, _("Profile submitted for review!"))
            return redirect("crush_lu:profile_submitted")
    else:
        form = CrushProfileForm(instance=profile)

    latest_submission = ProfileSubmission.latest_for_profile(profile)

    current_step_to_show = None
    if latest_submission and latest_submission.status in [
        "rejected",
        "revision",
        "recontact_coach",
    ]:
        current_step_to_show = None
    elif profile.verification_status in ("pending", "verified"):
        current_step_to_show = None
    elif profile.verification_status == "incomplete":
        # wizard_step returns an Alpine sub-step number (1/3) for
        # data-initial-step, or None when the profile is ready to submit —
        # in that case resume on the Review step (4).
        current_step_to_show = profile.wizard_step or 4
    else:
        current_step_to_show = None

    context = {
        "form": form,
        "profile": profile,
        "is_editing": True,
        "current_step": current_step_to_show,
        "submission": latest_submission,
    }
    return render(request, "crush_lu/create_profile.html", context)


_TRAIT_CATEGORY_META = {
    "social": "crush_lu/includes/ghost-story-chatting-pair.html",
    "emotional": "crush_lu/includes/ghost-story-inlove.html",
    "mindset": "crush_lu/includes/ghost-story-blushing-reader.html",
    "relational": "crush_lu/includes/ghost-story-cuddling.html",
    "energy": "crush_lu/includes/ghost-story-running.html",
}


def _group_traits_by_category(qs):
    """Group a trait queryset by category with ghost artwork include paths."""
    from .models.matching import TraitCategory

    grouped = []
    for cat_value, cat_label in TraitCategory.choices:
        traits = [t for t in qs if t.category == cat_value]
        if traits:
            grouped.append(
                {
                    "key": cat_value,
                    "label": cat_label,
                    "ghost_include": _TRAIT_CATEGORY_META.get(cat_value, ""),
                    "traits": traits,
                }
            )
    return grouped


@crush_login_required
def crush_preferences(request):
    """The standalone "Ideal Crush" preferences page has been retired — the
    preferences now live in the opt-in Crush Connect onboarding. Kept as a
    redirect so old links/bookmarks don't 404."""

    return redirect("crush_lu:dashboard")


@crush_login_required
def matches_list(request):
    """Page showing compatible matches sorted by score."""
    from .matching import get_matches_for_user, get_score_display
    from django.core.paginator import Paginator

    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, _("You need to create a profile first."))
        return redirect("crush_lu:create_profile")

    if not profile.is_approved:
        messages.warning(
            request, _("Your profile must be approved before viewing matches.")
        )
        return redirect("crush_lu:dashboard")

    has_traits = profile.sought_qualities.exists()
    matches = []

    if has_traits:
        match_scores = get_matches_for_user(request.user)

        for ms in match_scores:
            other_user = ms.user_b if ms.user_a == request.user else ms.user_a
            try:
                other_profile = CrushProfile.objects.get(
                    user=other_user, verification_status="verified", is_active=True
                )
            except CrushProfile.DoesNotExist:
                continue

            # Gender filter (age filtering handled by hard filter in matching.py)
            if (
                profile.preferred_genders
                and other_profile.gender not in profile.preferred_genders
            ):
                continue

            score_display = get_score_display(ms.score_final)
            if score_display:
                matches.append(
                    {
                        "profile": other_profile,
                        "score": ms.score_final,
                        "score_percent": int(ms.score_final * 100),
                        "display": score_display,
                    }
                )

    paginator = Paginator(matches, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "crush_lu/matches.html",
        {
            "page_obj": page_obj,
            "matches": page_obj.object_list,
            "has_traits": has_traits,
            "profile": profile,
        },
    )


@require_http_methods(["GET"])
@crush_login_required
def api_match_list(request):
    """JSON API for match list (language-neutral)."""
    from .matching import get_matches_for_user, get_score_display

    try:
        profile = CrushProfile.objects.get(
            user=request.user, verification_status="verified"
        )
    except CrushProfile.DoesNotExist:
        return JsonResponse({"error": "Profile not found or not approved"}, status=404)

    page = int(request.GET.get("page", 1))
    per_page = min(int(request.GET.get("per_page", 20)), 50)

    match_scores = get_matches_for_user(request.user)
    results = []

    for ms in match_scores:
        other_user = ms.user_b if ms.user_a == request.user else ms.user_a
        try:
            other_profile = CrushProfile.objects.get(
                user=other_user, verification_status="verified", is_active=True
            )
        except CrushProfile.DoesNotExist:
            continue

        display = get_score_display(ms.score_final)
        if display:
            results.append(
                {
                    "user_id": other_user.pk,
                    "display_name": other_profile.display_name,
                    "age": other_profile.age if other_profile.show_exact_age else None,
                    "score_percent": int(ms.score_final * 100),
                    "score_label": display["label"],
                }
            )

    # Paginate
    start = (page - 1) * per_page
    end = start + per_page
    paginated = results[start:end]

    return JsonResponse(
        {
            "matches": paginated,
            "total": len(results),
            "page": page,
            "per_page": per_page,
        }
    )


@require_http_methods(["GET"])
@crush_login_required
def api_match_score(request, user_id):
    """JSON API for score detail between authenticated user and another user."""
    from .matching import compute_match_score, get_score_display

    try:
        my_profile = CrushProfile.objects.get(
            user=request.user, verification_status="verified"
        )
        other_profile = CrushProfile.objects.get(
            user_id=user_id, verification_status="verified", is_active=True
        )
    except CrushProfile.DoesNotExist:
        return JsonResponse({"error": "Profile not found"}, status=404)

    scores = compute_match_score(my_profile, other_profile)
    display = get_score_display(scores["score_final"])

    return JsonResponse(
        {
            "score_final": scores["score_final"],
            "score_percent": int(scores["score_final"] * 100),
            "score_qualities": scores["score_qualities"],
            "score_zodiac_west": scores["score_zodiac_west"],
            "score_zodiac_cn": scores["score_zodiac_cn"],
            "label": display["label"] if display else None,
            "color": display["hex"] if display else None,
        }
    )


@crush_login_required
def profile_submitted(request):
    """Confirmation page after profile submission with real-time stats."""
    from django.db.models import Avg, ExpressionWrapper, F, DurationField
    from django.db.models.functions import Extract
    from .utils.formatting import format_review_estimate

    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        return redirect("crush_lu:create_profile")

    # Redirect away if profile is not in the pending/verification funnel
    if profile.verification_status == "incomplete":
        return redirect("crush_lu:create_profile")
    if profile.verification_status == "verified":
        return redirect("crush_lu:dashboard")

    # Submission only exists for the paid coach path or revision re-submits.
    # For free LuxId verification, submission is None. An expired latest
    # submission (closed-out pre-pivot review) renders the same way — the
    # self-serve "Verify your identity" hero, not the old coach-review
    # messaging — and must not fall back to an older non-expired row.
    submission = ProfileSubmission.latest_for_profile(profile)

    now = timezone.now()

    # --- LuxID CTA — always computed when profile is pending ---
    has_luxid_account = False
    luxid_connect_url = None
    try:
        from allauth.socialaccount.models import SocialApp, SocialToken
        from django.contrib.sites.models import Site

        has_luxid_account = request.user.socialaccount_set.filter(
            provider="luxid"
        ).exists()

        _current_site = Site.objects.get_current(request)
        _available_providers = set(
            SocialApp.objects.filter(sites=_current_site).values_list(
                "provider", flat=True
            )
        )
        _oidc_app = SocialApp.objects.filter(
            provider="openid_connect", provider_id="luxid", sites=_current_site
        ).first()

        if not has_luxid_account and _oidc_app is not None:
            has_luxid_account = SocialToken.objects.filter(
                account__user=request.user,
                account__provider="openid_connect",
                app=_oidc_app,
            ).exists()

        if not has_luxid_account:
            luxid_connect_url = _luxid_connect_url(
                _available_providers, oidc_app=_oidc_app
            )
    except Exception:
        # The CTA is optional decoration — a lookup failure must not 500
        # the status page, but it should be visible in the logs.
        logger.exception("LuxID CTA lookup failed for user pk=%s", request.user.pk)

    if has_luxid_account and profile.verification_status == "pending":
        # Lazy fix-up: user already has LuxId but their submitted profile
        # is still awaiting verification. This can happen if LuxId was
        # connected before the submission existed (old flow) or via an edge
        # case, so the social_account_added signal never fired. Verify
        # directly now. Only "pending" profiles qualify — "rejected" users
        # must not be able to self-clear by reloading this page.
        from .signals import _execute_luxid_direct_verify

        try:
            _execute_luxid_direct_verify(request.user, profile, submission, request)
        except Exception:
            # Unlike the CTA above, a failure here leaves the user stuck on
            # "pending" — never swallow it silently.
            logger.exception(
                "[LUXID-VERIFY] Lazy fix-up failed for profile pk=%s", profile.pk
            )
        else:
            profile.refresh_from_db()
            if profile.verification_status == "verified":
                return redirect("crush_lu:dashboard")

    # --- Submission-dependent context (paid coach / revision path only) ---
    coach_contact_phone = ""
    coach_phone_available = False
    estimated_review_display = None
    is_coach_specific_estimate = False
    queue_position = 0
    total_coach_pending = 0
    total_channel = 0
    hours_waiting = 0
    wait_status = "fresh"
    progress_percent = 0
    hybrid_user_state = None
    recontact_days_remaining = None
    has_booking_token = False
    has_candidate_note = False
    pre_screening_visible = False
    pre_screening_submitted = False

    if submission:
        if submission.coach and submission.coach.phone_number:
            coach_contact_phone = submission.coach.whatsapp_number
            coach_phone_available = True
        else:
            from .models import CrushSiteConfig

            try:
                config = CrushSiteConfig.get_config()
                coach_contact_phone = config.whatsapp_number
                coach_phone_available = config.whatsapp_enabled and bool(
                    coach_contact_phone
                )
            except Exception:
                pass

        reviewed_qs = ProfileSubmission.objects.filter(
            reviewed_at__isnull=False, submitted_at__isnull=False
        )
        avg_review_hours = 0
        if reviewed_qs.count() >= 5:
            avg_result = reviewed_qs.annotate(
                review_duration=ExpressionWrapper(
                    F("reviewed_at") - F("submitted_at"),
                    output_field=DurationField(),
                )
            ).aggregate(avg_seconds=Avg(Extract("review_duration", "epoch")))
            avg_review_hours = max(0, (avg_result["avg_seconds"] or 0) / 3600)

        coach_avg_review_hours = None
        if submission.coach:
            coach_reviewed = reviewed_qs.filter(coach=submission.coach)
            if coach_reviewed.count() >= 3:
                coach_result = coach_reviewed.annotate(
                    review_duration=ExpressionWrapper(
                        F("reviewed_at") - F("submitted_at"),
                        output_field=DurationField(),
                    )
                ).aggregate(avg_seconds=Avg(Extract("review_duration", "epoch")))
                coach_avg_review_hours = max(
                    0, (coach_result["avg_seconds"] or 0) / 3600
                )

        effective_avg = coach_avg_review_hours or avg_review_hours
        estimated_review_display = format_review_estimate(effective_avg)
        is_coach_specific_estimate = coach_avg_review_hours is not None

        if submission.coach:
            queue_position = ProfileSubmission.objects.filter(
                status="pending",
                coach=submission.coach,
                submitted_at__lt=submission.submitted_at,
            ).count()
            total_coach_pending = submission.coach.get_active_reviews_count()
        else:
            queue_position = ProfileSubmission.objects.filter(
                status="pending",
                coach__isnull=True,
                submitted_at__lt=submission.submitted_at,
            ).count()
            total_channel = ProfileSubmission.objects.filter(
                status="pending", coach__isnull=True
            ).count()

        hours_waiting = (now - submission.submitted_at).total_seconds() / 3600
        wait_status = (
            "fresh"
            if hours_waiting < 6
            else (
                "normal"
                if hours_waiting < 24
                else "extended" if hours_waiting < 48 else "long"
            )
        )
        if effective_avg > 0:
            progress_percent = min(95, int((hours_waiting / effective_avg) * 100))
        else:
            progress_percent = min(95, int(hours_waiting / 36 * 100))

        hybrid_user_state = submission.hybrid_user_state
        recontact_days_remaining = submission.recontact_days_remaining
        has_booking_token = bool(submission.booking_token)
        has_candidate_note = bool(submission.candidate_note)

        from django.conf import settings as _settings

        pre_screening_enabled = getattr(_settings, "PRE_SCREENING_ENABLED", False)
        pre_screening_submitted = submission.pre_screening_submitted_at is not None
        pre_screening_visible = (
            pre_screening_enabled
            and submission.status == "pending"
            and not submission.review_call_completed
        )

    # Next current or upcoming event teaser.
    next_event_candidates = MeetupEvent.objects.filter(
        is_published=True,
        is_cancelled=False,
        date_time__gte=MeetupEvent.live_lookback_cutoff(now),
    ).order_by("date_time")
    next_event = next(
        (event for event in next_event_candidates if event.end_time >= now),
        None,
    )

    context = {
        "profile": profile,
        "submission": submission,
        "has_luxid_account": has_luxid_account,
        "luxid_connect_url": luxid_connect_url,
        # Submission-dependent (None when no submission)
        "coach_contact_phone": coach_contact_phone,
        "coach_phone_available": coach_phone_available,
        "estimated_review_display": estimated_review_display,
        "is_coach_specific_estimate": is_coach_specific_estimate,
        "queue_position": queue_position,
        "total_coach_pending": total_coach_pending,
        "total_channel": total_channel,
        "hours_waiting": round(hours_waiting, 1),
        "wait_status": wait_status,
        "progress_percent": progress_percent,
        "next_event": next_event,
        "has_candidate_note": has_candidate_note,
        "pre_screening_visible": pre_screening_visible,
        "pre_screening_submitted": pre_screening_submitted,
        "hybrid_user_state": hybrid_user_state,
        "recontact_days_remaining": recontact_days_remaining,
        "has_booking_token": has_booking_token,
        **_verification_path_context(profile, request.user),
    }

    # Final journey step: "Get verified". Verified users are redirected to the
    # dashboard above, so this page always renders at the get-verified step.
    context.update(onboarding.stepper_context(current=5))
    return render(request, "crush_lu/profile_submitted.html", context)


@crush_login_required
@require_http_methods(["GET"])
def api_submission_status(request):
    """JSON endpoint for polling submission status changes."""
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        return JsonResponse({"error": "No submission found"}, status=404)
    submission = ProfileSubmission.latest_for_profile(profile)
    if submission is None:
        return JsonResponse({"error": "No submission found"}, status=404)

    now = timezone.now()
    hours_waiting = (now - submission.submitted_at).total_seconds() / 3600

    if hours_waiting < 6:
        wait_status = "fresh"
    elif hours_waiting < 24:
        wait_status = "normal"
    elif hours_waiting < 48:
        wait_status = "extended"
    else:
        wait_status = "long"

    queue_position = 0
    if submission.coach:
        queue_position = ProfileSubmission.objects.filter(
            status="pending",
            coach=submission.coach,
            submitted_at__lt=submission.submitted_at,
        ).count()

    return JsonResponse(
        {
            "status": submission.status,
            "status_display": submission.get_status_display(),
            "coach_assigned": submission.coach is not None,
            "queue_position": queue_position,
            "hours_waiting": round(hours_waiting, 1),
            "wait_status": wait_status,
            "has_feedback": bool(submission.feedback_to_user),
        }
    )


@crush_login_required
@require_http_methods(["POST"])
def api_submission_note(request):
    """Allow candidate to send a one-time note to their coach during review."""
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        return JsonResponse({"error": "No submission found"}, status=404)
    submission = ProfileSubmission.latest_for_profile(profile)
    if submission is None:
        return JsonResponse({"error": "No submission found"}, status=404)

    if submission.status != "pending":
        return JsonResponse(
            {"error": "Notes can only be sent while your profile is under review"},
            status=400,
        )

    if submission.candidate_note:
        return JsonResponse(
            {"error": "You have already sent a note to your coach"},
            status=400,
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid request"}, status=400)

    note = data.get("note", "").strip()
    if len(note) < 10 or len(note) > 500:
        return JsonResponse(
            {"error": "Note must be between 10 and 500 characters"},
            status=400,
        )

    submission.candidate_note = note
    submission.candidate_note_at = timezone.now()
    submission.save(update_fields=["candidate_note", "candidate_note_at"])

    # Notify coach via push notification
    if submission.coach:
        try:
            from .coach_notifications import notify_coach_system_alert

            display_name = profile.display_name or request.user.first_name
            notify_coach_system_alert(
                coach=submission.coach,
                title=str(_("Candidate Note")),
                message=str(
                    _("%(name)s sent a note about their profile review")
                    % {"name": display_name}
                ),
                url=f"/coach/review/{submission.id}/",
            )
        except Exception:
            logger.warning("Failed to send coach notification for candidate note")

    return JsonResponse({"success": True})


@crush_login_required
def profile_rejected(request):
    """Page shown when a profile has been rejected and cannot be resubmitted."""
    try:
        profile = CrushProfile.objects.get(user=request.user)
        submission = ProfileSubmission.objects.filter(
            profile=profile, status="rejected"
        ).latest("submitted_at")
    except (CrushProfile.DoesNotExist, ProfileSubmission.DoesNotExist):
        return redirect("crush_lu:dashboard")

    context = {
        "submission": submission,
    }
    return render(request, "crush_lu/profile_rejected.html", context)


# Membership program page
def membership(request):
    """
    Membership program landing page.
    Public access for viewing, login required for wallet actions.
    """
    profile = None
    referral_url = None

    if request.user.is_authenticated:
        try:
            profile = CrushProfile.objects.get(user=request.user)
            from .models import ReferralCode
            from .referrals import build_referral_url

            referral_code = ReferralCode.get_or_create_for_profile(profile)
            referral_url = build_referral_url(referral_code.code, request=request)
        except CrushProfile.DoesNotExist:
            pass

    tiers = [
        {
            "name": _("Basic"),
            "key": "basic",
            "points": 0,
            "emoji": "💜",
            "benefits": [
                _("Access to public events"),
                _("Basic profile features"),
                _("Connection messaging"),
            ],
        },
        {
            "name": _("Bronze"),
            "key": "bronze",
            "points": 100,
            "emoji": "🥉",
            "benefits": [
                _("All Basic benefits"),
                _("Priority event registration"),
                _("Profile badge"),
            ],
        },
        {
            "name": _("Silver"),
            "key": "silver",
            "points": 500,
            "emoji": "🥈",
            "benefits": [
                _("All Bronze benefits"),
                _("Exclusive events access"),
                _("Extended profile features"),
            ],
        },
        {
            "name": _("Gold"),
            "key": "gold",
            "points": 1000,
            "emoji": "🥇",
            "benefits": [
                _("All Silver benefits"),
                _("VIP event access"),
                _("Personal coach session"),
            ],
        },
    ]

    context = {
        "profile": profile,
        "referral_url": referral_url,
        "tiers": tiers,
        "current_tier": profile.membership_tier if profile else "basic",
        "current_points": profile.referral_points if profile else 0,
    }

    return render(request, "crush_lu/membership.html", context)
