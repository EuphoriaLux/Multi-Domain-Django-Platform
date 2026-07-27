"""
Event check-in API endpoint.

Handles QR code scans from coaches at event entrances. The QR code contains
a signed URL with the registration ID and token. When scanned, this endpoint
verifies the token and marks the registration as attended.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.core.signing import BadSignature, Signer
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .decorators import coach_required
from .models import CrushProfile, EventRegistration, ProfileSubmission

logger = logging.getLogger(__name__)


def _scanning_coach(request):
    """The active coach whose session made this request, or ``None``.

    ``event_checkin_api`` authenticates on the **signed token only** — it has no
    ``@coach_required`` — because the QR itself is the credential. That is fine
    for marking attendance, but the attendee holds their own QR: without this
    check a member could POST their own check-in URL and self-verify.

    The scanner page is used by a logged-in coach and posts same-origin
    (`fetch(url, {method:"POST"})` sends cookies), so a genuine door scan always
    carries a coach session and a self-post never does.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    coach = getattr(user, "crushcoach", None)
    return coach if coach is not None and coach.is_active else None


def _apply_verification(profile, method, coach, now):
    """Persist a verification — fields AND the pending submission.

    Shared by the manual Verify button and the auto-verify on attendance so the
    two cannot drift: an earlier version of the auto-verify wrote only the
    profile fields, silently skipping the submission approval (and the referral
    reward and welcome email in `_run_post_verification_side_effects`). Because
    it marked the profile verified immediately, the manual path that does that
    work would never run afterwards.

    DB writes only — safe to call inside a transaction. Run the side effects
    after it commits.
    """
    profile.is_approved = True
    profile.approved_at = now
    profile.verification_status = "verified"
    profile.verification_method = method
    profile.save(
        update_fields=[
            "is_approved",
            "approved_at",
            "verification_status",
            "verification_method",
        ]
    )

    # Approve a pending submission opportunistically, gated on the
    # expired-latest invariant: when the newest row was closed out by the pivot
    # cleanup (latest_for_profile returns None), the member is a self-serve
    # case — never resurrect an older pending row.
    submission = None
    if ProfileSubmission.latest_for_profile(profile) is not None:
        submission = (
            ProfileSubmission.objects.filter(profile=profile, status="pending")
            .order_by("-submitted_at")
            .first()
        )
    if submission:
        submission.status = "approved"
        submission.reviewed_at = now
        submission.review_call_completed = True
        if not submission.coach_id:
            submission.coach = coach
        submission.coach_notes = (
            (submission.coach_notes + "\n" if submission.coach_notes else "")
            + "Verified in person at event by coach"
        ).strip()
        submission.save(
            update_fields=[
                "status",
                "reviewed_at",
                "coach_notes",
                "review_call_completed",
                "coach",
            ]
        )


def _run_post_verification_side_effects(user, profile, request, ref):
    """Referral credit + welcome email. Call AFTER the transaction commits.

    Event verification is the primary path for new members, so skipping these
    would silently stop paying referrers and stop welcoming people. Both are
    best-effort: neither may break the door flow.
    """
    try:
        from .referrals import check_and_apply_profile_approved_reward

        check_and_apply_profile_approved_reward(profile)
    except Exception:
        logger.warning(
            "Failed to apply profile-approved referral reward for registration %s", ref
        )

    try:
        from .notification_service import notify_profile_approved

        notify_profile_approved(
            user=user, profile=profile, coach_notes=None, request=request
        )
    except Exception:
        logger.warning("Failed to send approval notification for registration %s", ref)


def _auto_verify_on_attendance(request, registration, now):
    """Verify a `pending` profile because a coach checked them in at the door.

    Attending the event *is* the verification for the ordinary walk-in, so the
    coach should not have to tap a second button. Two deliberate exceptions keep
    their own explicit action (the Verify button stays visible for exactly
    these):

    * **Premium members** — the "only their own coach may verify" rule is
      intentional for paying members, so it is left to that coach.
    * **Profiles with no photo** — since the fast-track change (PR #650) a
      member can complete their profile without one, and a scan cannot confirm
      an identity there is nothing on screen to compare. Verification would be
      asserting something nobody checked.

    Returns the verified profile (so the caller can run the side effects once
    the transaction commits), or ``None``.
    """
    coach = _scanning_coach(request)
    if coach is None:
        return None

    profile = CrushProfile.objects.filter(user_id=registration.user_id).first()
    if profile is None or profile.verification_status != "pending":
        return None
    if profile.has_active_premium:
        return None
    if not profile.photo_1:
        return None

    _apply_verification(profile, "coach_event", coach, now)
    logger.info(
        "[CHECKIN-VERIFY] Verified profile pk=%s via attendance at event pk=%s",
        profile.pk,
        registration.event_id,
    )
    return profile


@csrf_exempt
@require_POST
def event_checkin_api(request, registration_id, token):
    """
    Check in an attendee via signed QR code token.

    The token is generated by Django's Signer and encodes
    "{registration_id}:{event_id}". This endpoint:

    1. Verifies the signed token
    2. Validates registration exists and is confirmed
    3. Checks the event is within the check-in window (default: 12 hours)
    4. Marks registration as attended with checked_in_at timestamp
    5. Returns JSON with attendee name and success status
    """
    # Verify token
    signer = Signer()
    try:
        unsigned = signer.unsign(token)
    except BadSignature:
        logger.warning("Invalid check-in token for registration %s", registration_id)
        return JsonResponse(
            {"success": False, "error": "Invalid or expired check-in token."},
            status=400,
        )

    # Parse token payload
    try:
        token_reg_id, token_event_id = unsigned.split(":")
        token_reg_id = int(token_reg_id)
        token_event_id = int(token_event_id)
    except (ValueError, AttributeError):
        return JsonResponse(
            {"success": False, "error": "Malformed check-in token."},
            status=400,
        )

    # Verify token matches URL
    if token_reg_id != registration_id:
        return JsonResponse(
            {"success": False, "error": "Token does not match registration."},
            status=400,
        )

    # Fetch registration
    try:
        registration = EventRegistration.objects.select_related(
            "event", "user", "user__crushprofile"
        ).get(id=registration_id)
    except EventRegistration.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "Registration not found."},
            status=404,
        )

    # Verify event ID matches
    if registration.event_id != token_event_id:
        return JsonResponse(
            {"success": False, "error": "Token does not match event."},
            status=400,
        )

    # Check if already attended
    if registration.status == "attended":
        # A re-scan must still verify. Two ways to be here with a pending
        # profile: the first scan carried no coach session (the self-scan this
        # endpoint deliberately still allows), or the row was marked attended
        # before this feature existed. Without this the coach is silently back
        # to tapping Verify.
        # Lock the registration first, matching the main check-in path below.
        # Without it two near-simultaneous re-scans (a double-tap, or two
        # coaches scanning the same badge) can both read `pending`, both
        # verify, and both schedule the side effects. The referral reward
        # self-guards, but `notify_profile_approved` does not — the attendee
        # would get two "you're approved" emails. Serialising here means the
        # loser re-reads `verified` and does nothing.
        # `now` is only bound below, with the check-in window check.
        with transaction.atomic():
            locked_registration = (
                EventRegistration.objects.select_for_update()
                .select_related("event", "user")
                .get(id=registration_id)
            )
            rescan_verified = _auto_verify_on_attendance(
                request, locked_registration, timezone.now()
            )
        if rescan_verified is not None:
            # `registration` was loaded with `user__crushprofile` selected, so
            # its cached profile predates the write above — re-read it or the
            # toast would report the attendee as still unverified.
            registration = EventRegistration.objects.select_related(
                "event", "user", "user__crushprofile"
            ).get(id=registration_id)

        display_name = _get_display_name(registration)
        response_data = {
            "success": True,
            "already_checked_in": True,
            "registration_id": registration.id,
            "attendee_name": display_name,
            "checked_in_at": (
                registration.checked_in_at.isoformat()
                if registration.checked_in_at
                else None
            ),
            "message": f"{display_name} was already checked in.",
            "profile": _get_profile_data(registration),
            "auto_verified": rescan_verified is not None,
        }
        table_info = _get_existing_table_assignment(registration)
        if table_info:
            response_data.update(table_info)
        if rescan_verified is not None:
            _run_post_verification_side_effects(
                registration.user, rescan_verified, request, registration.id
            )
            _broadcast_checkin(registration.event_id, response_data)
        return JsonResponse(response_data)

    # Verify status is confirmed
    if registration.status != "confirmed":
        return JsonResponse(
            {
                "success": False,
                "error": f"Registration status is '{registration.get_status_display()}'. Only confirmed registrations can be checked in.",
            },
            status=400,
        )

    # Check event is within check-in window
    checkin_window_hours = getattr(settings, "EVENT_CHECKIN_WINDOW_HOURS", 12)
    now = timezone.now()
    event_start = registration.event.date_time
    from datetime import timedelta

    window_start = event_start - timedelta(hours=checkin_window_hours)
    window_end = event_start + timedelta(hours=checkin_window_hours)

    if not (window_start <= now <= window_end):
        return JsonResponse(
            {
                "success": False,
                "error": f"Check-in is only available within {checkin_window_hours} hours of the event.",
            },
            status=400,
        )

    # Mark as attended (with lock to prevent duplicate concurrent check-ins)
    table_assignment = None
    with transaction.atomic():
        registration = (
            EventRegistration.objects.select_for_update()
            .select_related("event", "user")
            .get(id=registration_id)
        )
        if registration.status == "attended":
            # A re-scan must still be able to verify. Two ways to arrive here
            # with a pending profile: the first scan carried no coach session
            # (the self-scan case this endpoint deliberately still allows), or
            # the registration was marked attended before this feature existed.
            # Without this the coach is silently back to tapping Verify.
            already_verified_profile = _auto_verify_on_attendance(
                request, registration, now
            )
            display_name = _get_display_name(registration)
            response_data = {
                "success": True,
                "already_checked_in": True,
                "registration_id": registration.id,
                "attendee_name": display_name,
                "checked_in_at": (
                    registration.checked_in_at.isoformat()
                    if registration.checked_in_at
                    else None
                ),
                "message": f"{display_name} was already checked in.",
                "profile": _get_profile_data(registration),
                "auto_verified": already_verified_profile is not None,
            }
            table_info = _get_existing_table_assignment(registration)
            if table_info:
                response_data.update(table_info)
            if already_verified_profile is not None:
                # This branch is the loser of the `select_for_update` race, but
                # it still performs real writes, so it must announce them like
                # its sibling above — otherwise other coaches watching the live
                # list never see the row flip to verified until they reload.
                # Deferred to commit so the broadcast cannot describe a state
                # that gets rolled back.
                transaction.on_commit(
                    lambda: (
                        _run_post_verification_side_effects(
                            registration.user,
                            already_verified_profile,
                            request,
                            registration.id,
                        ),
                        _broadcast_checkin(registration.event_id, response_data),
                    )
                )
            return JsonResponse(response_data)
        registration.status = "attended"
        registration.checked_in_at = now
        registration.save(update_fields=["status", "checked_in_at", "updated_at"])

        # Attending IS the verification, for the ordinary case. Side effects
        # (referral credit, welcome email) run on commit — never inside the
        # transaction, or a rollback would leave the email already sent.
        verified_profile = _auto_verify_on_attendance(request, registration, now)
        auto_verified = verified_profile is not None
        if auto_verified:
            transaction.on_commit(
                lambda: _run_post_verification_side_effects(
                    registration.user, verified_profile, request, registration.id
                )
            )

        # Quiz table assignment (if this is a quiz night event)
        try:
            quiz_event = getattr(registration.event, "quiz", None)
            if quiz_event and quiz_event.num_tables:
                from .services.quiz_rotation import assign_table_on_checkin

                table_assignment = assign_table_on_checkin(
                    quiz_event, registration.user
                )
        except Exception:
            logger.exception(
                "Quiz table assignment failed for registration %s",
                registration.id,
            )

    # Crush Connect Event Lobby: evaluate participation only after attendance
    # committed. Best-effort — a lobby failure must be logged but must never
    # fail or delay a valid event check-in (spec §10.1/§19).
    try:
        from .services.event_lobby import handle_checkin as lobby_handle_checkin

        _lobby_participation, lobby_created = lobby_handle_checkin(registration)
        if lobby_created:
            from .views_event_lobby import broadcast_participant_joined

            broadcast_participant_joined(registration.event_id)
    except Exception:
        logger.exception(
            "Event lobby participation evaluation failed for registration %s",
            registration.id,
        )

    display_name = _get_display_name(registration)

    logger.info(
        "Checked in registration %s (user %s) for event %s",
        registration.id,
        registration.user_id,
        registration.event_id,
    )

    response_data = {
        "success": True,
        "already_checked_in": False,
        "registration_id": registration.id,
        "attendee_name": display_name,
        "checked_in_at": now.isoformat(),
        "message": f"{display_name} has been checked in!",
        # Built AFTER the auto-verify above, so `profile.is_approved` in the
        # toast reflects this scan rather than the pre-check-in state — the
        # scanner must not show an "Unverified Profile" warning for someone it
        # just verified.
        "profile": _get_profile_data(registration),
        "auto_verified": auto_verified,
    }
    if table_assignment:
        response_data["table_number"] = table_assignment["table_number"]
        response_data["role"] = table_assignment["role"]

    _broadcast_checkin(registration.event_id, response_data)

    # Notify quiz participants about the new tablemate
    if table_assignment:
        _broadcast_quiz_table_update(registration.event, table_assignment)

    return JsonResponse(response_data)


@coach_required
@require_POST
def coach_mark_verified(request, event_id, registration_id):
    """Verify an attendee in person at an event (Option 2 / premium Option 3).

    A coach running the event can flip an unverified attendee to ``verified``.
    For ordinary walk-ins any active coach may do this (method ``coach_event``).
    For a premium member (one with an ``assigned_coach``) only that assigned
    coach may verify them, and the method is recorded as ``premium_coach``.
    """
    coach = request.coach
    now = timezone.now()

    with transaction.atomic():
        # Lock only the registration row — crushprofile is the nullable side of
        # an outer join, which PostgreSQL refuses to lock under FOR UPDATE.
        registration = (
            EventRegistration.objects.select_for_update(of=("self",))
            .select_related("user", "event")
            .filter(id=registration_id, event_id=event_id)
            .first()
        )
        if registration is None:
            return JsonResponse(
                {"success": False, "error": str(_("Registration not found."))},
                status=404,
            )
        if registration.status not in ("confirmed", "attended"):
            return JsonResponse(
                {
                    "success": False,
                    "error": str(
                        _("Only confirmed or checked-in attendees can be verified.")
                    ),
                },
                status=400,
            )

        try:
            profile = registration.user.crushprofile
        except Exception:
            return JsonResponse(
                {"success": False, "error": str(_("Attendee has no profile."))},
                status=404,
            )

        # Premium members may only be verified by their own assigned coach.
        # Gate on the real entitlement, not on `assigned_coach`. A coach is
        # granted free on first attendance (`assign_coach_on_first_attendance`),
        # so keying off the FK made an ordinary attendee "premium" the moment
        # they were scanned — and every coach except the event's `.first()` one
        # then got a 403 trying to verify them at the door.
        if profile.has_active_premium:
            if profile.assigned_coach_id and profile.assigned_coach_id != coach.id:
                return JsonResponse(
                    {
                        "success": False,
                        "error": str(
                            _("This premium member is verified by their own coach.")
                        ),
                    },
                    status=403,
                )
            method = "premium_coach"
        else:
            method = "coach_event"

        if profile.verification_status == "verified":
            return JsonResponse(
                {
                    "success": True,
                    "already_verified": True,
                    "registration_id": registration.id,
                    "attendee_name": _get_display_name(registration),
                    "verification_method": profile.verification_method,
                    "message": str(_("Already verified.")),
                }
            )

        # Same workflow the auto-verify on attendance runs, so the two paths
        # cannot drift apart.
        _apply_verification(profile, method, coach, now)

    logger.info(
        "Coach %s verified registration %s (user %s) at event %s via %s",
        coach,
        registration.id,
        registration.user_id,
        event_id,
        method,
    )

    # Referral credit + welcome email. Shared with the auto-verify on
    # attendance: event verification is the primary path for new members, so
    # both paths must run these or referrers silently stop being paid.
    _run_post_verification_side_effects(
        registration.user, profile, request, registration.id
    )

    response_data = {
        "success": True,
        "already_verified": False,
        "registration_id": registration.id,
        "attendee_name": _get_display_name(registration),
        "verification_method": method,
        "message": str(_("%(name)s is now verified."))
        % {"name": _get_display_name(registration)},
        "profile": _get_profile_data(registration),
    }
    _broadcast_checkin(registration.event_id, response_data)
    return JsonResponse(response_data)


def _get_display_name(registration):
    """Get privacy-aware display name for attendee."""
    try:
        return registration.user.crushprofile.display_name
    except Exception:
        return _("Attendee")


def _get_profile_data(registration):
    """Build privacy-aware profile card data for check-in toast."""
    from .models import ProfileSubmission

    try:
        profile = registration.user.crushprofile
    except Exception:
        return {}
    data = {
        "display_name": profile.display_name,
        "gender": profile.gender or "",
        "age_display": profile.age_display,
        "is_approved": profile.is_approved,
        "user_id": registration.user_id,
        "location": profile.location or "",
        # Structured taxonomy labels replace the retired free-text interests
        # (Event Identity redesign §7): the check-in toast never ships the
        # legacy free-text field to event staff again.
        "interests": ", ".join(profile.checkin_interest_labels()),
    }
    if profile.photo_1:
        data["photo_url"] = reverse(
            "crush_lu:serve_profile_photo",
            kwargs={"user_id": registration.user_id, "photo_field": "photo_1"},
        )

    # Include coach info for unverified profiles
    if not profile.is_approved:
        latest_submission = (
            ProfileSubmission.objects.filter(profile=profile)
            .select_related("coach__user")
            .order_by("-submitted_at")
            .first()
        )
        if latest_submission:
            data["submission_status"] = latest_submission.get_status_display()
            if latest_submission.coach:
                data["coach_name"] = (
                    f"{latest_submission.coach.user.first_name} {latest_submission.coach.user.last_name}".strip()
                )

    return data


def _broadcast_checkin(event_id, response_data):
    """Broadcast check-in update to WebSocket group for live coach updates."""
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            f"checkin_{event_id}",
            {"type": "checkin.update", "data": response_data},
        )


def _broadcast_quiz_table_update(event, table_assignment):
    """Notify quiz participants at a table that a new person has joined."""
    from .models.quiz import QuizTable

    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    try:
        quiz_event = getattr(event, "quiz", None)
        if not quiz_event:
            return
        table_number = table_assignment.get("table_number")
        if not table_number:
            return
        # Look up the QuizTable PK — the consumer subscribes using table PK, not table_number
        quiz_table = QuizTable.objects.filter(
            quiz=quiz_event, table_number=table_number
        ).first()
        if not quiz_table:
            return
        # Broadcast to the specific table group so only affected participants refresh
        async_to_sync(channel_layer.group_send)(
            f"quiz_{quiz_event.id}_table_{quiz_table.id}",
            {
                "type": "quiz.table_update",
                "data": {"table_number": table_number},
            },
        )
        # Also broadcast to display-specific group so the projector page updates
        async_to_sync(channel_layer.group_send)(
            f"quiz_{quiz_event.id}_display",
            {
                "type": "quiz.table_update",
                "data": {"table_number": table_number},
            },
        )
    except Exception:
        logger.exception("Failed to broadcast quiz table update for event %s", event.id)


def _get_existing_table_assignment(registration):
    """Look up existing quiz table assignment for a registration.

    Returns dict with table_number and role, or None.
    """
    try:
        quiz_event = getattr(registration.event, "quiz", None)
        if not quiz_event:
            return None
        from .models.quiz import QuizRotationSchedule, QuizTableMembership

        membership = (
            QuizTableMembership.objects.filter(
                table__quiz=quiz_event, user=registration.user
            )
            .select_related("table")
            .first()
        )
        if not membership:
            return None
        rotation = QuizRotationSchedule.objects.filter(
            quiz=quiz_event, round_number=0, user=registration.user
        ).first()
        return {
            "table_number": membership.table.table_number,
            "role": rotation.role if rotation else "",
        }
    except Exception:
        return None
