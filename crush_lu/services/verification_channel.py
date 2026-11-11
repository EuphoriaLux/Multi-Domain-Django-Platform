"""Typed presentation adapter for the existing coach Verification Channel."""

from dataclasses import dataclass

from django.urls import reverse
from django.utils import timezone

from crush_lu.models import ConnectRealPersonCheck
from crush_lu.real_person_check_rules import MAX_OPEN_CLAIMS_PER_COACH
from crush_lu.services.verification_funnel import video_channel_requests


@dataclass
class ChannelItem:
    id: int
    profile: object
    submitted_at: object
    kind: str
    at_capacity: bool
    claim_url: str = ""


def channel_video_context(coach, *, lang="", region="", age_min=None, age_max=None):
    now = timezone.now()
    owned = list(
        ConnectRealPersonCheck.objects.filter(
            coach=coach,
            purpose="connect_week",
            status="open",
            lease_expires_at__gt=now,
        ).select_related("profile__user")
    )
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
    return items, owned
