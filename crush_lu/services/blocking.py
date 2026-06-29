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
