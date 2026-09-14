"""Read-only navigation state: never start a cycle or generate discovery cards."""

from django.db.models import Q
from django.utils import timezone

from crush_lu.connect_phase import cycle_access_open
from crush_lu.models import ConnectCycleCard, ConnectTemporaryChat, ConnectWeekSession
from crush_lu.services.connect_cycle import get_pending_inbox


def get_connect_summary(user):
    membership = getattr(user, "crush_connect_membership", None)
    participating = bool(membership and membership.is_participating)
    cycle_access = participating and (user.is_staff or cycle_access_open(user))
    session = ConnectWeekSession.objects.filter(user=user).first()
    cards = (
        ConnectCycleCard.objects.filter(
            session=session, generated_date=timezone.localdate(), is_expired=False
        )
        if session
        else ConnectCycleCard.objects.none()
    )
    chats = ConnectTemporaryChat.objects.filter(
        Q(participant_1=user) | Q(participant_2=user),
        expires_at__gt=timezone.now(),
    ).exclude(status__in=["closed", "blocked"])
    return {
        "cycle_access": cycle_access,
        "pending_requests": len(get_pending_inbox(user)) if participating else 0,
        "chat_count": chats.count(),
        "session": session,
        "review_open": bool(cycle_access and session and session.is_review_active),
        "daily_total": cards.count(),
        "daily_completed": cards.filter(is_completed=True).count(),
    }
