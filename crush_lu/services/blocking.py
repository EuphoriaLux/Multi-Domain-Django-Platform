"""
Peer-block enforcement helpers.

A ``UserBlock`` is one-directional in storage but enforced *symmetrically*: once
A blocks B, neither should encounter the other on any surface (Drops, Sparks,
event connections). Centralising the symmetric ``Q(...) | Q(...)`` here keeps it
out of every call site and matches the ``existing_connection_subq`` idiom already
used in ``services.crush_connect``.
"""

from __future__ import annotations

from django.db.models import Exists, OuterRef, Q


def blocked_user_ids(user) -> set[int]:
    """Set of user ids ``user`` can no longer see (symmetric — blocked + blockers)."""
    from crush_lu.models import UserBlock

    return UserBlock.objects.blocked_ids_for(user)


def is_blocked_pair(user_a, user_b) -> bool:
    """True if either user has blocked the other (order-independent)."""
    from crush_lu.models import UserBlock

    return UserBlock.objects.between(user_a, user_b).exists()


def block_exists_subquery(user, outer_field: str = "pk"):
    """``Exists`` subquery flagging rows whose user is block-related to ``user``.

    Drop-in mirror of ``existing_connection_subq`` — annotate a User queryset with
    this and ``.filter(<alias>=False)`` to drop blocked counterparts. ``outer_field``
    is the column on the outer queryset holding the candidate user's pk.
    """
    from crush_lu.models import UserBlock

    ref = OuterRef(outer_field)
    return Exists(
        UserBlock.objects.filter(
            Q(blocker=user, blocked=ref) | Q(blocker=ref, blocked=user)
        )
    )


def terminate_active_connections(user_a, user_b) -> int:
    """Decline any in-flight EventConnection between the two users.

    Hiding connections from member pages isn't enough: the coach-facilitation
    queue (``views_coach``) still surfaces ``accepted``/``coach_reviewing`` pairs
    and lets a coach approve them. Blocking must stop that too, so on block we
    flip every non-terminal connection between the pair to ``declined``.

    ``shared`` is left untouched — contact was already exchanged, so there's
    nothing left to facilitate (and it can't be un-shared). The update is a bulk
    ``.update()`` on purpose: it's silent (no decline notification), matching the
    silent-block semantic. Returns the number of connections terminated.
    """
    from django.utils import timezone

    from crush_lu.models import EventConnection

    return (
        EventConnection.objects.filter(
            Q(requester=user_a, recipient=user_b)
            | Q(requester=user_b, recipient=user_a)
        )
        .exclude(status__in=["declined", "shared"])
        .update(status="declined", responded_at=timezone.now())
    )


def withdraw_active_coach_picks(user_a, user_b) -> int:
    """Withdraw any live ``ConnectCoachPick`` between the two users on block.

    Companion to ``terminate_active_connections`` for the coach-pick workflow:
    ``coach_connect_members`` surfaces ``proposed``/``accepted`` picks to the
    coach for facilitation, so a block placed after a pick was accepted must
    withdraw it too — otherwise the coach can still facilitate the blocked pair.
    The pick is symmetric in spirit (member↔candidate), so both directions are
    covered. Returns the number of picks withdrawn.
    """
    from django.utils import timezone

    from crush_lu.models import ConnectCoachPick

    return (
        ConnectCoachPick.objects.filter(
            Q(member=user_a, candidate=user_b)
            | Q(member=user_b, candidate=user_a)
        )
        .exclude(status__in=["declined", "withdrawn"])
        .update(status="withdrawn", responded_at=timezone.now())
    )


def decline_active_sparks(user_a, user_b) -> int:
    """Decline any live ``CuriositySpark`` between the two users on block.

    An *accepted* Spark is the coach's date-arranging queue (``CuriositySparkAdmin``),
    so — like EventConnections and coach picks — a block placed after acceptance
    must take it out of that queue. Pending Sparks are declined too (the recipient
    blocking the sender is an implicit pass). Symmetric; returns the count.
    """
    from django.utils import timezone

    from crush_lu.models import CuriositySpark

    return (
        CuriositySpark.objects.filter(
            Q(sender=user_a, recipient=user_b)
            | Q(sender=user_b, recipient=user_a)
        )
        .exclude(status="declined")
        .update(status="declined", responded_at=timezone.now())
    )


def apply_block(blocker, blocked, reason="") -> None:
    """Create the block (idempotent) and terminate every in-flight
    facilitation surface between the pair.

    The single choke point behind every "block" entry point on the platform:
    the member-card/report flow (``views_moderation._block``, a thin wrapper
    around this) and the Connect Cycle temp-chat 1-click block
    (``services.connect_chat.block_chat_partner``) both call this so a block
    placed from either surface reaches the other's facilitation queues too.
    Declining EventConnections/coach picks/Sparks is what makes the block
    actually stop contact, not just hide it from member pages.

    Connect Cycle re-matching and the temp chat itself are NOT touched here
    — a general block doesn't know about Cycle-specific rows, and not every
    block happens from inside an active Cycle chat. Callers that need that
    (the temp-chat block flow) layer ``ConnectPairExclusion.exclude_pair``
    and chat closure on top, via ``services.connect_chat``.
    """
    from crush_lu.models import UserBlock

    valid = {c for c, _label in UserBlock.REASON_CHOICES}
    UserBlock.objects.get_or_create(
        blocker=blocker,
        blocked=blocked,
        defaults={"reason": reason if reason in valid else ""},
    )
    terminate_active_connections(blocker, blocked)
    withdraw_active_coach_picks(blocker, blocked)
    decline_active_sparks(blocker, blocked)
    cancel_legacy_sparks(blocker, blocked)


def cancel_legacy_sparks(user_a, user_b) -> int:
    """Cancel any in-flight legacy ``CrushSpark`` (Wonderland journey) between the pair.

    The legacy post-event Spark routes (``sparks/``, ``spark_detail``,
    ``spark_create_journey``) stay reachable, so a block must cancel an
    identified-pair Spark too — otherwise the sender could still build/deliver
    the journey and both sides keep seeing it. Only rows with an identified
    recipient form a pair; terminal states are left alone. Returns the count.
    """
    from crush_lu.models import CrushSpark

    return (
        CrushSpark.objects.filter(
            Q(sender=user_a, recipient=user_b)
            | Q(sender=user_b, recipient=user_a)
        )
        .exclude(status__in=["completed", "cancelled", "expired"])
        .update(status="cancelled")
    )


def purge_user_from_connect_queues(user) -> None:
    """Decline/withdraw every live Spark and coach pick involving ``user``.

    Used by the coach panic button (admin "exclude reported user") — flipping
    ``excluded_by_coach`` removes the user from future pools but leaves any
    already-accepted Spark or pick sitting in the coach date-arrangement queues
    (``CuriositySparkAdmin`` / ``coach_connect_members``). This clears those too,
    in every direction, so an excluded member can't linger there.
    """
    from django.utils import timezone

    from crush_lu.models import ConnectCoachPick, CuriositySpark

    now = timezone.now()
    CuriositySpark.objects.filter(
        Q(sender=user) | Q(recipient=user)
    ).exclude(status="declined").update(status="declined", responded_at=now)
    ConnectCoachPick.objects.filter(
        Q(member=user) | Q(candidate=user)
    ).exclude(status__in=["declined", "withdrawn"]).update(
        status="withdrawn", responded_at=now
    )
