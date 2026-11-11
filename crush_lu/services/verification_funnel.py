"""Member-requested video verification, separate from optional badge outreach.

Spec: ai-memory-hub/specs/2026-11-10-crush-january-video-verification-funnel.md
Lock order: claiming coach -> profile -> check -> appointment. No submissions
are created or reopened for this route.
"""

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from crush_lu.models import ConnectRealPersonCheck, CrushProfile, UserDataConsent
from crush_lu.real_person_check_rules import (
    CLAIM_TTL,
    CONNECT_VIDEO_ADMISSION_POLICY_VERSION,
    MAX_OPEN_CLAIMS_PER_COACH,
    MEDIUM_VIDEO,
    PURPOSE_CONNECT_WEEK,
)
from crush_lu.services.connect_verification import (
    human_verification_method,
    human_verification_policy_applies,
    video_verification_requests_open,
)
from crush_lu.services.real_person_check import (
    RealPersonCheckError,
    _lock_check,
    _lock_coach,
    _lock_profile,
    _unresolved_fake_reports,
)

logger = logging.getLogger(__name__)


def member_prerequisite(profile):
    """Public, non-sensitive reason the digital route cannot start yet."""
    from crush_lu.services.photo_publication import get_public_photo_key

    if not profile:
        return "profile"
    if not profile.is_active or not profile.user.is_active:
        return "restricted"
    consent = UserDataConsent.objects.filter(user_id=profile.user_id).first()
    if not consent or not consent.crushlu_consent_given or consent.crushlu_banned:
        return "restricted"
    membership = getattr(profile.user, "crush_connect_membership", None)
    if (
        profile.verification_status == "rejected"
        or profile.photo_review_status == "flagged_fake"
        or (membership and membership.excluded_by_coach)
        or _unresolved_fake_reports().filter(reported_user_id=profile.user_id).exists()
    ):
        return "restricted"
    if profile.verification_status == "incomplete":
        return "profile"
    if not profile.has_luxid_connected:
        return "luxid"
    if (
        profile.verification_status != "verified"
        or not profile.is_approved
        or not profile.approved_at
    ):
        return "profile"
    if not profile.photo_1 or profile.photo_review_status == "needs_revision":
        return "photo"
    if get_public_photo_key(profile, "photo_1") != profile.photo_1.name:
        return "photo_review"
    return ""


def admission_guards(coach, profile, *, photo_key=None, confirmed=None):
    """Same safeguards as optional checks, with the new purpose's cohort."""
    from crush_lu.services.real_person_check import check_guards

    failures = check_guards(
        coach,
        profile,
        photo_key=photo_key,
        medium=MEDIUM_VIDEO,
        confirmed=confirmed,
        purpose=PURPOSE_CONNECT_WEEK,
    )
    return failures


def request_verification(user):
    from crush_lu.services.video_call import video_calls_available

    if not video_verification_requests_open(user):
        raise RealPersonCheckError("video_admission_closed", 403)
    if not video_calls_available():
        raise RealPersonCheckError("video_unavailable", 403)
    profile_id = (
        CrushProfile.objects.filter(user=user).values_list("pk", flat=True).first()
    )
    if not profile_id:
        raise RealPersonCheckError("profile", 403)
    with transaction.atomic():
        profile = _lock_profile(profile_id)
        reason = member_prerequisite(profile)
        if reason:
            raise RealPersonCheckError(reason, 403)
        if human_verification_method(user):
            raise RealPersonCheckError("already_verified", 409)
        existing = ConnectRealPersonCheck.objects.filter(
            profile=profile, status="open"
        ).first()
        if existing:
            if existing.purpose == PURPOSE_CONNECT_WEEK:
                return existing
            if existing.is_live():
                raise RealPersonCheckError("taken", 409)
            existing.status = "released"
            existing.save(update_fields=["status"])
        return ConnectRealPersonCheck.objects.create(
            profile=profile,
            purpose=PURPOSE_CONNECT_WEEK,
            requested_at=timezone.now(),
            admission_policy_version=CONNECT_VIDEO_ADMISSION_POLICY_VERSION,
            admission_profile_approved_at=profile.approved_at,
        )


def video_channel_requests(coach):
    """Only actionable member requests, including safely reclaimable leases."""
    from crush_lu.services.blocking import block_exists_subquery
    from crush_lu.services.premium_billing import entitled_q
    from crush_lu.models import PremiumMembership

    now = timezone.now()
    blocked = (
        get_user_model()
        .objects.filter(pk=OuterRef("profile__user_id"))
        .filter(block_exists_subquery(coach.user))
    )
    qs = (
        ConnectRealPersonCheck.objects.filter(
            purpose=PURPOSE_CONNECT_WEEK,
            status="open",
            admission_invalidated_at__isnull=True,
        )
        .filter(Q(coach__isnull=True) | Q(lease_expires_at__lte=now))
        .select_related("profile__user")
    )
    qs = qs.exclude(Exists(blocked)).exclude(profile__user_id=coach.user_id)
    has_premium = Exists(
        PremiumMembership.objects.filter(
            entitled_q(now=now), user_id=OuterRef("profile__user_id")
        )
    )
    qs = qs.annotate(_premium=has_premium).filter(
        Q(_premium=False)
        | Q(profile__assigned_coach__isnull=True)
        | Q(profile__assigned_coach=coach)
    )
    return [
        check
        for check in qs
        if video_verification_requests_open(check.profile.user)
        and not member_prerequisite(check.profile)
        and not human_verification_method(check.profile.user)
    ]


def claim_video_request(coach, check_id):
    with transaction.atomic():
        coach = _lock_coach(coach.pk)
        profile_id = (
            ConnectRealPersonCheck.objects.filter(
                pk=check_id, purpose=PURPOSE_CONNECT_WEEK
            )
            .values_list("profile_id", flat=True)
            .first()
        )
        if profile_id is None:
            raise RealPersonCheckError("not_found", 404)
        profile = _lock_profile(profile_id)
        check = _lock_check(check_id)
        now = timezone.now()
        if check.status != "open" or check.admission_invalidated_at:
            raise RealPersonCheckError("not_open", 409)
        failures = admission_guards(coach, profile)
        if failures:
            raise RealPersonCheckError(failures[0], 403, failures=failures)
        if check.is_live(now):
            if check.coach_id == coach.pk:
                return check
            raise RealPersonCheckError("taken", 409)
        if (
            ConnectRealPersonCheck.objects.filter(
                coach=coach, status="open", lease_expires_at__gt=now
            ).count()
            >= MAX_OPEN_CLAIMS_PER_COACH
        ):
            raise RealPersonCheckError("capacity", 409)
        finish_appointments(check, "cancelled")
        check.coach = coach
        check.claimed_at = now
        check.lease_expires_at = now + CLAIM_TTL
        check.save(update_fields=["coach", "claimed_at", "lease_expires_at"])
        return check


def finish_appointments(check, status="cancelled"):
    check.appointments.filter(status="booked").update(
        status=status, updated_at=timezone.now()
    )


def complete_from_event(user_id):
    """Event qualification releases redundant unfinished video work after commit."""
    profile_id = (
        CrushProfile.objects.filter(user_id=user_id)
        .values_list("pk", flat=True)
        .first()
    )
    if not profile_id:
        return
    with transaction.atomic():
        profile = _lock_profile(profile_id)
        if not profile.has_attended_event:
            return
        checks = ConnectRealPersonCheck.objects.select_for_update().filter(
            profile=profile,
            purpose=PURPOSE_CONNECT_WEEK,
            status="open",
        )
        for check in checks:
            finish_appointments(check)
            check.status = "released"
            check.decided_at = timezone.now()
            check.save(update_fields=["status", "decided_at"])


def cancel_verification(user):
    profile_id = (
        CrushProfile.objects.filter(user=user).values_list("pk", flat=True).first()
    )
    if not profile_id:
        return
    with transaction.atomic():
        profile = _lock_profile(profile_id)
        check = (
            ConnectRealPersonCheck.objects.select_for_update()
            .filter(profile=profile, purpose=PURPOSE_CONNECT_WEEK, status="open")
            .first()
        )
        if check:
            finish_appointments(check)
            check.status = "released"
            check.decided_at = timezone.now()
            check.save(update_fields=["status", "decided_at"])


def notify_outcome(check_id, event="decision"):
    """After-commit, deduplicated bell notice; notes and adverse verdicts stay private."""
    from crush_lu.models import Notification

    try:
        check = ConnectRealPersonCheck.objects.select_related("profile__user").get(
            pk=check_id
        )
        with translation.override(check.profile.preferred_language or "en"):
            Notification.objects.get_or_create(
                user_id=check.profile.user_id,
                notification_type="video_verification",
                dedupe_key=f"{event}:{check.pk}",
                defaults={
                    "title": _("Your video verification has an update"),
                    "body": _("Open your verification page to see your next step."),
                    "link_url": reverse(
                        "crush_lu:connect_verification",
                        urlconf="azureproject.urls_crush",
                    ),
                },
            )
    except Exception:
        logger.warning(
            "Video verification outcome notice failed for check %s", check_id
        )


def funnel_visible(user):
    return human_verification_policy_applies(user) or video_verification_requests_open(
        user
    )


def member_funnel(user):
    """One next action; never exposes internal notes, contact details or reports."""
    from crush_lu.connect_phase import cycle_access_open
    from crush_lu.services.real_person_check import member_call_check
    from crush_lu.views_crush_connect import _connect_readiness

    profile = getattr(user, "crushprofile", None)
    reason = member_prerequisite(profile)
    membership = getattr(user, "crush_connect_membership", None)
    method = human_verification_method(user)
    latest = (
        ConnectRealPersonCheck.objects.filter(
            profile=profile, purpose=PURPOSE_CONNECT_WEEK
        )
        .select_related("coach__user")
        .first()
        if profile
        else None
    )
    from crush_lu.services.video_call import video_calls_available

    data = {
        "method": method,
        "request_open": video_verification_requests_open(user)
        and video_calls_available(),
        "can_cancel": bool(latest and latest.status == "open"),
        "state": "",
        "action_url": "",
        "action_label": "",
        "can_request": False,
    }
    states = {
        "restricted": (
            _("Your account needs review"),
            _("Contact the team for help with your verification."),
            "",
            _("Contact the team"),
        ),
        "profile": (
            _("Finish your profile"),
            _("Complete and submit your profile before arranging verification."),
            "crush_lu:edit_profile",
            _("Complete my profile"),
        ),
        "luxid": (
            _("Confirm your identity"),
            _(
                "Link LuxID before a coach video verification, or meet a coach at an event."
            ),
            "crush_lu:profile_submitted",
            _("Link LuxID"),
        ),
        "photo": (
            _("Prepare your Connect photo"),
            _("Add a clear main photo so a coach can compare it with you on video."),
            "crush_lu:edit_profile",
            _("View my photos"),
        ),
        "photo_review": (
            _("Your photo is awaiting coach review"),
            _("You can prepare your Connect card while a coach reviews your photo."),
            "crush_lu:crush_connect_onboarding",
            _("Prepare my Connect card"),
        ),
    }
    # Event-only members do not need LuxID. Human approval survives ordinary
    # photo replacement; actual publication/setup requirements still apply.
    if method and reason in ("luxid", "photo", "photo_review"):
        reason = ""
    if reason:
        title, body, route, label = states[reason]
        data.update(
            state=reason,
            title=title,
            body=body,
            action_url=reverse(route) if route else "mailto:support@crush.lu",
            action_label=label,
        )
    elif membership and membership.paused_at:
        data.update(
            state="paused",
            title=_("Connect is paused"),
            body=_(
                "Your verification history is retained. Resume Connect when you are ready."
            ),
            action_url=reverse("crush_lu:crush_connect_hub"),
            action_label=_("Manage Connect"),
        )
    elif method:
        readiness = _connect_readiness(user)
        remaining = [s for s in readiness["steps"] if not s["complete"]]
        if remaining:
            step = remaining[0]
            data.update(
                state="finish_setup",
                title=_("Coach verification complete; finish your setup"),
                body=step["description"],
                action_url=step["cta_url"],
                action_label=step["cta_label"],
            )
        elif cycle_access_open(user):
            data.update(
                state="ready",
                title=_("Connect Week is available"),
                body=_("Your verification and Connect card are ready."),
                action_url=reverse("crush_lu:connect_week_home"),
                action_label=_("Open Connect Week"),
            )
        else:
            data.update(
                state="phase_closed",
                title=_("Coach verification complete"),
                body=_(
                    "Your verification is saved. Connect Week will be available when this phase opens."
                ),
            )
    elif latest and latest.status == "open":
        if not data["request_open"]:
            data.update(
                state="video_unavailable",
                title=_("Video verification is currently unavailable"),
                body=_(
                    "Your request is saved. You can still have your participation confirmed by a coach at a Crush event."
                ),
                action_url=reverse("crush_lu:event_list"),
                action_label=_("Browse upcoming events"),
            )
            return data
        slot = latest.appointments.filter(status="booked").first()
        if slot and slot.end_at < timezone.now():
            data.update(
                state="retry",
                title=_("Video verification needs another attempt"),
                body=_(
                    "Arrange another video call with your coach, or choose an event."
                ),
            )
        elif slot:
            data.update(
                state="arranged",
                title=_("Your video verification is arranged"),
                body=_(
                    "Join your coach at the agreed time. Keep your camera and microphone ready."
                ),
                appointment=slot,
            )
        else:
            data.update(
                state="waiting",
                title=_("Your video verification request is received"),
                body=_(
                    "A coach will contact you to agree a time. You can finish your Connect preparation meanwhile."
                ),
            )
        if latest.coach_id:
            data["coach_name"] = latest.coach.user.first_name
        if member_call_check(user):
            data.update(
                action_url=reverse(
                    "crush_lu:crush_connect_real_person_call", args=[latest.pk]
                ),
                action_label=_("Join video verification"),
            )
    else:
        data.update(
            state="choose",
            title=_("Meet a coach to open Connect Week"),
            body=_(
                "Choose a coach video verification with LuxID, or have your participation confirmed at a Crush event. Both routes give the same Connect Week access once your card is ready."
            ),
            can_request=data["request_open"],
        )
        if latest and latest.status in ("unreachable", "released", "declined"):
            data.update(
                state="retry", title=_("Video verification needs another attempt")
            )
        if not data["request_open"]:
            data.update(
                body=_(
                    "Video verification is currently unavailable. You can have your participation confirmed by a coach at a Crush event."
                ),
                action_url=reverse("crush_lu:event_list"),
                action_label=_("Browse upcoming events"),
            )
    return data
