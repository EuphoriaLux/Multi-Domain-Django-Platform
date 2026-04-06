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

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.db.models import Q, Count
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.conf import settings
from datetime import timedelta
import logging
import json

logger = logging.getLogger(__name__)

from .models import (
    CrushProfile,
    CrushCoach,
    ProfileSubmission,
    MeetupEvent,
    EventRegistration,
    CoachSession,
    EventConnection,
    ConnectionMessage,
    CoachPushSubscription,
)
from .models.crush_connect import CrushConnectWaitlist
from .forms import (
    CrushSignupForm,
    CrushProfileForm,
    CrushCoachForm,
    ProfileReviewForm,
    CoachSessionForm,
    EventRegistrationForm,
    IdealCrushPreferencesForm,
)
from .decorators import crush_login_required, ratelimit
from .email_helpers import (
    send_welcome_email,
    send_profile_submission_confirmation,
    send_coach_assignment_notification,
    send_profile_submission_notifications,
    send_profile_approved_notification,
    send_profile_revision_request,
    send_profile_rejected_notification,
    send_event_registration_confirmation,
    send_event_waitlist_notification,
    send_event_cancellation_confirmation,
)
from .notification_service import (
    NotificationService,
    NotificationType,
    notify_profile_approved,
    notify_profile_revision,
    notify_profile_rejected,
    notify_new_message,
    notify_new_connection,
    notify_connection_accepted,
)
from .coach_notifications import (
    notify_coach_new_submission,
    notify_coach_user_revision,
)
from .referrals import (
    capture_referral,
    capture_referral_from_request,
    apply_referral_to_user,
)
from .utils.i18n import is_valid_language

# =============================================================================
# Re-export all views from split modules so urls.py continues to work
# with `views.function_name` references unchanged.
# =============================================================================

# Static pages
from .views_static import (  # noqa: F401
    home,
    test_ghost_story,
    test_upstair,
    about,
    how_it_works,
    privacy_policy,
    terms_of_service,
    data_deletion_request,
    crush_coach,
    crush_connect_teaser,
)

# Account & auth
from .views_account import (  # noqa: F401
    oauth_complete,
    facebook_data_deletion_callback,
    parse_facebook_signed_request,
    delete_crushlu_profile_only,
    delete_full_account,
    delete_user_data,
    data_deletion_status,
    account_settings,
    update_email_preferences,
    email_unsubscribe,
    set_password,
    disconnect_social_account,
    delete_crushlu_profile_view,
    gdpr_data_management,
    delete_account,
    consent_confirm,
    account_banned,
    referral_redirect,
    signup,
    export_user_data,
    apple_relay_link_prompt,
)

# Events
from .views_events import (  # noqa: F401
    event_list,
    event_detail,
    event_calendar_download,
    event_register,
    event_cancel,
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
)

# Coach
from .views_coach import (  # noqa: F401
    coach_dashboard,
    coach_mark_review_call_complete,
    coach_log_failed_call,
    coach_log_sms_sent,
    coach_review_profile,
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
    api_coach_claim_submission,
    coach_members,
    coach_member_matches,
    coach_match_pairs,
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
    pwa_debug_view,
)


# =============================================================================
# Dashboard, profile management, and remaining core views
# =============================================================================


@crush_login_required
def dashboard(request):
    """User dashboard - redirects ACTIVE coaches to their dashboard unless ?user_view=1"""
    # Check if user is an ACTIVE coach
    # Allow coaches to view their user dashboard via ?user_view=1 parameter
    user_view = request.GET.get("user_view") == "1"
    try:
        coach = CrushCoach.objects.get(user=request.user, is_active=True)
        if not user_view:
            return redirect("crush_lu:coach_dashboard")
    except CrushCoach.DoesNotExist:
        # Either no coach record, or coach is inactive - show dating dashboard
        pass

    # Regular user dashboard
    try:
        profile = CrushProfile.objects.get(user=request.user)
        # Get latest submission status
        latest_submission = (
            ProfileSubmission.objects.filter(profile=profile)
            .order_by("-submitted_at")
            .first()
        )

        # Get user's event registrations
        registrations = (
            EventRegistration.objects.filter(user=request.user)
            .select_related("event")
            .order_by("-event__date_time")
        )

        # Get connection count
        connection_count = EventConnection.objects.active_for_user(request.user).count()

        # Get or create referral code for this user's profile
        from .models import ReferralCode
        from .referrals import build_referral_url

        referral_code = ReferralCode.get_or_create_for_profile(profile)
        referral_url = build_referral_url(referral_code.code, request=request)

        coach = latest_submission.coach if latest_submission else None

        has_crush_preferences = bool(profile.preferred_genders) or (
            profile.preferred_age_min != 18 or profile.preferred_age_max != 99
        ) or bool(profile.first_step_preference)

        # Crush Connect waitlist status
        on_crush_connect_waitlist = False
        crush_connect_position = None
        crush_connect_total = CrushConnectWaitlist.objects.count()
        try:
            cc_entry = CrushConnectWaitlist.objects.get(user=request.user)
            on_crush_connect_waitlist = True
            crush_connect_position = cc_entry.waitlist_position
        except CrushConnectWaitlist.DoesNotExist:
            pass

        # Time-based greeting
        hour = timezone.now().hour
        name = profile.display_name
        if 5 <= hour < 12:
            greeting = _("Good morning, %(name)s") % {"name": name}
        elif 18 <= hour < 24:
            greeting = _("Good evening, %(name)s") % {"name": name}
        else:
            greeting = _("Hey, %(name)s") % {"name": name}

        context = {
            "profile": profile,
            "submission": latest_submission,
            "coach": coach,
            "registrations": registrations,
            "connection_count": connection_count,
            "referral_url": referral_url,
            "has_crush_preferences": has_crush_preferences,
            "on_crush_connect_waitlist": on_crush_connect_waitlist,
            "crush_connect_position": crush_connect_position,
            "crush_connect_total": crush_connect_total,
            "greeting": greeting,
        }
    except CrushProfile.DoesNotExist:
        messages.warning(request, _("Please complete your profile first."))
        return redirect("crush_lu:create_profile")

    return render(request, "crush_lu/dashboard.html", context)


def _get_coaches_for_selection(user_language=None):
    """Return active coaches with pending review counts for coach selection step."""
    coaches = CrushCoach.objects.filter(is_active=True).annotate(
        pending_count=Count(
            'profilesubmission',
            filter=Q(profilesubmission__status='pending'),
        )
    ).select_related('user')
    return [
        {
            'coach': coach,
            'pending_count': coach.pending_count,
            'available': coach.pending_count < coach.max_active_reviews,
            'language_match': user_language in (coach.spoken_languages or []) if user_language else False,
        }
        for coach in coaches
    ]


@crush_login_required
@ratelimit(key="user", rate="10/15m", method="POST", block=True)
def create_profile(request):
    """Profile creation - coaches can also create dating profiles"""
    from crush_lu.models.profiles import UserDataConsent

    # Check if user is banned from Crush.lu
    if hasattr(request.user, 'data_consent') and request.user.data_consent.crushlu_banned:
        messages.error(
            request,
            _('You cannot create a new Crush.lu profile. Your previous profile was permanently deleted.')
        )
        return redirect('crush_lu:account_settings')

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

            # Enforce phone verification before allowing submission
            # The AJAX step-by-step flow checks this in save_profile_step2(),
            # but the form POST path must also enforce it to prevent bypass.
            # Check existing_profile first (has DB state), fall back to new profile object.
            phone_check_profile = existing_profile or profile
            if not phone_check_profile.phone_verified:
                messages.error(
                    request,
                    _("Please verify your phone number before submitting your profile."),
                )
                from .social_photos import get_all_social_photos
                context = {
                    "form": form,
                    "profile": phone_check_profile,
                    "current_step": "step1",
                    "social_photos": get_all_social_photos(request.user),
                    "coaches": _get_coaches_for_selection(user_language=getattr(request, 'LANGUAGE_CODE', None)),
                }
                return render(request, "crush_lu/create_profile.html", context)

            # Check if this is first submission or resubmission
            is_first_submission = profile.completion_status != "submitted"

            # Set preferred language from current request language on first submission
            if is_first_submission and hasattr(request, "LANGUAGE_CODE"):
                current_lang = request.LANGUAGE_CODE
                if is_valid_language(current_lang):
                    profile.preferred_language = current_lang
                    logger.debug(
                        f"Set preferred_language to '{current_lang}' for {request.user.email}"
                    )

            # Mark profile as completed and submitted
            profile.completion_status = "submitted"

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
                            _("Your profile has been rejected and cannot be resubmitted. Please contact support@crush.lu."),
                        )
                        return redirect("crush_lu:profile_rejected")

                    revision_submission = (
                        ProfileSubmission.objects.select_for_update()
                        .filter(profile=profile, status="revision")
                        .first()
                    )

                    is_revision = False
                    if existing_submission:
                        submission = existing_submission
                        created = False
                        logger.warning(
                            f"⚠️ Existing pending submission found for {request.user.email}"
                        )
                    elif revision_submission:
                        submission = revision_submission
                        submission.status = "pending"
                        submission.submitted_at = timezone.now()
                        submission.save()
                        created = False
                        is_revision = True
                        logger.info(
                            f"✅ Revision submission updated to pending for {request.user.email}"
                        )
                    else:
                        submission = ProfileSubmission.objects.create(
                            profile=profile, status="pending"
                        )
                        created = True

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
                    "coaches": _get_coaches_for_selection(user_language=getattr(request, 'LANGUAGE_CODE', None)),
                }
                return render(request, "crush_lu/create_profile.html", context)

            # Assign selected coach (mandatory user selection)
            selected_coach_id = request.POST.get('selected_coach')
            if selected_coach_id:
                try:
                    selected_coach = CrushCoach.objects.get(
                        id=selected_coach_id, is_active=True
                    )
                    # Allow re-selecting the same coach even if at capacity
                    # (the existing submission already counts in their pending count)
                    is_same_coach = submission.coach_id == selected_coach.id
                    if not is_same_coach and not selected_coach.can_accept_reviews():
                        messages.warning(
                            request,
                            _("The coach you selected is no longer available. Please choose another coach."),
                        )
                        from .social_photos import get_all_social_photos
                        context = {
                            "form": form,
                            "profile": profile,
                            "current_step": "step3",
                            "social_photos": get_all_social_photos(request.user),
                            "coaches": _get_coaches_for_selection(user_language=getattr(request, 'LANGUAGE_CODE', None)),
                        }
                        return render(request, "crush_lu/create_profile.html", context)
                    submission.coach = selected_coach
                    submission.save()
                except CrushCoach.DoesNotExist:
                    messages.error(
                        request,
                        _("The coach you selected is no longer available. Please choose another coach."),
                    )
                    from .social_photos import get_all_social_photos
                    context = {
                        "form": form,
                        "profile": profile,
                        "current_step": "step3",
                        "social_photos": get_all_social_photos(request.user),
                        "coaches": _get_coaches_for_selection(user_language=getattr(request, 'LANGUAGE_CODE', None)),
                    }
                    return render(request, "crush_lu/create_profile.html", context)
            else:
                # No coach selected — edge case / tampering
                messages.error(request, _("Please select a coach before submitting."))
                from .social_photos import get_all_social_photos
                context = {
                    "form": form,
                    "profile": profile,
                    "current_step": "step3",
                    "social_photos": get_all_social_photos(request.user),
                    "coaches": _get_coaches_for_selection(user_language=getattr(request, 'LANGUAGE_CODE', None)),
                }
                return render(request, "crush_lu/create_profile.html", context)

            # Only send emails for NEW submissions
            if created:
                logger.info(f"NEW profile submission created for {request.user.email}")

                if submission.coach:
                    try:
                        notify_coach_new_submission(submission.coach, submission)
                        logger.info(
                            f"Coach push notification sent for submission {submission.id}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send coach push notification: {e}")

                send_profile_submission_notifications(
                    submission,
                    request,
                    add_message_func=lambda msg: messages.warning(request, msg),
                )
            elif is_revision:
                if submission.coach:
                    try:
                        notify_coach_user_revision(submission.coach, submission)
                        logger.info(
                            f"Coach revision notification sent for submission {submission.id}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to send coach revision notification: {e}"
                        )
            else:
                logger.warning(
                    f"⚠️ Duplicate submission attempt prevented for {request.user.email}"
                )

            messages.success(request, _("Profile submitted for review!"))
            return redirect("crush_lu:profile_submitted")
        else:
            logger.error(
                f"❌ Profile form validation failed for user {request.user.email}"
            )
            logger.error(f"❌ Form errors: {form.errors.as_json()}")

            for field, errors in form.errors.items():
                for error in errors:
                    if field == "__all__":
                        messages.error(request, _("Form error: %(error)s") % {"error": error})
                    else:
                        messages.error(
                            request, _("%(field)s: %(error)s") % {"field": field.replace('_', ' ').title(), "error": error}
                        )

            from .social_photos import get_all_social_photos

            context = {
                "form": form,
                "current_step": "step3",
                "social_photos": get_all_social_photos(request.user),
                "coaches": _get_coaches_for_selection(user_language=getattr(request, 'LANGUAGE_CODE', None)),
            }
            return render(request, "crush_lu/create_profile.html", context)

    # GET request - check if profile already exists and redirect accordingly
    try:
        profile = CrushProfile.objects.get(user=request.user)

        if profile.completion_status == "submitted":
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
                    "coaches": _get_coaches_for_selection(user_language=getattr(request, 'LANGUAGE_CODE', None)),
                    "selected_coach_id": latest_submission.coach_id if latest_submission.coach else None,
                }
                return render(request, "crush_lu/create_profile.html", context)

            messages.info(
                request, _("Your profile has been submitted. Check the status below.")
            )
            return redirect("crush_lu:profile_submitted")
        elif profile.completion_status == "not_started":
            from .social_photos import get_all_social_photos

            form = CrushProfileForm(instance=profile)
            return render(
                request,
                "crush_lu/create_profile.html",
                {
                    "form": form,
                    "profile": profile,
                    "social_photos": get_all_social_photos(request.user),
                    "coaches": _get_coaches_for_selection(user_language=getattr(request, 'LANGUAGE_CODE', None)),
                },
            )
        elif profile.completion_status in ["step1", "step2", "step3", "step4"]:
            from .social_photos import get_all_social_photos

            form = CrushProfileForm(instance=profile)
            return render(
                request,
                "crush_lu/create_profile.html",
                {
                    "form": form,
                    "profile": profile,
                    "current_step": profile.completion_status,
                    "social_photos": get_all_social_photos(request.user),
                    "coaches": _get_coaches_for_selection(user_language=getattr(request, 'LANGUAGE_CODE', None)),
                },
            )
        else:
            return redirect("crush_lu:edit_profile")
    except CrushProfile.DoesNotExist:
        from .social_photos import get_all_social_photos

        form = CrushProfileForm()
        return render(
            request,
            "crush_lu/create_profile.html",
            {
                "form": form,
                "profile": None,
                "social_photos": get_all_social_photos(request.user),
                "coaches": _get_coaches_for_selection(user_language=getattr(request, 'LANGUAGE_CODE', None)),
            },
        )


def _render_edit_profile_form(request):
    """Internal: Render card-based profile editing for approved profiles.

    Supports section-based navigation:
    - No section param → card list (section overview)
    - ?section=photos|about|preferences|privacy|account → section detail
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
    valid_sections = ("photos", "about", "preferences", "privacy", "account")

    # --- Section: Photos ---
    if section == "photos":
        return _edit_section_photos(request, profile)

    # --- Section: About (bio, interests, traits, contact, details, event prefs) ---
    if section == "about":
        return _edit_section_about(request, profile)

    # --- Section: Preferences (ideal crush) ---
    if section == "preferences":
        return _edit_section_preferences(request, profile)

    # --- Section: Privacy ---
    if section == "privacy":
        return _edit_section_privacy(request, profile)

    # --- Section: Account (language, theme, notifications, logout) ---
    if section == "account":
        return _edit_section_account(request, profile)

    # --- Default: Card-based section list ---
    has_crush_preferences = bool(profile.preferred_genders) or (
        profile.preferred_age_min and profile.preferred_age_min != 18
    ) or (
        profile.preferred_age_max and profile.preferred_age_max != 99
    )

    context = {
        "profile": profile,
        "section": section,
        "has_crush_preferences": has_crush_preferences,
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
    return render(request, "crush_lu/edit_profile.html", {**context, "section_template": template})


def _edit_section_about(request, profile):
    """Handle about section editing (bio, interests, traits, contact, details, event prefs)."""
    from .models import Trait

    qualities_list = list(Trait.objects.filter(trait_type="quality"))
    defects_list = list(Trait.objects.filter(trait_type="defect"))
    qualities_grouped = _group_traits_by_category(qualities_list)
    defects_grouped = _group_traits_by_category(defects_list)
    selected_qualities = list(profile.qualities.values_list("pk", flat=True))
    selected_defects = list(profile.defects.values_list("pk", flat=True))

    trait_context = {
        "qualities_grouped": qualities_grouped,
        "defects_grouped": defects_grouped,
        "selected_qualities_json": json.dumps(selected_qualities),
        "selected_defects_json": json.dumps(selected_defects),
    }

    if request.method == "POST":
        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            updated_profile = form.save()
            from .matching import update_match_scores_for_user

            transaction.on_commit(
                lambda: update_match_scores_for_user(request.user)
            )
            if request.htmx:
                return render(
                    request,
                    "crush_lu/edit_profile.html#edit_success",
                    {"profile": updated_profile},
                )
            messages.success(request, _("Profile updated successfully!"))
            return redirect("crush_lu:edit_profile")
        else:
            if request.htmx:
                return render(
                    request,
                    "crush_lu/partials/edit_about.html",
                    {
                        "form": form,
                        "profile": profile,
                        "has_errors": True,
                        **trait_context,
                    },
                )
    else:
        form = CrushProfileForm(instance=profile)

    context = {
        "form": form,
        "profile": profile,
        "section": "about",
        **trait_context,
    }
    template = "crush_lu/partials/edit_about.html"
    if request.htmx:
        return render(request, template, context)
    return render(request, "crush_lu/edit_profile.html", {**context, "section_template": template})


def _edit_section_preferences(request, profile):
    """Handle ideal crush preferences section."""
    from .models import Trait
    from .matching import (
        get_western_zodiac, get_chinese_zodiac,
        update_match_scores_for_user,
        ZODIAC_SIGN_EMOJIS, CHINESE_ANIMAL_EMOJIS,
        ZODIAC_SIGN_LABELS, CHINESE_ANIMAL_LABELS,
    )

    if request.method == "POST":
        form = IdealCrushPreferencesForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            transaction.on_commit(
                lambda: update_match_scores_for_user(request.user)
            )
            if request.htmx:
                return render(
                    request,
                    "crush_lu/edit_profile.html#edit_success",
                    {"profile": profile},
                )
            messages.success(request, _("Preferences saved!"))
            return redirect("crush_lu:edit_profile")
    else:
        form = IdealCrushPreferencesForm(instance=profile)

    qualities_list = list(Trait.objects.filter(trait_type="quality"))
    qualities_grouped = _group_traits_by_category(qualities_list)
    selected_sought = list(profile.sought_qualities.values_list("pk", flat=True))

    zodiac_sign = get_western_zodiac(profile.date_of_birth)
    chinese_animal = get_chinese_zodiac(profile.date_of_birth)

    context = {
        "form": form,
        "profile": profile,
        "section": "preferences",
        "qualities_grouped": qualities_grouped,
        "selected_sought_json": json.dumps(selected_sought),
        "zodiac_sign": ZODIAC_SIGN_LABELS.get(zodiac_sign, zodiac_sign),
        "zodiac_emoji": ZODIAC_SIGN_EMOJIS.get(zodiac_sign, ""),
        "chinese_animal": CHINESE_ANIMAL_LABELS.get(chinese_animal, chinese_animal),
        "chinese_emoji": CHINESE_ANIMAL_EMOJIS.get(chinese_animal, ""),
    }
    template = "crush_lu/partials/edit_preferences.html"
    if request.htmx:
        return render(request, template, context)
    return render(request, "crush_lu/edit_profile.html", {**context, "section_template": template})


def _edit_section_privacy(request, profile):
    """Handle privacy settings section."""
    if request.method == "POST":
        form = CrushProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            if request.htmx:
                return render(
                    request,
                    "crush_lu/edit_profile.html#edit_success",
                    {"profile": profile},
                )
            messages.success(request, _("Privacy settings updated!"))
            return redirect("crush_lu:edit_profile")
    else:
        form = CrushProfileForm(instance=profile)

    context = {
        "form": form,
        "profile": profile,
        "section": "privacy",
    }
    template = "crush_lu/partials/edit_privacy.html"
    if request.htmx:
        return render(request, template, context)
    return render(request, "crush_lu/edit_profile.html", {**context, "section_template": template})


def _edit_section_account(request, profile):
    """Handle account settings section (language, theme, notifications)."""
    context = {
        "profile": profile,
        "section": "account",
    }
    template = "crush_lu/partials/edit_account.html"
    if request.htmx:
        return render(request, template, context)
    return render(request, "crush_lu/edit_profile.html", {**context, "section_template": template})


@crush_login_required
def edit_profile(request):
    """Edit existing profile - routes to appropriate edit flow"""
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, _("You need to create a profile first."))
        return redirect("crush_lu:create_profile")

    # 1. If profile is approved → use simple single-page edit
    if profile.is_approved:
        return _render_edit_profile_form(request)

    # 2. If profile is submitted and under review → redirect to status page
    if profile.completion_status == "submitted":
        try:
            submission = ProfileSubmission.objects.filter(profile=profile).latest(
                "submitted_at"
            )
            if submission.status in ["pending", "under_review"]:
                messages.info(
                    request,
                    _(
                        "Your profile is currently under review. You'll be notified once it's approved."
                    ),
                )
                return redirect("crush_lu:profile_submitted")
            elif submission.status == "rejected":
                return redirect("crush_lu:profile_rejected")
            elif submission.status == "revision":
                messages.warning(
                    request,
                    _(
                        "Your profile needs updates. Please review the coach feedback below."
                    ),
                )
                return redirect("crush_lu:create_profile")
            elif submission.status == "recontact_coach":
                messages.info(
                    request,
                    _(
                        "Your coach is trying to reach you. Please contact them to schedule your screening call."
                    ),
                )
                return redirect("crush_lu:profile_submitted")
        except ProfileSubmission.DoesNotExist:
            pass

    # 3. Profile is incomplete → redirect to create_profile
    if profile.completion_status in [
        "not_started",
        "step1",
        "step2",
        "step3",
        "step4",
    ]:
        messages.info(request, _("Please complete your profile to continue."))
        return redirect("crush_lu:create_profile")

    # 4. Default: Use multi-step form for any other edge cases
    if request.method == "POST":
        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.completion_status = "submitted"
            profile.save()

            submission, created = ProfileSubmission.objects.get_or_create(
                profile=profile, defaults={"status": "pending"}
            )
            if created:
                submission.assign_coach()

                if submission.coach:
                    try:
                        notify_coach_new_submission(submission.coach, submission)
                        logger.info(
                            f"Coach push notification sent for submission {submission.id}"
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send coach push notification: {e}")

                send_profile_submission_notifications(
                    submission,
                    request,
                    add_message_func=lambda msg: messages.warning(request, msg),
                )

            messages.success(request, _("Profile submitted for review!"))
            return redirect("crush_lu:profile_submitted")
    else:
        form = CrushProfileForm(instance=profile)

    latest_submission = None
    try:
        latest_submission = ProfileSubmission.objects.filter(profile=profile).latest(
            "submitted_at"
        )
    except ProfileSubmission.DoesNotExist:
        pass

    current_step_to_show = None
    if latest_submission and latest_submission.status in ["rejected", "revision", "recontact_coach"]:
        current_step_to_show = None
    elif profile.completion_status == "submitted":
        current_step_to_show = None
    elif profile.completion_status == "not_started":
        current_step_to_show = None
    elif profile.completion_status == "step1" and (
        not profile.date_of_birth or not profile.phone_number
    ):
        current_step_to_show = None
    else:
        current_step_to_show = profile.completion_status

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
            grouped.append({
                "key": cat_value,
                "label": cat_label,
                "ghost_include": _TRAIT_CATEGORY_META.get(cat_value, ""),
                "traits": traits,
            })
    return grouped


@crush_login_required
def crush_preferences(request):
    """Redirect to new section-based preferences page.
    Kept for backwards compatibility with existing URL."""
    from django.urls import reverse

    return HttpResponseRedirect(
        reverse("crush_lu:edit_profile") + "?section=preferences"
    )


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
        messages.warning(request, _("Your profile must be approved before viewing matches."))
        return redirect("crush_lu:dashboard")

    has_traits = profile.sought_qualities.exists()
    matches = []

    if has_traits:
        match_scores = get_matches_for_user(request.user)

        for ms in match_scores:
            other_user = ms.user_b if ms.user_a == request.user else ms.user_a
            try:
                other_profile = CrushProfile.objects.get(
                    user=other_user, is_approved=True, is_active=True
                )
            except CrushProfile.DoesNotExist:
                continue

            # Gender filter (age filtering handled by hard filter in matching.py)
            if profile.preferred_genders and other_profile.gender not in profile.preferred_genders:
                continue

            score_display = get_score_display(ms.score_final)
            if score_display:
                matches.append({
                    "profile": other_profile,
                    "score": ms.score_final,
                    "score_percent": int(ms.score_final * 100),
                    "display": score_display,
                })

    paginator = Paginator(matches, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "crush_lu/matches.html", {
        "page_obj": page_obj,
        "matches": page_obj.object_list,
        "has_traits": has_traits,
        "profile": profile,
    })


@require_http_methods(["GET"])
@crush_login_required
def api_match_list(request):
    """JSON API for match list (language-neutral)."""
    from .matching import get_matches_for_user, get_score_display

    try:
        profile = CrushProfile.objects.get(user=request.user, is_approved=True)
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
                user=other_user, is_approved=True, is_active=True
            )
        except CrushProfile.DoesNotExist:
            continue

        display = get_score_display(ms.score_final)
        if display:
            results.append({
                "user_id": other_user.pk,
                "display_name": other_profile.display_name,
                "age": other_profile.age if other_profile.show_exact_age else None,
                "score_percent": int(ms.score_final * 100),
                "score_label": display["label"],
            })

    # Paginate
    start = (page - 1) * per_page
    end = start + per_page
    paginated = results[start:end]

    return JsonResponse({
        "matches": paginated,
        "total": len(results),
        "page": page,
        "per_page": per_page,
    })


@require_http_methods(["GET"])
@crush_login_required
def api_match_score(request, user_id):
    """JSON API for score detail between authenticated user and another user."""
    from .matching import compute_match_score, get_score_display

    try:
        my_profile = CrushProfile.objects.get(user=request.user, is_approved=True)
        other_profile = CrushProfile.objects.get(
            user_id=user_id, is_approved=True, is_active=True
        )
    except CrushProfile.DoesNotExist:
        return JsonResponse({"error": "Profile not found"}, status=404)

    scores = compute_match_score(my_profile, other_profile)
    display = get_score_display(scores["score_final"])

    return JsonResponse({
        "score_final": scores["score_final"],
        "score_percent": int(scores["score_final"] * 100),
        "score_qualities": scores["score_qualities"],
        "score_zodiac_west": scores["score_zodiac_west"],
        "score_zodiac_cn": scores["score_zodiac_cn"],
        "label": display["label"] if display else None,
        "color": display["hex"] if display else None,
    })


@crush_login_required
def profile_submitted(request):
    """Confirmation page after profile submission"""
    try:
        profile = CrushProfile.objects.get(user=request.user)
        submission = ProfileSubmission.objects.filter(profile=profile).latest(
            "submitted_at"
        )
    except (CrushProfile.DoesNotExist, ProfileSubmission.DoesNotExist):
        messages.error(request, _("No profile submission found."))
        return redirect("crush_lu:create_profile")

    # Determine coach contact phone: per-coach or global fallback
    coach_contact_phone = ""
    coach_phone_available = False
    if submission and submission.coach and submission.coach.phone_number:
        coach_contact_phone = submission.coach.whatsapp_number
        coach_phone_available = True
    else:
        from .models import CrushSiteConfig
        try:
            config = CrushSiteConfig.get_config()
            coach_contact_phone = config.whatsapp_number
            coach_phone_available = config.whatsapp_enabled and bool(coach_contact_phone)
        except Exception:
            pass

    context = {
        "submission": submission,
        "coach_contact_phone": coach_contact_phone,
        "coach_phone_available": coach_phone_available,
    }
    return render(request, "crush_lu/profile_submitted.html", context)


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


def luxid_mockup_view(request):
    """Mockup view for LuxID integration demonstration (NOT PRODUCTION)"""
    from django.http import Http404

    host = request.META.get("HTTP_HOST", "").split(":")[0].lower()
    is_staging = host.startswith("test.")
    is_development = settings.DEBUG or host in ["localhost", "127.0.0.1"]

    if not is_development and not is_staging:
        raise Http404(
            "This mockup is only available on staging and development environments"
        )

    context = {
        "submission": {
            "status": "pending",
            "submitted_at": timezone.now() - timedelta(hours=2),
            "coach": None,
            "get_status_display": lambda: _("Pending Review"),
        }
    }
    return render(request, "crush_lu/profile_submitted_luxid_mockup.html", context)


def luxid_auth_mockup_view(request):
    """Mockup view for LuxID login/signup integration (NOT PRODUCTION)"""
    from django.http import Http404
    from allauth.account.forms import LoginForm

    host = request.META.get("HTTP_HOST", "").split(":")[0].lower()
    is_staging = host.startswith("test.")
    is_development = settings.DEBUG or host in ["localhost", "127.0.0.1"]

    if not is_development and not is_staging:
        raise Http404(
            "This mockup is only available on staging and development environments"
        )

    context = {
        "signup_form": CrushSignupForm(),
        "login_form": LoginForm(),
        "mode": request.GET.get("mode", "login"),
    }
    return render(request, "crush_lu/auth_luxid_mockup.html", context)


def luxid_meeting_guide_view(request):
    """Meeting preparation guide for LuxID CIAM integration (NOT PRODUCTION)"""
    from django.http import Http404

    host = request.META.get("HTTP_HOST", "").split(":")[0].lower()
    is_staging = host.startswith("test.")
    is_development = settings.DEBUG or host in ["localhost", "127.0.0.1"]

    if not is_development and not is_staging:
        raise Http404(
            "This page is only available on staging and development environments"
        )

    context = {
        "callback_uat": "https://test.crush.lu/accounts/luxid/login/callback/",
        "callback_prod": "https://crush.lu/accounts/luxid/login/callback/",
        "privacy_urls": {
            "en": "/en/privacy/",
            "de": "/de/privacy/",
            "fr": "/fr/privacy/",
        },
        "attributes_needed": [
            {"name": "sub", "description": "Unique user identifier", "required": True},
            {"name": "email", "description": "User email address", "required": True},
            {
                "name": "email_verified",
                "description": "Email verification status",
                "required": True,
            },
            {"name": "given_name", "description": "First name", "required": True},
            {"name": "family_name", "description": "Last name", "required": True},
            {"name": "birthdate", "description": "Date of birth", "required": True},
            {"name": "gender", "description": "Gender", "required": True},
            {
                "name": "phone_number",
                "description": "Phone number",
                "required": False,
            },
            {"name": "locale", "description": "Preferred language", "required": False},
            {"name": "picture", "description": "Profile photo URL", "required": False},
        ],
        "attributes_available": [
            "sub",
            "email",
            "email_verified",
            "given_name",
            "family_name",
            "name",
            "birthdate",
            "gender",
            "phone_number",
            "phone_number_verified",
            "address",
            "locale",
            "picture",
            "updated_at",
        ],
    }
    return render(request, "crush_lu/luxid_meeting_guide.html", context)


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


# Wallet
@crush_login_required
def wallet_apple_pass(request):
    """Redirect to the Apple Wallet pass URL if configured."""
    pass_url = getattr(settings, "APPLE_WALLET_PASS_URL", None)
    if pass_url:
        return redirect(pass_url)
    messages.error(
        request, _("Membership card is not available yet. Please try again later.")
    )
    return redirect("crush_lu:dashboard")


@crush_login_required
def wallet_google_save(request):
    """Redirect to the Google Wallet Save URL (JWT) if configured."""
    save_url = getattr(settings, "GOOGLE_WALLET_SAVE_URL", None)
    if save_url:
        return redirect(save_url)
    messages.error(
        request, _("Membership card is not available yet. Please try again later.")
    )
    return redirect("crush_lu:dashboard")
