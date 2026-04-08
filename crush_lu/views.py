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

from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
from django.db.models import Q, Count
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_http_methods
from django.conf import settings
from datetime import timedelta
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
}
AUTOSAVE_PREFERENCES_FIELDS = {
    "preferred_age_min",
    "preferred_age_max",
    "preferred_genders",
    "first_step_preference",
    "sought_qualities_ids",
    "astro_enabled",
}
AUTOSAVE_PRIVACY_FIELDS = {
    "show_full_name",
    "show_exact_age",
}

from .models import (
    CrushProfile,
    CrushCoach,
    ProfileSubmission,
    MeetupEvent,
    EventRegistration,
    EventConnection,
    CoachPushSubscription,
)
from .models.crush_connect import CrushConnectWaitlist
from .forms import (
    CrushSignupForm,
    CrushProfileForm,
    IdealCrushPreferencesForm,
)
from .decorators import crush_login_required, ratelimit
from .email_helpers import (
    send_profile_submission_notifications,
)
from .coach_notifications import (
    notify_coach_new_submission,
    notify_coach_user_revision,
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
    data_deletion_status,
    account_settings,
    update_email_preferences,
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
    """User dashboard - always shows the dating profile dashboard.
    Coaches access their coach dashboard via the dedicated Coach tab.
    """
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

        has_crush_preferences = (
            bool(profile.preferred_genders)
            or (profile.preferred_age_min != 18 or profile.preferred_age_max != 99)
            or bool(profile.first_step_preference)
        )

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

        # Time-based greeting (use localtime for correct Luxembourg timezone)
        hour = timezone.localtime().hour
        name = profile.display_name
        if 5 <= hour < 12:
            greeting = _("Good morning, %(name)s") % {"name": name}
        elif 18 <= hour < 24:
            greeting = _("Good evening, %(name)s") % {"name": name}
        else:
            greeting = _("Hey, %(name)s") % {"name": name}

        from .views_wallet import _is_apple_wallet_configured

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
            "apple_wallet_enabled": _is_apple_wallet_configured(),
        }
    except CrushProfile.DoesNotExist:
        messages.warning(request, _("Please complete your profile first."))
        return redirect("crush_lu:create_profile")

    return render(request, "crush_lu/dashboard.html", context)


def _get_coaches_for_selection(user_language=None):
    """Return active coaches with pending review counts for coach selection step."""
    coaches = (
        CrushCoach.objects.filter(is_active=True)
        .annotate(
            pending_count=Count(
                "profilesubmission",
                filter=Q(profilesubmission__status="pending"),
            )
        )
        .select_related("user")
    )
    return [
        {
            "coach": coach,
            "pending_count": coach.pending_count,
            "available": coach.pending_count < coach.max_active_reviews,
            "language_match": (
                user_language in (coach.spoken_languages or [])
                if user_language
                else False
            ),
        }
        for coach in coaches
    ]


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
                    _(
                        "Please verify your phone number before submitting your profile."
                    ),
                )
                from .social_photos import get_all_social_photos

                context = {
                    "form": form,
                    "profile": phone_check_profile,
                    "current_step": "step1",
                    "social_photos": get_all_social_photos(request.user),
                    "coaches": _get_coaches_for_selection(
                        user_language=getattr(request, "LANGUAGE_CODE", None)
                    ),
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
                            _(
                                "Your profile has been rejected and cannot be resubmitted. Please contact support@crush.lu."
                            ),
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
                    "coaches": _get_coaches_for_selection(
                        user_language=getattr(request, "LANGUAGE_CODE", None)
                    ),
                }
                return render(request, "crush_lu/create_profile.html", context)

            # Assign selected coach (mandatory user selection)
            selected_coach_id = request.POST.get("selected_coach")
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
                            _(
                                "The coach you selected is no longer available. Please choose another coach."
                            ),
                        )
                        from .social_photos import get_all_social_photos

                        context = {
                            "form": form,
                            "profile": profile,
                            "current_step": "step3",
                            "social_photos": get_all_social_photos(request.user),
                            "coaches": _get_coaches_for_selection(
                                user_language=getattr(request, "LANGUAGE_CODE", None)
                            ),
                        }
                        return render(request, "crush_lu/create_profile.html", context)
                    submission.coach = selected_coach
                    submission.save()
                except CrushCoach.DoesNotExist:
                    messages.error(
                        request,
                        _(
                            "The coach you selected is no longer available. Please choose another coach."
                        ),
                    )
                    from .social_photos import get_all_social_photos

                    context = {
                        "form": form,
                        "profile": profile,
                        "current_step": "step3",
                        "social_photos": get_all_social_photos(request.user),
                        "coaches": _get_coaches_for_selection(
                            user_language=getattr(request, "LANGUAGE_CODE", None)
                        ),
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
                    "coaches": _get_coaches_for_selection(
                        user_language=getattr(request, "LANGUAGE_CODE", None)
                    ),
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
                "current_step": "step3",
                "social_photos": get_all_social_photos(request.user),
                "coaches": _get_coaches_for_selection(
                    user_language=getattr(request, "LANGUAGE_CODE", None)
                ),
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
                    "coaches": _get_coaches_for_selection(
                        user_language=getattr(request, "LANGUAGE_CODE", None)
                    ),
                    "selected_coach_id": (
                        latest_submission.coach_id if latest_submission.coach else None
                    ),
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
                    "coaches": _get_coaches_for_selection(
                        user_language=getattr(request, "LANGUAGE_CODE", None)
                    ),
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
                    "coaches": _get_coaches_for_selection(
                        user_language=getattr(request, "LANGUAGE_CODE", None)
                    ),
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
                "coaches": _get_coaches_for_selection(
                    user_language=getattr(request, "LANGUAGE_CODE", None)
                ),
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
    valid_sections = ("photos", "about", "preferences", "privacy", "account", "about_crushlu")

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

    # --- Section: About Crush.lu (mobile footer content) ---
    if section == "about_crushlu":
        return _edit_section_about_crushlu(request, profile)

    # --- Default: Card-based section list ---
    has_crush_preferences = (
        bool(profile.preferred_genders)
        or (profile.preferred_age_min and profile.preferred_age_min != 18)
        or (profile.preferred_age_max and profile.preferred_age_max != 99)
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
    return {
        "phone_number": profile.phone_number or "",
        "date_of_birth": (
            profile.date_of_birth.isoformat() if profile.date_of_birth else ""
        ),
        "gender": profile.gender or "",
        "location": profile.location or "",
        "bio": profile.bio or "",
        "interests": profile.interests or "",
        "event_languages": list(profile.event_languages or []),
        "qualities_ids": ",".join(
            str(pk) for pk in profile.qualities.values_list("pk", flat=True)
        ),
        "defects_ids": ",".join(
            str(pk) for pk in profile.defects.values_list("pk", flat=True)
        ),
        "show_full_name": bool(profile.show_full_name),
        "show_exact_age": bool(profile.show_exact_age),
    }


def _get_preferences_form_initial_data(profile):
    return {
        "preferred_age_min": profile.preferred_age_min or 18,
        "preferred_age_max": profile.preferred_age_max or 99,
        "preferred_genders": list(profile.preferred_genders or []),
        "first_step_preference": profile.first_step_preference or "",
        "sought_qualities_ids": ",".join(
            str(pk) for pk in profile.sought_qualities.values_list("pk", flat=True)
        ),
        "astro_enabled": bool(profile.astro_enabled),
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


def _render_about_section(request, profile, form=None):
    from .models import Trait

    qualities_list = list(Trait.objects.filter(trait_type="quality"))
    defects_list = list(Trait.objects.filter(trait_type="defect"))
    qualities_grouped = _group_traits_by_category(qualities_list)
    defects_grouped = _group_traits_by_category(defects_list)
    selected_qualities = list(profile.qualities.values_list("pk", flat=True))
    selected_defects = list(profile.defects.values_list("pk", flat=True))

    if form is None:
        form = CrushProfileForm(instance=profile)

    return {
        "form": form,
        "profile": profile,
        "section": "about",
        "qualities_grouped": qualities_grouped,
        "defects_grouped": defects_grouped,
        "selected_qualities_json": json.dumps(selected_qualities),
        "selected_defects_json": json.dumps(selected_defects),
    }


def _render_preferences_section(request, profile, form=None):
    from .models import Trait
    from .matching import (
        get_western_zodiac,
        get_chinese_zodiac,
        ZODIAC_SIGN_EMOJIS,
        CHINESE_ANIMAL_EMOJIS,
        ZODIAC_SIGN_LABELS,
        CHINESE_ANIMAL_LABELS,
    )

    if form is None:
        form = IdealCrushPreferencesForm(instance=profile)

    qualities_list = list(Trait.objects.filter(trait_type="quality"))
    qualities_grouped = _group_traits_by_category(qualities_list)
    selected_sought = list(profile.sought_qualities.values_list("pk", flat=True))

    zodiac_sign = get_western_zodiac(profile.date_of_birth)
    chinese_animal = get_chinese_zodiac(profile.date_of_birth)

    return {
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


def _render_privacy_section(profile, form=None):
    if form is None:
        form = CrushProfileForm(instance=profile)

    return {
        "form": form,
        "profile": profile,
        "section": "privacy",
    }


def _edit_section_about(request, profile):
    """Handle about section editing (bio, interests, traits, contact, details, event prefs)."""
    if request.method == "POST":
        form = CrushProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            updated_profile = form.save()
            from .matching import update_match_scores_for_user

            transaction.on_commit(lambda: update_match_scores_for_user(request.user))
            if request.htmx:
                context = _render_about_section(request, updated_profile)
                return render(request, "crush_lu/partials/edit_about.html", context)
            messages.success(request, _("Profile updated successfully!"))
            return redirect("crush_lu:edit_profile")
        else:
            if request.htmx:
                context = _render_about_section(request, profile, form=form)
                context["has_errors"] = True
                return render(request, "crush_lu/partials/edit_about.html", context)
    else:
        form = CrushProfileForm(instance=profile)

    context = _render_about_section(request, profile, form=form)
    template = "crush_lu/partials/edit_about.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


def _edit_section_preferences(request, profile):
    """Handle ideal crush preferences section."""
    from .matching import (
        update_match_scores_for_user,
    )

    if request.method == "POST":
        form = IdealCrushPreferencesForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            transaction.on_commit(lambda: update_match_scores_for_user(request.user))
            if request.htmx:
                context = _render_preferences_section(request, profile)
                return render(
                    request, "crush_lu/partials/edit_preferences.html", context
                )
            messages.success(request, _("Preferences saved!"))
            return redirect("crush_lu:edit_profile")
    else:
        form = IdealCrushPreferencesForm(instance=profile)

    context = _render_preferences_section(request, profile, form=form)
    template = "crush_lu/partials/edit_preferences.html"
    if request.htmx:
        return render(request, template, context)
    return render(
        request, "crush_lu/edit_profile.html", {**context, "section_template": template}
    )


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

    CRUSH_SOCIAL_PROVIDERS = ["google", "facebook", "microsoft", "apple"]

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

    context = {
        "profile": profile,
        "section": "account",
        "google_connected": "google" in connected_providers,
        "facebook_connected": "facebook" in connected_providers,
        "microsoft_connected": "microsoft" in connected_providers,
        "apple_connected": "apple" in connected_providers,
        "google_available": "google" in available_providers,
        "facebook_available": "facebook" in available_providers,
        "microsoft_available": "microsoft" in available_providers,
        "apple_available": "apple" in available_providers,
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

    if section == "about":
        data = _merge_autosave_payload(
            _get_profile_form_initial_data(profile),
            payload,
            AUTOSAVE_ABOUT_FIELDS,
        )
        form = CrushProfileForm(data, instance=profile)
        if form.is_valid():
            updated_profile = form.save()
            from .matching import update_match_scores_for_user

            transaction.on_commit(lambda: update_match_scores_for_user(request.user))
            return JsonResponse(
                {
                    "success": True,
                    "saved_fields": sorted(AUTOSAVE_ABOUT_FIELDS & set(payload.keys())),
                    "values": {
                        "bio": updated_profile.bio or "",
                        "interests": updated_profile.interests or "",
                        "phone_number": updated_profile.phone_number or "",
                        "location": updated_profile.location or "",
                        "event_languages": list(updated_profile.event_languages or []),
                        "qualities_ids": list(
                            updated_profile.qualities.values_list("pk", flat=True)
                        ),
                        "defects_ids": list(
                            updated_profile.defects.values_list("pk", flat=True)
                        ),
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

    if section == "preferences":
        data = _merge_autosave_payload(
            _get_preferences_form_initial_data(profile),
            payload,
            AUTOSAVE_PREFERENCES_FIELDS,
        )
        form = IdealCrushPreferencesForm(data, instance=profile)
        if form.is_valid():
            updated_profile = form.save()
            from .matching import update_match_scores_for_user

            transaction.on_commit(lambda: update_match_scores_for_user(request.user))
            return JsonResponse(
                {
                    "success": True,
                    "saved_fields": sorted(
                        AUTOSAVE_PREFERENCES_FIELDS & set(payload.keys())
                    ),
                    "values": {
                        "preferred_age_min": updated_profile.preferred_age_min or 18,
                        "preferred_age_max": updated_profile.preferred_age_max or 99,
                        "preferred_genders": list(
                            updated_profile.preferred_genders or []
                        ),
                        "first_step_preference": updated_profile.first_step_preference
                        or "",
                        "sought_qualities_ids": list(
                            updated_profile.sought_qualities.values_list(
                                "pk", flat=True
                            )
                        ),
                        "astro_enabled": bool(updated_profile.astro_enabled),
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
    if latest_submission and latest_submission.status in [
        "rejected",
        "revision",
        "recontact_coach",
    ]:
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
    """Redirect to new section-based preferences page.
    Kept for backwards compatibility with existing URL."""

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
                    user=other_user, is_approved=True, is_active=True
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
        my_profile = CrushProfile.objects.get(user=request.user, is_approved=True)
        other_profile = CrushProfile.objects.get(
            user_id=user_id, is_approved=True, is_active=True
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
            coach_phone_available = config.whatsapp_enabled and bool(
                coach_contact_phone
            )
        except Exception:
            pass

    # --- Review time statistics ---
    now = timezone.now()

    # Global average review time
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
        avg_seconds = avg_result["avg_seconds"] or 0
        avg_review_hours = max(0, avg_seconds / 3600)

    # Per-coach average (if coach assigned and has enough data)
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
            coach_seconds = coach_result["avg_seconds"] or 0
            coach_avg_review_hours = max(0, coach_seconds / 3600)

    # Use coach-specific avg if available, else global
    effective_avg = coach_avg_review_hours or avg_review_hours
    estimated_review_display = format_review_estimate(effective_avg)
    is_coach_specific_estimate = coach_avg_review_hours is not None

    # Queue position
    queue_position = 0
    total_coach_pending = 0
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

    # Wait time and status
    hours_waiting = (now - submission.submitted_at).total_seconds() / 3600
    if hours_waiting < 6:
        wait_status = "fresh"
    elif hours_waiting < 24:
        wait_status = "normal"
    elif hours_waiting < 48:
        wait_status = "extended"
    else:
        wait_status = "long"

    # Progress bar percentage (capped at 95%)
    if effective_avg > 0:
        progress_percent = min(95, int((hours_waiting / effective_avg) * 100))
    else:
        progress_percent = min(95, int(hours_waiting / 36 * 100))

    # Next upcoming event teaser
    next_event = (
        MeetupEvent.objects.filter(
            is_published=True, is_cancelled=False, date_time__gte=now
        )
        .order_by("date_time")
        .first()
    )

    context = {
        "submission": submission,
        "coach_contact_phone": coach_contact_phone,
        "coach_phone_available": coach_phone_available,
        "estimated_review_display": estimated_review_display,
        "is_coach_specific_estimate": is_coach_specific_estimate,
        "queue_position": queue_position,
        "total_coach_pending": total_coach_pending,
        "hours_waiting": round(hours_waiting, 1),
        "wait_status": wait_status,
        "progress_percent": progress_percent,
        "next_event": next_event,
        "has_candidate_note": bool(submission.candidate_note),
    }
    return render(request, "crush_lu/profile_submitted.html", context)


@crush_login_required
@require_http_methods(["GET"])
def api_submission_status(request):
    """JSON endpoint for polling submission status changes."""
    try:
        profile = CrushProfile.objects.get(user=request.user)
        submission = ProfileSubmission.objects.filter(profile=profile).latest(
            "submitted_at"
        )
    except (CrushProfile.DoesNotExist, ProfileSubmission.DoesNotExist):
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
        submission = ProfileSubmission.objects.filter(profile=profile).latest(
            "submitted_at"
        )
    except (CrushProfile.DoesNotExist, ProfileSubmission.DoesNotExist):
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
