"""Self-booking flow for hybrid-coach screening calls (Phase 5).

Entry point is /book/<uuid:booking_token>/. The token is issued by the
Phase 3 `sla_offer_fallback_task` when a user's SLA is about to slip;
until then no user reaches these views.

Views here are deliberately unauthenticated — the UUID is the credential
(122 bits of entropy, expires 30 days after fallback_offered_at). Rate
limiting on the confirm POST adds a second line of defence against
token guessing.
"""
from datetime import datetime
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .decorators import ratelimit
from .models import CrushCoach, ProfileSubmission, ScreeningSlot
from .services.slot_generator import bookable_slots


def _resolve_token(booking_token):
    """Normalise path param + raise 404 on anything invalid or expired."""
    try:
        token = UUID(str(booking_token))
    except (TypeError, ValueError):
        raise Http404("Invalid booking token")

    try:
        submission = ProfileSubmission.objects.select_related(
            "coach__user", "profile__user"
        ).get(booking_token=token)
    except ProfileSubmission.DoesNotExist:
        raise Http404("Invalid booking token")

    if (
        submission.booking_token_expires_at
        and submission.booking_token_expires_at < timezone.now()
    ):
        raise Http404("Booking link expired")

    # Refuse tokens once the submission is no longer in the bookable state.
    # Tokens are only ever minted while status='pending' and the call hasn't
    # happened yet, so anything else means the submission moved on (approved,
    # rejected, review_call_completed, or is_paused). Without this guard, a
    # stale emailed link could still reach claim_for_submission and spawn a
    # booked slot / coach reassignment against a closed submission.
    if submission.status != "pending":
        raise Http404("Booking link no longer valid")
    if submission.review_call_completed or submission.is_paused:
        raise Http404("Booking link no longer valid")

    return submission


def book_screening(request, booking_token):
    """Public landing page listing available slots across opted-in coaches."""
    submission = _resolve_token(booking_token)

    # Show existing booking if the user has one (lets them review without
    # double-booking). Otherwise list bookable slots from hybrid-enabled
    # coaches, preferring their assigned coach at the top.
    existing_booking = (
        ScreeningSlot.objects.filter(
            submission=submission, status="booked"
        )
        .select_related("coach__user")
        .first()
    )

    coach_blocks = []
    if not existing_booking:
        coaches = list(
            CrushCoach.objects.filter(
                is_active=True, hybrid_features_enabled=True
            ).select_related("user")
        )
        # Put the assigned coach first for continuity.
        assigned_id = submission.coach_id
        coaches.sort(key=lambda c: (0 if c.id == assigned_id else 1, c.id))

        for coach in coaches:
            slots = bookable_slots(coach, days=14)
            if not slots:
                continue
            coach_blocks.append(
                {
                    "coach": coach,
                    "is_assigned": coach.id == assigned_id,
                    "slots": slots,
                }
            )

    context = {
        "submission": submission,
        "existing_booking": existing_booking,
        "coach_blocks": coach_blocks,
    }
    return render(request, "crush_lu/book_screening.html", context)


@require_http_methods(["POST"])
@ratelimit(key="ip", rate="20/h", method="POST")
def confirm_booking(request, booking_token):
    """Claim a slot for this submission (race-safe)."""
    submission = _resolve_token(booking_token)

    coach_id = request.POST.get("coach_id")
    start_iso = request.POST.get("start_at")
    end_iso = request.POST.get("end_at")

    if not (coach_id and start_iso and end_iso):
        messages.error(request, _("Missing slot information. Please try again."))
        return redirect("crush_lu:book_screening", booking_token=booking_token)

    try:
        coach_id_int = int(coach_id)
        start_at = datetime.fromisoformat(start_iso)
        end_at = datetime.fromisoformat(end_iso)
    except (TypeError, ValueError):
        messages.error(request, _("Invalid slot selection."))
        return redirect("crush_lu:book_screening", booking_token=booking_token)

    try:
        slot, submission = ScreeningSlot.claim_for_submission(
            coach_id=coach_id_int,
            start_at=start_at,
            end_at=end_at,
            submission_token=submission.booking_token,
        )
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else _("Could not book this slot."))
        return redirect("crush_lu:book_screening", booking_token=booking_token)
    except ProfileSubmission.DoesNotExist:
        raise Http404("Invalid booking token")

    _send_confirmation_email(submission, slot, request)

    messages.success(request, _("Your screening call is booked. Check your email for the calendar invite."))
    return redirect("crush_lu:book_screening", booking_token=booking_token)


@require_http_methods(["POST"])
@ratelimit(key="ip", rate="10/h", method="POST")
def cancel_booking(request, booking_token):
    """Cancel the current screening-call booking (if any)."""
    submission = _resolve_token(booking_token)
    slot = ScreeningSlot.objects.filter(
        submission=submission, status="booked"
    ).first()
    if not slot:
        messages.info(request, _("No active booking to cancel."))
        return redirect("crush_lu:book_screening", booking_token=booking_token)

    slot.status = "cancelled"
    slot.cancelled_reason = "user_cancelled"
    slot.save(update_fields=["status", "cancelled_reason", "updated_at"])
    submission.log_system_action(
        "booking_cancelled",
        actor=f"user:{submission.profile.user_id}",
        slot_id=slot.id,
    )
    submission.save(update_fields=["system_actions"])

    messages.success(request, _("Your booking has been cancelled. You can pick a new slot any time."))
    return redirect("crush_lu:book_screening", booking_token=booking_token)


def _send_confirmation_email(submission, slot, request):
    """Send the user a booking confirmation with an ICS attachment.

    Uses the same Graph email backend everything else does; falls back
    to console in DEBUG. Silent on error — the booking itself is already
    durable, and the missing email will surface in logs.
    """
    import logging

    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    from .services.ics_helper import generate_screening_ics

    logger = logging.getLogger(__name__)
    try:
        user_email = submission.profile.user.email
        if not user_email:
            logger.info(
                "hybrid_coach.booking_confirmed_no_email",
                extra={"submission_id": submission.id},
            )
            return

        booking_url = request.build_absolute_uri(
            reverse(
                "crush_lu:book_screening",
                kwargs={"booking_token": submission.booking_token},
            )
        )
        coach = slot.coach
        context = {
            "submission": submission,
            "slot": slot,
            "coach": coach,
            "booking_url": booking_url,
        }
        body_txt = render_to_string("crush_lu/emails/screening_confirmed.txt", context)
        body_html = render_to_string("crush_lu/emails/screening_confirmed.html", context)

        msg = EmailMultiAlternatives(
            subject=str(_("Your Crush.lu screening call is booked")),
            body=body_txt,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=[user_email],
        )
        msg.attach_alternative(body_html, "text/html")
        msg.attach(
            "screening-call.ics",
            generate_screening_ics(submission, slot, booking_url),
            "text/calendar; method=REQUEST",
        )
        msg.send(fail_silently=True)
        logger.info(
            "hybrid_coach.booking_confirmed",
            extra={
                "submission_id": submission.id,
                "slot_id": slot.id,
                "coach_id": coach.id,
            },
        )
    except Exception as exc:  # pragma: no cover — never block booking on email failure
        logger.warning("hybrid_coach.booking_email_failed: %s", exc)
