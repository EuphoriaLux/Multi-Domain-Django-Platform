"""
Views for the Crush Spark feature.

Sender views: request a spark, view status, create journey content.
Recipient views: view received anonymous sparks.
Coach views: list pending sparks, assign recipients.
API views: poll spark status.
"""
import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from .decorators import crush_login_required, coach_required
from .forms_crush_spark import CoachSparkAssignForm, SparkJourneyForm, SparkRequestForm
from .models import (
    CrushCoach,
    CrushProfile,
    EventRegistration,
    MeetupEvent,
)
from .models.crush_spark import CrushSpark

logger = logging.getLogger(__name__)


# =============================================================================
# SENDER VIEWS
# =============================================================================


@crush_login_required
def spark_request(request, event_id):
    """Submit a Crush Spark request for an event."""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Verify user attended the event
    registration = EventRegistration.objects.filter(
        event=event, user=request.user, status="attended"
    ).first()
    if not registration:
        messages.error(request, _("You must have attended this event to send a Crush Spark."))
        return redirect("crush_lu:event_detail", event_id=event.id)

    # Check deadline
    deadline = event.date_time + timedelta(hours=event.spark_request_deadline_hours)
    if timezone.now() > deadline:
        messages.error(request, _("The deadline for Crush Spark requests has passed."))
        return redirect("crush_lu:event_detail", event_id=event.id)

    # Check max sparks limit (use select_for_update for race conditions)
    with transaction.atomic():
        existing_count = CrushSpark.objects.select_for_update().filter(
            event=event,
            sender=request.user,
        ).exclude(status=CrushSpark.Status.CANCELLED).count()

        if existing_count >= event.max_sparks_per_event:
            messages.error(
                request,
                _("You've reached the maximum number of Crush Sparks for this event (%(max)s).")
                % {"max": event.max_sparks_per_event},
            )
            return redirect("crush_lu:spark_list")

    if request.method == "POST":
        form = SparkRequestForm(request.POST)
        if form.is_valid():
            spark = form.save(commit=False)
            spark.event = event
            spark.sender = request.user

            # Auto-assign coach from the event (use sender's coach if available)
            try:
                coach = CrushCoach.objects.filter(
                    user__crushprofile__assigned_coach__isnull=False,
                    is_active=True,
                ).first()
                spark.assigned_coach = coach
            except Exception:
                pass

            spark.save()
            messages.success(
                request,
                _("Your Crush Spark has been submitted! A coach will identify your person."),
            )
            return redirect("crush_lu:spark_detail", spark_id=spark.id)
    else:
        form = SparkRequestForm()

    context = {
        "form": form,
        "event": event,
        "existing_count": existing_count,
        "max_sparks": event.max_sparks_per_event,
    }
    return render(request, "crush_lu/spark_request.html", context)


@crush_login_required
def spark_list(request):
    """Dashboard showing all sent and received sparks."""
    sent_sparks = (
        CrushSpark.objects.filter(sender=request.user)
        .select_related("event", "recipient__crushprofile", "assigned_coach")
        .order_by("-created_at")
    )
    received_sparks = (
        CrushSpark.objects.filter(
            recipient=request.user,
            status__in=[
                CrushSpark.Status.DELIVERED,
                CrushSpark.Status.COMPLETED,
            ],
        )
        .select_related("event")
        .order_by("-delivered_at")
    )

    context = {
        "sent_sparks": sent_sparks,
        "received_sparks": received_sparks,
    }
    return render(request, "crush_lu/spark_list.html", context)


@crush_login_required
def spark_detail(request, spark_id):
    """View a single spark's status and details."""
    spark = get_object_or_404(
        CrushSpark.objects.select_related(
            "event", "recipient__crushprofile", "assigned_coach", "journey"
        ),
        id=spark_id,
    )

    # Only sender or recipient can view
    if spark.sender != request.user and spark.recipient != request.user:
        messages.error(request, _("You don't have permission to view this spark."))
        return redirect("crush_lu:spark_list")

    is_sender = spark.sender == request.user

    context = {
        "spark": spark,
        "is_sender": is_sender,
    }
    return render(request, "crush_lu/spark_detail.html", context)


@crush_login_required
def spark_create_journey(request, spark_id):
    """Multi-step form for sender to create journey content (upload media, write message)."""
    spark = get_object_or_404(
        CrushSpark.objects.select_related("event", "recipient__crushprofile"),
        id=spark_id,
        sender=request.user,
        status__in=[CrushSpark.Status.COACH_ASSIGNED, CrushSpark.Status.COACH_APPROVED],
    )

    if request.method == "POST":
        form = SparkJourneyForm(request.POST, request.FILES, instance=spark)
        if form.is_valid():
            form.save()

            # Create the Wonderland journey
            from .utils.journey_creator import create_spark_wonderland_journey

            try:
                journey, special_exp = create_spark_wonderland_journey(spark)
                spark.journey = journey
                spark.special_experience = special_exp
                spark.status = CrushSpark.Status.JOURNEY_CREATED
                spark.journey_created_at = timezone.now()
                spark.save(
                    update_fields=[
                        "journey",
                        "special_experience",
                        "status",
                        "journey_created_at",
                    ]
                )

                # Deliver to recipient
                spark.status = CrushSpark.Status.DELIVERED
                spark.delivered_at = timezone.now()
                spark.save(update_fields=["status", "delivered_at"])

                messages.success(
                    request,
                    _("Your anonymous journey has been created and delivered!"),
                )
                return redirect("crush_lu:spark_detail", spark_id=spark.id)

            except Exception as e:
                logger.error(f"Failed to create spark journey {spark.id}: {e}", exc_info=True)
                messages.error(
                    request,
                    _("There was an error creating the journey. Please try again."),
                )
    else:
        form = SparkJourneyForm(instance=spark)

    context = {
        "form": form,
        "spark": spark,
    }
    return render(request, "crush_lu/spark_create_journey.html", context)


# =============================================================================
# HTMX INLINE VIEWS (attendees page)
# =============================================================================


@crush_login_required
@require_http_methods(["POST"])
def spark_send_inline(request, event_id, user_id):
    """HTMX: Send a spark to an attendee inline from the attendees page."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    event = get_object_or_404(MeetupEvent, id=event_id)
    recipient = get_object_or_404(User, id=user_id)

    # Verify sender attended the event
    sender_reg = EventRegistration.objects.filter(
        event=event, user=request.user, status="attended"
    ).first()
    if not sender_reg:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": _("You must have attended this event to send a Crush Spark.")},
        )

    # Verify recipient attended the event
    recipient_reg = EventRegistration.objects.filter(
        event=event, user=recipient, status="attended"
    ).first()
    if not recipient_reg:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": _("This person did not attend the event.")},
        )

    # Prevent self-spark
    if recipient == request.user:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": _("You cannot send a spark to yourself.")},
        )

    # Check deadline
    deadline = event.date_time + timedelta(hours=event.spark_request_deadline_hours)
    if timezone.now() > deadline:
        return render(
            request,
            "crush_lu/_htmx_error.html",
            {"message": _("The deadline for Crush Spark requests has passed.")},
        )

    # Check for existing spark to same person
    existing = CrushSpark.objects.filter(
        event=event, sender=request.user, recipient=recipient,
    ).exclude(status=CrushSpark.Status.CANCELLED).exists()
    if existing:
        return render(
            request,
            "crush_lu/_spark_sent_success.html",
            {"already_sent": True},
        )

    # Check max sparks limit
    with transaction.atomic():
        existing_count = CrushSpark.objects.select_for_update().filter(
            event=event, sender=request.user,
        ).exclude(status=CrushSpark.Status.CANCELLED).count()

        if existing_count >= event.max_sparks_per_event:
            return render(
                request,
                "crush_lu/_htmx_error.html",
                {"message": _("You've reached the maximum number of Crush Sparks for this event (%(max)s).")
                 % {"max": event.max_sparks_per_event}},
            )

        # Create the spark with recipient already set
        CrushSpark.objects.create(
            event=event,
            sender=request.user,
            recipient=recipient,
            status=CrushSpark.Status.PENDING_REVIEW,
        )

    return render(
        request,
        "crush_lu/_spark_sent_success.html",
        {"already_sent": False},
    )


@crush_login_required
def spark_actions(request, event_id, user_id):
    """HTMX: Get current spark actions/status for an attendee."""
    event = get_object_or_404(MeetupEvent, id=event_id)

    # Check if spark exists from request.user to this user for this event
    spark = CrushSpark.objects.filter(
        event=event, sender=request.user, recipient_id=user_id,
    ).exclude(status=CrushSpark.Status.CANCELLED).first()

    # Check deadline
    deadline = event.date_time + timedelta(hours=event.spark_request_deadline_hours)
    spark_deadline_active = timezone.now() <= deadline

    # Check remaining sparks
    existing_count = CrushSpark.objects.filter(
        event=event, sender=request.user,
    ).exclude(status=CrushSpark.Status.CANCELLED).count()

    context = {
        "event": event,
        "attendee": {"user": type("obj", (), {"id": user_id})()},
        "spark": spark,
        "spark_deadline_active": spark_deadline_active,
        "sparks_remaining": event.max_sparks_per_event - existing_count,
    }
    return render(request, "crush_lu/_attendee_spark_actions.html", context)


# =============================================================================
# RECIPIENT VIEWS
# =============================================================================


@crush_login_required
def spark_received(request):
    """Inbox of received anonymous sparks (delivered or completed)."""
    sparks = (
        CrushSpark.objects.filter(
            recipient=request.user,
            status__in=[
                CrushSpark.Status.DELIVERED,
                CrushSpark.Status.COMPLETED,
            ],
        )
        .select_related("event", "journey")
        .order_by("-delivered_at")
    )

    context = {
        "sparks": sparks,
    }
    return render(request, "crush_lu/spark_received.html", context)


# =============================================================================
# COACH VIEWS
# =============================================================================


@coach_required
def coach_spark_list(request):
    """Coach dashboard showing pending spark reviews."""
    pending_sparks = (
        CrushSpark.objects.filter(
            status__in=[CrushSpark.Status.PENDING_REVIEW, CrushSpark.Status.REQUESTED],
        )
        .select_related("event", "sender__crushprofile", "recipient__crushprofile")
        .order_by("created_at")
    )

    # Also show recently reviewed sparks
    recent_sparks = (
        CrushSpark.objects.exclude(
            status__in=[CrushSpark.Status.PENDING_REVIEW, CrushSpark.Status.REQUESTED]
        )
        .select_related("event", "sender__crushprofile", "recipient__crushprofile")
        .order_by("-coach_assigned_at")[:20]
    )

    context = {
        "coach": request.coach,
        "pending_sparks": pending_sparks,
        "recent_sparks": recent_sparks,
    }
    return render(request, "crush_lu/coach_spark_list.html", context)


@coach_required
def coach_spark_assign(request, spark_id):
    """Coach reviews and approves/rejects a spark.

    For new-flow sparks (pending_review): recipient is already set, coach approves or rejects.
    For old-flow sparks (requested): coach assigns recipient from attendee list.
    """
    spark = get_object_or_404(
        CrushSpark.objects.select_related(
            "event", "sender__crushprofile", "recipient__crushprofile"
        ),
        id=spark_id,
        status__in=[CrushSpark.Status.PENDING_REVIEW, CrushSpark.Status.REQUESTED],
    )

    event = spark.event
    is_review_mode = spark.status == CrushSpark.Status.PENDING_REVIEW and spark.recipient is not None

    # For old-flow (requested without recipient), show attendee list
    attendees = None
    search = ""
    if not is_review_mode:
        attendees = (
            EventRegistration.objects.filter(event=event, status="attended")
            .exclude(user=spark.sender)
            .select_related("user__crushprofile")
            .order_by("user__first_name")
        )
        search = request.GET.get("q", "")
        if search:
            attendees = attendees.filter(
                Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
                | Q(user__crushprofile__display_name__icontains=search)
            )

    if request.method == "POST":
        action = request.POST.get("action", "")

        if is_review_mode:
            # New flow: approve or reject
            coach_notes = request.POST.get("coach_notes", "").strip()
            spark.assigned_coach = request.coach
            spark.coach_notes = coach_notes
            spark.coach_assigned_at = timezone.now()

            if action == "approve":
                spark.status = CrushSpark.Status.COACH_APPROVED
                spark.save(
                    update_fields=[
                        "assigned_coach",
                        "coach_notes",
                        "status",
                        "coach_assigned_at",
                    ]
                )
                messages.success(
                    request,
                    _("Spark approved! The sender will be notified to create their journey."),
                )
            elif action == "reject":
                spark.status = CrushSpark.Status.CANCELLED
                spark.save(
                    update_fields=[
                        "assigned_coach",
                        "coach_notes",
                        "status",
                        "coach_assigned_at",
                    ]
                )
                messages.success(request, _("Spark rejected."))
            return redirect("crush_lu:coach_spark_list")
        else:
            # Old flow: assign recipient
            form = CoachSparkAssignForm(request.POST)
            if form.is_valid():
                recipient_user_id = form.cleaned_data["recipient_user_id"]

                recipient_reg = EventRegistration.objects.filter(
                    event=event, user_id=recipient_user_id, status="attended"
                ).first()
                if not recipient_reg:
                    messages.error(request, _("Selected person did not attend this event."))
                    return redirect("crush_lu:coach_spark_assign", spark_id=spark.id)

                if recipient_user_id == spark.sender_id:
                    messages.error(request, _("Cannot assign sender as recipient."))
                    return redirect("crush_lu:coach_spark_assign", spark_id=spark.id)

                spark.recipient_id = recipient_user_id
                spark.assigned_coach = request.coach
                spark.coach_notes = form.cleaned_data.get("coach_notes", "")
                spark.status = CrushSpark.Status.COACH_ASSIGNED
                spark.coach_assigned_at = timezone.now()
                spark.save(
                    update_fields=[
                        "recipient_id",
                        "assigned_coach",
                        "coach_notes",
                        "status",
                        "coach_assigned_at",
                    ]
                )
                messages.success(
                    request,
                    _("Recipient assigned! The sender will be notified to create their journey."),
                )
                return redirect("crush_lu:coach_spark_list")
    else:
        form = CoachSparkAssignForm() if not is_review_mode else None

    context = {
        "coach": request.coach,
        "spark": spark,
        "event": event,
        "is_review_mode": is_review_mode,
        "attendees": attendees,
        "form": form,
        "search": search,
    }
    return render(request, "crush_lu/coach_spark_assign.html", context)


# =============================================================================
# API VIEWS (language-neutral, outside i18n_patterns)
# =============================================================================


@crush_login_required
def api_spark_status(request, spark_id):
    """API endpoint to poll spark status."""
    spark = get_object_or_404(CrushSpark, id=spark_id)

    # Only sender or recipient can check status
    if spark.sender != request.user and spark.recipient != request.user:
        return JsonResponse({"error": "forbidden"}, status=403)

    data = {
        "id": spark.id,
        "status": spark.status,
        "status_display": spark.get_status_display(),
        "is_sender_revealed": spark.is_sender_revealed,
    }

    if spark.sender == request.user:
        data["recipient_assigned"] = spark.recipient is not None

    return JsonResponse(data)
