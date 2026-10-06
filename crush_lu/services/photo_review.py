"""
Coach Photo Review service for Crush.lu & Crush Connect.

Manages the fast photo-moderation queue and swipe decisions (Approve, Flag Fake, Request Revision).
"""

import logging
from datetime import timedelta
from django.db import models, transaction
from django.db.models import Case, Q, Value, When
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from crush_lu.models import (
    CrushCoach,
    CrushConnectMembership,
    CrushProfile,
    ProfilePhotoReviewLog,
    UserReport,
)
from crush_lu.notification_service import notify_profile_revision
from crush_lu.services.blocking import purge_user_from_connect_queues

logger = logging.getLogger(__name__)


def get_photo_review_queue(coach: CrushCoach, limit: int = 40):
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
            photo_review_status__in=["pending", "needs_revision"],
        )
        .exclude(Q(photo_1="") | Q(photo_1__isnull=True))
        .exclude(verification_status="rejected")
        .exclude(user=coach.user)
        .select_related("user", "user__crush_connect_membership", "user__crush_connect_membership__story_prompt")
        .prefetch_related("user__socialaccount_set")
    )

    # Priority score:
    # 3: Connect onboarded
    # 2: Connect membership exists
    # 1: General profile
    annotated_qs = base_qs.annotate(
        priority=Case(
            When(user__crush_connect_membership__onboarded_at__isnull=False, then=Value(3)),
            When(user__crush_connect_membership__isnull=False, then=Value(2)),
            default=Value(1),
            output_field=models.IntegerField(),
        )
    ).order_by("-priority", "-updated_at", "-id")

    profiles = list(annotated_qs[:limit])
    cards = []
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

        cards.append(
            {
                "id": p.id,
                "user_id": p.user.id,
                "display_name": p.display_name or p.user.first_name or p.user.username,
                "age": p.age_display or "",
                "gender": p.get_gender_display() or "",
                "location": p.city or p.region or "",
                "photos": photos,
                "photo_count": len(photos),
                "photo_key": getattr(p.photo_1, "name", "") or "",
                "photo_review_status": p.photo_review_status,
                "story_prompt": story_prompt,
                "story_text": story_text,
                "relationship_goal": relationship_goal,
                "lifestyle_tags": lifestyle_tags,
                "is_luxid_verified": p.has_luxid_connected,
                "has_attended_event": p.has_attended_event,
                "is_onboarded": bool(mem and mem.is_onboarded),
                "member_since": p.created_at.strftime("%b %Y") if p.created_at else "",
            }
        )

    total_waiting = (
        CrushProfile.objects.filter(
            is_active=True,
            user__is_active=True,
            photo_review_status__in=["pending", "needs_revision"],
        )
        .exclude(Q(photo_1="") | Q(photo_1__isnull=True))
        .exclude(verification_status="rejected")
        .count()
    )

    return cards, total_waiting


def submit_photo_review(
    coach: CrushCoach,
    profile_id: int,
    decision: str,
    reason: str = "",
    notes: str = "",
    request=None,
):
    """
    Process a coach swipe decision:
    - 'approved': Marks photo reviewed & approved.
    - 'flagged_fake': Flags fake profile, trips coach panic button exclusion, files UserReport.
    - 'needs_revision': Flags photo as needing revision and notifies user via push/email.
    - 'skipped': Leaves profile unchanged.
    """
    if decision not in ("approved", "flagged_fake", "needs_revision", "skipped"):
        raise ValueError(f"Invalid decision '{decision}'")

    with transaction.atomic():
        profile = (
            CrushProfile.objects.select_for_update()
            .select_related("user")
            .get(id=profile_id)
        )
        current_key = getattr(profile.photo_1, "name", "") or ""
        prev_status = profile.photo_review_status
        now = timezone.now()

        if decision == "approved":
            profile.photo_review_status = "approved"
            profile.photo_review_key = current_key
            profile.photo_reviewed_at = now
            profile.photo_reviewed_by = coach
            profile.photo_review_notes = notes
            profile.save(
                update_fields=[
                    "photo_review_status",
                    "photo_review_key",
                    "photo_reviewed_at",
                    "photo_reviewed_by",
                    "photo_review_notes",
                ]
            )

        elif decision == "flagged_fake":
            profile.photo_review_status = "flagged_fake"
            profile.photo_review_key = current_key
            profile.photo_reviewed_at = now
            profile.photo_reviewed_by = coach
            profile.photo_review_notes = notes
            profile.save(
                update_fields=[
                    "photo_review_status",
                    "photo_review_key",
                    "photo_reviewed_at",
                    "photo_reviewed_by",
                    "photo_review_notes",
                ]
            )

            coach_label = coach.user.get_full_name() or coach.user.username

            # Trip coach panic button on CrushConnectMembership
            membership, _created = CrushConnectMembership.objects.get_or_create(
                user=profile.user
            )
            if not membership.excluded_by_coach:
                membership.excluded_by_coach = True
                membership.excluded_at = now
                membership.excluded_by = coach
                membership.exclusion_reason = (
                    f"Photo review: Flagged fake/suspicious by coach {coach_label}. "
                    f"Reason: {reason}. {notes}".strip()
                )
                membership.save(
                    update_fields=[
                        "excluded_by_coach",
                        "excluded_at",
                        "excluded_by",
                        "exclusion_reason",
                    ]
                )
                purge_user_from_connect_queues(profile.user)

            # File moderation queue report for audit trail
            UserReport.objects.create(
                reporter=coach.user,
                reported_user=profile.user,
                reason="fake_profile",
                details=f"Coach Photo Review Deck: {reason}. {notes}".strip(),
                source="profile",
                source_id=profile.id,
                status="actioned",
                handled_by=coach.user,
                handled_at=now,
                resolution_notes=f"Auto-excluded from Connect by coach {coach_label}",
            )

        elif decision == "needs_revision":
            profile.photo_review_status = "needs_revision"
            profile.photo_review_key = current_key
            profile.photo_reviewed_at = now
            profile.photo_reviewed_by = coach
            profile.photo_review_notes = notes
            profile.save(
                update_fields=[
                    "photo_review_status",
                    "photo_review_key",
                    "photo_reviewed_at",
                    "photo_reviewed_by",
                    "photo_review_notes",
                ]
            )

            # Send member notification
            feedback_text = (
                notes
                or _("Please upload a clearer profile photo where your face is visible.")
            )
            try:
                notify_profile_revision(
                    user=profile.user,
                    profile=profile,
                    feedback=feedback_text,
                    request=request,
                )
            except Exception:
                logger.exception(
                    "Failed to send photo revision notification to user %s",
                    profile.user_id,
                )

        # Log decision
        log = ProfilePhotoReviewLog.objects.create(
            profile=profile,
            coach=coach,
            photo_key=current_key,
            decision=decision,
            reason=reason,
            notes=notes,
            previous_status=prev_status,
        )

        return {
            "success": True,
            "decision": decision,
            "profile_id": profile.id,
            "log_id": log.id,
            "new_status": profile.photo_review_status,
        }


def undo_last_photo_review(coach: CrushCoach):
    """
    Undo the coach's most recent photo review decision (within the last 15 minutes).
    Restores the previous status on CrushProfile and removes the exclusion if applicable.
    """
    cutoff = timezone.now() - timedelta(minutes=15)
    log = (
        ProfilePhotoReviewLog.objects.filter(coach=coach, created_at__gte=cutoff)
        .order_by("-created_at")
        .first()
    )
    if not log:
        return {"success": False, "message": _("No recent review to undo.")}

    with transaction.atomic():
        profile = (
            CrushProfile.objects.select_for_update()
            .select_related("user")
            .get(id=log.profile_id)
        )
        restored_status = log.previous_status or "pending"
        profile.photo_review_status = restored_status
        if restored_status == "pending":
            profile.photo_review_key = ""
            profile.photo_reviewed_at = None
            profile.photo_reviewed_by = None
            profile.photo_review_notes = ""
        profile.save(
            update_fields=[
                "photo_review_status",
                "photo_review_key",
                "photo_reviewed_at",
                "photo_reviewed_by",
                "photo_review_notes",
            ]
        )

        # If it was flagged_fake, restore membership exclusion if coach was the one who set it
        if log.decision == "flagged_fake":
            membership = CrushConnectMembership.objects.filter(
                user=profile.user, excluded_by=coach
            ).first()
            if membership and "Photo review: Flagged fake" in membership.exclusion_reason:
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

        undone_decision = log.decision
        profile_name = profile.display_name or profile.user.first_name or profile.user.username
        log.delete()

        return {
            "success": True,
            "undone_decision": undone_decision,
            "profile_id": profile.id,
            "profile_name": profile_name,
            "restored_status": restored_status,
        }
