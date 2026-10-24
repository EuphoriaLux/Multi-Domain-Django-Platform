"""Crush Connect real-person check: which members a coach should call.

A Connect candidate who was never seen by a coach at an event has only LuxID
behind their card. A coach can call them (video or voice) and grant a badge
that other members see. The check is badge-only: it never changes the
catalogue, identity or Connect Week gates.

This module holds the cohort, the coach queue, the guards every coach-facing
view runs, and the state changes (claim, log, decide, release, revoke). It
sends nothing: no email, no push, no SMS. A coach opens the ``sms:`` /
``tel:`` / ``wa.me`` links on their own phone, and ``log_outreach`` only
records that they did.

Lock order, always: ``CrushProfile`` first, then the check row (the order
``services.photo_review.submit_member_review`` uses). ``claim_check`` alone
takes one lock before those, the claiming coach's ``CrushCoach`` row, because
it must count that coach's open claims without a second claim slipping in
(``PremiumMembership.confirm`` locks coach then profile too, so the two agree).
SQLite ignores ``select_for_update``, so ``test_real_person_check_claims``
asserts the order on the source.

Logs carry ids only, never a phone number, a note or a rejected payload.

Spec: ai-memory-hub/specs/2026-10-24-crush-connect-real-person-check.md
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from crush_lu.real_person_check_rules import (
    CHECKLIST_KEYS,
    CLAIM_TTL,
    DECLINE_COOLDOWN,
    MAX_OPEN_CLAIMS_PER_COACH,
    MEDIUM_VIDEO,
    MEDIUM_VOICE,
    MEDIUMS,
    NOTES_MAX_LENGTH,
    REQUIRED_CHECKLIST_KEYS,
)
from crush_lu.services.crush_connect import (
    coach_attested_attendance_q,
    filter_catalogue_eligible,
)

logger = logging.getLogger(__name__)

# Outcomes ``decide_check`` accepts.
OUTCOME_PASS_VIDEO = "pass_video"
OUTCOME_PASS_VOICE = "pass_voice"
OUTCOME_UNREACHABLE = "unreachable"
OUTCOME_DECLINED = "declined"
OUTCOME_SUSPECTED_FAKE = "suspected_fake"
OUTCOMES = (
    OUTCOME_PASS_VIDEO,
    OUTCOME_PASS_VOICE,
    OUTCOME_UNREACHABLE,
    OUTCOME_DECLINED,
    OUTCOME_SUSPECTED_FAKE,
)
_PASS_MEDIUM = {OUTCOME_PASS_VIDEO: MEDIUM_VIDEO, OUTCOME_PASS_VOICE: MEDIUM_VOICE}

# Guard codes ``check_guards`` returns. Views translate them into messages.
GUARD_SELF = "self"
GUARD_COACH_INACTIVE = "coach_inactive"
GUARD_INACTIVE = "inactive"
GUARD_NO_CONSENT = "no_consent"
GUARD_BANNED = "banned"
GUARD_NO_PHOTO = "no_photo"
GUARD_STALE_PHOTO = "stale_photo"
GUARD_PHOTO_NOT_PUBLIC = "photo_not_public"
GUARD_BLOCKED = "blocked"
GUARD_FAKE_REPORT_PENDING = "fake_report_pending"
GUARD_OTHER_COACH_PREMIUM = "other_coach_premium"
GUARD_NOT_CONFIRMED = "not_confirmed"
GUARD_ALREADY_VERIFIED = "already_verified"
GUARD_NOT_IN_COHORT = "not_in_cohort"

# What a coach may log between claim and decision. ``success`` is written only
# by a passing decision.
LOGGABLE_RESULTS = ("sms_sent", "whatsapp_sent", "failed")

# Report statuses that still await a superuser.
_UNRESOLVED_REPORT_STATUSES = ("open", "reviewing")


def _unresolved_fake_reports():
    """Fake-profile reports a superuser has not acted on yet.

    One definition for the queue and for ``check_guards``, so a member the queue
    hides can never be claimed or passed from a stale page.
    """
    from crush_lu.models import UserReport

    return UserReport.objects.filter(
        reason="fake_profile", status__in=_UNRESOLVED_REPORT_STATUSES
    )


class RealPersonCheckError(ValueError):
    """A refusal with a machine-readable ``code`` the view turns into a message.

    ``failures`` lists every guard that failed when the refusal came from
    ``check_guards``; ``status`` is the HTTP status a view should answer with.
    """

    def __init__(self, code, status=409, *, failures=None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.failures = list(failures) if failures else [code]


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def real_person_check_enabled() -> bool:
    """Whether the coach queue and call tooling are switched on."""
    return bool(getattr(settings, "CONNECT_REAL_PERSON_CHECK_ENABLED", False))


def real_person_badge_visible() -> bool:
    """Whether other members see a granted badge.

    Only effective while the tooling flag is on too: the badge flag alone never
    shows anything.
    """
    return real_person_check_enabled() and bool(
        getattr(settings, "CONNECT_REAL_PERSON_BADGE_VISIBLE", False)
    )


# ---------------------------------------------------------------------------
# Cohort and queue
# ---------------------------------------------------------------------------


def real_person_check_cohort(qs=None):
    """Catalogue members, at any login age, that no coach has seen in person.

    Starts from the catalogue gate without its 30-day ``last_login`` window and
    removes every coach-attested arm of the identity gate. A self-scanned
    attendance attests nothing, so those members stay in the cohort.
    """
    if qs is None:
        qs = get_user_model().objects.all()
    return filter_catalogue_eligible(qs, require_recent_login=False).exclude(
        coach_attested_attendance_q()
    )


def real_person_unchecked(qs=None, now=None):
    """Cohort users some coach could still reach: who the outreach segments invite.

    ``real_person_check_cohort`` minus members with a valid badge, a decline
    inside the cooldown, or an unresolved fake-profile report: the
    coach-independent half of ``real_person_check_queue``, so a campaign never
    invites someone who just said no or who awaits a superuser. Still a
    ``User`` queryset (the subqueries read ``OuterRef("pk")`` against
    ``User``); callers map it onto profiles themselves.

    Not the coach-specific rules (live claim, blocked pair, another coach's
    Premium member). Consent is the sending channel's job (the SMS page,
    newsletter and campaign resolvers each apply their own).
    """
    now = now or timezone.now()
    return (
        real_person_check_cohort(qs)
        .exclude(_valid_badge_subquery())
        .exclude(_recently_declined_subquery(now))
        .exclude(_unresolved_fake_report_subquery())
    )


def granted_badge_medium(profile) -> str:
    """``"video"`` / ``"voice"`` for a still-valid granted badge, else ``""``.

    Ignores both flags, so the coach panel can say what was granted even while
    members cannot see it; ``CrushProfile.real_person_badge`` adds the flag and
    the door badge on top. Valid means the profile is still verified and
    active, ``approved_at`` still equals the value the badge was granted
    against, and (video only) ``photo_1`` is still the file the coach
    compared; if it was replaced, the badge stays true as ``"voice"``.

    A de-verification that goes through ``CrushProfile.save()`` also deletes
    the row (``discard_badge``), so re-verifying later never revives it, even
    where ``approved_at`` was left alone (the admin change form).
    """
    from django.core.exceptions import ObjectDoesNotExist

    try:
        badge = profile.connect_real_person_badge
    except ObjectDoesNotExist:
        return ""
    if (
        profile.verification_status != "verified"
        or not profile.is_active
        or not profile.user.is_active
        or badge.profile_approved_at != profile.approved_at
    ):
        return ""
    current_key = getattr(profile.photo_1, "name", "") or ""
    if (
        badge.medium == MEDIUM_VIDEO
        and badge.photo_key
        and badge.photo_key == current_key
    ):
        return MEDIUM_VIDEO
    return MEDIUM_VOICE


def discard_badge(profile_id) -> int:
    """Delete the profile's current badge row; returns how many rows went.

    The backstop for a de-verification that does not move ``approved_at``.
    The badge is valid only while ``profile_approved_at`` equals the profile's
    ``approved_at``, and the verification writers (``profile_verification``,
    the bulk approve actions) move that value. The Django admin change form is
    the exception: its ``approved_at`` is read-only, so de-verifying a member
    there and ticking them verified again would otherwise bring the old badge
    back with no new call. ``CrushProfile.save()`` therefore calls this when
    a save takes a profile out of ``verified``.

    The source check stays as history (``passed``); only the live badge goes.
    """
    from crush_lu.models import ConnectRealPersonBadge

    deleted, _by_model = ConnectRealPersonBadge.objects.filter(
        profile_id=profile_id
    ).delete()
    return deleted


def _valid_badge_subquery():
    """``Exists`` of a badge row that is still valid for the user's profile.

    Valid means its ``profile_approved_at`` snapshot still equals the
    profile's ``approved_at`` (NULL matches NULL, as the property compares).
    """
    from crush_lu.models import ConnectRealPersonBadge

    return Exists(
        ConnectRealPersonBadge.objects.filter(profile__user_id=OuterRef("pk")).filter(
            Q(profile_approved_at=F("profile__approved_at"))
            | Q(profile_approved_at__isnull=True, profile__approved_at__isnull=True)
        )
    )


def _recently_declined_subquery(now):
    """``Exists``: the user said no to a coach within ``DECLINE_COOLDOWN``."""
    from crush_lu.models import ConnectRealPersonCheck

    return Exists(
        ConnectRealPersonCheck.objects.filter(
            profile__user_id=OuterRef("pk"),
            status=ConnectRealPersonCheck.STATUS_DECLINED,
            decided_at__gte=now - DECLINE_COOLDOWN,
        )
    )


def _unresolved_fake_report_subquery():
    """``Exists``: a fake-profile report against the user awaits a superuser."""
    return Exists(_unresolved_fake_reports().filter(reported_user_id=OuterRef("pk")))


def member_awaits_outreach(user, now=None) -> bool:
    """Whether some coach could reach ``user`` about a check right now.

    The coach-independent part of ``real_person_check_queue``: in the cohort,
    no valid badge, no decline inside the cooldown, no unresolved fake-profile
    report. Not the coach-specific rules (a live claim, a block, another
    coach's Premium member). The member hub asks this before it tells someone
    a coach may text them, so the note is never shown to a member the queue
    keeps from every coach.
    """
    return real_person_unchecked(
        get_user_model().objects.filter(pk=user.pk), now=now
    ).exists()


def real_person_check_queue(coach, now=None):
    """Users ``coach`` may claim, most recently active first.

    The cohort minus: the coach, members with a valid badge, members another
    coach holds a live claim on, members who declined within the cooldown,
    members without Crush.lu consent or banned, blocked pairs, members with an
    unresolved fake-profile report, and Premium members of another coach.
    The member's own live claim by ``coach`` stays in (it is theirs to work).
    """
    from crush_lu.models import (
        ConnectRealPersonCheck,
        PremiumMembership,
        UserDataConsent,
    )
    from crush_lu.services.blocking import block_exists_subquery
    from crush_lu.services.premium_billing import entitled_q

    now = now or timezone.now()
    live_elsewhere = Exists(
        ConnectRealPersonCheck.objects.filter(
            profile__user_id=OuterRef("pk"),
            status=ConnectRealPersonCheck.STATUS_OPEN,
            lease_expires_at__gt=now,
        ).exclude(coach=coach)
    )
    recently_declined = _recently_declined_subquery(now)
    unresolved_fake_report = _unresolved_fake_report_subquery()
    has_premium = Exists(
        PremiumMembership.objects.filter(entitled_q(now=now), user_id=OuterRef("pk"))
    )
    return (
        real_person_check_cohort()
        .exclude(pk=coach.user_id)
        .annotate(
            _rpc_valid_badge=_valid_badge_subquery(),
            _rpc_live_elsewhere=live_elsewhere,
            _rpc_recently_declined=recently_declined,
            _rpc_fake_report=unresolved_fake_report,
            _rpc_blocked=block_exists_subquery(coach.user),
            _rpc_premium=has_premium,
        )
        .filter(
            _rpc_valid_badge=False,
            _rpc_live_elsewhere=False,
            _rpc_recently_declined=False,
            _rpc_fake_report=False,
            _rpc_blocked=False,
            pk__in=UserDataConsent.objects.filter(
                crushlu_consent_given=True, crushlu_banned=False
            ).values("user_id"),
        )
        # Same rule as views_coach.coach_verify_member: Premium belongs to the
        # member's own coach. An assigned coach alone is not Premium.
        .filter(
            Q(_rpc_premium=False)
            | Q(crushprofile__assigned_coach__isnull=True)
            | Q(crushprofile__assigned_coach=coach)
        )
        .select_related("crushprofile")
        .order_by(F("last_login").desc(nulls_last=True), "pk")
    )


def open_checks_for_coach(coach, now=None):
    """The coach's live claims, soonest to lapse first."""
    from crush_lu.models import ConnectRealPersonCheck

    now = now or timezone.now()
    return (
        ConnectRealPersonCheck.objects.filter(
            coach=coach,
            status=ConnectRealPersonCheck.STATUS_OPEN,
            lease_expires_at__gt=now,
        )
        .select_related("profile__user")
        .order_by("lease_expires_at", "pk")
    )


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def check_guards(coach, profile, *, photo_key=None, medium=None, confirmed=None):
    """Every reason ``coach`` may not work on ``profile`` right now.

    Returns a list of guard codes, empty when all clear. The coach panel's
    ``coach_verify_member`` guard set, plus the cohort rule and the queue's
    "no unresolved fake-profile report" rule:

    * ``photo_key``: the key the coach's page was showing. ``None`` skips the
      stale-photo guard (claim, page render); a mismatch means the photo
      changed since.
    * ``medium``: ``"video"`` also needs the live photo to be the one other
      members see, or the coach compared a face nobody else can see.
    * ``confirmed``: ``None`` skips it; on a pass the form's ``confirm=yes``
      must be ``True``.

    Pass a profile loaded with ``select_related("user")``.
    """
    from crush_lu.models import UserDataConsent
    from crush_lu.services.blocking import is_blocked_pair
    from crush_lu.services.photo_publication import get_public_photo_key

    failures = []
    user = profile.user
    if profile.user_id == coach.user_id:
        failures.append(GUARD_SELF)
    if not coach.is_active or not coach.user.is_active:
        failures.append(GUARD_COACH_INACTIVE)
    if not user.is_active or not profile.is_active:
        failures.append(GUARD_INACTIVE)

    consent = UserDataConsent.objects.filter(user_id=profile.user_id).first()
    if consent is None or not consent.crushlu_consent_given:
        failures.append(GUARD_NO_CONSENT)
    elif consent.crushlu_banned:
        failures.append(GUARD_BANNED)

    current_key = getattr(profile.photo_1, "name", "") or ""
    if not current_key:
        failures.append(GUARD_NO_PHOTO)
    else:
        if photo_key is not None and photo_key != current_key:
            failures.append(GUARD_STALE_PHOTO)
        if (
            medium == MEDIUM_VIDEO
            and get_public_photo_key(profile, "photo_1") != current_key
        ):
            failures.append(GUARD_PHOTO_NOT_PUBLIC)

    if is_blocked_pair(coach.user, user):
        failures.append(GUARD_BLOCKED)
    if _unresolved_fake_reports().filter(reported_user_id=profile.user_id).exists():
        # The queue already hides this member; a stale queue page, a direct
        # claim POST or a report filed mid-claim must not get around that
        # while a superuser has yet to act.
        failures.append(GUARD_FAKE_REPORT_PENDING)
    if profile.has_active_premium and profile.assigned_coach_id not in (
        None,
        coach.pk,
    ):
        failures.append(GUARD_OTHER_COACH_PREMIUM)
    if confirmed is not None and not confirmed:
        failures.append(GUARD_NOT_CONFIRMED)
    if (
        get_user_model()
        .objects.filter(pk=profile.user_id)
        .filter(_valid_badge_subquery())
        .exists()
    ):
        failures.append(GUARD_ALREADY_VERIFIED)
    if not real_person_check_cohort(
        get_user_model().objects.filter(pk=profile.user_id)
    ).exists():
        failures.append(GUARD_NOT_IN_COHORT)
    return failures


# ---------------------------------------------------------------------------
# Checklist
# ---------------------------------------------------------------------------


def validate_checklist(medium, payload):
    """Return the checklist as ``{key: True}`` or raise ``RealPersonCheckError``.

    With a medium (``"video"`` or ``"voice"``) the payload must tick every key
    that medium requires and may hold no other key. With ``""`` (an outcome
    that is not a pass) only the shape is checked: known keys, boolean values.
    Any other value type is refused rather than coerced, so a tampered form
    cannot pass by posting ``"false"``.
    """
    if medium and medium not in MEDIUMS:
        raise RealPersonCheckError("invalid_medium", 400)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise RealPersonCheckError("invalid_checklist", 400)
    allowed = REQUIRED_CHECKLIST_KEYS[medium] if medium else CHECKLIST_KEYS
    if set(payload) - set(allowed):
        raise RealPersonCheckError("unknown_checklist_keys", 400)
    if any(type(value) is not bool for value in payload.values()):
        raise RealPersonCheckError("invalid_checklist", 400)
    ticked = {key: True for key in allowed if payload.get(key) is True}
    if medium and len(ticked) != len(allowed):
        raise RealPersonCheckError("checklist_incomplete", 400)
    return ticked


def _clean_notes(notes):
    notes = (notes or "").strip()
    if len(notes) > NOTES_MAX_LENGTH:
        raise RealPersonCheckError("notes_too_long", 400)
    return notes


# ---------------------------------------------------------------------------
# State changes
# ---------------------------------------------------------------------------


def _lock_profile(profile_id):
    """Lock the member's profile row, always the first lock of any change."""
    from crush_lu.models import CrushProfile

    profile = (
        CrushProfile.objects.select_for_update(of=("self",))
        .select_related("user")
        .filter(pk=profile_id)
        .first()
    )
    if profile is None:
        raise RealPersonCheckError("not_found", 404)
    return profile


def _lock_coach(coach_id):
    """Lock the claiming coach's row so their claim count cannot race.

    Taken only by ``claim_check``, and before ``_lock_profile``: two claims by
    one coach on different members hold different profile locks, so without
    this both count the same number of open claims and both insert, going past
    ``MAX_OPEN_CLAIMS_PER_COACH``. The order (coach, then profile) is the one
    ``PremiumMembership.confirm`` uses.

    ``no_key=True`` (``FOR NO KEY UPDATE``) still makes concurrent claims by
    this coach wait for each other, but does not conflict with the
    ``FOR KEY SHARE`` that any insert carrying a ``coach`` foreign key takes
    (``log_outreach`` holds a profile lock and then inserts a ``CallAttempt``).
    A plain ``FOR UPDATE`` here would deadlock a claim against such a write on
    the same member.

    Returns the fresh row (``user`` loaded), so the guards see the coach's
    current ``is_active``.
    """
    from crush_lu.models import CrushCoach

    coach = (
        CrushCoach.objects.select_for_update(of=("self",), no_key=True)
        .select_related("user")
        .filter(pk=coach_id)
        .first()
    )
    if coach is None:
        raise RealPersonCheckError("not_found", 404)
    return coach


def _lock_check(check_id):
    """Lock one check row; only ever called after ``_lock_profile``."""
    from crush_lu.models import ConnectRealPersonCheck

    check = (
        ConnectRealPersonCheck.objects.select_for_update().filter(pk=check_id).first()
    )
    if check is None:
        raise RealPersonCheckError("not_found", 404)
    return check


def _profile_id_of(check_id, coach=None):
    """The profile a check belongs to, read without a lock (to lock it first)."""
    from crush_lu.models import ConnectRealPersonCheck

    qs = ConnectRealPersonCheck.objects.filter(pk=check_id)
    if coach is not None:
        qs = qs.filter(coach=coach)
    profile_id = qs.values_list("profile_id", flat=True).first()
    if profile_id is None:
        raise RealPersonCheckError("not_found", 404)
    return profile_id


def _close_expired(profile_id, now):
    """Close this member's expired open rows as released (lazy lease expiry)."""
    from crush_lu.models import ConnectRealPersonCheck

    return (
        ConnectRealPersonCheck.objects.filter(
            profile_id=profile_id, status=ConnectRealPersonCheck.STATUS_OPEN
        )
        .filter(Q(lease_expires_at__lte=now) | Q(lease_expires_at__isnull=True))
        .update(status=ConnectRealPersonCheck.STATUS_RELEASED)
    )


def _open_check_for(profile):
    """The profile's open row, locked. Expired rows are closed before this."""
    from crush_lu.models import ConnectRealPersonCheck

    return (
        ConnectRealPersonCheck.objects.select_for_update()
        .filter(profile=profile, status=ConnectRealPersonCheck.STATUS_OPEN)
        .first()
    )


def _lock_owned_check(coach, check_id):
    """Lock and return ``(profile, check)`` for an open check the coach owns.

    Call inside ``transaction.atomic()``. The profile is locked first, then the
    check. Raises ``not_found`` for another coach's check (so an id belonging to
    someone else looks like any missing id) and ``not_open`` for a closed one.
    """
    profile_id = _profile_id_of(check_id, coach)
    profile = _lock_profile(profile_id)
    check = _lock_check(check_id)
    if check.coach_id != coach.pk:
        raise RealPersonCheckError("not_found", 404)
    if check.status != check.STATUS_OPEN:
        raise RealPersonCheckError("not_open", 409)
    return profile, check


def claim_check(coach, profile_id, *, now=None):
    """Claim a member for ``coach`` and return the open ``ConnectRealPersonCheck``.

    Idempotent for the coach who already holds the live claim. Raises
    ``RealPersonCheckError`` with code ``taken`` (another coach's live claim,
    or lost the race), ``capacity`` (too many live claims), ``cooldown`` (the
    member declined recently), or the first failed guard.
    """
    from crush_lu.models import ConnectRealPersonCheck

    now = now or timezone.now()
    with transaction.atomic():
        # Coach first, then the member's profile, then the check row; the open
        # claim count below is only exact while no other claim by this coach
        # can run alongside it.
        coach = _lock_coach(coach.pk)
        profile = _lock_profile(profile_id)
        failures = check_guards(coach, profile)
        if failures:
            raise RealPersonCheckError(failures[0], 403, failures=failures)

        _close_expired(profile.pk, now)
        existing = _open_check_for(profile)
        if existing is not None:
            if existing.coach_id == coach.pk:
                return existing
            raise RealPersonCheckError("taken", 409)

        if ConnectRealPersonCheck.objects.filter(
            profile=profile,
            status=ConnectRealPersonCheck.STATUS_DECLINED,
            decided_at__gte=now - DECLINE_COOLDOWN,
        ).exists():
            raise RealPersonCheckError("cooldown", 409)

        live = ConnectRealPersonCheck.objects.filter(
            coach=coach,
            status=ConnectRealPersonCheck.STATUS_OPEN,
            lease_expires_at__gt=now,
        ).count()
        if live >= MAX_OPEN_CLAIMS_PER_COACH:
            raise RealPersonCheckError("capacity", 409)

        try:
            # A savepoint: the partial unique index is the real race guard, and
            # losing the race must not poison the outer transaction.
            with transaction.atomic():
                check = ConnectRealPersonCheck.objects.create(
                    profile=profile,
                    coach=coach,
                    claimed_at=now,
                    lease_expires_at=now + CLAIM_TTL,
                )
        except IntegrityError:
            raise RealPersonCheckError("taken", 409) from None
    logger.info("Real-person check %s claimed by coach %s", check.pk, coach.pk)
    return check


def log_outreach(
    coach, check_id, *, result, failure_reason="", notes="", logged_by=None, now=None
):
    """Record one contact attempt and renew the lease; returns the ``CallAttempt``.

    ``result`` is ``sms_sent``, ``whatsapp_sent`` or ``failed`` (which needs a
    ``failure_reason``). The attempt carries ids only: the phone number and the
    message body are never stored here.
    """
    from crush_lu.models import CallAttempt

    now = now or timezone.now()
    if result not in LOGGABLE_RESULTS:
        raise RealPersonCheckError("invalid_result", 400)
    valid_reasons = {code for code, _label in CallAttempt.FAILURE_REASON_CHOICES}
    if result == "failed" and failure_reason not in valid_reasons:
        raise RealPersonCheckError("invalid_failure_reason", 400)
    notes = _clean_notes(notes)

    expired = False
    with transaction.atomic():
        profile, check = _lock_owned_check(coach, check_id)
        if not check.is_live(now):
            # Commit the expiry, then refuse: the coach re-claims from the queue.
            _close_expired(profile.pk, now)
            expired = True
        else:
            failures = check_guards(coach, profile)
            if failures:
                raise RealPersonCheckError(failures[0], 403, failures=failures)
            attempt = CallAttempt.objects.create(
                profile=profile,
                real_person_check=check,
                coach=coach,
                logged_by=logged_by or coach.user,
                result=result,
                failure_reason=failure_reason if result == "failed" else None,
                notes=notes,
            )
            check.lease_expires_at = now + CLAIM_TTL
            check.save(update_fields=["lease_expires_at"])
    if expired:
        raise RealPersonCheckError("lease_expired", 409)
    logger.info("Real-person check %s outreach logged (%s)", check.pk, result)
    return attempt


def release_check(coach, check_id, *, now=None):
    """Give the claim back; the member returns to the queue."""
    from crush_lu.models import ConnectRealPersonCheck

    now = now or timezone.now()
    with transaction.atomic():
        _profile, check = _lock_owned_check(coach, check_id)
        check.status = ConnectRealPersonCheck.STATUS_RELEASED
        check.decided_at = now
        check.save(update_fields=["status", "decided_at"])
    logger.info("Real-person check %s released by coach %s", check.pk, coach.pk)
    return check


def _apply_outcome(
    coach, profile, check, outcome, *, checklist, notes, failure_reason, now
):
    """Write one decision. Runs inside ``decide_check``'s transaction, locks held."""
    from crush_lu.models import (
        CallAttempt,
        ConnectRealPersonBadge,
        ConnectRealPersonCheck,
        UserReport,
    )

    medium = _PASS_MEDIUM.get(outcome, "")
    check.decided_at = now
    check.notes = notes
    check.checklist = checklist
    if medium:
        check.status = ConnectRealPersonCheck.STATUS_PASSED
        check.medium = medium
        # Voice stores no key, so it can never equal photo_1 and show as video.
        check.photo_key = (
            getattr(profile.photo_1, "name", "") if medium == MEDIUM_VIDEO else ""
        )
        check.save()
        ConnectRealPersonBadge.objects.update_or_create(
            profile=profile,
            defaults={
                "source_check": check,
                "medium": medium,
                "photo_key": check.photo_key,
                "granted_at": now,
                "profile_approved_at": profile.approved_at,
            },
        )
        CallAttempt.objects.create(
            profile=profile,
            real_person_check=check,
            coach=coach,
            logged_by=coach.user,
            result="success",
        )
    elif outcome == OUTCOME_UNREACHABLE:
        check.status = ConnectRealPersonCheck.STATUS_UNREACHABLE
        check.save()
        CallAttempt.objects.create(
            profile=profile,
            real_person_check=check,
            coach=coach,
            logged_by=coach.user,
            result="failed",
            failure_reason=failure_reason,
        )
    elif outcome == OUTCOME_DECLINED:
        check.status = ConnectRealPersonCheck.STATUS_DECLINED
        check.save()
    else:
        check.status = ConnectRealPersonCheck.STATUS_SUSPECTED_FAKE
        check.save()
        # Names the check by id only: the notes stay on the check row.
        UserReport.objects.create(
            reporter=coach.user,
            reported_user=profile.user,
            reason="fake_profile",
            details=f"Real-person check #{check.pk}",
            source="real_person",
            source_id=check.pk,
            status="open",
        )


def decide_check(
    coach,
    check_id,
    outcome,
    *,
    checklist=None,
    notes="",
    photo_key=None,
    confirmed=False,
    failure_reason="no_answer",
    now=None,
):
    """Close an open check with ``outcome`` and return it.

    * ``pass_video`` / ``pass_voice``: needs the full checklist, ``confirmed``
      and every guard clear (a video pass also needs ``photo_key``, the key the
      coach's page showed). Upserts the badge and logs a successful
      ``CallAttempt``. A video pass records the exact ``photo_1`` key the coach
      compared, and requires it to be the photo other members see.
    * ``unreachable``: logs a failed ``CallAttempt``; the member stays queued.
    * ``declined``: the member is left alone for ``DECLINE_COOLDOWN``.
    * ``suspected_fake``: opens a ``fake_profile`` ``UserReport`` for a
      superuser. The report names the check by id only, never the notes. The
      coach excludes nobody themselves.

    Only a pass runs the full guard set; the other outcomes only need the coach
    not to be deciding on their own profile, so a coach can always close a
    check that has gone stale (banned, blocked, left the cohort).
    """
    from crush_lu.models import CallAttempt

    if outcome not in OUTCOMES:
        raise RealPersonCheckError("invalid_outcome", 400)
    now = now or timezone.now()
    medium = _PASS_MEDIUM.get(outcome, "")
    if medium == MEDIUM_VIDEO and photo_key is None:
        # A video pass says which photo was compared; leaving it out must not
        # skip the stale-photo guard, so it reads as a mismatch.
        photo_key = ""
    cleaned_checklist = validate_checklist(medium, checklist)
    notes = _clean_notes(notes)
    if outcome == OUTCOME_UNREACHABLE:
        valid_reasons = {code for code, _label in CallAttempt.FAILURE_REASON_CHOICES}
        if failure_reason not in valid_reasons:
            raise RealPersonCheckError("invalid_failure_reason", 400)

    expired = False
    with transaction.atomic():
        profile, check = _lock_owned_check(coach, check_id)
        if not check.is_live(now):
            # Commit the expiry, then refuse: the coach re-claims from the queue.
            _close_expired(profile.pk, now)
            expired = True
        else:
            if medium:
                failures = check_guards(
                    coach,
                    profile,
                    photo_key=photo_key,
                    medium=medium,
                    confirmed=confirmed,
                )
                if failures:
                    raise RealPersonCheckError(failures[0], 403, failures=failures)
            elif profile.user_id == coach.user_id:
                raise RealPersonCheckError(GUARD_SELF, 403)
            _apply_outcome(
                coach,
                profile,
                check,
                outcome,
                checklist=cleaned_checklist,
                notes=notes,
                failure_reason=failure_reason,
                now=now,
            )
    if expired:
        raise RealPersonCheckError("lease_expired", 409)
    logger.info("Real-person check %s decided: %s", check.pk, outcome)
    return check


def revoke_check(actor, check_id, reason="", *, now=None):
    """Withdraw a passed check and its badge. Superusers only.

    Deletes the profile's badge only when it was granted by this very check, so
    revoking an old pass never removes a newer badge.
    """
    from crush_lu.models import ConnectRealPersonBadge, ConnectRealPersonCheck

    if not (
        getattr(actor, "is_authenticated", False)
        and getattr(actor, "is_active", False)
        and actor.is_superuser
    ):
        raise RealPersonCheckError("forbidden", 403)
    now = now or timezone.now()
    profile_id = _profile_id_of(check_id)
    with transaction.atomic():
        _lock_profile(profile_id)
        check = _lock_check(check_id)
        if check.status != ConnectRealPersonCheck.STATUS_PASSED:
            raise RealPersonCheckError("not_passed", 409)
        check.status = ConnectRealPersonCheck.STATUS_REVOKED
        check.revoked_at = now
        check.revoked_by = actor
        check.revoke_reason = (reason or "").strip()[:255]
        check.save(
            update_fields=["status", "revoked_at", "revoked_by", "revoke_reason"]
        )
        ConnectRealPersonBadge.objects.filter(
            profile_id=profile_id, source_check=check
        ).delete()
    logger.info("Real-person check %s revoked by user %s", check.pk, actor.pk)
    return check
