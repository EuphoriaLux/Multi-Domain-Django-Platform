"""
Coach Photo Review service for Crush.lu & Crush Connect.

Manages the fast photo-moderation queue and swipe decisions (Approve, Flag Fake, Request Revision).
"""

import logging
from datetime import timedelta
from django.core import signing
from django.db import models, transaction
from django.db.models import Case, Exists, OuterRef, Q, Value, When
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import override

from crush_lu.models import (
    CrushCoach,
    ConnectCoachPick,
    CrushConnectMembership,
    CrushProfile,
    ProfilePhotoReviewLog,
    UserReport,
    UserDataConsent,
    EventRegistration,
    Notification,
)
from crush_lu.models.crush_connect_cycle import ConnectPairExclusion
from crush_lu.notification_service import (
    notify_photo_revision,
    NotificationService,
    NotificationType,
)
from crush_lu.services.blocking import is_blocked_pair
from crush_lu.services.crush_connect import is_catalogue_eligible

logger = logging.getLogger(__name__)


LANGUAGE_NAMES = {
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "lu": "Lëtzebuergesch",
    "pt": "Português",
    "es": "Español",
    "it": "Italiano",
}


def _format_phone_info(phone_number: str):
    if not phone_number:
        return {"number": "", "country": "", "is_local": True}
    cleaned = phone_number.strip().replace(" ", "").replace("-", "")
    if cleaned.startswith("+352") or cleaned.startswith("00352"):
        return {
            "number": phone_number,
            "country": _("Luxembourg (+352)"),
            "is_local": True,
        }
    elif cleaned.startswith("+33") or cleaned.startswith("0033"):
        return {"number": phone_number, "country": _("France (+33)"), "is_local": True}
    elif cleaned.startswith("+49") or cleaned.startswith("0049"):
        return {"number": phone_number, "country": _("Germany (+49)"), "is_local": True}
    elif cleaned.startswith("+32") or cleaned.startswith("0032"):
        return {"number": phone_number, "country": _("Belgium (+32)"), "is_local": True}
    elif cleaned.startswith("+351") or cleaned.startswith("00351"):
        return {
            "number": phone_number,
            "country": _("Portugal (+351)"),
            "is_local": True,
        }
    elif cleaned.startswith("+1") or cleaned.startswith("001"):
        return {
            "number": phone_number,
            "country": _("USA/Canada (+1)"),
            "is_local": False,
        }
    elif cleaned.startswith("+44") or cleaned.startswith("0044"):
        return {"number": phone_number, "country": _("UK (+44)"), "is_local": False}
    else:
        prefix = cleaned[:4] if len(cleaned) >= 4 else cleaned
        return {
            "number": phone_number,
            "country": _("International (%(prefix)s)") % {"prefix": prefix},
            "is_local": False,
        }


def get_photo_review_queue(coach: CrushCoach, limit: int = 40, *, cursor=""):
    """
    Fetch profiles waiting for photo review, ordered by urgency:
    1. Crush Connect onboarded members whose photo is pending review
    2. Members who started Connect onboarding
    3. Other active members with a photo
    Excludes the reviewing coach, deactivated/banned users, and already approved/flagged photos.
    """
    base_qs = (
        CrushProfile.objects.filter(
            is_active=True,
            user__is_active=True,
            photo_review_status="pending",
            user_id__in=UserDataConsent.objects.filter(
                crushlu_consent_given=True, crushlu_banned=False
            ).values("user_id"),
        )
        .exclude(Q(photo_1="") | Q(photo_1__isnull=True))
        .exclude(verification_status="rejected")
        .exclude(user=coach.user)
        .select_related(
            "user",
            "user__crush_connect_membership",
            "user__crush_connect_membership__story_prompt",
        )
        .prefetch_related(
            "interests_new",
        )
    )

    # Priority score, snapshotted at the instant the review pass started (the
    # first page; every cursor carries it). A membership created or onboarded
    # mid-pass would otherwise raise a card's priority and move it behind the
    # cursor, skipping it for the rest of the pass.
    # 3: Connect onboarded
    # 2: Connect membership exists
    # 1: General profile
    as_of = timezone.now()
    position = None
    if cursor:
        try:
            position = signing.loads(cursor, salt="coach-photo-queue", max_age=86400)
            priority, profile_id = int(position["priority"]), int(position["id"])
            as_of = parse_datetime(position["as_of"])
            if as_of is None:
                raise ValueError("Invalid queue snapshot")
        except (signing.BadSignature, KeyError, TypeError, ValueError):
            raise PhotoReviewError(_("Invalid request payload"), 400) from None
    native_luxid, oidc_luxid = CrushProfile.luxid_account_querysets(OuterRef("user_id"))
    attended = EventRegistration.objects.filter(
        user_id=OuterRef("user_id"),
        status="attended",
    ).filter(
        Q(checkin_granted_coach__isnull=False)
        | ~Q(checkin_attested_photo_key="")
        | Q(event__coaches=OuterRef("assigned_coach_id"))
    )
    annotated_qs = base_qs.annotate(
        review_has_native_luxid=Exists(native_luxid),
        review_has_oidc_luxid=Exists(oidc_luxid),
        review_has_attendance=Exists(attended),
        priority=Case(
            When(
                user__crush_connect_membership__onboarded_at__lte=as_of,
                then=Value(3),
            ),
            When(
                user__crush_connect_membership__created_at__lte=as_of,
                then=Value(2),
            ),
            default=Value(1),
            output_field=models.IntegerField(),
        ),
    )
    # Keyset on keys that hold still for the pass: the pass-start priority
    # snapshot and the id. An auto_now updated_at would move a card behind the
    # cursor whenever its member edits their profile mid-session.
    annotated_qs = annotated_qs.order_by("-priority", "-id")

    if position is not None:
        annotated_qs = annotated_qs.filter(
            Q(priority__lt=priority) | Q(priority=priority, pk__lt=profile_id)
        )
    profiles = list(annotated_qs[:limit])
    cards = []
    now = timezone.now()
    for p in profiles:
        mem = getattr(p.user, "crush_connect_membership", None)
        photo_1_url = (
            reverse(
                "crush_lu:serve_profile_photo",
                kwargs={"user_id": p.user_id, "photo_field": "photo_1"},
            )
            if p.photo_1
            else ""
        )
        photo_2_url = (
            reverse(
                "crush_lu:serve_profile_photo",
                kwargs={"user_id": p.user_id, "photo_field": "photo_2"},
            )
            if p.photo_2
            else ""
        )
        photo_3_url = (
            reverse(
                "crush_lu:serve_profile_photo",
                kwargs={"user_id": p.user_id, "photo_field": "photo_3"},
            )
            if p.photo_3
            else ""
        )

        photos = []
        if photo_1_url:
            photos.append({"field": "photo_1", "url": photo_1_url})
        if photo_2_url:
            photos.append({"field": "photo_2", "url": photo_2_url})
        if photo_3_url:
            photos.append({"field": "photo_3", "url": photo_3_url})

        story_text = ""
        story_prompt = ""
        relationship_goal = ""
        lifestyle_tags = []
        work_field = ""
        education_level = ""
        height = ""
        if mem:
            story_prompt = mem.story_prompt.text if mem.story_prompt else ""
            story_text = mem.story_answer or ""
            relationship_goal = mem.get_relationship_goal_display() or ""
            if mem.lifestyle_energy:
                lifestyle_tags.append(mem.get_lifestyle_energy_display())
            if mem.lifestyle_social:
                lifestyle_tags.append(mem.get_lifestyle_social_display())
            if mem.lifestyle_pace:
                lifestyle_tags.append(mem.get_lifestyle_pace_display())
            work_field = mem.get_work_field_display() or mem.work_field or ""
            education_level = (
                mem.get_education_level_display() or mem.education_level or ""
            )
            if mem.height_cm:
                height = f"{mem.height_cm} cm"

        phone_info = _format_phone_info(p.phone_number)

        raw_langs = []
        if p.preferred_language:
            raw_langs.append(p.preferred_language)
        if p.event_languages and isinstance(p.event_languages, list):
            raw_langs.extend(p.event_languages)
        if mem and mem.languages and isinstance(mem.languages, list):
            raw_langs.extend(mem.languages)

        seen_langs = set()
        formatted_languages = []
        for code in raw_langs:
            if isinstance(code, str) and code and code not in seen_langs:
                seen_langs.add(code)
                formatted_languages.append(LANGUAGE_NAMES.get(code, code.upper()))

        interests = [i.label for i in p.interests_new.all()]

        is_luxid_verified = bool(p.review_has_native_luxid or p.review_has_oidc_luxid)
        has_attended_event = bool(
            p.verification_status == "verified"
            and (
                p.verification_method in ("coach_event", "premium_coach")
                or p.review_has_attendance
            )
        )

        risk_flags = []
        if p.phone_number:
            if not phone_info["is_local"]:
                risk_flags.append(
                    _("Non-local phone (%(country)s)")
                    % {"country": phone_info["country"]}
                )
            if not p.phone_verified:
                risk_flags.append(_("Phone unverified"))
        else:
            risk_flags.append(_("No phone number"))

        if not (p.bio or "").strip() and not (story_text or "").strip():
            risk_flags.append(_("Empty bio & prompt"))

        if p.created_at and (now - p.created_at).total_seconds() < 86400 * 2:
            risk_flags.append(_("New account (< 48h)"))

        trust_signals = []
        if is_luxid_verified:
            trust_signals.append(_("LuxID Verified"))
        if has_attended_event:
            trust_signals.append(_("Attended In-Person Event"))
        if p.phone_verified and phone_info["number"]:
            trust_signals.append(
                _("Phone verified (%(country)s)") % {"country": phone_info["country"]}
            )
        if mem and mem.is_onboarded:
            trust_signals.append(_("Connect Onboarded"))

        cards.append(
            {
                "id": p.id,
                "queue_cursor": signing.dumps(
                    {"priority": p.priority, "id": p.pk, "as_of": as_of.isoformat()},
                    salt="coach-photo-queue",
                ),
                "user_id": p.user.id,
                "display_name": p.display_name or p.user.first_name or p.user.username,
                "age": p.age_display or "",
                "date_of_birth": (
                    p.date_of_birth.strftime("%Y-%m-%d") if p.date_of_birth else ""
                ),
                "gender": p.get_gender_display() or "",
                "location": p.city or p.location or "",
                "phone_number": phone_info["number"],
                "phone_country": phone_info["country"],
                "phone_badge": (
                    (
                        phone_info["country"]
                        + " · "
                        + (_("SMS verified") if p.phone_verified else _("Unverified"))
                    )
                    if phone_info["number"]
                    else _("No phone")
                ),
                "dob_label": (
                    _("Born %(date)s")
                    % {"date": date_format(p.date_of_birth, "DATE_FORMAT")}
                    if p.date_of_birth
                    else ""
                ),
                "member_since_label": (
                    _("Member since %(date)s")
                    % {"date": date_format(p.created_at, "M Y")}
                    if p.created_at
                    else ""
                ),
                "is_phone_local": phone_info["is_local"],
                "phone_verified": bool(p.phone_verified),
                "bio": p.bio or "",
                "work": work_field,
                "education": education_level,
                "height": height,
                "languages": formatted_languages,
                "interests": interests,
                "photos": photos,
                "photo_count": len(photos),
                "photo_key": getattr(p.photo_1, "name", "") or "",
                "photo_review_status": p.photo_review_status,
                "story_prompt": story_prompt or _("Prompt"),
                "story_text": story_text,
                "relationship_goal": relationship_goal,
                "lifestyle_tags": lifestyle_tags,
                "risk_flags": risk_flags,
                "trust_signals": trust_signals,
                "is_luxid_verified": is_luxid_verified,
                "has_attended_event": has_attended_event,
                "is_onboarded": bool(mem and mem.is_onboarded),
                "member_since": p.created_at.strftime("%b %Y") if p.created_at else "",
            }
        )

    total_waiting = base_qs.count()

    return cards, total_waiting


class PhotoReviewError(ValueError):
    """A safe, user-facing refusal of a review or undo."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


def _retract_revision_safely(log_id, request):
    """Replace only this review's notice and correct external messages once."""
    try:
        log = ProfilePhotoReviewLog.objects.select_related("profile__user").get(
            pk=log_id
        )
        if (
            log.revision_notification_state != "retract_pending"
            or log.undone_at is None
        ):
            return
        user = log.profile.user
        payload = NotificationService._render_inapp_payload(
            user,
            NotificationType.PHOTO_REVIEW_RETRACTED,
            {"photo_review_log_id": log.pk},
            request,
        )
        revision_notice = Notification.objects.filter(
            user=user, dedupe_key=f"photo-review:{log.pk}:revision"
        )
        # Converted first, so the bell never shows a live request even if the
        # correction below fails before its claim.
        revision_notice.update(
            title=payload["title"],
            body=payload["body"],
            metadata={"photo_review_log_id": log.pk, "withdrawn": True},
        )
        result = NotificationService.notify(
            user=user,
            notification_type=NotificationType.PHOTO_REVIEW_RETRACTED,
            context={"photo_review_log_id": log.pk},
            request=request,
            dedupe_key=f"photo-review:{log.pk}:retracted",
        )
        if result.inapp_created:
            # The correction wrote its own bell row: keep exactly one entry.
            revision_notice.delete()
        ProfilePhotoReviewLog.objects.filter(
            pk=log.pk, revision_notification_state="retract_pending"
        ).update(revision_notification_state="retracted")
    except Exception:
        logger.exception(
            "Failed to correct photo revision notification for review %s", log_id
        )


def _notify_revision_safely(profile, reason, notes, request, log_id):
    try:
        _send_revision_and_reconcile(profile, reason, notes, request, log_id)
    except Exception:
        # A post-commit delivery/claim failure must not turn a saved decision
        # into a failed API response or encourage a duplicate moderation action.
        logger.exception("Photo revision delivery failed for review %s", log_id)


def _send_revision_and_reconcile(profile, reason, notes, request, log_id):
    # The CAS coordinates cross-worker Undo with a sender already in flight.
    # No network work happens inside the moderation transaction or row lock.
    if not ProfilePhotoReviewLog.objects.filter(
        pk=log_id,
        undone_at__isnull=True,
        revision_notification_state="",
        profile__photo_1=models.F("photo_key"),
        profile__photo_review_key=models.F("photo_key"),
        profile__photo_review_status="needs_revision",
    ).update(revision_notification_state="sending"):
        return
    delivered = False
    try:
        with override(profile.preferred_language or "en"):
            feedback = {
                "inappropriate": _(
                    "Please replace the inappropriate image with a suitable photo of yourself."
                ),
                "group_photo": _(
                    "Please upload a photo showing only you, so members can identify you."
                ),
                "unclear_face": _(
                    "Please upload a clearer profile photo where your face is visible."
                ),
                "other": _(
                    "Please replace your profile photo with a clear, suitable photo of yourself."
                ),
            }
            notify_photo_revision(
                user=profile.user,
                feedback=notes or feedback.get(reason, feedback["other"]),
                request=request,
                photo_review_log_id=log_id,
            )
        delivered = True
    except Exception:
        logger.exception(
            "Failed to send photo revision notification to user %s", profile.user_id
        )
    finally:
        # notify() raises only before its dedupe claim, so a raise means the
        # member received nothing: never "withdraw" a request never delivered.
        settled = ProfilePhotoReviewLog.objects.filter(
            pk=log_id, revision_notification_state="sending"
        ).update(revision_notification_state="sent" if delivered else "failed")
        if not settled:
            # Undo moved the state to retract_pending while this was in flight.
            if delivered:
                _retract_revision_safely(log_id, request)
            else:
                ProfilePhotoReviewLog.objects.filter(
                    pk=log_id, revision_notification_state="retract_pending"
                ).update(revision_notification_state="cancelled")


def submit_photo_review(
    coach: CrushCoach,
    profile_id: int,
    decision: str,
    reason: str = "",
    notes: str = "",
    request=None,
    *,
    photo_key: str,
    photo_field: str = "photo_1",
):
    """Claim one pending, exact-photo decision; notify only after commit."""
    # "skipped" stays a valid stored choice for old rows, but the deck skips
    # client-side: a skip changes no state, so it must not pad the audit log.
    if (
        decision not in dict(ProfilePhotoReviewLog.DECISION_CHOICES)
        or decision == "skipped"
    ):
        raise PhotoReviewError(_("Invalid photo review decision."), 400)
    if reason and reason not in dict(ProfilePhotoReviewLog.REASON_CHOICES):
        raise PhotoReviewError(_("Invalid photo review reason."), 400)
    if photo_field != "photo_1":
        raise PhotoReviewError(_("Only the primary photo can be reviewed."), 400)
    allowed_reasons = {
        "approved": {"", "clear_authentic"},
        "flagged_fake": {"fake_profile"},
        "needs_revision": {"inappropriate", "unclear_face", "group_photo", "other"},
    }
    if reason not in allowed_reasons[decision]:
        raise PhotoReviewError(_("Invalid photo review reason."), 400)
    if len(notes) > 255 or len(photo_key) > 255 or not photo_key:
        raise PhotoReviewError(_("Invalid photo review payload."), 400)

    with transaction.atomic():
        # Lock only the profile, never joined nullable membership rows or the user.
        profile = (
            CrushProfile.objects.select_for_update(of=("self",))
            .select_related("user")
            .filter(pk=profile_id)
            .first()
        )
        if profile is None:
            raise PhotoReviewError(_("Profile not available for review."), 404)
        consent = UserDataConsent.objects.filter(user_id=profile.user_id).first()
        if (
            profile.user_id == coach.user_id
            or not coach.is_active
            or not coach.user.is_active
            or not profile.is_active
            or not profile.user.is_active
            or profile.verification_status == "rejected"
            or not consent
            or not consent.crushlu_consent_given
            or consent.crushlu_banned
        ):
            raise PhotoReviewError(_("Profile not available for review."), 403)
        current_key = profile.photo_1.name or ""
        if current_key != photo_key:
            raise PhotoReviewError(
                _("This member's photo changed. Reload and check the new one.")
            )
        if profile.photo_review_status != "pending":
            raise PhotoReviewError(
                _("This photo has already been reviewed. Reload the queue.")
            )
        now = timezone.now()
        # The conditional UPDATE also protects the file key at the write boundary.
        claimed = CrushProfile.objects.filter(
            pk=profile.pk,
            photo_1=photo_key,
            photo_review_status="pending",
        ).update(
            photo_review_status=decision,
            photo_review_key=photo_key,
            photo_reviewed_at=now,
            photo_reviewed_by=coach,
            photo_review_notes=notes,
        )
        if not claimed:
            raise PhotoReviewError(
                _("This member's photo changed. Reload and check the new one.")
            )
        profile.refresh_from_db()
        log = ProfilePhotoReviewLog.objects.create(
            profile=profile,
            coach=coach,
            photo_key=photo_key,
            decision=decision,
            reason=reason,
            notes=notes,
            previous_status="pending",
            decision_at=now,
        )
        if decision == "flagged_fake":
            membership, log.membership_created = (
                CrushConnectMembership.objects.get_or_create(user=profile.user)
            )
            membership = CrushConnectMembership.objects.select_for_update().get(
                pk=membership.pk
            )
            if not membership.excluded_by_coach:
                membership.excluded_by_coach = True
                membership.excluded_at = now
                membership.excluded_by = coach
                membership.exclusion_reason = (
                    f"Photo review #{log.pk}: {reason}. {notes}"
                )
                membership.save(
                    update_fields=[
                        "excluded_by_coach",
                        "excluded_at",
                        "excluded_by",
                        "exclusion_reason",
                    ]
                )
                log.exclusion_created = True
            # Preserve the exact rows and withdrawal timestamp owned by this
            # decision so Undo can restore them without reviving later actions.
            picks = list(
                ConnectCoachPick.objects.select_for_update()
                .filter(
                    Q(member=profile.user) | Q(candidate=profile.user),
                    status__in=["proposed", "accepted"],
                )
                .order_by("pk")
            )
            log.withdrawn_picks = [
                {
                    "id": pick.pk,
                    "status": pick.status,
                    "responded_at": (
                        pick.responded_at.isoformat() if pick.responded_at else None
                    ),
                }
                for pick in picks
            ]
            ConnectCoachPick.objects.filter(pk__in=[pick.pk for pick in picks]).update(
                status="withdrawn",
                responded_at=now,
            )
            log.report = UserReport.objects.create(
                reporter=coach.user,
                reported_user=profile.user,
                reason="fake_profile",
                details=f"Coach photo review #{log.pk}: {reason}. {notes}",
                source="profile",
                source_id=profile.pk,
                status="actioned",
                handled_by=coach.user,
                handled_at=now,
                resolution_notes=f"Photo review #{log.pk}: excluded from Connect.",
            )
            log.save(
                update_fields=[
                    "exclusion_created",
                    "membership_created",
                    "report",
                    "withdrawn_picks",
                ]
            )
        elif decision == "needs_revision":
            transaction.on_commit(
                lambda: _notify_revision_safely(profile, reason, notes, request, log.pk)
            )
        return {
            "success": True,
            "decision": decision,
            "profile_id": profile.pk,
            "log_id": log.pk,
            "new_status": profile.photo_review_status,
        }


def undo_last_photo_review(coach: CrushCoach, *, log_id=None, request=None):
    """Undo only the still-current decision, preserving the immutable audit record."""
    cutoff = timezone.now() - timedelta(minutes=15)
    logs = ProfilePhotoReviewLog.objects.filter(
        coach=coach,
        created_at__gte=cutoff,
        undone_at__isnull=True,
    ).exclude(decision="skipped")
    if log_id is not None:
        logs = logs.filter(pk=log_id)
    log = logs.order_by("-pk").first()
    if log is None:
        raise PhotoReviewError(_("No recent review to undo."))

    with transaction.atomic():
        profile = (
            CrushProfile.objects.select_for_update(of=("self",))
            .select_related("user")
            .get(pk=log.profile_id)
        )
        log = ProfilePhotoReviewLog.objects.select_for_update().get(pk=log.pk)
        latest = (
            profile.photo_review_logs.filter(undone_at__isnull=True)
            .exclude(decision="skipped")
            .order_by("-pk")
            .first()
        )
        if (
            log.undone_at is not None
            or latest is None
            or latest.pk != log.pk
            or profile.photo_1.name != log.photo_key
            or profile.photo_review_key != log.photo_key
            or profile.photo_review_status != log.decision
            or profile.photo_reviewed_by_id != coach.pk
            or profile.photo_reviewed_at != log.decision_at
            or log.previous_status != "pending"
        ):
            raise PhotoReviewError(
                _("This review is no longer current and cannot be undone.")
            )
        # The flag's report is a staff work item: once staff touched it (status,
        # handler, notes), Undo would overwrite their decision and lift an
        # exclusion they may just have confirmed.
        report_state = {}
        if log.report_id:
            report_state = {
                "status": "actioned",
                "handled_by_id": coach.user_id,
                "handled_at": log.decision_at,
                "resolution_notes": f"Photo review #{log.pk}: excluded from Connect.",
            }
            report = (
                UserReport.objects.select_for_update().filter(pk=log.report_id).first()
            )
            if report is not None and any(
                getattr(report, field) != value for field, value in report_state.items()
            ):
                raise PhotoReviewError(
                    _("This review is no longer current and cannot be undone.")
                )
        if log.exclusion_created:
            membership = (
                CrushConnectMembership.objects.select_for_update()
                .filter(user=profile.user)
                .first()
            )
            if (
                not membership
                or not membership.excluded_by_coach
                or membership.excluded_by_id != coach.pk
                or membership.excluded_at != log.decision_at
                or not membership.exclusion_reason.startswith(
                    f"Photo review #{log.pk}:"
                )
            ):
                raise PhotoReviewError(
                    _("This review is no longer current and cannot be undone.")
                )
            if (
                log.membership_created
                and membership.onboarding_started_at is None
                and membership.onboarded_at is None
            ):
                # The flag created this row only to carry the exclusion;
                # never leave a Connect membership the member did not start.
                membership.delete()
            else:
                membership.excluded_by_coach = False
                membership.excluded_at = None
                membership.excluded_by = None
                membership.exclusion_reason = ""
                membership.save(
                    update_fields=[
                        "excluded_by_coach",
                        "excluded_at",
                        "excluded_by",
                        "exclusion_reason",
                    ]
                )
        CrushProfile.objects.filter(pk=profile.pk, photo_1=log.photo_key).update(
            photo_review_status="pending",
            photo_review_key="",
            photo_reviewed_at=None,
            photo_reviewed_by=None,
            photo_review_notes="",
        )
        restored_picks = skipped_picks = 0
        for snapshot in log.withdrawn_picks:
            pick = (
                ConnectCoachPick.objects.select_for_update(of=("self",))
                .select_related(
                    "member__crushprofile",
                    "member__crush_connect_membership",
                    "candidate__crushprofile",
                    "candidate__crush_connect_membership",
                    "coach",
                )
                .filter(
                    pk=snapshot["id"], status="withdrawn", responded_at=log.decision_at
                )
                .first()
            )
            if not pick:
                skipped_picks += 1
                continue
            consenting = (
                UserDataConsent.objects.filter(
                    user_id__in=[pick.member_id, pick.candidate_id],
                    crushlu_consent_given=True,
                    crushlu_banned=False,
                ).count()
                == 2
            )
            if (
                not consenting
                or not pick.coach.is_active
                or not is_catalogue_eligible(pick.member)
                or pick.member.crushprofile.assigned_coach_id != pick.coach_id
                or not is_catalogue_eligible(pick.candidate)
                or is_blocked_pair(pick.member, pick.candidate)
                or ConnectPairExclusion.are_excluded(pick.member, pick.candidate)
                # Only a pick made after this decision supersedes the restore;
                # one the member already held alongside it is pre-flag state.
                or ConnectCoachPick.objects.filter(
                    member_id=pick.member_id,
                    status__in=["proposed", "accepted"],
                    created_at__gte=log.decision_at,
                ).exists()
                # A member never holds two open proposals at once.
                or (
                    snapshot["status"] == "proposed"
                    and ConnectCoachPick.objects.filter(
                        member_id=pick.member_id, status="proposed"
                    ).exists()
                )
            ):
                skipped_picks += 1
                continue
            restored_picks += 1
            ConnectCoachPick.objects.filter(
                pk=pick.pk, status="withdrawn", responded_at=log.decision_at
            ).update(
                status=snapshot["status"],
                responded_at=(
                    parse_datetime(snapshot["responded_at"])
                    if snapshot["responded_at"]
                    else None
                ),
            )
        log.undone_at = timezone.now()
        if log.decision == "needs_revision":
            # "", "failed": nothing reached the member, so nothing to retract.
            state = log.revision_notification_state
            log.revision_notification_state = (
                "retract_pending" if state in ("sending", "sent") else "cancelled"
            )
            if state == "sent":
                transaction.on_commit(lambda: _retract_revision_safely(log.pk, request))
        log.save(update_fields=["undone_at", "revision_notification_state"])
        if log.report_id:
            UserReport.objects.filter(pk=log.report_id, **report_state).update(
                status="dismissed",
                handled_by=coach.user,
                handled_at=log.undone_at,
                resolution_notes=f"Photo review #{log.pk} undone by the reviewing coach.",
            )
        return {
            "success": True,
            "undone_decision": log.decision,
            "profile_id": profile.pk,
            "restored_status": "pending",
            "restored_picks": restored_picks,
            "skipped_picks": skipped_picks,
        }
