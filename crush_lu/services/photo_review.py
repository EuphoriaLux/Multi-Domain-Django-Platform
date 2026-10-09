"""
Coach Photo Review service for Crush.lu & Crush Connect.

Manages the photo-moderation queue: one card per member with every photo
waiting for review, claimed by one coach at a time, decided in one submit
(Approve, Flag Fake, Request Revision) and notified to the member once.
"""

import logging
import uuid
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
    ProfilePhotoReviewState,
    ProfilePhotoReviewLog,
    UserReport,
    UserDataConsent,
    EventRegistration,
    Notification,
    PhotoReviewClaim,
    PublishedProfilePhoto,
)
from crush_lu.models.crush_connect_cycle import ConnectPairExclusion
from crush_lu.notification_service import (
    notify_photo_revision,
    NotificationService,
    NotificationType,
)
from crush_lu.services.blocking import is_blocked_pair
from crush_lu.photo_review_reasons import (
    PHOTO_REVISION_REASONS,
)
from crush_lu.services.crush_connect import is_catalogue_eligible
from crush_lu.services.photo_publication import (
    get_published_key,
    publish_photo,
    restore_publication,
    unpublish_photo,
)

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


PHOTO_REVIEW_FIELDS = ("photo_1", "photo_2", "photo_3")
DECIDED_STATUSES = ("approved", "needs_revision", "flagged_fake")

# A coach's deck holds each member card for this long. Other coaches' queues
# skip the member meanwhile; an expired claim simply lets the card be dealt
# again, so nothing ever has to release one for correctness.
CLAIM_TTL = timedelta(minutes=10)
QUEUE_PAGE_SIZE = 10


def _photo_slot_labels():
    """The names the member's photo editor uses for each slot."""
    return {
        "photo_1": _("Main photo"),
        "photo_2": _("Photo 2"),
        "photo_3": _("Photo 3"),
    }


def claim_members_for_review(coach: CrushCoach, profile_ids, now=None):
    """Hold member cards for ``coach``; return the ids it now holds.

    A member another coach holds (unexpired) is left out. A constant number
    of queries for the whole page: renew own or expired claims, insert the
    missing ones, then read back which this coach actually holds — the
    read-back is what settles a race between two coaches dealing at once.
    """
    profile_ids = list(profile_ids)
    if not profile_ids:
        return set()
    now = now or timezone.now()
    until = now + CLAIM_TTL
    # On PostgreSQL the UPDATE re-checks its WHERE after waiting on a
    # concurrent claim, so two coaches can never both take an expired row.
    PhotoReviewClaim.objects.filter(profile_id__in=profile_ids).filter(
        Q(coach=coach) | Q(expires_at__lte=now)
    ).update(coach=coach, expires_at=until)
    PhotoReviewClaim.objects.bulk_create(
        [
            PhotoReviewClaim(profile_id=pk, coach=coach, expires_at=until)
            for pk in profile_ids
        ],
        ignore_conflicts=True,
    )
    return set(
        PhotoReviewClaim.objects.filter(
            profile_id__in=profile_ids, coach=coach, expires_at=until
        ).values_list("profile_id", flat=True)
    )


def _reviewable_profiles(coach, scope):
    if scope not in ("all", "connect"):
        raise PhotoReviewError(_("Invalid review queue."), 400)
    base_qs = (
        CrushProfile.objects.filter(
            is_active=True,
            user__is_active=True,
            user_id__in=UserDataConsent.objects.filter(
                crushlu_consent_given=True, crushlu_banned=False
            ).values("user_id"),
        )
        .exclude(Q(photo_review_status="flagged_fake"))
        .exclude(verification_status="rejected")
        .exclude(user=coach.user)
    )
    if scope == "connect":
        base_qs = base_qs.filter(
            user__crush_connect_membership__isnull=False,
            user__crush_connect_membership__excluded_by_coach=False,
        )
    # A member waits while any slot holds a file no coach has decided yet.
    annotations = {}
    waiting = Q()
    for photo_field in PHOTO_REVIEW_FIELDS:
        annotations[f"_decided_{photo_field}"] = Exists(
            ProfilePhotoReviewState.objects.filter(
                profile_id=OuterRef("pk"),
                photo_field=photo_field,
                photo_key=OuterRef(photo_field),
                status__in=DECIDED_STATUSES,
            )
        )
        slot_waiting = Q(**{f"{photo_field}__gt": "", f"_decided_{photo_field}": False})
        if photo_field == "photo_1":
            slot_waiting &= ~Q(photo_review_status__in=("approved", "needs_revision"))
        waiting |= slot_waiting
    return base_qs.annotate(**annotations).filter(waiting)


def get_photo_review_queue(
    coach: CrushCoach, limit: int = QUEUE_PAGE_SIZE, *, cursor="", scope="all"
):
    """Deal member cards — one per member, every waiting photo on it.

    Connect members come first. Each dealt card is claimed for ``coach``
    (``CLAIM_TTL``) and skipped by other coaches' queues meanwhile, so two
    coaches never review the same member at once. ``connect`` narrows the
    queue to users with a Crush Connect membership; both scopes share review
    state and claims.
    """
    # Priority score, snapshotted at the instant the review pass started (the
    # first page; every cursor carries it). A membership created or onboarded
    # mid-pass would otherwise raise a card's priority and move it behind the
    # cursor, skipping it for the rest of the pass.
    # 3: Connect onboarded
    # 2: Connect membership exists
    # 1: General profile
    now = timezone.now()
    as_of = now
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
    claimed_elsewhere = PhotoReviewClaim.objects.filter(
        profile_id=OuterRef("pk"), expires_at__gt=now
    ).exclude(coach=coach)
    waiting_qs = (
        _reviewable_profiles(coach, scope)
        .annotate(_claimed_elsewhere=Exists(claimed_elsewhere))
        .filter(_claimed_elsewhere=False)
    )
    total_waiting = waiting_qs.count()
    queue_qs = waiting_qs.annotate(
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
    if position is not None:
        queue_qs = queue_qs.filter(
            Q(priority__lt=priority) | Q(priority=priority, pk__lt=profile_id)
        )
    queue_qs = (
        queue_qs.select_related(
            "user",
            "user__crush_connect_membership",
            "user__crush_connect_membership__story_prompt",
        )
        .prefetch_related("interests_new", "photo_review_states", "published_photos")
        .order_by("-priority", "-id")
    )
    # Over-fetch: a member another coach claims in the meantime is skipped,
    # and the next candidates are claimed in their place. Only dealt cards
    # are claimed, so no member is held back that this coach will not see.
    candidates = list(queue_qs[: limit * 2])
    cards = []
    position = 0
    while len(cards) < limit and position < len(candidates):
        window = candidates[position : position + limit - len(cards)]
        position += len(window)
        held = claim_members_for_review(coach, [p.pk for p in window], now)
        for profile in window:
            if profile.pk in held:
                card = _build_card(profile, as_of=as_of, scope=scope, now=now)
                if card["pending_fields"]:
                    cards.append(card)
    return cards, total_waiting


def _build_card(p, *, as_of, scope, now):
    mem = getattr(p.user, "crush_connect_membership", None)
    labels = _photo_slot_labels()
    status_labels = {
        "pending": _("Waiting for review"),
        "approved": _("Approved"),
        "needs_revision": _("Replacement requested"),
        "flagged_fake": _("Flagged"),
    }
    photos = []
    for photo_field in PHOTO_REVIEW_FIELDS:
        photo = getattr(p, photo_field)
        if not photo:
            continue
        status = p.get_photo_field_review_status(photo_field)
        published = get_published_key(p, photo_field)
        photos.append(
            {
                "field": photo_field,
                "label": labels[photo_field],
                "url": reverse(
                    "crush_lu:serve_profile_photo",
                    kwargs={"user_id": p.user_id, "photo_field": photo_field},
                ),
                "photo_key": photo.name,
                "status": status,
                "status_label": str(status_labels.get(status, "")),
                "is_pending": status == "pending",
                # Members still see an earlier approved photo in this slot.
                "is_replacement": bool(published and published != photo.name),
            }
        )

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
        education_level = mem.get_education_level_display() or mem.education_level or ""
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
                _("Non-local phone (%(country)s)") % {"country": phone_info["country"]}
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

    return {
        "id": p.id,
        "queue_cursor": signing.dumps(
            {"priority": p.priority, "id": p.pk, "as_of": as_of.isoformat()},
            salt="coach-photo-queue",
        ),
        "scope": scope,
        "pending_fields": [photo["field"] for photo in photos if photo["is_pending"]],
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
            _("Born %(date)s") % {"date": date_format(p.date_of_birth, "DATE_FORMAT")}
            if p.date_of_birth
            else ""
        ),
        "member_since_label": (
            _("Member since %(date)s") % {"date": date_format(p.created_at, "M Y")}
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


class PhotoReviewError(ValueError):
    """A safe, user-facing refusal of a review or undo."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.message = message
        self.status = status


def _revision_group(lead):
    """The revision decisions one member notice covers (lead = lowest pk)."""
    logs = ProfilePhotoReviewLog.objects.filter(decision="needs_revision")
    if lead.batch_id:
        return logs.filter(batch_id=lead.batch_id)
    return logs.filter(pk=lead.pk)


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
        _revision_group(log).filter(
            revision_notification_state="retract_pending"
        ).update(revision_notification_state="retracted")
    except Exception:
        logger.exception(
            "Failed to correct photo revision notification for review %s", log_id
        )


def _notify_revision_safely(profile, log_ids, request):
    try:
        _send_revision_and_reconcile(profile, log_ids, request)
    except Exception:
        # A post-commit delivery/claim failure must not turn a saved decision
        # into a failed API response or encourage a duplicate moderation action.
        logger.exception("Photo revision delivery failed for reviews %s", log_ids)


def _send_revision_and_reconcile(profile, log_ids, request):
    # The CAS coordinates cross-worker Undo with a sender already in flight.
    # No network work happens inside the moderation transaction or row lock.
    # One member card is one notice: every still-current request of the
    # batch is claimed together and listed in it.
    current = (
        ProfilePhotoReviewLog.objects.filter(
            pk__in=log_ids,
            undone_at__isnull=True,
            revision_notification_state="",
            profile__photo_review_states__photo_field=models.F("photo_field"),
            profile__photo_review_states__photo_key=models.F("photo_key"),
            profile__photo_review_states__status="needs_revision",
        )
        .annotate(
            current_photo_key=models.Case(
                models.When(photo_field="photo_1", then=models.F("profile__photo_1")),
                models.When(photo_field="photo_2", then=models.F("profile__photo_2")),
                models.When(photo_field="photo_3", then=models.F("profile__photo_3")),
                output_field=models.CharField(max_length=255),
            )
        )
        .filter(photo_key=models.F("current_photo_key"))
    )
    if not current.update(revision_notification_state="sending"):
        return
    claimed = list(
        ProfilePhotoReviewLog.objects.filter(
            pk__in=log_ids, revision_notification_state="sending"
        )
        .order_by("photo_field")
        .values("pk", "photo_field", "reason")
    )
    claimed_ids = [row["pk"] for row in claimed]
    lead_id = min(claimed_ids)
    delivered = False
    try:
        with override(profile.preferred_language or "en"):
            notify_photo_revision(
                user=profile.user,
                photos=[
                    {"photo_field": row["photo_field"], "reason": row["reason"]}
                    for row in claimed
                ],
                request=request,
                photo_review_log_id=lead_id,
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
            pk__in=claimed_ids, revision_notification_state="sending"
        ).update(revision_notification_state="sent" if delivered else "failed")
        if settled < len(claimed_ids):
            # Undo moved the batch to retract_pending while this was in flight.
            if delivered:
                _retract_revision_safely(lead_id, request)
            else:
                ProfilePhotoReviewLog.objects.filter(
                    pk__in=claimed_ids, revision_notification_state="retract_pending"
                ).update(revision_notification_state="cancelled")


def _validate_decision(decision, reason, notes, photo_key, photo_field):
    # "skipped" stays a valid stored choice for old rows, but the deck skips
    # client-side: a skip changes no state, so it must not pad the audit log.
    if (
        decision not in dict(ProfilePhotoReviewLog.DECISION_CHOICES)
        or decision == "skipped"
    ):
        raise PhotoReviewError(_("Invalid photo review decision."), 400)
    if reason and reason not in dict(ProfilePhotoReviewLog.REASON_CHOICES):
        raise PhotoReviewError(_("Invalid photo review reason."), 400)
    if photo_field not in PHOTO_REVIEW_FIELDS:
        raise PhotoReviewError(_("Invalid photo field."), 400)
    allowed_reasons = {
        "approved": {"", "clear_authentic"},
        "flagged_fake": {"fake_profile"},
        "needs_revision": set(PHOTO_REVISION_REASONS),
    }
    if reason not in allowed_reasons[decision]:
        raise PhotoReviewError(_("Invalid photo review reason."), 400)
    if len(notes) > 255 or len(photo_key) > 255 or not photo_key:
        raise PhotoReviewError(_("Invalid photo review payload."), 400)


def _normalize_decisions(decisions):
    if not isinstance(decisions, list) or not 1 <= len(decisions) <= len(
        PHOTO_REVIEW_FIELDS
    ):
        raise PhotoReviewError(_("Invalid photo review payload."), 400)
    normalized = []
    for item in decisions:
        if not isinstance(item, dict):
            raise PhotoReviewError(_("Invalid photo review payload."), 400)
        values = {}
        for name, default in (
            ("decision", ""),
            ("reason", ""),
            ("notes", ""),
            ("photo_key", ""),
            ("photo_field", "photo_1"),
        ):
            value = item.get(name, default)
            if not isinstance(value, str):
                raise PhotoReviewError(_("Invalid photo review payload."), 400)
            values[name] = value
        values["decision"] = values["decision"].strip().lower()
        values["reason"] = values["reason"].strip()
        values["notes"] = values["notes"].strip()
        _validate_decision(
            values["decision"],
            values["reason"],
            values["notes"],
            values["photo_key"],
            values["photo_field"],
        )
        normalized.append(values)
    if len({item["photo_field"] for item in normalized}) != len(normalized):
        raise PhotoReviewError(_("Invalid photo review payload."), 400)
    if len(normalized) > 1 and any(
        item["decision"] == "flagged_fake" for item in normalized
    ):
        # A fake flag judges the member, not one photo: it is never combined.
        raise PhotoReviewError(
            _("A fake-profile flag cannot be combined with other decisions."), 400
        )
    return normalized


def _apply_decision(coach, profile, item, *, batch_id, now):
    """Record one photo's decision on a locked, reviewable profile."""
    photo_field = item["photo_field"]
    photo_key = item["photo_key"]
    decision = item["decision"]
    reason = item["reason"]
    notes = item["notes"]
    current_key = getattr(profile, photo_field).name or ""
    if current_key != photo_key:
        raise PhotoReviewError(
            _("This member's photo changed. Reload and check the new one.")
        )
    if profile.photo_review_status == "flagged_fake":
        raise PhotoReviewError(_("Profile not available for review."), 403)
    review_state = (
        ProfilePhotoReviewState.objects.select_for_update()
        .filter(profile=profile, photo_field=photo_field)
        .first()
    )
    current_status = (
        review_state.status
        if review_state is not None and review_state.photo_key == current_key
        else "pending"
    )
    if (
        review_state is None
        and photo_field == "photo_1"
        and profile.photo_review_key == current_key
        and profile.photo_review_status in ("approved", "needs_revision")
    ):
        current_status = profile.photo_review_status
    if current_status != "pending":
        raise PhotoReviewError(
            _("This photo has already been reviewed. Reload the queue.")
        )
    state, _created = ProfilePhotoReviewState.objects.update_or_create(
        profile=profile,
        photo_field=photo_field,
        defaults={
            "photo_key": photo_key,
            "status": decision,
            "reviewed_at": now,
            "reviewed_by": coach,
            "notes": notes,
        },
    )
    if photo_field == "photo_1" or decision == "flagged_fake":
        legacy_defaults = {
            "photo_review_status": decision,
            "photo_review_key": photo_key if photo_field == "photo_1" else "",
            "photo_reviewed_at": now,
            "photo_reviewed_by": coach,
            "photo_review_notes": notes,
        }
        CrushProfile.objects.filter(pk=profile.pk).update(**legacy_defaults)
        for field, value in legacy_defaults.items():
            setattr(profile, field, value)
    # What other members see for this slot (services/photo_publication.py):
    # an approval publishes the file, a revision request on the published
    # file withdraws it, and a fake flag hides every slot on its own.
    if decision == "approved":
        previous_published = publish_photo(profile, photo_field, photo_key)
    else:
        previous_published = (
            PublishedProfilePhoto.objects.filter(
                profile_id=profile.pk, photo_field=photo_field
            )
            .values_list("photo_key", flat=True)
            .first()
            or ""
        )
        if decision == "needs_revision" and previous_published == photo_key:
            unpublish_photo(profile, photo_field, photo_key)
    log = ProfilePhotoReviewLog.objects.create(
        profile=profile,
        coach=coach,
        photo_field=photo_field,
        photo_key=photo_key,
        decision=decision,
        reason=reason,
        notes=notes,
        previous_status=current_status,
        decision_at=now,
        batch_id=batch_id,
        previous_published_key=previous_published,
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
            membership.exclusion_reason = f"Photo review #{log.pk}: {reason}. {notes}"
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
    return log, state


def submit_member_review(coach: CrushCoach, profile_id: int, decisions, request=None):
    """Decide every reviewed photo of one member card in one transaction.

    The member gets a single notice listing every photo to replace (after
    commit), and Undo reverts the whole card.
    """
    normalized = _normalize_decisions(decisions)
    batch_id = uuid.uuid4()
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
        now = timezone.now()
        if (
            PhotoReviewClaim.objects.filter(profile=profile, expires_at__gt=now)
            .exclude(coach=coach)
            .exists()
        ):
            raise PhotoReviewError(
                _("Another coach is reviewing this member. Skip to the next card.")
            )
        results = [
            _apply_decision(coach, profile, item, batch_id=batch_id, now=now)
            for item in normalized
        ]
        PhotoReviewClaim.objects.filter(profile=profile, coach=coach).delete()
        revision_ids = [
            log.pk for log, _state in results if log.decision == "needs_revision"
        ]
        if revision_ids:
            transaction.on_commit(
                lambda: _notify_revision_safely(profile, revision_ids, request)
            )
        return {
            "success": True,
            "profile_id": profile.pk,
            "batch_id": str(batch_id),
            "log_id": results[-1][0].pk,
            "log_ids": [log.pk for log, _state in results],
            "decisions": {log.photo_field: state.status for log, state in results},
        }


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
    result = submit_member_review(
        coach,
        profile_id,
        [
            {
                "decision": decision,
                "reason": reason,
                "notes": notes,
                "photo_key": photo_key,
                "photo_field": photo_field,
            }
        ],
        request=request,
    )
    return {
        "success": True,
        "decision": decision,
        "profile_id": result["profile_id"],
        "log_id": result["log_id"],
        "new_status": result["decisions"][photo_field],
    }


def _not_current():
    return PhotoReviewError(_("This review is no longer current and cannot be undone."))


def _undo_decision(coach, profile, log):
    """Revert one still-current decision; raises (rolling back) otherwise."""
    current_key = getattr(profile, log.photo_field).name or ""
    state = (
        ProfilePhotoReviewState.objects.select_for_update()
        .filter(
            profile=profile,
            photo_field=log.photo_field,
            photo_key=log.photo_key,
        )
        .first()
    )
    if (
        log.undone_at is not None
        or log.coach_id != coach.pk
        or current_key != log.photo_key
        or state is None
        or state.status != log.decision
        or state.reviewed_by_id != coach.pk
        or state.reviewed_at != log.decision_at
        or log.previous_status != "pending"
        or (
            log.photo_field == "photo_1"
            and (
                profile.photo_review_status != log.decision
                or profile.photo_review_key != log.photo_key
            )
        )
    ):
        raise _not_current()
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
        report = UserReport.objects.select_for_update().filter(pk=log.report_id).first()
        if report is not None and any(
            getattr(report, field) != value for field, value in report_state.items()
        ):
            raise _not_current()
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
            or not membership.exclusion_reason.startswith(f"Photo review #{log.pk}:")
        ):
            raise _not_current()
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
    state.delete()
    if log.photo_field == "photo_1" or log.decision == "flagged_fake":
        primary_key = profile.photo_1.name or ""
        primary_state = (
            ProfilePhotoReviewState.objects.filter(
                profile=profile,
                photo_field="photo_1",
                photo_key=primary_key,
            ).first()
            if primary_key
            else None
        )
        legacy_values = {
            "photo_review_status": (
                primary_state.status if primary_state else "pending"
            ),
            "photo_review_key": primary_state.photo_key if primary_state else "",
            "photo_reviewed_at": (primary_state.reviewed_at if primary_state else None),
            "photo_reviewed_by_id": (
                primary_state.reviewed_by_id if primary_state else None
            ),
            "photo_review_notes": primary_state.notes if primary_state else "",
        }
        CrushProfile.objects.filter(pk=profile.pk).update(**legacy_values)
    # Restore what other members saw before the decision. An approval that
    # replaced an earlier published photo already deleted that file, so the
    # slot stays hidden until the photo is reviewed again.
    if log.decision == "approved":
        if log.previous_published_key != log.photo_key:
            unpublish_photo(profile, log.photo_field, log.photo_key)
    elif log.decision == "needs_revision":
        if log.previous_published_key == log.photo_key:
            restore_publication(profile, log.photo_field, log.photo_key)
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
            .filter(pk=snapshot["id"], status="withdrawn", responded_at=log.decision_at)
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
        state_before = log.revision_notification_state
        log.revision_notification_state = (
            "retract_pending" if state_before in ("sending", "sent") else "cancelled"
        )
    log.save(update_fields=["undone_at", "revision_notification_state"])
    if log.report_id:
        UserReport.objects.filter(pk=log.report_id, **report_state).update(
            status="dismissed",
            handled_by=coach.user,
            handled_at=log.undone_at,
            resolution_notes=f"Photo review #{log.pk} undone by the reviewing coach.",
        )
    return restored_picks, skipped_picks


def undo_last_photo_review(coach: CrushCoach, *, log_id=None, request=None):
    """Undo the coach's still-current card decision (every photo on it),
    preserving the immutable audit record."""
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
        batch_qs = ProfilePhotoReviewLog.objects.select_for_update()
        batch = list(
            batch_qs.filter(batch_id=log.batch_id).order_by("pk")
            if log.batch_id
            else batch_qs.filter(pk=log.pk)
        )
        latest = (
            profile.photo_review_logs.filter(undone_at__isnull=True)
            .exclude(decision="skipped")
            .order_by("-pk")
            .first()
        )
        if latest is None or latest.pk not in {item.pk for item in batch}:
            raise _not_current()
        sent_before = [
            item.pk
            for item in batch
            if item.decision == "needs_revision"
            and item.revision_notification_state in ("sending", "sent")
        ]
        delivered = any(item.revision_notification_state == "sent" for item in batch)
        restored_picks = skipped_picks = 0
        for item in batch:
            restored, skipped = _undo_decision(coach, profile, item)
            restored_picks += restored
            skipped_picks += skipped
        if delivered and sent_before:
            lead_id = min(sent_before)
            transaction.on_commit(lambda: _retract_revision_safely(lead_id, request))
        return {
            "success": True,
            "undone_decision": log.decision,
            "undone_decisions": {item.photo_field: item.decision for item in batch},
            "profile_id": profile.pk,
            "restored_status": "pending",
            "restored_picks": restored_picks,
            "skipped_picks": skipped_picks,
        }
