"""Read-only navigation state: never start a cycle or generate discovery cards."""

from django.db.models import Q
from django.utils import timezone

from crush_lu.connect_phase import cycle_access_open
from crush_lu.models import (
    ConnectChatMessage,
    ConnectCycleCard,
    ConnectTemporaryChat,
    ConnectWeekSession,
)
from crush_lu.services.connect_cycle import get_pending_inbox
from crush_lu.services.blocking import blocked_user_ids


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
    blocked = blocked_user_ids(user)
    chats = ConnectTemporaryChat.objects.filter(
        Q(participant_1=user) | Q(participant_2=user),
        expires_at__gt=timezone.now(),
        participant_1__is_active=True,
        participant_2__is_active=True,
        participant_1__crushprofile__is_active=True,
        participant_2__crushprofile__is_active=True,
    ).exclude(
        Q(status__in=["closed", "blocked"])
        | Q(participant_1_id__in=blocked)
        | Q(participant_2_id__in=blocked)
        | Q(participant_1__crush_connect_membership__excluded_by_coach=True)
        | Q(participant_2__crush_connect_membership__excluded_by_coach=True)
    )
    return {
        "cycle_access": cycle_access,
        "pending_requests": len(get_pending_inbox(user)) if participating else 0,
        "chat_count": chats.count(),
        "unread_chats": chats.filter(
            pk__in=ConnectChatMessage.objects.filter(read_at__isnull=True)
            .exclude(sender=user)
            .values("chat_id")
        ).count(),
        "session": session,
        "review_open": bool(cycle_access and session and session.is_review_active),
        "daily_total": cards.count(),
        "daily_completed": cards.filter(is_completed=True).count(),
    }
