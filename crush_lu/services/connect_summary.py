"""Read-only navigation state: never start a cycle or generate discovery cards."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from crush_lu.connect_phase import candidate_access_open, cycle_access_open
from crush_lu.models import (
    ConfirmedEncounter,
    ConnectChatMessage,
    ConnectCycleCard,
    ConnectTemporaryChat,
    ConnectWeeklyRequest,
    ConnectWeekSession,
    UserBlock,
)
from crush_lu.services.connect_cycle import (
    CYCLE_LENGTH_DAYS,
    get_pending_inbox,
    sync_session_state,
    visible_cycle_cards,
    week_timeline_state,
)
from crush_lu.services.crush_connect import (
    filter_catalogue_eligible,
    get_active_coach_pick,
    is_catalogue_eligible,
)

NAV_BADGE_CACHE_SECONDS = 60


def _blocked_counterparts(user):
    """Subquery twins of ``blocked_user_ids`` (both directions)."""
    return (
        UserBlock.objects.filter(blocker=user).values("blocked_id"),
        UserBlock.objects.filter(blocked=user).values("blocker_id"),
    )


def _open_chats(user):
    """The Chats tab's live chats. Blocks are subqueries so that the nav badge
    can count unread chats in one query."""
    blocked = Q()
    for ids in _blocked_counterparts(user):
        blocked |= Q(participant_1_id__in=ids) | Q(participant_2_id__in=ids)
    return ConnectTemporaryChat.objects.filter(
        Q(participant_1=user) | Q(participant_2=user),
        expires_at__gt=timezone.now(),
        participant_1__is_active=True,
        participant_2__is_active=True,
        participant_1__crushprofile__is_active=True,
        participant_2__crushprofile__is_active=True,
        participant_1__crush_connect_membership__onboarded_at__isnull=False,
        participant_2__crush_connect_membership__onboarded_at__isnull=False,
    ).exclude(
        Q(status__in=["closed", "blocked"])
        | blocked
        | Q(participant_1__crush_connect_membership__excluded_by_coach=True)
        | Q(participant_2__crush_connect_membership__excluded_by_coach=True)
    )


def _unread_chat_count(chats, user):
    return chats.filter(
        pk__in=ConnectChatMessage.objects.filter(read_at__isnull=True)
        .exclude(sender=user)
        .values("chat_id")
    ).count()


def _pending_request_count(user):
    """One-COUNT twin of the summary's ``pending_requests`` (``inbox_access``
    gate + ``get_pending_inbox``) that never writes: an expired row is
    skipped here and flipped to EXPIRED by the next full read."""
    membership = getattr(user, "crush_connect_membership", None)
    if (membership and membership.is_paused) or not (
        user.is_staff or candidate_access_open()
    ):
        return 0
    users = get_user_model().objects
    requests = ConnectWeeklyRequest.objects.filter(
        recipient=user,
        status=ConnectWeeklyRequest.Status.PENDING,
        expires_at__gte=timezone.now(),
        requester__in=filter_catalogue_eligible(users.all()).values("pk"),
    )
    if not user.is_staff:
        requests = requests.filter(
            recipient__in=filter_catalogue_eligible(users.filter(pk=user.pk)).values(
                "pk"
            )
        )
    hidden = ConfirmedEncounter.objects.filter(
        status__in=("removal_pending", "removed")
    )
    blocked_by_me, blocking_me = _blocked_counterparts(user)
    return requests.exclude(
        Q(requester__in=blocked_by_me)
        | Q(requester__in=blocking_me)
        | Q(requester__in=hidden.filter(user_low=user).values("user_high_id"))
        | Q(requester__in=hidden.filter(user_high=user).values("user_low_id"))
        | Q(requester__crushcoach__assigned_members__user=user)
        | Q(requester__crushprofile__assigned_coach__user=user)
    ).count()


def connect_nav_badge_count(user):
    """Pending requests + unread chats for the desktop Connect link, cached
    per user for a minute. Two COUNTs on a miss; never the full summary."""
    key = f"crush_lu:connect_nav_badge:{user.pk}"
    count = cache.get(key)
    if count is None:
        count = _pending_request_count(user) + _unread_chat_count(
            _open_chats(user), user
        )
        cache.set(key, count, NAV_BADGE_CACHE_SECONDS)
    return count


def get_connect_summary(user):
    membership = getattr(user, "crush_connect_membership", None)
    # Today / Requests / Chats are Connect features: a member still preparing
    # on the hub would only be bounced to onboarding or the teaser by them.
    nav_access = user.is_staff or bool(
        membership
        and membership.onboarded_at is not None
        and not membership.excluded_by_coach
    )
    participating = bool(membership and membership.is_participating)
    visible = is_catalogue_eligible(user)
    inbox_access = not (membership and membership.is_paused) and (
        user.is_staff or (candidate_access_open() and visible)
    )
    cycle_access = user.is_staff or (participating and cycle_access_open(user))
    session = ConnectWeekSession.objects.filter(user=user).first()
    if session:
        session = sync_session_state(session)
    cards = (
        ConnectCycleCard.objects.filter(
            session=session, generated_date=timezone.localdate(), is_expired=False
        )
        if session
        else ConnectCycleCard.objects.none()
    )
    cards = visible_cycle_cards(
        cards.select_related(
            "target_user__crushprofile", "target_user__crush_connect_membership"
        ),
        user,
    )
    chats = _open_chats(user)
    coach_pick = (
        get_active_coach_pick(user, include_accepted=True) if participating else None
    )
    return {
        "nav_access": nav_access,
        "cycle_access": cycle_access,
        "coach_pick_status": coach_pick.status if coach_pick else "",
        "is_visible": visible,
        "pending_requests": len(get_pending_inbox(user)) if inbox_access else 0,
        "chat_count": chats.count(),
        "unread_chats": _unread_chat_count(chats, user),
        "session": session,
        "review_open": bool(cycle_access and session and session.is_review_active),
        "daily_total": len(cards),
        "daily_completed": sum(card.is_completed for card in cards),
        "cycle_length": CYCLE_LENGTH_DAYS,
        "timeline": week_timeline_state(session) if cycle_access and session else None,
    }
