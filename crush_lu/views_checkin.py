"""
Event check-in API endpoint.

Handles QR code scans from coaches at event entrances. The QR code contains
a signed URL with the registration ID and token. When scanned, this endpoint
verifies the token and marks the registration as attended.
"""

import logging
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.core.signing import BadSignature, Signer
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .decorators import coach_required
from .models import CrushProfile, EventRegistration, ProfileSubmission
from .models.events import SEAT_HOLDING_STATUSES
from .services.profile_verification import (
    claim_profile_verification,
    reject_door_verification,
    transition_unverified_profile,
)

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


#: Fields every check-in write names. `updated_at` is `auto_now`, so it is only
#: written when listed; the provenance fields are what makes
#: the undo reversible without guessing (see `_record_checkin_provenance`).
_CHECKIN_UPDATE_FIELDS = [
    "status",
    "checked_in_at",
    "checkin_prior_status",
    "checkin_granted_coach",
    "checkin_granted_coach_at",
    "checkin_attested_photo_key",
    "checkin_attested_photo_at",
    "checkin_auto_verified",
    "updated_at",
]

#: Statuses `coach_undo_checkin` will restore a row to. The two check-in paths
#: are the only ones that record a prior status, and they accept exactly these:
#: `event_checkin_api` requires `confirmed`, `coach_promote_from_waitlist`
#: requires `waitlist`. Anything else on the row is a stale or hand-written
#: value and falls back to `confirmed`, which is what the undo did for every
#: row before provenance existed.
# "pending" is restorable now that a Pending Payment seat can be scanned:
# without it, undoing an accidental scan silently converted an unpaid
# registration into a confirmed one.
UNDO_RESTORABLE_STATUSES = frozenset({"confirmed", "waitlist", "pending"})


def _record_checkin_provenance(registration):
    """Stamp how this attendance is being reached, before the status flips.

    Call immediately before setting ``status = "attended"``. Records the status
    being left behind and clears any coach grant left by an earlier check-in of
    the same row — ``assign_coach_on_first_attendance`` fills the grant back in
    during the save that follows, if this check-in is what earns one.
    """
    registration.checkin_prior_status = registration.status
    registration.checkin_granted_coach = None
    registration.checkin_granted_coach_at = None
    registration.checkin_attested_photo_key = ""
    registration.checkin_attested_photo_at = None
    registration.checkin_auto_verified = False


#: Statuses the manual Verify button may transition from. The endpoint has
#: always guarded only on "already verified" and approved anything else, so
#: narrowing this to `pending` would silently stop coaches verifying
#: `incomplete` walk-ins at the door — the scanner still offers the button for
#: every unapproved profile.
MANUAL_VERIFY_CLAIM_FROM = ("incomplete", "pending", "rejected")


def _apply_verification(profile, method, coach, now, claim_from=("pending",)):
    """Persist a verification — fields AND the open/rejected submission.

    Shared by the manual Verify button and the auto-verify on attendance so the
    two cannot drift: an earlier version of the auto-verify wrote only the
    profile fields, silently skipping the submission approval (and the referral
    reward and welcome email in `_run_post_verification_side_effects`). Because
    it marked the profile verified immediately, the manual path that does that
    work would never run afterwards.

    DB writes only — safe to call inside a transaction. Run the side effects
    after it commits.
    """
    # Conditional pending -> verified claim, so exactly one verification path
    # wins. A LuxID callback can land concurrently with a coach scan: both
    # would otherwise read `pending`, both approve, the later save would
    # overwrite the method that actually came first, and both would send the
    # welcome email. The UPDATE's own WHERE clause settles it without needing
    # every caller to hold the same lock.
    if not claim_profile_verification(
        profile,
        method=method,
        approved_at=now,
        claim_from=claim_from,
    ):
        return False

    # Approve a pending OR previously-rejected submission opportunistically,
    # gated on the expired-latest invariant: when the newest row was closed out
    # by the pivot cleanup (latest_for_profile returns None), the member is a
    # self-serve case — never resurrect an older pending row.
    #
    # A rejected row is reopened because the manual Verify button may claim a
    # profile the reject endpoint put into "rejected" (e.g. after a photo
    # mismatch was resolved at the door). Leaving the submission rejected
    # while the profile says verified is a user-visible split state:
    # context_processors then serves profile_is_approved=True alongside
    # profile_status="rejected".
    # The LATEST row, then a status check on that row — never a filtered search
    # for the newest pending/rejected one. A profile can carry a newer
    # "revision" or "recontact_coach" row above an older rejection, and
    # searching would reach past the live record to approve the stale one,
    # leaving the profile verified while its actual latest submission still
    # says something else.
    submission = ProfileSubmission.latest_for_profile(profile)
    if submission is not None and submission.status not in ("pending", "rejected"):
        submission = None
    if submission:
        reopened = submission.status == "rejected"
        submission.status = "approved"
        submission.reviewed_at = now
        submission.review_call_completed = True
        if not submission.coach_id:
            submission.coach = coach
        note = (
            "Re-verified in person at event by coach (rejection overturned)"
            if reopened
            else "Verified in person at event by coach"
        )
        submission.coach_notes = (
            (submission.coach_notes + "\n" if submission.coach_notes else "") + note
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
    return True


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


def _evaluate_lobby_participation(registration):
    """Crush Connect lobby participation, after attendance is committed.

    Called from the normal check-in AND after a re-scan verification: the gate
    requires `verification_status == "verified"`, so a member verified by the
    re-scan produced nothing on their earlier (pending) scan.

    Best-effort — a lobby failure must be logged but must never fail or delay a
    valid event check-in (spec §10.1/§19).
    """
    try:
        from .services.event_lobby import handle_checkin as lobby_handle_checkin

        _participation, created = lobby_handle_checkin(registration)
        if created:
            from .views_event_lobby import broadcast_participant_joined

            broadcast_participant_joined(registration.event_id)
    except Exception:
        logger.exception(
            "Event lobby participation evaluation failed for registration %s",
            registration.id,
        )


def _attest_and_record_photo(
    profile, registration, now, allowed_statuses=("pending", "verified"), coach=None
) -> bool:
    """Attest member's photo and record provenance on registration without extra post-save signals.

    Refuses to attest when the scanning ``coach`` is the attendee themselves.
    Both call sites (auto-verify-on-attendance and the manual Verify button)
    authenticate on an active coach account, and an attendee who happens to
    also be an active coach can otherwise scan or click their own way to a
    self-issued "Photo Verified" badge -- exactly the independent, in-person
    attestation this feature exists to guarantee.
    """
    if coach is not None and coach.user_id == registration.user_id:
        logger.warning(
            "[CHECKIN-VERIFY] Refused photo attestation: coach %s attempted to "
            "attest their own registration %s",
            coach.pk,
            registration.pk,
        )
        return False
    if not profile or not profile.photo_1:
        return False
    attested = profile.mark_current_photo_verified(
        verified_at=now,
        allowed_statuses=allowed_statuses,
    )
    if attested:
        attested_key = getattr(profile.photo_1, "name", "") or ""
        registration.checkin_attested_photo_key = attested_key
        registration.checkin_attested_photo_at = profile.photo_verified_at
        EventRegistration.objects.filter(pk=registration.pk).update(
            checkin_attested_photo_key=attested_key,
            checkin_attested_photo_at=profile.photo_verified_at,
        )
        return True
    return False


def _auto_verify_on_attendance(request, registration, now):
    """Auto-verify an unverified attendee at event check-in.

    Runs inside the registration's ``transaction.atomic`` block. Does NOT save
    the registration — that is the caller's responsibility.

    Only applies to members with ``verification_status == 'pending'`` and no
    active premium membership (premium members are verified by their own coach).
    If verification was already granted via another path (e.g. LuxID, coach
    approval), this is a no-op that leaves the existing verification intact.

    The scan also attests the attendee's primary photo (``is_photo_verified``),
    even for members already verified via LuxID or a previous event, provided
    their profile is still in good standing. Photoless profiles are skipped —
    asserting something nobody checked.

    Returns the verified profile (so the caller can run the side effects once
    the transaction commits), or ``None``.
    """
    coach = _scanning_coach(request)
    if coach is None:
        return None

    profile = (
        CrushProfile.objects.select_for_update()
        .filter(user_id=registration.user_id)
        .first()
    )
    if profile is None:
        return None
    # The coach-authenticated scan attests the exact photo visible at the
    # door, independently of whether LuxID (or an earlier coach) already won
    # the member-level verification race. Photoless scans write nothing, so a
    # later upload cannot inherit this badge.
    if profile.verification_status in ("pending", "verified"):
        _attest_and_record_photo(
            profile,
            registration,
            now,
            allowed_statuses=("pending", "verified"),
            coach=coach,
        )

    if profile.verification_status != "pending":
        return None
    if profile.has_active_premium:
        return None
    if not profile.photo_1:
        return None

    if not _apply_verification(profile, "coach_event", coach, now):
        # Another path (e.g. a concurrent LuxID callback) verified them first.
        return None

    registration.checkin_auto_verified = True
    EventRegistration.objects.filter(pk=registration.pk).update(
        checkin_auto_verified=True,
    )

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

    # A signed ticket proves that this registration once held a seat; it does
    # not freeze the member's verification forever. A door rejection is an
    # explicit identity revocation, so a previously issued QR must not admit
    # that profile to a verified-members-only event later. Keep the registration
    # (and its payment/history) intact and enforce the current audience rule at
    # the door instead.
    if registration.event.profile_requirement == "approved":
        profile = getattr(registration.user, "crushprofile", None)
        if profile is None or profile.verification_status != "verified":
            return JsonResponse(
                {
                    "success": False,
                    "error": "This event is for currently verified members only.",
                },
                status=403,
            )

    # Check-in window, computed here because the already-attended branch below
    # must respect it too: tickets do not expire, so without this a coach could
    # scan a months-old QR and newly approve a pending profile (with referral
    # credit and a welcome email) at a moment when an ordinary check-in would
    # be refused outright. The rejection itself stays where it was — a re-scan
    # outside the window still reports "already checked in" as before, it just
    # no longer verifies.
    checkin_window_hours = getattr(settings, "EVENT_CHECKIN_WINDOW_HOURS", 12)
    now = timezone.now()
    event_start = registration.event.date_time
    from datetime import timedelta

    window_start = event_start - timedelta(hours=checkin_window_hours)
    window_end = event_start + timedelta(hours=checkin_window_hours)
    within_checkin_window = window_start <= now <= window_end

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
        rescan_verified = None
        if within_checkin_window:
            with transaction.atomic():
                locked_registration = (
                    EventRegistration.objects.select_for_update(of=("self",))
                    .select_related("event", "user")
                    .get(id=registration_id)
                )
                if locked_registration.status == "attended":
                    rescan_verified = _auto_verify_on_attendance(
                        request, locked_registration, now
                    )
        if rescan_verified is not None:
            # `registration` was loaded with `user__crushprofile` selected, so
            # its cached profile predates the write above — re-read it or the
            # toast would report the attendee as still unverified.
            registration = EventRegistration.objects.select_related(
                "event", "user", "user__crushprofile"
            ).get(id=registration_id)

        display_name = _get_display_name(registration)
        # Computed once, used twice: the row payload and the response's own
        # table fields — `_row_state` no longer re-derives it (#845).
        table_info = _get_existing_table_assignment(registration)
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
            "row": _row_state(registration, table_assignment=table_info),
            "print_payload_base64": _get_ticket_print_payload(
                registration, table_info=table_info
            ),
        }
        if table_info:
            response_data.update(table_info)
        if rescan_verified is not None:
            _run_post_verification_side_effects(
                registration.user, rescan_verified, request, registration.id
            )
            # Re-evaluate lobby participation. `participant_gate` requires
            # `verification_status == "verified"`, so the hook that ran on the
            # original (pending) scan created nothing — without this the member
            # stays off the live roster until they open the lobby themselves and
            # trip its self-heal.
            _evaluate_lobby_participation(registration)
            _broadcast_checkin(registration.event_id, response_data)
        return JsonResponse(response_data)

    # Verify the registration holds a seat.
    #
    # "pending" (Pending Payment) is admitted deliberately: a paid event's signup
    # sits in that status until the money lands, and payment is still taken out
    # of band for most attendees (bank transfer, cash at the door). Rejecting it
    # here would hand those people a QR ticket that always fails at the door --
    # the seat is what is being scanned, not the receipt. ("attended" is already
    # handled by the re-scan branch above.)
    if registration.status not in SEAT_HOLDING_STATUSES:
        return JsonResponse(
            {
                "success": False,
                "error": f"Registration status is '{registration.get_status_display()}'. Only registrations holding a seat can be checked in.",
            },
            status=400,
        )

    # Check event is within check-in window (computed above, before the
    # already-attended branch, which needs it too).
    if not within_checkin_window:
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
            # Same once-only lookup as the re-scan branch above (#845).
            table_info = _get_existing_table_assignment(registration)
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
                "row": _row_state(registration, table_assignment=table_info),
                "print_payload_base64": _get_ticket_print_payload(
                    registration,
                    table_info=table_info,
                    coach_authenticated=request.user.is_authenticated
                    and hasattr(request.user, "coach_profile"),
                ),
            }
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
        # The scanning coach becomes the member's permanent coach (when they
        # have none): read by `assign_coach_on_first_attendance` during the
        # save below, in preference to `event.coaches.first()`.
        registration._checkin_coach = _scanning_coach(request)
        _record_checkin_provenance(registration)
        registration.status = "attended"
        registration.checked_in_at = now
        registration.save(update_fields=_CHECKIN_UPDATE_FIELDS)

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
    # committed.
    _evaluate_lobby_participation(registration)

    display_name = _get_display_name(registration)

    logger.info(
        "Checked in registration %s (user %s) for event %s",
        registration.id,
        registration.user_id,
        registration.event_id,
    )

    # Same fresh read the coach endpoints take before broadcasting: the
    # instance above was locked inside the check-in transaction, and an
    # overlapping door action committed since (a second scanner, an undo)
    # would otherwise ride along as stale state on the payload every screen
    # converges its row on.
    fresh_registration = (
        EventRegistration.objects.select_related("user", "event")
        .filter(id=registration.id)
        .first()
    )
    if fresh_registration is not None:
        registration = fresh_registration

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
        # What the shared row-state applier renders the manual row from, on
        # this response AND the broadcast of it. The assignment dict built
        # above is handed over rather than re-derived — when there is none
        # (no quiz, or a failed assignment) the sentinel lets `_row_state`
        # fall back to its own lookup, preserving the badge-on-old-
        # membership behaviour of the failure path.
        "row": _row_state(
            registration, table_assignment=table_assignment or _TABLE_ASSIGNMENT_UNSET
        ),
        "print_payload_base64": _get_ticket_print_payload(
            registration,
            table_info=table_assignment,
            coach_authenticated=request.user.is_authenticated
            and hasattr(request.user, "coach_profile"),
        ),
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
        # A pending seat is admitted at the door, so a coach must be able to
        # verify that person too -- otherwise the cash-at-the-door attendee
        # can be scanned in but not verified.
        if registration.status not in SEAT_HOLDING_STATUSES:
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

        # Same workflow the auto-verify on attendance runs, so the two paths
        # cannot drift apart.
        already_verified = False
        if profile.verification_status != "verified":
            if not _apply_verification(
                profile, method, coach, now, claim_from=MANUAL_VERIFY_CLAIM_FROM
            ):
                # The claim can only fail here because another path verified them
                # first — every other status is claimable. Report the same
                # idempotent success as an already-verified profile rather than
                # running the side effects twice. Reporting this for a still-
                # unapproved profile would be worse than useless: the client
                # removes the Verify button on `already_verified`, so the row would
                # claim verified while the database disagreed.
                profile.refresh_from_db()
                if profile.verification_status != "verified":
                    return JsonResponse(
                        {
                            "success": False,
                            "error": str(
                                _("Could not verify this profile. Please try again.")
                            ),
                        },
                        status=409,
                    )
                already_verified = True
        else:
            already_verified = True

        # This explicit coach action attests the photo even when member-level
        # identity was already verified through LuxID. The method remains the
        # first verification path; the photo key is a separate, versioned fact.
        _attest_and_record_photo(
            profile,
            registration,
            now,
            allowed_statuses=("incomplete", "pending", "rejected", "verified"),
            coach=coach,
        )

        if already_verified:
            return JsonResponse(
                {
                    "success": True,
                    "already_verified": True,
                    "registration_id": registration.id,
                    "attendee_name": _get_display_name(registration, coach_authenticated=True),
                    "verification_method": profile.verification_method,
                    "message": str(_("Already verified.")),
                    "row": _row_state(registration, coach_authenticated=True),
                }
            )

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

    # The response — and the broadcast of it — converges every other
    # screen's row on this payload, so it must describe committed state.
    # `registration` is still the instance locked inside the transaction
    # above: an overlapping door action that committed since (a scan, an
    # undo) would ride along as stale state and the broadcast would un-check
    # an already-attended row on every screen. The summary refetch is
    # sequence-guarded (c65d1136) — the row payload is not, so it is built
    # from a fresh read, taken as late as possible.
    fresh_registration = (
        EventRegistration.objects.select_related("user", "event")
        .filter(id=registration_id, event_id=event_id)
        .first()
    )
    if fresh_registration is not None:
        registration = fresh_registration

    response_data = {
        "success": True,
        "already_verified": False,
        "registration_id": registration.id,
        "attendee_name": _get_display_name(registration, coach_authenticated=True),
        "verification_method": method,
        "message": str(_("%(name)s is now verified."))
        % {"name": _get_display_name(registration, coach_authenticated=True)},
        "profile": _get_profile_data(registration),
        "row": _row_state(registration, coach_authenticated=True),
    }
    _broadcast_checkin(registration.event_id, response_data)
    return JsonResponse(response_data)


@coach_required
@require_POST
def coach_reject_verification(request, event_id, registration_id):
    """Explicitly reject or revoke verification for an attendee at the door (photo mismatch).

    Called when the coach observes that the attendee's profile photo does not match
    the person presenting the ticket at check-in.
    Sets verification_status to 'rejected', is_approved to False, clears verification_method,
    updates latest ProfileSubmission notes with a durable structured audit record
    (actor/timestamp/reason, appended to ``coach_notes``) and logs a warning.
    """
    coach = request.coach
    now = timezone.now()

    with transaction.atomic():
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
        # Same seat-holding gate as coach_mark_verified: rejecting at the door
        # only makes sense for someone actually holding a seat at this event.
        if registration.status not in SEAT_HOLDING_STATUSES:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(
                        _(
                            "Only confirmed or checked-in attendees can have "
                            "their verification rejected."
                        )
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

        # Authorization parity with coach_mark_verified: a premium member's
        # verification belongs to their own assigned coach. Reject is
        # destructive without a restore path for anyone else — a coach whom
        # the verify endpoint's 403 stops from restoring verification must be
        # stopped from destroying it too.
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

        if not reject_door_verification(profile, target_status="rejected"):
            # The conditional claim failed: another reject already moved the
            # row to "rejected". Mirror the verify endpoint's claim-failure
            # 409 — reporting idempotent success here would append a second
            # rejection note to the submission on every double-click.
            return JsonResponse(
                {
                    "success": False,
                    "error": str(
                        _("This verification has already been rejected.")
                    ),
                },
                status=409,
            )

        # Update latest submission if one exists, and append the durable audit
        # note (actor/timestamp/reason) to its rejection notes.
        submission = (
            ProfileSubmission.objects.filter(profile=profile)
            .select_related("coach__user")
            .order_by("-submitted_at")
            .first()
        )
        if submission:
            # An expired row keeps its terminal status. `latest_for_profile`
            # reads "expired latest" as no submission at all — the cleanup
            # closed out that whole coach-review story — so flipping it to
            # "rejected" would launder it back into a live state that
            # `_apply_verification`'s claim query now selects and approves,
            # resurrecting exactly the record the cleanup retired. The audit
            # note is still appended: what happened at the door is worth
            # recording even on a row nothing will reopen.
            expired = submission.status == "expired"
            if not expired:
                submission.status = "rejected"
            if not submission.coach_id:
                submission.coach = coach
            note = (
                f"[{now.isoformat()}] Door verification rejected by coach {coach} "
                f"(coach_id={coach.pk}, event_id={event_id}): photo mismatch"
            )
            submission.coach_notes = (
                (submission.coach_notes + "\n" if submission.coach_notes else "") + note
            ).strip()
            fields = ["coach", "coach_notes"] if expired else [
                "status",
                "coach",
                "coach_notes",
            ]
            submission.save(update_fields=fields)

        # The atomic QuerySet.update() above bypasses post_save, so re-run the
        # Outlook contact sync explicitly — the shared-mailbox contact must
        # observe the revoke, not keep serving "Status: Approved". Same call
        # the coach-review approve path makes; the Graph work defers to
        # on_commit, so this is safe inside the transaction.
        try:
            from .signals import sync_profile_to_outlook

            sync_profile_to_outlook(
                sender=CrushProfile,
                instance=profile,
                created=False,
                update_fields=None,
            )
        except Exception:
            logger.exception(
                "Failed to sync door-rejected profile %s to Outlook", profile.pk
            )

    logger.warning(
        "Coach %s rejected verification for registration %s (user %s) at event %s due to photo mismatch",
        coach,
        registration.id,
        registration.user_id,
        event_id,
    )

    # Notify the member. In the photo-mismatch scenario the account owner is
    # plausibly the impersonation victim — the coach-review path already
    # notifies, and silence here is the worse failure mode. Best-effort, after
    # commit: a notification failure must not fail the door action.
    try:
        from .notification_service import notify_profile_rejected

        result = notify_profile_rejected(
            user=registration.user,
            profile=profile,
            # Deliberately NOT str()-ed: `_` is gettext_lazy, and forcing it
            # here resolves the string under the *coach's* active locale.
            # Passed lazily, the email renders it inside
            # send_profile_rejected_notification's translation.override(lang)
            # and the bell row inside _render_inapp_payload's — both in the
            # member's own language.
            feedback=_(
                "Your verification was rejected because the person at the "
                "event did not match your profile photos."
            ),
            request=request,
        )
        if result.any_delivered:
            logger.info(
                "Door rejection notification delivered for user %s: "
                "push=%s email=%s",
                registration.user_id,
                result.push_success,
                result.email_sent,
            )
    except Exception:
        logger.exception(
            "Failed to send door rejection notification for registration %s",
            registration.id,
        )

    display_name = _get_display_name(registration, coach_authenticated=True)
    response_data = {
        "success": True,
        "rejected": True,
        "registration_id": registration.id,
        "attendee_name": display_name,
        "verification_status": "rejected",
        "is_approved": False,
        "message": str(_("Verification rejected for %(name)s (photo mismatch)."))
        % {"name": display_name},
        # The submission fetched above is passed through so the payload does
        # not re-run the same latest-submission query.
        "profile": _get_profile_data(registration, latest_submission=submission),
    }
    _broadcast_checkin(registration.event_id, response_data)
    return JsonResponse(response_data)


#: How long after a check-in a coach may undo it. This is a correction tool for
#: the wrong badge scanned at the door, not an attendance editor — the event
#: record itself must stay honest, so the window is short.
CHECKIN_UNDO_WINDOW_MINUTES = 15


@coach_required
@require_POST
def coach_undo_checkin(request, event_id, registration_id):
    """Undo a check-in a coach just made by mistake.

    Attendance is no longer only a status flip: it auto-verifies the profile
    (referral reward + welcome email) and grants a *permanent* coach. A re-scan
    cannot repair either — the already-attended branch never re-saves the row,
    so ``assign_coach_on_first_attendance`` never fires again. Without this
    endpoint one mis-scan can only be fixed in Django admin.

    What it reverts, both from the provenance the check-in recorded rather
    than from anything inferred afterwards:

    - ``attended`` -> the status the row actually held before
      (``checkin_prior_status``), so undoing a mistaken waitlist promotion
      returns the walk-up to the **waitlist** instead of handing them a
      confirmed seat they were never given.
    - The permanent coach, only while the profile still holds exactly the
      grant this check-in wrote (``checkin_granted_coach`` +
      ``checkin_granted_coach_at``). A member who arrived with a coach keeps
      it, and so does one whose coach was rewritten inside the undo window by
      a premium confirmation or an admin reassignment — both write
      ``assigned_coach_at``, so the old ``assigned_coach_at >= checked_in_at``
      test read a *paid* coach as a door grant and stripped it.

    Rows checked in before this provenance existed carry none of it, and fall
    back to restoring ``confirmed`` and leaving the coach alone — the safe
    direction of each guess, and reachable for at most the 15 minutes after a
    deploy in which a pre-deploy check-in is still undoable.

    - A quiz seat, if the original check-in took one — membership, rotation
      rows and any scores collected inside the undo window. All of those are
      separate objects that outlive the status flip, so an undone attendee
      would otherwise keep a chair their table is short of and points on the
      individual leaderboard.

    What it deliberately does NOT revert: the verification. The welcome email
    and the referral credit have already gone out, and un-verifying a member
    who is standing in the room is worse than an over-verified walk-in. The
    coach can see the profile is verified and act accordingly.

    Refused for a row with no ``checked_in_at``. The window is what keeps this
    a correction rather than an attendance editor, and a row with no timestamp
    has no window — those belong to an administrator.
    """
    coach = _scanning_coach(request)
    if coach is None:
        return JsonResponse(
            {"success": False, "error": str(_("Authentication required."))},
            status=401,
        )

    # Lock the registration so an undo cannot race another coach scanning or
    # editing the same attendee at the door, then the profile, so the
    # verification decision below is serialised against a concurrent door
    # rejection or LuxID callback. The order is registration → crushprofile and
    # must stay that way: every other writer that takes both takes them in this
    # order, and reversing it here is how a door deadlock gets built.
    with transaction.atomic():
        try:
            registration = (
                EventRegistration.objects.select_for_update(of=("self",))
                .select_related("event", "user")
                .get(pk=registration_id, event_id=event_id)
            )
        except EventRegistration.DoesNotExist:
            return JsonResponse(
                {"success": False, "error": str(_("Registration not found."))},
                status=404,
            )

        if registration.status != "attended":
            return JsonResponse(
                {
                    "success": False,
                    "error": str(_("This attendee is not checked in.")),
                },
                status=409,
            )

        # The door is loud: a coach correcting a mis-scan needs a few minutes,
        # but attendance from last week must not be rewritable here.
        # superusers/lead coaches can still fix records in Django admin.
        now = timezone.now()
        checked_in_at = registration.checked_in_at
        undo_window = timedelta(minutes=CHECKIN_UNDO_WINDOW_MINUTES)
        if checked_in_at is None or now - checked_in_at > undo_window:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(
                        _(
                            "This check-in is too old to undo here. Ask an "
                            "administrator to correct it."
                        )
                    ),
                },
                status=409,
            )

        # Clear the coach only while the profile still holds exactly the grant
        # this check-in recorded. Equality on both the coach and the timestamp
        # is what makes it provenance rather than ordering: every other writer
        # of `assigned_coach` / `assigned_coach_at` (PremiumMembership.confirm,
        # an admin editing either field) changes at least one of them, so a
        # rewrite inside the undo window no longer looks like a door grant.
        # A member who arrived with a coach records no grant at all and is
        # untouched, as before.
        coach_cleared = False
        profile = (
            CrushProfile.objects.select_for_update()
            .filter(user_id=registration.user_id)
            .first()
        )
        granted_coach_id = registration.checkin_granted_coach_id
        granted_at = registration.checkin_granted_coach_at
        if (
            granted_coach_id
            and granted_at
            and profile is not None
            and profile.assigned_coach_id == granted_coach_id
            and profile.assigned_coach_at == granted_at
        ):
            CrushProfile.objects.filter(
                pk=profile.pk,
                assigned_coach_id=granted_coach_id,
                assigned_coach_at=granted_at,
            ).update(
                assigned_coach=None,
                assigned_coach_at=None,
            )
            coach_cleared = True

        # Revoke scan-created photo attestation atomically if the profile still holds the exact key and timestamp
        attested_key = registration.checkin_attested_photo_key
        attested_at = registration.checkin_attested_photo_at
        if attested_key and attested_at and profile is not None:
            CrushProfile.objects.filter(
                pk=profile.pk,
                photo_verification_key=attested_key,
                photo_verified_at=attested_at,
            ).update(
                photo_verification_key="",
                photo_verified_at=None,
            )

        # Revoke auto-verification only when this check-in is what carries the
        # member's verification, unless independent current proof (a connected
        # LuxID, or another coach-authenticated attended event) supersedes it.
        #
        # `checkin_auto_verified` is that fact and the only one: a coach grant is
        # revoked above, a photo attestation is revoked directly above that, and
        # neither is evidence this scan verified the *member*. A coach who used
        # the Verify button before the scan (`coach_mark_verified`) leaves an
        # attested photo on the row while the later scan sets no
        # `checkin_auto_verified` — that independent decision must survive
        # undoing the scan. The transfer branch below keeps the flag set on a
        # surviving coach-authenticated registration, so a chain of undos still
        # ends in a demotion.
        if (
            profile is not None
            and registration.checkin_auto_verified
            and profile.verification_status == "verified"
            and profile.verification_method == "coach_event"
        ):
            # LuxID settles it on its own, so it is checked first: the survival
            # query below is a three-way OR with an m2m join and there is no
            # point paying for it when the member carries their own proof.
            if profile.has_luxid_connected:
                CrushProfile.objects.filter(
                    pk=profile.pk,
                    verification_status="verified",
                    verification_method="coach_event",
                ).update(
                    verification_method="luxid",
                )
            else:
                # The grant cleared above is gone from the database but still set
                # on this in-memory `profile` (the clear was a raw UPDATE). Read
                # it back as absent rather than counting the coach we just
                # revoked as ongoing evidence of coach-authenticated attendance.
                surviving_coach_id = (
                    None if coach_cleared else profile.assigned_coach_id
                )
                other_coach_attendances = (
                    EventRegistration.objects.filter(
                        user_id=profile.user_id,
                        status="attended",
                    )
                    .filter(
                        Q(checkin_granted_coach_id__isnull=False)
                        | ~Q(checkin_attested_photo_key="")
                        | (
                            Q(event__coaches=surviving_coach_id)
                            if surviving_coach_id
                            else Q()
                        )
                    )
                    .exclude(pk=registration.pk)
                )
                # Materialise once. `.exists()` does not populate the result
                # cache, so branching on it and then re-filtering the same
                # queryset would issue the whole three-way OR twice.
                surviving_pks = list(
                    other_coach_attendances.values_list("pk", flat=True).distinct()
                )
                if surviving_pks:
                    # Transfer provenance to surviving coach-authenticated check-in so subsequent undos still know it verified the member
                    EventRegistration.objects.filter(
                        pk__in=surviving_pks,
                        checkin_auto_verified=False,
                    ).update(checkin_auto_verified=True)
                else:
                    demoted = transition_unverified_profile(
                        profile,
                        target_status="pending",
                        transition_from=("verified",),
                    )
                    if demoted:
                        try:
                            from .referrals import revoke_profile_approved_reward

                            revoke_profile_approved_reward(profile)
                        except Exception:
                            # Broad on purpose: the coach is correcting a mis-scan
                            # at the door and the undo itself must still commit —
                            # raising here would roll the whole correction back.
                            # `exception` rather than `warning` so the traceback
                            # survives for the manual reconciliation it leaves.
                            logger.exception(
                                "Could not revoke referral reward on checkin undo for %s",
                                profile.pk,
                            )

        # Back to whatever the row actually held. A promoted walk-up returns to
        # the waitlist: undoing a mistaken promotion must not leave them
        # holding a confirmed seat nobody gave them, which is what event
        # capacity and registration priority are counted from.
        restored_status = registration.checkin_prior_status
        if restored_status not in UNDO_RESTORABLE_STATUSES:
            restored_status = "confirmed"
        # The provenance value is a snapshot from scan time. Payment can land
        # between the scan and the undo (SumUp settling, or staff recording
        # cash), so restoring a stale "pending" would relabel a paid seat as
        # Pending Payment and drop it out of every confirmed-only workflow.
        if restored_status == "pending" and registration.payment_confirmed:
            restored_status = "confirmed"

        registration.status = restored_status
        registration.checked_in_at = None
        # Provenance describes an attendance, and this row no longer has one.
        registration.checkin_prior_status = ""
        registration.checkin_granted_coach = None
        registration.checkin_granted_coach_at = None
        registration.checkin_attested_photo_key = ""
        registration.checkin_attested_photo_at = None
        registration.checkin_auto_verified = False
        # Tells handle_event_ticket_on_registration_change that this really is
        # an attended -> confirmed transition. The same idiom as
        # _checkin_coach: the signal cannot tell a transition from any other
        # save that happens to leave the row confirmed. A restore to `waitlist`
        # is not in that signal's status map at all and correctly touches no
        # pass — a waitlisted row is never issued a live one.
        registration._reactivate_ticket = True
        # updated_at is auto_now, so it is only written when named here —
        # every other save in this file lists it for the same reason.
        registration.save(update_fields=_CHECKIN_UPDATE_FIELDS)

        # The status flip alone does not free the quiz seat the check-in took.
        # Best-effort, like the assignment it undoes: a quiz problem must not
        # cost the coach the correction they came here to make.
        released_table = None
        try:
            quiz_event = getattr(registration.event, "quiz", None)
            if quiz_event:
                from .services.quiz_rotation import release_table_on_undo

                released_table = release_table_on_undo(quiz_event, registration.user)
        except Exception:
            logger.exception(
                "Quiz table release failed for undone registration %s",
                registration.id,
            )

    logger.info(
        "Coach %s undid check-in of registration %s (user %s) at event %s; "
        "restored_status=%s coach_cleared=%s",
        coach,
        registration.id,
        registration.user_id,
        event_id,
        restored_status,
        coach_cleared,
    )

    display_name = _get_display_name(registration, coach_authenticated=True)
    response_data = {
        "success": True,
        "undone": True,
        "registration_id": registration.id,
        "attendee_name": display_name,
        # Where the row landed. The door page counts a returned-to-waitlist row
        # differently from a returned-to-confirmed one — the waitlist was never
        # in the expected set — so this cannot be assumed client-side.
        "restored_status": restored_status,
        "coach_cleared": coach_cleared,
        "message": str(_("Check-in undone for %(name)s.")) % {"name": display_name},
        # The full row state the shared applier renders from — status,
        # cleared coach line, table badge — for the local path AND the
        # broadcast, which used to have no undo branch at all (#710).
        #
        # Built from a fresh read for the same reason the other endpoints are:
        # the instance was locked inside the transaction, `release_table_on_undo`
        # is a round trip after the lock, and a second coach re-scanning in the
        # gap before the broadcast would otherwise have this payload un-check an
        # already-re-attended row on every screen.
        "row": _row_state(_fresh(registration), coach_authenticated=True),
    }
    # The *membership* table, not the current one. The door page's table-fill
    # grid is rendered from QuizTableMembership (views_coach.py), so it counts
    # the seat where the person checked in however far the rotation has since
    # moved them — decrementing the current table would leave that tile short
    # and the round-0 tile still holding a seat nobody occupies.
    #
    # Under its own key, never `table_number`: this payload is broadcast to
    # every coach page, and the arrival key is what the check-in path reads.
    # The door grid itself is put right by the summary refetch every path now
    # runs; this key is what the acting coach's page and the tests name the
    # freed seats by. The quiz table group is a different channel, carries
    # the current table, and reaches the players.
    if released_table and released_table.get("membership_table_numbers"):
        response_data["released_table_numbers"] = released_table[
            "membership_table_numbers"
        ]
    _broadcast_checkin(registration.event_id, response_data)
    if released_table:
        # Naming the user is what lets the consumer drop their live socket.
        # An undo can put a promoted walk-up back on the `waitlist`, which a
        # fresh WS connection refuses — but theirs was authorised once at
        # connect and never again, so without this they keep receiving
        # questions and standings for an event they are no longer attending.
        _broadcast_quiz_table_update(
            registration.event, released_table, affected_user_id=registration.user_id
        )
        # Deleting their IndividualScore rows changes the standings everyone
        # in the room is looking at, not just the people at their table — and
        # the table broadcast reaches only the latter, whose handler refetches
        # an assignment rather than a leaderboard. Sent on the shared group as
        # the existing `quiz.leaderboard`, which every client already handles
        # and which triggers no assignment refetch.
        if released_table.get("scores_removed"):
            _broadcast_quiz_leaderboard(registration.event)
    return JsonResponse(response_data)


@coach_required
@require_POST
def coach_promote_from_waitlist(request, event_id, registration_id):
    """Check in a waitlisted walk-up in one action.

    On a night with no-shows this is the most ordinary door operation there
    is, and until now the scanner could not do it at all: the page only ever
    receives ``confirmed`` and ``attended`` rows, so the coach had to leave the
    scanner, promote the person on the event management page, and come back.

    Deliberately does NOT re-check capacity or gender limits. A coach standing
    at the door with the person in front of them is overriding those on
    purpose; the page shows the live counts so the decision is informed.

    Everything else matches an ordinary scan, because a promotion IS a check-in
    and must not diverge from one: the same check-in window, the promoting
    coach becomes their permanent coach, attendance auto-verifies, a quiz-night
    walk-up gets a table, and lobby participation is evaluated on commit.
    """
    coach = request.coach
    now = timezone.now()
    table_assignment = None

    with transaction.atomic():
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
        if registration.status == "attended":
            return JsonResponse(
                {
                    "success": False,
                    "error": str(_("This attendee is already checked in.")),
                },
                status=409,
            )
        if registration.status != "waitlist":
            return JsonResponse(
                {
                    "success": False,
                    "error": str(_("Only waitlisted attendees can be promoted.")),
                },
                status=409,
            )

        # Cancelling an event only flips MeetupEvent.is_cancelled; the
        # waitlist rows keep their status, so an already-open scanner tab
        # would still promote them — granting attendance, verification and a
        # permanent coach for an event that is not happening.
        if registration.event.is_cancelled:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(_("This event has been cancelled.")),
                },
                status=409,
            )

        # Same window as event_checkin_api. A promotion grants a seat, a
        # verification and a permanent coach; none of that should be reachable
        # from a stale event page weeks later just because this route skipped
        # the check the scanner enforces.
        checkin_window_hours = getattr(settings, "EVENT_CHECKIN_WINDOW_HOURS", 12)
        event_start = registration.event.date_time
        if not (
            event_start - timedelta(hours=checkin_window_hours)
            <= now
            <= event_start + timedelta(hours=checkin_window_hours)
        ):
            return JsonResponse(
                {
                    "success": False,
                    "error": str(
                        _(
                            "Check-in is only available within %(hours)s hours "
                            "of the event."
                        )
                    )
                    % {"hours": checkin_window_hours},
                },
                status=400,
            )

        registration._checkin_coach = _scanning_coach(request)
        # Records `waitlist` as the prior status, which is the whole reason an
        # undo of this action can put the walk-up back where they were instead
        # of promoting them to a confirmed seat by accident.
        _record_checkin_provenance(registration)
        registration.status = "attended"
        registration.checked_in_at = now
        registration.save(update_fields=_CHECKIN_UPDATE_FIELDS)

        verified_profile = _auto_verify_on_attendance(request, registration, now)
        auto_verified = verified_profile is not None
        if auto_verified:
            transaction.on_commit(
                lambda: _run_post_verification_side_effects(
                    registration.user, verified_profile, request, registration.id
                )
            )

        # A quiz-night walk-up needs a table like anyone else, or they sit
        # down to a round they cannot take part in.
        try:
            quiz_event = getattr(registration.event, "quiz", None)
            if quiz_event and quiz_event.num_tables:
                from .services.quiz_rotation import assign_table_on_checkin

                table_assignment = assign_table_on_checkin(
                    quiz_event, registration.user
                )
        except Exception:
            logger.exception(
                "Quiz table assignment failed for promoted registration %s",
                registration.id,
            )

    _evaluate_lobby_participation(registration)

    logger.info(
        "Coach %s promoted waitlisted registration %s (user %s) at event %s",
        coach,
        registration.id,
        registration.user_id,
        event_id,
    )

    # Same hazard `coach_mark_verified` guards against: the instance held here
    # was locked inside the transaction above, and an overlapping door action
    # committed since (a scan, an undo) would ride along as stale state on a
    # payload every screen converges its row on.
    fresh_registration = (
        EventRegistration.objects.select_related("user", "event")
        .filter(id=registration_id, event_id=event_id)
        .first()
    )
    if fresh_registration is not None:
        registration = fresh_registration

    display_name = _get_display_name(registration, coach_authenticated=True)
    response_data = {
        "success": True,
        "promoted": True,
        "already_checked_in": False,
        "registration_id": registration.id,
        "attendee_name": display_name,
        "checked_in_at": now.isoformat(),
        "message": str(_("%(name)s promoted from the waitlist and checked in."))
        % {"name": display_name},
        "profile": _get_profile_data(registration),
        "auto_verified": auto_verified,
        # The confirmed list never contained this row, so the applier builds
        # it from the identity fields carried here. Coach-only endpoint, so
        # this is also the one row payload allowed to carry the legal
        # name/email search haystack.
        "row": _row_state(
            registration,
            include_search=True,
            table_assignment=table_assignment or _TABLE_ASSIGNMENT_UNSET,
            coach_authenticated=True,
        ),
        "print_payload_base64": _get_ticket_print_payload(
            registration,
            table_info=table_assignment,
            coach_authenticated=True,
        ),
    }
    if table_assignment:
        response_data["table_number"] = table_assignment["table_number"]
        response_data["role"] = table_assignment["role"]
    _broadcast_checkin(registration.event_id, response_data)
    if table_assignment:
        _broadcast_quiz_table_update(registration.event, table_assignment)
    return JsonResponse(response_data)


def _get_display_name(registration, *, coach_authenticated=False):
    """Get privacy-aware display name for attendee.

    ``display_name`` is a name the member chose to publish on a dating
    profile, so it travels on any authenticated response. A profileless
    attendee has no such chosen name, and the account fields behind it are not
    interchangeable with one:

    * ``username`` is never used — signup is email-only
      (``ACCOUNT_SIGNUP_FIELDS``), so allauth derives it from the address and
      it is generally the address itself;
    * ``first_name`` is real account data the member never published, so it
      needs ``coach_authenticated``.

    That flag defaults to False because the riskier caller is the quiet one:
    `event_checkin_api` is CSRF-exempt and authenticated solely by the signed
    URL baked into the ticket QR, so its response reaches anyone holding a
    photographed ticket. Coach endpoints opt in explicitly.
    """
    try:
        return registration.user.crushprofile.display_name
    except Exception:
        if coach_authenticated:
            # Names the person on the door list instead of rendering every
            # profileless row as the same placeholder.
            return registration.user.first_name or str(_("Attendee"))
        return str(_("Attendee"))


def _fresh(registration):
    """Re-read a registration straight before its state is broadcast.

    Every door endpoint locks its row inside a transaction and then builds a
    payload every screen converges on. Between the commit and the broadcast a
    concurrent door action can land, and the in-memory instance would carry the
    pre-race state out to all of them. Falls back to the instance in hand when
    the row is gone, so a delete cannot turn a correction into a 500.
    """
    return (
        EventRegistration.objects.select_related("user", "event")
        .filter(pk=registration.pk)
        .first()
        or registration
    )


#: Sentinel distinguishing "the caller has not computed the table assignment"
#: from "the caller computed it and there is none". ``_row_state`` used to
#: run ``_get_existing_table_assignment`` unconditionally, so the callers
#: that had just computed it (the re-scan branches, a fresh
#: ``assign_table_on_checkin``) paid the quiz/membership/rotation queries
#: twice per door action on quiz nights.
_TABLE_ASSIGNMENT_UNSET = object()


def _row_state(
    registration,
    include_search=False,
    table_assignment=_TABLE_ASSIGNMENT_UNSET,
    coach_authenticated=False,
):
    """Row-level state for the door page's shared row-state applier (#710).

    Every door action returns this under ``row`` and the page applies it with
    one ``_applyRowState`` on BOTH the local and the broadcast path, so no
    handler patches its own subset of the DOM. It carries exactly what the
    applier cannot know or derive: the status after the action, the arrival
    time, which table badge to render, the permanent-coach line, the
    verification pill — plus the identity fields a promoted row needs,
    because the confirmed list never contained it.

    ``include_search`` adds the legal-name/email search haystack, and is set
    ONLY by ``coach_promote_from_waitlist`` — the one action whose row the
    page has to rebuild from scratch. ``event_checkin_api`` authenticates on
    the signed QR alone (an attendee can POST their own check-in URL), so its
    response must not hand them the account's legal name and email under a
    key nothing on that path needs.

    The profile is re-read rather than taken from the registration's cached
    ``user__crushprofile``: the coach grant is written by a signal on its own
    profile instance during ``save()``, so the cached copy predates the very
    field this is asked to report.

    ``table_assignment`` is the caller's already-computed
    ``_get_existing_table_assignment`` result (or an ``assign_table_on_checkin``
    dict — only ``table_number`` is read) and spares the duplicate lookup
    above; leave it unset to have this helper compute it.
    """
    # str() coerces the lazy "Attendee" fallback — it must be a plain string
    # both for the join below and for JsonResponse.
    display_name = str(
        _get_display_name(registration, coach_authenticated=coach_authenticated)
    )
    row = {
        "registration_id": registration.id,
        "user_id": registration.user_id,
        "status": registration.status,
        "checked_in_at": (
            registration.checked_in_at.isoformat()
            if registration.checked_in_at
            else None
        ),
        # `registered_at` deliberately absent. It went in as groundwork for
        # restoring an undone promotion at its FIFO queue position, but that
        # also needs the timestamp on the rendered rows and was deferred —
        # leaving nothing reading it, on a payload three `event_checkin_api`
        # responses hand to anyone holding a photographed ticket. Add it back
        # with the feature, gated to the coach-only branch.
        "display_name": display_name,
        "has_profile": False,
        "is_approved": False,
        "coach_name": None,
    }
    if include_search:
        # The client-side search haystack a rebuilt row carries — matches the
        # template's: a guest at the door gives the name on their ID or the
        # email they booked with, neither of which need match display_name.
        row["search"] = " ".join(
            part
            for part in (
                display_name,
                registration.user.first_name,
                registration.user.last_name,
                registration.user.email,
            )
            if part
        )
    if coach_authenticated and registration.checkin_token:
        # `_buildConfirmedRow` has no other source for this: the path is signed
        # per registration, so the client cannot derive it. Without it a row the
        # applier built from scratch — a `no_show` recovered by `mark_attended`
        # has none on the page to begin with — offers Undo but no way back in,
        # stranding the attendee until the coach reloads.
        row["checkin_url"] = (
            f"/api/events/checkin/{registration.pk}/{registration.checkin_token}/"
        )
    profile = (
        CrushProfile.objects.select_related("assigned_coach__user")
        .filter(user_id=registration.user_id)
        .first()
    )
    if profile is not None:
        row["has_profile"] = True
        row["is_approved"] = profile.is_approved
        row["gender"] = profile.gender or ""
        row["age_display"] = profile.age_display or ""
        if profile.assigned_coach_id:
            coach_user = profile.assigned_coach.user
            row["coach_name"] = (
                f"{coach_user.first_name} {coach_user.last_name}".strip()
                or coach_user.username
            )
    if table_assignment is _TABLE_ASSIGNMENT_UNSET:
        table_assignment = _get_existing_table_assignment(registration)
    if table_assignment:
        row["table_number"] = table_assignment["table_number"]
    return row


@coach_required
@require_GET
def event_checkin_summary(request, event_id):
    """Read-only door counters, refetched by the page after every door action.

    The check-in page used to maintain its tiles by bumping whichever counter
    each handler knew about, which drifted on the first unhandled path and
    could render a gender tile as ``6 / 5``. This endpoint is the single
    source: the page refetches it after any scan, promote, undo or verify —
    local or broadcast — and re-renders the counters wholesale (#710).

    Mirrors the arithmetic of ``coach_event_checkin`` (views_coach.py) on
    purpose: the tiles must not change their meaning between page load and
    the first refetch. All three gender buckets are always present, so the
    client can write numerators AND denominators without probing the DOM.
    """
    from .models import MeetupEvent

    event = get_object_or_404(MeetupEvent, id=event_id)

    registrations = (
        EventRegistration.objects.filter(event=event)
        .exclude(status="cancelled")
        .select_related("user__crushprofile")
    )
    confirmed = [r for r in registrations if r.status in SEAT_HOLDING_STATUSES]
    attended_count = sum(1 for r in confirmed if r.status == "attended")
    waitlist_count = sum(1 for r in registrations if r.status == "waitlist")

    gender_checked_in = {"F": 0, "M": 0, "other": 0}
    gender_expected = {"F": 0, "M": 0, "other": 0}
    for reg in confirmed:
        gender = getattr(getattr(reg.user, "crushprofile", None), "gender", None)
        key = gender if gender in ("F", "M") else "other"
        gender_expected[key] += 1
        if reg.status == "attended":
            gender_checked_in[key] += 1

    table_fill = []
    # Same gate as the page view (coach_event_checkin in views_coach.py): a
    # QuizEvent row can exist under an event that is not a quiz night, and
    # following it here made the refetch disagree with the page that rendered
    # it — plus the membership queries on every non-quiz refetch.
    # getattr's default, not a bare except: Django builds the reverse-one-to-one
    # "no related object" error as a subclass of both AttributeError and the
    # related model's DoesNotExist precisely so this narrows to the missing-row
    # case. Catching Exception here would swallow a database error into an
    # empty table-fill panel instead of surfacing it.
    quiz_event = None
    if event.event_type == "quiz_night":
        quiz_event = getattr(event, "quiz", None)
    if quiz_event and quiz_event.num_tables:
        from collections import Counter

        from .models.quiz import QuizTableMembership

        fill_counts = Counter(
            QuizTableMembership.objects.filter(table__quiz=quiz_event).values_list(
                "table__table_number", flat=True
            )
        )
        for number in range(1, quiz_event.num_tables + 1):
            table_fill.append({"number": number, "count": fill_counts.get(number, 0)})

    return JsonResponse(
        {
            "success": True,
            "attended_count": attended_count,
            "expected_count": len(confirmed),
            "outstanding_count": len(confirmed) - attended_count,
            "waitlist_count": waitlist_count,
            "gender_checked_in": gender_checked_in,
            "gender_expected": gender_expected,
            "table_fill": table_fill,
        }
    )


def _get_profile_data(registration, latest_submission=None):
    """Build privacy-aware profile card data for check-in toast.

    ``latest_submission`` lets a caller that already fetched the newest
    submission (e.g. the reject endpoint, which annotates it) pass it in
    instead of re-running the same query for the response payload.
    """
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
        if latest_submission is None:
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


def _broadcast_quiz_table_update(event, table_assignment, affected_user_id=None):
    """Tell the people at a table that its membership changed.

    Someone joining (a scan or a waitlist promotion) or leaving (an undone
    check-in) both land here — only ``table_number`` is read, so either
    direction can pass its own dict, and it may be None when a cleanup freed
    no seat in the current round.

    Reaches three audiences, each on its own group: the players at the table,
    the projector, and whoever is running the room. The host is last because
    the host panel had never refreshed for *any* door action — not a scan, not
    a promotion, not an undo — so a walk-up promoted at the door has been
    invisible on the host's table overview since #703 (#722).

    ``affected_user_id`` names the person whose seat changed, when a caller
    knows it. It travels on the channel-layer event and is stripped before
    anything is sent to a browser — the consumer uses it to re-run its
    connect-time authorisation for exactly that user, and nothing else needs
    to know an internal id (AUTHZ-02).
    """
    from .models.quiz import QuizTable

    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    try:
        quiz_event = getattr(event, "quiz", None)
        if not quiz_event:
            return
        table_number = table_assignment.get("table_number")
        if table_number:
            # Look up the QuizTable PK — the consumer subscribes using table PK,
            # not table_number
            quiz_table = QuizTable.objects.filter(
                quiz=quiz_event, table_number=table_number
            ).first()
            if quiz_table:
                # Only the affected participants refresh — and this is the one
                # group the departing player is still in, so it is where the
                # re-authorisation has to ride.
                async_to_sync(channel_layer.group_send)(
                    f"quiz_{quiz_event.id}_table_{quiz_table.id}",
                    {
                        "type": "quiz.table_update",
                        "data": {"table_number": table_number},
                        "affected_user_id": affected_user_id,
                    },
                )
        # The projector always refreshes, even with no table to name. It shows
        # attendance and the individual leaderboard, which a cleanup changes
        # whether or not it freed a seat in the current round — and the
        # no-current-seat case is exactly the one that carries no table number.
        # handleTableUpdate ignores the payload and refetches, so a null is fine.
        async_to_sync(channel_layer.group_send)(
            f"quiz_{quiz_event.id}_display",
            {
                "type": "quiz.table_update",
                "data": {"table_number": table_number},
            },
        )
        # The host, for the same reason as the projector: their overview is a
        # roster of the whole room, so it goes stale on any seat change and not
        # only on one that names a table. A dedicated group rather than
        # `quiz_<id>`, which the host does subscribe to but shares with every
        # player — whose own `quiz.table_update` branch refetches their
        # assignment, so the room would refetch as one on every door scan.
        async_to_sync(channel_layer.group_send)(
            f"quiz_{quiz_event.id}_host",
            {
                "type": "quiz.table_update",
                "data": {"table_number": table_number},
            },
        )
    except Exception:
        logger.exception("Failed to broadcast quiz table update for event %s", event.id)


def _broadcast_quiz_leaderboard(event):
    """Push fresh standings to everyone connected to the quiz.

    The individual leaderboard is otherwise only rebuilt when the host scores
    or reveals, so removing someone's scores mid-round leaves them on every
    other player's standings until the next question is marked. Goes to
    ``quiz_<id>`` — the shared group — deliberately: the standings are the one
    thing on that page that really is the same for the whole room, and
    ``quiz.leaderboard`` is already handled by the player, host and projector
    clients without triggering an assignment refetch.
    """
    from .consumers import build_leaderboard

    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    try:
        quiz_event = getattr(event, "quiz", None)
        if not quiz_event:
            return
        async_to_sync(channel_layer.group_send)(
            f"quiz_{quiz_event.id}",
            {
                "type": "quiz.leaderboard",
                "data": build_leaderboard(quiz_event.id),
            },
        )
    except Exception:
        logger.exception("Failed to broadcast quiz leaderboard for event %s", event.id)


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


def _get_ticket_print_payload(
    registration, table_info=None, coach_authenticated=False
):
    """Safely build 80mm base64 ESC/POS ticket payload without failing check-in."""
    try:
        from .services.ticket_printer import build_checkin_ticket_base64

        table_number = None
        seat_label = ""
        if table_info and isinstance(table_info, dict):
            table_number = table_info.get("table_number")
            seat_label = table_info.get("seat_label", "") or table_info.get("role", "")

        return build_checkin_ticket_base64(
            registration=registration,
            event=getattr(registration, "event", None),
            table_number=table_number,
            seat_label=seat_label,
            coach_authenticated=coach_authenticated,
        )
    except Exception:
        logger.exception(
            "Failed to build ticket print payload for registration %s",
            getattr(registration, "id", None),
        )
        return ""


@require_GET
@coach_required
def event_reprint_ticket_api(request, event_id, registration_id):
    """Reprint a check-in ticket for an attendee (coach-authenticated)."""
    registration = get_object_or_404(
        EventRegistration.objects.select_related("user", "event", "user__crushprofile"),
        id=registration_id,
        event_id=event_id,
    )
    table_info = _get_existing_table_assignment(registration)
    print_payload = _get_ticket_print_payload(
        registration, table_info=table_info, coach_authenticated=True
    )

    return JsonResponse(
        {
            "success": True,
            "registration_id": registration.id,
            "attendee_name": _get_display_name(registration, coach_authenticated=True),
            "print_payload_base64": print_payload,
        }
    )


@require_GET
@coach_required
def event_test_ticket_api(request, event_id):
    """Generate a sample 80mm ESC/POS test ticket for hardware check."""
    from .models import MeetupEvent
    from .services.ticket_printer import build_checkin_ticket_base64

    event = get_object_or_404(MeetupEvent, id=event_id)
    sample_payload = build_checkin_ticket_base64(
        registration=None,
        event=event,
        table_number=1,
        seat_label="A",
    )
    return JsonResponse(
        {
            "success": True,
            "event_id": event.id,
            "print_payload_base64": sample_payload,
        }
    )
