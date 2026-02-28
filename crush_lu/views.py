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
from django.db.models import Q
from django.db import transaction
from django.http import JsonResponse, HttpResponse
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
    UserActivity,
    CoachPushSubscription,
)
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

        # Check PWA status from UserActivity model (not CrushProfile)
        is_pwa_user = False
        try:
            activity = UserActivity.objects.filter(user=request.user).first()
            if activity:
                is_pwa_user = activity.is_pwa_user
        except Exception:
            logger.warning("Failed to check PWA status for user %s", request.user.id, exc_info=True)
        # Get or create referral code for this user's profile
        from .models import ReferralCode
        from .referrals import build_referral_url

        referral_code = ReferralCode.get_or_create_for_profile(profile)
        referral_url = build_referral_url(referral_code.code, request=request)

        coach = latest_submission.coach if latest_submission else None

        context = {
            "profile": profile,
            "submission": latest_submission,
            "coach": coach,
            "registrations": registrations,
            "connection_count": connection_count,
            "is_pwa_user": is_pwa_user,
            "referral_url": referral_url,
        }
    except CrushProfile.DoesNotExist:
        messages.warning(request, _("Please complete your profile first."))
        return redirect("crush_lu:create_profile")

    return render(request, "crush_lu/dashboard.html", context)


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

                    revision_submission = (
                        ProfileSubmission.objects.select_for_update()
                        .filter(profile=profile, status__in=["revision", "rejected", "recontact_coach"])
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
                }
                return render(request, "crush_lu/create_profile.html", context)

            # Only assign coach and send emails for NEW submissions
            if created:
                submission.assign_coach()
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
            }
            return render(request, "crush_lu/create_profile.html", context)

    # GET request - check if profile already exists and redirect accordingly
    try:
        profile = CrushProfile.objects.get(user=request.user)

        if profile.completion_status == "submitted":
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
                },
            )
        elif profile.completion_status in ["step1", "step2", "step3"]:
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
            },
        )


def _render_edit_profile_form(request):
    """Internal: Render single-page edit form for approved profiles."""
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, _("You need to create a profile first."))
        return redirect("crush_lu:create_profile")

    if not profile.is_approved:
        messages.warning(request, _("Your profile must be approved before editing."))
        return redirect("crush_lu:edit_profile")

    from .social_photos import get_all_social_photos

    if request.method == "POST":
        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            updated_profile = form.save()

            if request.htmx:
                return render(
                    request,
                    "crush_lu/partials/edit_profile_success.html",
                    {
                        "profile": updated_profile,
                    },
                )

            messages.success(request, _("Profile updated successfully!"))
            return redirect("crush_lu:dashboard")
        else:
            if request.htmx:
                return render(
                    request,
                    "crush_lu/partials/edit_profile_form.html",
                    {
                        "form": form,
                        "profile": profile,
                        "social_photos": get_all_social_photos(request.user),
                        "has_errors": True,
                    },
                )

            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(
                        request, f"{field.replace('_', ' ').title()}: {error}"
                    )
    else:
        form = CrushProfileForm(instance=profile)

    context = {
        "form": form,
        "profile": profile,
        "social_photos": get_all_social_photos(request.user),
    }
    return render(request, "crush_lu/edit_profile.html", context)


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
            elif submission.status in ["rejected", "revision", "recontact_coach"]:
                messages.warning(
                    request,
                    _(
                        "Your profile needs updates. Please review the coach feedback below."
                    ),
                )
                return redirect("crush_lu:create_profile")
        except ProfileSubmission.DoesNotExist:
            pass

    # 3. Profile is incomplete → redirect to create_profile
    if profile.completion_status in [
        "not_started",
        "step1",
        "step2",
        "step3",
        "completed",
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


@crush_login_required
def crush_preferences(request):
    """Standalone page for ideal crush preferences (age range, gender)"""
    try:
        profile = CrushProfile.objects.get(user=request.user)
    except CrushProfile.DoesNotExist:
        messages.info(request, _("You need to create a profile first."))
        return redirect("crush_lu:create_profile")

    if not profile.is_approved:
        messages.warning(request, _("Your profile must be approved before setting preferences."))
        return redirect("crush_lu:dashboard")

    if request.method == "POST":
        form = IdealCrushPreferencesForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            logger.info(
                "Crush preferences saved for user %s: age=%s-%s, genders=%s",
                request.user.pk,
                form.cleaned_data.get("preferred_age_min"),
                form.cleaned_data.get("preferred_age_max"),
                form.cleaned_data.get("preferred_genders"),
            )
            messages.success(request, _("Your preferences have been saved."))
            return redirect("crush_lu:dashboard")
        else:
            logger.warning(
                "Crush preferences form errors for user %s: %s",
                request.user.pk,
                form.errors,
            )
    else:
        form = IdealCrushPreferencesForm(instance=profile)
        logger.info(
            "Loading crush preferences for user %s: age=%s-%s, genders=%s",
            request.user.pk,
            profile.preferred_age_min,
            profile.preferred_age_max,
            profile.preferred_genders,
        )

    return render(request, "crush_lu/crush_preferences.html", {
        "form": form,
        "profile": profile,
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

    context = {
        "submission": submission,
    }
    return render(request, "crush_lu/profile_submitted.html", context)


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
    is_pwa_user = False
    profile = None
    referral_url = None

    if request.user.is_authenticated:
        try:
            activity = UserActivity.objects.get(user=request.user)
            is_pwa_user = activity.is_pwa_user
        except UserActivity.DoesNotExist:
            pass

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
        "is_pwa_user": is_pwa_user,
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
