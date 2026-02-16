from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.paginator import Paginator
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.db.models import Q, Count
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
import json
import logging

logger = logging.getLogger(__name__)

from .models import (
    CrushProfile,
    CrushCoach,
    ProfileSubmission,
    MeetupEvent,
    EventRegistration,
    CoachSession,
    EventConnection,
    EventInvitation,
)
from .forms import (
    CrushCoachForm,
    ProfileReviewForm,
    CoachSessionForm,
)
from .decorators import crush_login_required, coach_required
from .notification_service import (
    notify_profile_approved,
    notify_profile_revision,
    notify_profile_rejected,
)
from .coach_notifications import (
    notify_coach_new_submission,
    notify_coach_user_revision,
)


# Coach views
@coach_required
def coach_dashboard(request):
    """Coach dashboard for reviewing profiles"""
    coach = request.coach

    # Get pending submissions assigned to this coach, ordered by oldest first
    pending_submissions = (
        ProfileSubmission.objects.filter(coach=coach, status="pending")
        .select_related("profile__user")
        .order_by("submitted_at")
    )

    # Calculate wait time and urgency for each submission
    now = timezone.now()
    for submission in pending_submissions:
        hours_waiting = (now - submission.submitted_at).total_seconds() / 3600
        submission.is_urgent = hours_waiting > 48  # Red: > 48 hours
        submission.is_warning = 24 < hours_waiting <= 48  # Yellow: 24-48 hours

    # Split by gender: Women (F), Men (M), Other (NB, O, P)
    pending_women = [s for s in pending_submissions if s.profile.gender == "F"]
    pending_men = [s for s in pending_submissions if s.profile.gender == "M"]
    pending_other = [
        s for s in pending_submissions if s.profile.gender in ["NB", "O", "P", ""]
    ]

    # Get recently reviewed
    recent_reviews = (
        ProfileSubmission.objects.filter(
            coach=coach, status__in=["approved", "rejected", "revision"]
        )
        .select_related("profile__user")
        .order_by("-reviewed_at")[:10]
    )

    # Note: Coach push notifications are now managed in Account Settings
    # (see account_settings view for coach push subscription handling)

    # Pending Crush Sparks awaiting review
    from .models import CrushSpark

    pending_sparks_count = CrushSpark.objects.filter(
        status="pending_review"
    ).count()

    context = {
        "coach": coach,
        "pending_submissions": pending_submissions,
        "pending_women": pending_women,
        "pending_men": pending_men,
        "pending_other": pending_other,
        "recent_reviews": recent_reviews,
        "pending_sparks_count": pending_sparks_count,
    }
    return render(request, "crush_lu/coach_dashboard.html", context)


@coach_required
@require_http_methods(["POST"])
def coach_mark_review_call_complete(request, submission_id):
    """Mark screening call as complete during profile review"""
    is_htmx = request.headers.get("HX-Request")
    coach = request.coach

    # Handle submission not found or not assigned to this coach
    # Use select_related to prefetch profile and user in single query (reduces latency)
    try:
        submission = ProfileSubmission.objects.select_related(
            "profile", "profile__user"
        ).get(id=submission_id, coach=coach)
    except ProfileSubmission.DoesNotExist:
        if is_htmx:
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {
                    "message": _("Submission not found or not assigned to you."),
                    "target_id": "screening-call-section",
                },
            )
        messages.error(request, _("Submission not found."))
        return redirect("crush_lu:coach_dashboard")

    submission.review_call_completed = True
    submission.review_call_date = timezone.now()
    submission.review_call_notes = request.POST.get("call_notes", "")

    # Parse and save checklist data
    checklist_data_str = request.POST.get("checklist_data", "{}")
    try:
        checklist_data = json.loads(checklist_data_str) if checklist_data_str else {}
    except json.JSONDecodeError:
        checklist_data = {}
    submission.review_call_checklist = checklist_data

    # Only update specific fields (faster than full model save)
    submission.save(
        update_fields=[
            "review_call_completed",
            "review_call_date",
            "review_call_notes",
            "review_call_checklist",
        ]
    )

    # Return HTMX partial or redirect
    if is_htmx:
        return render(
            request,
            "crush_lu/_screening_call_section.html",
            {
                "submission": submission,
                "profile": submission.profile,
            },
        )

    messages.success(
        request,
        f"Screening call marked complete for {submission.profile.user.first_name}. You can now approve the profile.",
    )
    return redirect("crush_lu:coach_review_profile", submission_id=submission.id)


@coach_required
def coach_log_failed_call(request, submission_id):
    """Log a failed call attempt - HTMX endpoint"""
    from .models import CallAttempt
    from .forms import CallAttemptForm

    coach = request.coach

    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user"),
        id=submission_id,
        coach=coach,
    )

    if request.method == "POST":
        form = CallAttemptForm(request.POST)
        if form.is_valid():
            # Create failed call attempt
            attempt = form.save(commit=False)
            attempt.submission = submission
            attempt.result = "failed"
            attempt.coach = coach
            attempt.save()

            messages.success(request, _("Failed call attempt logged."))

            # Return updated screening section via HTMX
            if request.headers.get("HX-Request"):
                context = {
                    "submission": submission,
                    "profile": submission.profile,
                }
                return render(request, "crush_lu/_screening_call_section.html", context)

            return redirect(
                "crush_lu:coach_review_profile", submission_id=submission.id
            )

    # For GET or invalid POST, return form
    context = {
        "submission": submission,
        "profile": submission.profile,
        "form": CallAttemptForm(),
    }

    if request.headers.get("HX-Request"):
        return render(request, "crush_lu/_call_attempt_form.html", context)

    return redirect("crush_lu:coach_review_profile", submission_id=submission.id)


@coach_required
def coach_review_profile(request, submission_id):
    """Review a profile submission"""
    coach = request.coach

    submission = get_object_or_404(ProfileSubmission, id=submission_id, coach=coach)

    if request.method == "POST":
        form = ProfileReviewForm(request.POST, instance=submission)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.reviewed_at = timezone.now()

            # Update profile approval status and send notifications
            if submission.status == "approved":
                # REQUIRE screening call before approval
                if not submission.review_call_completed:
                    messages.error(
                        request,
                        _(
                            "You must complete a screening call before approving this profile."
                        ),
                    )
                    form = ProfileReviewForm(instance=submission)
                    context = {
                        "coach": coach,
                        "submission": submission,
                        "form": form,
                    }
                    return render(
                        request, "crush_lu/coach_review_profile.html", context
                    )
                submission.profile.is_approved = True
                submission.profile.approved_at = timezone.now()
                submission.profile.save()
                messages.success(request, _("Profile approved!"))

                # Send approval notification to user (push first, email fallback)
                try:
                    result = notify_profile_approved(
                        user=submission.profile.user,
                        profile=submission.profile,
                        coach_notes=submission.feedback_to_user,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Profile approval notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send profile approval notification: {e}")

            elif submission.status == "rejected":
                submission.profile.is_approved = False
                submission.profile.save()
                messages.info(request, _("Profile rejected."))

                # Send rejection notification to user (push first, email fallback)
                try:
                    result = notify_profile_rejected(
                        user=submission.profile.user,
                        profile=submission.profile,
                        feedback=submission.feedback_to_user,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Profile rejection notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send profile rejection notification: {e}")

            elif submission.status == "revision":
                messages.info(request, _("Revision requested."))

                # Send revision request to user (push first, email fallback)
                try:
                    result = notify_profile_revision(
                        user=submission.profile.user,
                        profile=submission.profile,
                        feedback=submission.feedback_to_user,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Profile revision notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send profile revision request: {e}")

            elif submission.status == "recontact_coach":
                messages.info(request, _("User asked to recontact coach."))

                # Send notification to user
                try:
                    from .notification_service import notify_profile_recontact

                    result = notify_profile_recontact(
                        user=submission.profile.user,
                        profile=submission.profile,
                        coach=coach,
                        request=request,
                    )
                    if result.any_delivered:
                        logger.info(
                            f"Recontact notification sent: push={result.push_success}, email={result.email_sent}"
                        )
                except Exception as e:
                    logger.error(f"Failed to send recontact notification: {e}")

            submission.save()
            return redirect("crush_lu:coach_dashboard")
    else:
        form = ProfileReviewForm(instance=submission)

    # Get social login provider if exists
    social_account = submission.profile.user.socialaccount_set.first()

    context = {
        "submission": submission,
        "profile": submission.profile,
        "form": form,
        "social_account": social_account,
    }
    return render(request, "crush_lu/coach_review_profile.html", context)


@coach_required
def coach_preview_email(request, submission_id):
    """Preview the email that will be sent for a review decision"""
    import traceback
    from django.utils import translation
    from django.utils.translation import gettext as _
    from django.http import HttpResponse
    from .utils.i18n import get_user_preferred_language
    from .email_helpers import (
        get_email_context_with_unsubscribe,
        get_email_base_urls,
        get_user_language_url,
    )
    from django.template.loader import render_to_string

    coach = request.coach

    # Wrap entire function in try-except to catch any errors
    try:
        submission = get_object_or_404(ProfileSubmission, id=submission_id, coach=coach)

        # Get parameters from request
        status = request.GET.get("status", "")
        feedback = request.GET.get("feedback_to_user", "")
        coach_notes = request.GET.get("coach_notes", "")

        profile = submission.profile
        user = profile.user

        # Get user's preferred language
        lang = get_user_preferred_language(user=user, request=request, default="en")

        # If no valid status selected, show a helpful message
        if not status or status == "pending":
            preview_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Email Preview</title>
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    margin: 0;
                    padding: 40px;
                    background: #f3f4f6;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    min-height: 100vh;
                }
                .message {
                    background: white;
                    padding: 32px;
                    border-radius: 12px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                    text-align: center;
                    max-width: 500px;
                }
                .message h2 {
                    margin: 0 0 16px 0;
                    color: #6366f1;
                    font-size: 24px;
                }
                .message p {
                    margin: 0;
                    color: #6b7280;
                    line-height: 1.6;
                }
            </style>
        </head>
        <body>
            <div class="message">
                <h2>📧 No Preview Available</h2>
                <p>Please select a decision (Approved, Rejected, Revision Requested, or Recontact Coach) to preview the email that will be sent.</p>
            </div>
        </body>
        </html>
            """
            response = HttpResponse(preview_html, content_type="text/html")
            response["X-Frame-Options"] = "SAMEORIGIN"
            return response

        # Build context based on decision type
        if status == "approved":
            events_url = get_user_language_url(user, "crush_lu:event_list", request)
            context = get_email_context_with_unsubscribe(
                user,
                request,
                first_name=user.first_name,
                coach_notes=feedback or coach_notes,
                events_url=events_url,
            )
            template = "crush_lu/emails/profile_approved.html"
            with translation.override(lang):
                subject = _("Welcome to Crush.lu - Your Profile is Approved!")

        elif status == "rejected":
            base_urls = get_email_base_urls(user, request)
            context = {
                "user": user,
                "first_name": user.first_name,
                "reason": feedback,
                "LANGUAGE_CODE": lang,
                **base_urls,
            }
            template = "crush_lu/emails/profile_rejected.html"
            with translation.override(lang):
                subject = _("Profile Review Update - Crush.lu")

        elif status == "revision":
            edit_profile_url = get_user_language_url(
                user, "crush_lu:edit_profile", request
            )
            base_urls = get_email_base_urls(user, request)
            context = {
                "user": user,
                "first_name": user.first_name,
                "feedback": feedback,
                "edit_profile_url": edit_profile_url,
                "LANGUAGE_CODE": lang,
                **base_urls,
            }
            template = "crush_lu/emails/profile_revision_request.html"
            with translation.override(lang):
                subject = _("Profile Review Feedback - Crush.lu")

        elif status == "recontact_coach":
            base_urls = get_email_base_urls(user, request)
            context = {
                "user": user,
                "first_name": user.first_name,
                "coach": coach,
                "LANGUAGE_CODE": lang,
                **base_urls,
            }
            template = "crush_lu/emails/profile_recontact.html"
            with translation.override(lang):
                subject = _("Your Crush Coach Needs to Speak With You")
        else:
            return HttpResponse("Invalid status", status=400)

        # Render preview with language override
        try:
            with translation.override(lang):
                html_content = render_to_string(template, context)
        except Exception as e:
            logger.error(f"Error rendering email template: {e}")
            logger.error(traceback.format_exc())
            return HttpResponse(
                "An error occurred. Please try again later.",
                status=500,
            )

        # Wrap in preview container
        preview_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Email Preview</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                margin: 0;
                padding: 20px;
                background: #f3f4f6;
            }}
            .preview-header {{
                background: white;
                padding: 16px 24px;
                border-radius: 8px;
                margin-bottom: 16px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            }}
            .preview-header h2 {{
                margin: 0 0 8px 0;
                font-size: 18px;
                color: #111827;
            }}
            .preview-header p {{
                margin: 0;
                font-size: 14px;
                color: #6b7280;
            }}
            .email-container {{
                background: white;
                border-radius: 8px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                overflow: hidden;
            }}
        </style>
    </head>
    <body>
        <div class="preview-header">
            <h2>📧 {subject}</h2>
            <p>Language: {lang.upper()} | To: {user.email}</p>
        </div>
        <div class="email-container">
            {html_content}
        </div>
    </body>
    </html>
    """

        response = HttpResponse(preview_html, content_type="text/html")
        response["X-Frame-Options"] = "SAMEORIGIN"
        return response

    except Exception as e:
        logger.error(f"Error in coach_preview_email: {e}")
        logger.error(traceback.format_exc())
        error_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Preview Error</title>
            <style>
                body {
                    font-family: monospace;
                    padding: 20px;
                    background: #fee;
                }
                .error {
                    background: white;
                    padding: 20px;
                    border: 2px solid #f00;
                    border-radius: 8px;
                }
                h2 { color: #c00; }
            </style>
        </head>
        <body>
            <div class="error">
                <h2>Preview Error</h2>
                <p>An error occurred while generating the email preview. Please check the server logs for details.</p>
            </div>
        </body>
        </html>
        """
        return HttpResponse(error_html, status=500)


@coach_required
def coach_sessions(request):
    """View and manage coach sessions"""
    coach = request.coach

    sessions = CoachSession.objects.filter(coach=coach).order_by("-created_at")

    context = {
        "coach": coach,
        "sessions": sessions,
    }
    return render(request, "crush_lu/coach_sessions.html", context)


@coach_required
def coach_edit_profile(request):
    """Edit coach profile (bio, specializations, photo) - separate from dating profile"""
    coach = request.coach

    if request.method == "POST":
        form = CrushCoachForm(request.POST, request.FILES, instance=coach)
        if form.is_valid():
            form.save()
            messages.success(request, _("Coach profile updated successfully!"))
            return redirect("crush_lu:coach_dashboard")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(
                        request, f"{field.replace('_', ' ').title()}: {error}"
                    )
    else:
        form = CrushCoachForm(instance=coach)

    # Check if coach also has a dating profile
    try:
        profile = request.user.crushprofile
        has_dating_profile = True
    except CrushProfile.DoesNotExist:
        has_dating_profile = False

    context = {
        "coach": coach,
        "form": form,
        "has_dating_profile": has_dating_profile,
    }
    return render(request, "crush_lu/coach_edit_profile.html", context)


# ============================================================================
# COACH JOURNEY MANAGEMENT - Manage Wonderland Journey Experiences
# ============================================================================


@coach_required
def coach_journey_dashboard(request):
    """Coach dashboard for managing all active journeys and their challenges"""
    coach = request.coach

    from .models import JourneyConfiguration, JourneyProgress
    from django.db.models import Count

    # Get all active journeys with aggregated progress counts (single query)
    active_journeys = (
        JourneyConfiguration.objects.filter(is_active=True)
        .select_related("special_experience")
        .prefetch_related("chapters__challenges")
        .annotate(
            total_users=Count("journeyprogress"),
            completed_users=Count("journeyprogress", filter=Q(journeyprogress__is_completed=True)),
        )
    )

    # Get recent progress per journey (1 query per journey, but avoids 3N pattern)
    journeys_with_progress = []
    for journey in active_journeys:
        progress_list = (
            JourneyProgress.objects.filter(journey=journey)
            .select_related("user")
            .order_by("-last_activity")[:5]
        )

        journeys_with_progress.append(
            {
                "journey": journey,
                "recent_progress": progress_list,
                "total_users": journey.total_users,
                "completed_users": journey.completed_users,
            }
        )

    context = {
        "coach": coach,
        "journeys_with_progress": journeys_with_progress,
    }
    return render(request, "crush_lu/coach_journey_dashboard.html", context)


@coach_required
def coach_edit_journey(request, journey_id):
    """Edit a journey's chapters and challenges"""
    coach = request.coach

    from .models import JourneyConfiguration

    journey = get_object_or_404(JourneyConfiguration, id=journey_id)

    # Get all chapters with challenges
    chapters = journey.chapters.all().prefetch_related("challenges", "rewards")

    context = {
        "coach": coach,
        "journey": journey,
        "chapters": chapters,
    }
    return render(request, "crush_lu/coach_edit_journey.html", context)


@coach_required
def coach_edit_challenge(request, challenge_id):
    """Edit an individual challenge's question and content"""
    coach = request.coach

    from .models import JourneyChallenge, ChallengeAttempt

    challenge = get_object_or_404(JourneyChallenge, id=challenge_id)

    if request.method == "POST":
        # Update challenge fields
        challenge.question = request.POST.get("question", challenge.question)
        challenge.correct_answer = request.POST.get(
            "correct_answer", challenge.correct_answer
        )
        challenge.success_message = request.POST.get(
            "success_message", challenge.success_message
        )

        # Update hints
        challenge.hint_1 = request.POST.get("hint_1", challenge.hint_1)
        challenge.hint_2 = request.POST.get("hint_2", challenge.hint_2)
        challenge.hint_3 = request.POST.get("hint_3", challenge.hint_3)

        # Update points
        try:
            challenge.points_awarded = int(
                request.POST.get("points_awarded", challenge.points_awarded)
            )
            challenge.hint_1_cost = int(
                request.POST.get("hint_1_cost", challenge.hint_1_cost)
            )
            challenge.hint_2_cost = int(
                request.POST.get("hint_2_cost", challenge.hint_2_cost)
            )
            challenge.hint_3_cost = int(
                request.POST.get("hint_3_cost", challenge.hint_3_cost)
            )
        except ValueError:
            messages.error(request, _("Points must be valid numbers."))
            return redirect("crush_lu:coach_edit_challenge", challenge_id=challenge_id)

        challenge.save()
        messages.success(
            request, f'Challenge "{challenge.question[:50]}..." updated successfully!'
        )
        return redirect(
            "crush_lu:coach_edit_journey", journey_id=challenge.chapter.journey.id
        )

    # Get all user answers for this challenge
    all_attempts = (
        ChallengeAttempt.objects.filter(challenge=challenge)
        .select_related("chapter_progress__journey_progress__user")
        .order_by("-attempted_at")
    )

    context = {
        "coach": coach,
        "challenge": challenge,
        "all_attempts": all_attempts,
        "total_responses": all_attempts.count(),
    }
    return render(request, "crush_lu/coach_edit_challenge.html", context)


@coach_required
def coach_event_list(request):
    """Coach dashboard for managing events and viewing attendees"""
    now = timezone.now()

    upcoming_events = (
        MeetupEvent.objects.filter(date_time__gte=now, is_published=True)
        .with_registration_counts()
        .order_by("date_time")
    )

    past_events = (
        MeetupEvent.objects.filter(date_time__lt=now, is_published=True)
        .with_registration_counts()
        .order_by("-date_time")[:10]
    )

    context = {
        "coach": request.coach,
        "upcoming_events": upcoming_events,
        "past_events": past_events,
    }
    return render(request, "crush_lu/coach_event_list.html", context)


@coach_required
def coach_event_detail(request, event_id):
    """Detailed view of event registrations for coaches"""
    event = get_object_or_404(MeetupEvent, id=event_id)

    registrations = (
        EventRegistration.objects.filter(event=event)
        .exclude(status="cancelled")
        .select_related("user__crushprofile")
        .order_by("registered_at")
    )

    # Status filter
    status_filter = request.GET.get("status", "all")
    if status_filter == "confirmed":
        filtered_registrations = [r for r in registrations if r.status in ("confirmed", "attended")]
    elif status_filter == "waitlist":
        filtered_registrations = [r for r in registrations if r.status == "waitlist"]
    elif status_filter == "other":
        filtered_registrations = [r for r in registrations if r.status in ("pending", "no_show")]
    else:
        filtered_registrations = list(registrations)

    # Count stats
    confirmed_count = sum(1 for r in registrations if r.status in ("confirmed", "attended"))
    waitlist_count = sum(1 for r in registrations if r.status == "waitlist")
    spots_remaining = max(0, event.max_participants - confirmed_count)

    # Post-event activity: connections and sparks
    from .models.crush_spark import CrushSpark

    connections = EventConnection.objects.filter(event=event)
    connection_count = connections.count()
    mutual_connections = connections.filter(
        status__in=["accepted", "coach_reviewing", "coach_approved", "shared"]
    ).count()

    sparks = CrushSpark.objects.filter(event=event)
    spark_count = sparks.count()
    sparks_pending = sparks.filter(
        status__in=[CrushSpark.Status.PENDING_REVIEW, CrushSpark.Status.REQUESTED]
    ).count()

    context = {
        "coach": request.coach,
        "event": event,
        "registrations": filtered_registrations,
        "confirmed_count": confirmed_count,
        "waitlist_count": waitlist_count,
        "spots_remaining": spots_remaining,
        "total_registrations": len(registrations),
        "status_filter": status_filter,
        "connection_count": connection_count,
        "mutual_connections": mutual_connections,
        "spark_count": spark_count,
        "sparks_pending": sparks_pending,
    }
    return render(request, "crush_lu/coach_event_detail.html", context)


@coach_required
def coach_member_overview(request, user_id):
    """Coach view of a member's profile, event history, and connections"""
    from django.contrib.auth.models import User

    member = get_object_or_404(User, id=user_id)

    # Get profile (may not exist for invitation-only guests)
    try:
        profile = member.crushprofile
    except CrushProfile.DoesNotExist:
        profile = None

    # Latest profile submission
    latest_submission = (
        ProfileSubmission.objects.filter(profile=profile)
        .select_related("coach")
        .order_by("-submitted_at")
        .first()
        if profile
        else None
    )

    # Event history
    event_registrations = (
        EventRegistration.objects.filter(user=member)
        .select_related("event")
        .order_by("-event__date_time")
    )

    # Connections made
    connections = (
        EventConnection.objects.filter(Q(requester=member) | Q(recipient=member))
        .select_related("event", "requester", "recipient")
        .order_by("-requested_at")[:10]
    )

    # Available coaches for reassignment dropdown
    all_coaches = CrushCoach.objects.filter(is_active=True).select_related("user")

    context = {
        "coach": request.coach,
        "member": member,
        "profile": profile,
        "latest_submission": latest_submission,
        "event_registrations": event_registrations,
        "connections": connections,
        "all_coaches": all_coaches,
    }
    return render(request, "crush_lu/coach_member_overview.html", context)


@coach_required
@require_http_methods(["POST"])
def coach_reassign_submission(request, submission_id):
    """Claim or reassign a profile submission to a different coach"""
    submission = get_object_or_404(
        ProfileSubmission.objects.select_related("profile__user", "coach"),
        id=submission_id,
    )

    action = request.POST.get("action")

    if action == "claim":
        # Current coach claims this submission for themselves
        old_coach = submission.coach
        submission.coach = request.coach
        submission.save(update_fields=["coach"])
        logger.info(
            f"Coach {request.coach} claimed submission #{submission.id} "
            f"(was: {old_coach})"
        )
        messages.success(
            request,
            _("You have claimed this profile review."),
        )
    elif action == "reassign":
        # Reassign to a specific coach
        target_coach_id = request.POST.get("target_coach_id")
        if not target_coach_id:
            messages.error(request, _("Please select a coach."))
            return redirect(
                "crush_lu:coach_member_overview",
                user_id=submission.profile.user.id,
            )
        target_coach = get_object_or_404(
            CrushCoach, id=target_coach_id, is_active=True
        )
        old_coach = submission.coach
        submission.coach = target_coach
        submission.save(update_fields=["coach"])
        logger.info(
            f"Coach {request.coach} reassigned submission #{submission.id} "
            f"from {old_coach} to {target_coach}"
        )
        messages.success(
            request,
            _("Profile review reassigned to %(coach)s.")
            % {"coach": target_coach.user.get_full_name()},
        )
    else:
        messages.error(request, _("Invalid action."))

    return redirect(
        "crush_lu:coach_member_overview", user_id=submission.profile.user.id
    )


@coach_required
def coach_view_user_progress(request, progress_id):
    """View a specific user's journey progress and answers - Enhanced Report View"""
    coach = request.coach

    from .models import (
        JourneyProgress,
        ChallengeAttempt,
        JourneyChapter,
        JourneyChallenge,
    )
    from collections import defaultdict

    progress = get_object_or_404(
        JourneyProgress.objects.select_related("user", "journey"), id=progress_id
    )

    # Get all chapters for this journey with their challenges
    chapters = (
        JourneyChapter.objects.filter(journey=progress.journey)
        .prefetch_related("challenges")
        .order_by("chapter_number")
    )

    # Get all challenge attempts for this journey
    all_attempts = (
        ChallengeAttempt.objects.filter(chapter_progress__journey_progress=progress)
        .select_related("challenge", "challenge__chapter", "chapter_progress__chapter")
        .order_by("attempted_at")
    )

    # Build a structured report: for each challenge, get the FINAL successful attempt
    # or the last attempt if none were successful
    challenge_results = {}  # challenge_id -> best attempt
    challenge_attempt_counts = defaultdict(int)  # challenge_id -> total attempts

    for attempt in all_attempts:
        challenge_id = attempt.challenge_id
        challenge_attempt_counts[challenge_id] += 1

        # Keep the attempt if:
        # 1. No attempt recorded yet for this challenge
        # 2. This attempt is correct (overrides incorrect)
        # 3. This attempt earned more points
        if challenge_id not in challenge_results:
            challenge_results[challenge_id] = attempt
        elif attempt.is_correct and not challenge_results[challenge_id].is_correct:
            challenge_results[challenge_id] = attempt
        elif attempt.points_earned > challenge_results[challenge_id].points_earned:
            challenge_results[challenge_id] = attempt

    # Build chapter data with challenges and results
    chapter_data = []
    total_challenges = 0
    completed_challenges = 0
    questionnaire_responses = []

    for chapter in chapters:
        chapter_info = {
            "chapter": chapter,
            "challenges": [],
            "chapter_points": 0,
            "is_completed": False,
        }

        # Check if chapter is completed
        chapter_progress = progress.chapter_completions.filter(chapter=chapter).first()
        if chapter_progress:
            chapter_info["is_completed"] = chapter_progress.is_completed
            chapter_info["chapter_points"] = chapter_progress.points_earned

        for challenge in chapter.challenges.all():
            total_challenges += 1
            result = challenge_results.get(challenge.id)
            attempt_count = challenge_attempt_counts.get(challenge.id, 0)

            # Determine if this is a questionnaire challenge (no correct answer)
            is_questionnaire = (
                not challenge.correct_answer
                or challenge.challenge_type in ["open_text", "would_you_rather"]
            )

            # Parse the user's answer for multiple choice to show the full option text
            display_answer = None
            if result:
                completed_challenges += 1
                display_answer = result.user_answer

                # For multiple choice, map the letter to the full option
                if challenge.challenge_type == "multiple_choice" and challenge.options:
                    answer_key = result.user_answer.strip().upper()
                    if answer_key in challenge.options:
                        display_answer = (
                            f"{answer_key}: {challenge.options[answer_key]}"
                        )

                # For timeline sorting, show as readable list
                if challenge.challenge_type == "timeline_sort" and challenge.options:
                    try:
                        order = result.user_answer.split(",")
                        items = challenge.options.get("items", [])
                        if items:
                            display_answer = [
                                items[int(i)]
                                for i in order
                                if i.strip().isdigit() and int(i) < len(items)
                            ]
                    except (ValueError, IndexError):
                        pass

                # Collect questionnaire responses for insights section
                if is_questionnaire and result.user_answer:
                    questionnaire_responses.append(
                        {
                            "chapter": chapter,
                            "challenge": challenge,
                            "answer": result.user_answer,
                            "display_answer": display_answer,
                        }
                    )

            challenge_info = {
                "challenge": challenge,
                "result": result,
                "attempt_count": attempt_count,
                "is_questionnaire": is_questionnaire,
                "display_answer": display_answer,
                "options": challenge.options,
            }
            chapter_info["challenges"].append(challenge_info)

        chapter_data.append(chapter_info)

    # Calculate journey statistics
    journey_duration = None
    if progress.started_at and progress.final_response_at:
        journey_duration = progress.final_response_at - progress.started_at

    stats = {
        "total_challenges": total_challenges,
        "completed_challenges": completed_challenges,
        "total_attempts": sum(challenge_attempt_counts.values()),
        "avg_attempts_per_challenge": round(
            sum(challenge_attempt_counts.values()) / max(completed_challenges, 1), 1
        ),
        "journey_duration": journey_duration,
        "hardest_challenge": (
            max(challenge_attempt_counts.items(), key=lambda x: x[1])
            if challenge_attempt_counts
            else None
        ),
    }

    # Find the hardest challenge details
    if stats["hardest_challenge"]:
        hardest_id = stats["hardest_challenge"][0]
        hardest_challenge = JourneyChallenge.objects.filter(id=hardest_id).first()
        stats["hardest_challenge_obj"] = hardest_challenge
        stats["hardest_challenge_attempts"] = stats["hardest_challenge"][1]

    context = {
        "coach": coach,
        "progress": progress,
        "chapter_data": chapter_data,
        "stats": stats,
        "questionnaire_responses": questionnaire_responses,
        "all_attempts": all_attempts,  # Keep for backward compatibility
    }
    return render(request, "crush_lu/coach_view_user_progress.html", context)


@coach_required
def coach_verification_history(request):
    """Browse all past profile verifications with filters"""
    coach = request.coach

    submissions = (
        ProfileSubmission.objects.filter(
            coach=coach, status__in=["approved", "rejected", "revision"]
        )
        .select_related("profile__user")
        .order_by("-reviewed_at")
    )

    # Filter by status
    status_filter = request.GET.get("status", "all")
    if status_filter in ("approved", "rejected", "revision"):
        submissions = submissions.filter(status=status_filter)

    # Filter by gender
    gender_filter = request.GET.get("gender", "all")
    if gender_filter == "F":
        submissions = submissions.filter(profile__gender="F")
    elif gender_filter == "M":
        submissions = submissions.filter(profile__gender="M")
    elif gender_filter == "other":
        submissions = submissions.filter(profile__gender__in=["NB", "O", "P", ""])

    # Filter by date range
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")
    if date_from:
        try:
            from datetime import datetime

            dt = datetime.strptime(date_from, "%Y-%m-%d")
            submissions = submissions.filter(
                reviewed_at__date__gte=dt.date()
            )
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import datetime

            dt = datetime.strptime(date_to, "%Y-%m-%d")
            submissions = submissions.filter(
                reviewed_at__date__lte=dt.date()
            )
        except ValueError:
            pass

    paginator = Paginator(submissions, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "coach": coach,
        "page_obj": page_obj,
        "status_filter": status_filter,
        "gender_filter": gender_filter,
        "date_from": date_from,
        "date_to": date_to,
        "total_count": paginator.count,
    }
    return render(request, "crush_lu/coach_verification_history.html", context)
