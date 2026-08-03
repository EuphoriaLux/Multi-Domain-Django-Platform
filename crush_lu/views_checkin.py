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
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .decorators import coach_required
from .models import CrushProfile, EventRegistration, ProfileSubmission
from .models.events import SEAT_HOLDING_STATUSES
from .services.profile_verification import claim_profile_verification

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
#: written when listed; the three `checkin_*` provenance fields are what makes
#: the undo reversible without guessing (see `_record_checkin_provenance`).
_CHECKIN_UPDATE_FIELDS = [
    "status",
    "checked_in_at",
    "checkin_prior_status",
    "checkin_granted_coach",
    "checkin_granted_coach_at",
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


#: Statuses the manual Verify button may transition from. The endpoint has
#: always guarded only on "already verified" and approved anything else, so
#: narrowing this to `pending` would silently stop coaches verifying
#: `incomplete` walk-ins at the door — the scanner still offers the button for
#: every unapproved profile.
MANUAL_VERIFY_CLAIM_FROM = ("incomplete", "pending", "rejected")


def _apply_verification(profile, method, coach, now, claim_from=("pending",)):
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

    if not _apply_verification(profile, "coach_event", coach, now):
        # Another path (e.g. a concurrent LuxID callback) verified them first.
        return None
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
                    EventRegistration.objects.select_for_update()
                    .select_related("event", "user")
                    .get(id=registration_id)
                )
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

    The Event Lobby needs no cleanup: ``eligible_participations`` re-checks
    ``event_registration__status == "attended"`` at read time (§5.2), so the
    member drops off the roster, the photo route and the signal targets as
    soon as this commits.
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
        if registration.status != "attended":
            return JsonResponse(
                {
                    "success": False,
                    "error": str(_("This attendee is not checked in.")),
                },
                status=409,
            )

        # No timestamp, no window — and no undo. An attended row with a NULL
        # checked_in_at is valid: attendance entered administratively or by an
        # older flow. Letting it skip the age check is what would turn a
        # 15-minute mis-scan fix into an editor for attendance of any age,
        # because every attended row renders an Undo button.
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
        profile = CrushProfile.objects.filter(user_id=registration.user_id).first()
        granted_coach_id = registration.checkin_granted_coach_id
        granted_at = registration.checkin_granted_coach_at
        if (
            granted_coach_id
            and granted_at
            and profile is not None
            and profile.assigned_coach_id == granted_coach_id
            and profile.assigned_coach_at == granted_at
        ):
            profile.assigned_coach = None
            profile.assigned_coach_at = None
            profile.save(update_fields=["assigned_coach", "assigned_coach_at"])
            coach_cleared = True

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

    display_name = _get_display_name(registration)
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
        # The client decrements the live gender split with this. Only the
        # bucket letter travels — the toast payload would be gratuitous here.
        "gender": getattr(
            getattr(registration.user, "crushprofile", None), "gender", ""
        )
        or "",
        "message": str(_("Check-in undone for %(name)s.")) % {"name": display_name},
    }
    # The *membership* table, not the current one. The door page's table-fill
    # grid is rendered from QuizTableMembership (views_coach.py), so it counts
    # the seat where the person checked in however far the rotation has since
    # moved them — decrementing the current table would leave that tile short
    # and the round-0 tile still holding a seat nobody occupies.
    #
    # Under its own key, never `table_number`: handleRemoteCheckin has no
    # `undone` branch yet (#710), so an undo payload is still read as an
    # arrival, and the arrival key would make the other coaches' pages count
    # the freed seat *up*. `_updateUndoUI` reads this one, which only the
    # acting coach runs — so the door page that issued the correction fixes
    # its own table-fill grid, and no other page is misled into a bump. The
    # quiz table group is a different channel, carries the current table, and
    # reaches the players.
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

    display_name = _get_display_name(registration)
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
    }
    if table_assignment:
        response_data["table_number"] = table_assignment["table_number"]
        response_data["role"] = table_assignment["role"]
    _broadcast_checkin(registration.event_id, response_data)
    if table_assignment:
        _broadcast_quiz_table_update(registration.event, table_assignment)
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
