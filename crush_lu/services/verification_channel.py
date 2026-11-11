"""Typed presentation adapter for the existing coach Verification Channel."""

from dataclasses import dataclass

from django.core.paginator import Paginator
from django.urls import reverse
from django.utils import timezone

from crush_lu.models import ConnectRealPersonCheck, ProfileSubmission
from crush_lu.real_person_check_rules import MAX_OPEN_CLAIMS_PER_COACH
from crush_lu.services.real_person_check import (
    open_checks_for_coach,
    real_person_check_enabled,
    real_person_check_queue,
)
from crush_lu.services.verification_funnel import video_channel_requests

QUEUE_PAGE_SIZE = 20


@dataclass
class ChannelItem:
    id: int
    profile: object
    submitted_at: object
    kind: str
    at_capacity: bool
    claim_url: str = ""


@dataclass
class AssignedChannelItem:
    profile: object
    kind: str
    continue_url: str
    assigned_at: object
    lease_expires_at: object = None


def assignment_count(coach):
    count = ProfileSubmission.objects.filter(coach=coach, status="pending").count()
    if real_person_check_enabled():
        count += open_checks_for_coach(coach).count()
    return count


def assigned_channel_items(coach):
    """Every live assignment, independently of the waiting-list filters."""
    items = [
        AssignedChannelItem(
            profile=submission.profile,
            kind="submission",
            continue_url=reverse("crush_lu:coach_review_profile", args=[submission.pk]),
            assigned_at=submission.submitted_at,
        )
        for submission in ProfileSubmission.objects.filter(
            coach=coach, status="pending"
        ).select_related("profile__user")
    ]
    if real_person_check_enabled():
        items.extend(
            AssignedChannelItem(
                profile=check.profile,
                kind="video" if check.purpose == "connect_week" else "badge",
                continue_url=reverse(
                    "crush_lu:coach_real_person_check", args=[check.pk]
                ),
                assigned_at=check.requested_at or check.claimed_at,
                lease_expires_at=check.lease_expires_at,
            )
            for check in open_checks_for_coach(coach)
        )
    return sorted(items, key=lambda item: item.assigned_at)


def badge_channel_context(coach, page_number):
    """Proactive optional outreach, never counted as a waiting request."""
    mine = list(open_checks_for_coach(coach))
    queue = (
        real_person_check_queue(coach)
        .exclude(pk__in=[check.profile.user_id for check in mine])
        .prefetch_related("crushprofile__photo_framings")
    )
    paginator = Paginator(queue, QUEUE_PAGE_SIZE)
    return {
        "page": paginator.get_page(page_number),
        "total_waiting": paginator.count,
        "open_count": len(mine),
        "max_open": MAX_OPEN_CLAIMS_PER_COACH,
        "badge_at_capacity": len(mine) >= MAX_OPEN_CLAIMS_PER_COACH,
    }


def channel_video_context(coach, *, lang="", region="", age_min=None, age_max=None):
    now = timezone.now()
    open_count = ConnectRealPersonCheck.objects.filter(
        coach=coach, status="open", lease_expires_at__gt=now
    ).count()
    items = []
    for check in video_channel_requests(coach):
        profile = check.profile
        if lang and lang not in (profile.event_languages or []):
            continue
        if region and region.casefold() not in (profile.location or "").casefold():
            continue
        if age_min and (profile.age is None or profile.age < age_min):
            continue
        if age_max and (profile.age is None or profile.age > age_max):
            continue
        items.append(
            ChannelItem(
                id=check.pk,
                profile=profile,
                submitted_at=check.requested_at or check.claimed_at,
                kind="video",
                at_capacity=open_count >= MAX_OPEN_CLAIMS_PER_COACH,
                claim_url=reverse("crush_lu:coach_video_claim", args=[check.pk]),
            )
        )
    return items
