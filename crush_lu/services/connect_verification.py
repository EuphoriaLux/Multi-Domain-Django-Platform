"""Connect Week human verification, independent of a photo-specific badge.

The existing event route remains authoritative. A new video route requires
LuxID and an explicit, versioned approval; historical badge-only checks never
qualify automatically. Rollout controls default off. Pausing new checks does
not discard a previously granted approval.

Spec: ai-memory-hub/specs/2026-11-10-crush-january-video-verification-funnel.md
"""

from django.conf import settings
from django.db import transaction
from django.db.models import Case, Exists, F, OuterRef, Q, Value, When
from django.utils import timezone

from crush_lu.real_person_check_rules import (
    CONNECT_VIDEO_ADMISSION_POLICY_VERSION,
    CONNECT_VIDEO_PILOT_GROUP,
    MEDIUM_VIDEO,
    PURPOSE_CONNECT_WEEK,
)


def video_verification_mode():
    mode = str(getattr(settings, "CONNECT_VIDEO_VERIFICATION_MODE", "off")).lower()
    return mode if mode in ("off", "pilot", "public") else "off"


def is_video_verification_pilot(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and user.groups.filter(name=CONNECT_VIDEO_PILOT_GROUP).exists()
    )


def human_verification_policy_applies(user):
    # Enrollment continues to enforce the pilot policy when new requests are
    # paused. Otherwise switching mode to off during full launch would open
    # Connect Week to a pilot member who had never passed a human check.
    return bool(
        getattr(settings, "CONNECT_WEEK_HUMAN_VERIFICATION_REQUIRED", False)
        or is_video_verification_pilot(user)
    )


def video_verification_requests_open(user):
    from crush_lu.connect_phase import candidate_access_open

    if not (
        user
        and user.is_authenticated
        and user.is_active
        and candidate_access_open()
        and getattr(settings, "CONNECT_REAL_PERSON_CHECK_ENABLED", False)
    ):
        return False
    mode = video_verification_mode()
    return (mode == "public" and human_verification_policy_applies(user)) or (
        mode == "pilot" and is_video_verification_pilot(user)
    )


def valid_video_admissions():
    """Current admission evidence; changing a photo or hiding a badge is irrelevant.

    Photo, consent, setup, pause and discovery requirements still belong to
    their feature gates. This queryset is also used by the aggregate audit.
    """
    from crush_lu.models import ConnectRealPersonCheck, CrushProfile

    native, oidc = CrushProfile.luxid_account_querysets(OuterRef("profile__user_id"))
    return (
        ConnectRealPersonCheck.objects.filter(
            purpose=PURPOSE_CONNECT_WEEK,
            status=ConnectRealPersonCheck.STATUS_PASSED,
            medium=MEDIUM_VIDEO,
            admission_policy_version=CONNECT_VIDEO_ADMISSION_POLICY_VERSION,
            admission_granted_at__isnull=False,
            admission_invalidated_at__isnull=True,
            admission_profile_approved_at__isnull=False,
            profile__approved_at=F("admission_profile_approved_at"),
            profile__verification_status="verified",
            profile__is_approved=True,
            profile__is_active=True,
            profile__user__is_active=True,
        )
        .annotate(_luxid_native=Exists(native), _luxid_oidc=Exists(oidc))
        .filter(Q(_luxid_native=True) | Q(_luxid_oidc=True))
    )


def has_valid_video_admission(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and valid_video_admissions().filter(profile__user_id=user.pk).exists()
    )


def human_verification_method(user):
    """Return the existing event route or video, not overall Connect readiness.

    The event route preserves grandfathered coach-attested verification
    methods; its result alone must not claim attendance at a particular event.
    """
    from crush_lu.models import CrushProfile

    if not user or not user.is_authenticated or not user.is_active:
        return ""
    profile = (
        CrushProfile.objects.select_related("user")
        .filter(
            user_id=user.pk,
            user__is_active=True,
            is_active=True,
            verification_status="verified",
            is_approved=True,
        )
        .first()
    )
    if profile is None:
        return ""
    if profile.has_attended_event:
        return "event"
    return "video" if has_valid_video_admission(user) else ""


def invalidate_video_admissions(profile_id, *, now=None):
    """Permanently end admission evidence and release unfinished admission checks.

    Call in the transaction that de-verifies the profile. The persistent
    invalidation also covers the admin path which preserves approved_at.
    Badge-only checks and their history are untouched.
    """
    from crush_lu.models import ConnectRealPersonCheck

    return ConnectRealPersonCheck.objects.filter(
        profile_id=profile_id,
        purpose=PURPOSE_CONNECT_WEEK,
        admission_invalidated_at__isnull=True,
    ).update(
        admission_invalidated_at=now or timezone.now(),
        status=Case(
            When(status="open", then=Value("released")),
            default=F("status"),
        ),
    )


def grant_video_admission(coach, check_id, *, confirmed=False, now=None):
    """Grant a completed admission-purpose video check once, for its owning coach.

    Call inside the explicit video-decision transaction. This primitive does
    not expose an HTTP endpoint or promote historical badge-only checks.
    Locks are profile -> check, matching decide/revoke; no coach lock is
    acquired after those (claim_check alone locks coach before profile).
    """
    from crush_lu.models import CrushCoach, UserDataConsent
    from crush_lu.services.blocking import is_blocked_pair
    from crush_lu.services.photo_publication import get_public_photo_key
    from crush_lu.services.real_person_check import (
        RealPersonCheckError,
        _lock_check,
        _lock_profile,
        _profile_id_of,
        _unresolved_fake_reports,
        validate_checklist,
    )

    if confirmed is not True:
        raise RealPersonCheckError("not_confirmed", 400)
    profile_id = _profile_id_of(check_id, coach)
    now = now or timezone.now()
    with transaction.atomic():
        profile = _lock_profile(profile_id)
        check = _lock_check(check_id)
        fresh_coach = (
            CrushCoach.objects.select_related("user").filter(pk=coach.pk).first()
        )
        if check.coach_id != coach.pk:
            raise RealPersonCheckError("not_found", 404)
        if (
            fresh_coach is None
            or not fresh_coach.is_active
            or not fresh_coach.user.is_active
            or fresh_coach.user_id == profile.user_id
        ):
            raise RealPersonCheckError("coach_inactive", 403)
        if (
            check.purpose != PURPOSE_CONNECT_WEEK
            or check.admission_policy_version != CONNECT_VIDEO_ADMISSION_POLICY_VERSION
            or check.status != check.STATUS_PASSED
            or check.medium != MEDIUM_VIDEO
            or check.decided_at is None
        ):
            raise RealPersonCheckError("not_admission_video", 409)
        if (
            check.admission_invalidated_at is not None
            or profile.approved_at is None
            or check.admission_profile_approved_at != profile.approved_at
            or profile.verification_status != "verified"
            or not profile.is_approved
            or not profile.is_active
            or not profile.user.is_active
            or not profile.has_luxid_connected
        ):
            raise RealPersonCheckError("verification_changed", 403)
        # A retry of an already committed grant must not depend on whether
        # new requests are paused or an ordinary photo replacement followed.
        if check.admission_granted_at is not None:
            return False
        if not video_verification_requests_open(profile.user):
            raise RealPersonCheckError("video_admission_closed", 403)
        consent = UserDataConsent.objects.filter(user_id=profile.user_id).first()
        if (
            consent is None
            or not consent.crushlu_consent_given
            or consent.crushlu_banned
        ):
            raise RealPersonCheckError("no_consent", 403)
        if (
            is_blocked_pair(fresh_coach.user, profile.user)
            or _unresolved_fake_reports()
            .filter(reported_user_id=profile.user_id)
            .exists()
            or (
                profile.has_active_premium
                and profile.assigned_coach_id not in (None, fresh_coach.pk)
            )
        ):
            raise RealPersonCheckError("restricted", 403)
        if (
            not check.photo_key
            or check.photo_key != getattr(profile.photo_1, "name", "")
            or check.photo_key != get_public_photo_key(profile, "photo_1")
            or profile.photo_review_status in ("needs_revision", "flagged_fake")
        ):
            raise RealPersonCheckError("stale_photo", 403)
        validate_checklist(MEDIUM_VIDEO, check.checklist)
        check.admission_granted_at = now
        check.save(update_fields=["admission_granted_at"])
    return True
