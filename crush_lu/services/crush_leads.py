"""
"My Crush!" crush-lead services.

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md
Phase B — lead model: §7 call-tracking fields, routing tier, coach action
queue integration.
Phase C — member declaration: gender-independent counter under a
per-(requester, event) lock (§5/§7, O9).

Service/queryset level only. The lead queue UI, the 24h reminder sweep, and
notifications are Phase D.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from crush_lu.models.connections import EventConnection

# 24h untouched-lead reminder offset (spec §6/O8). The sweep itself and its
# scheduler wiring are Phase D; this constant keeps the offset in one place.
REMINDER_AFTER = EventConnection.CRUSH_LEAD_CALL_SLA / 2


class CrushDeclarationLimitReached(Exception):
    """The requester already used their per-event crush (spec O9)."""


def crushes_remaining(user, event):
    """How many crush declarations ``user`` can still make for ``event``."""
    used = EventConnection.crush_declaration_count(user, event)
    return max(0, EventConnection.MAX_CRUSHES_PER_EVENT - used)


def declare_crush(*, requester, recipient, event, requester_registration, note=""):
    """
    Create a "My Crush!" coach lead (``flow='crush'``) for the pair.

    The counter check is serialized per (requester, event) by locking the
    requester's event registration row (spec §7): two concurrent declarations
    by the same requester to different recipients can otherwise both read a
    count of zero and race past the limit, creating unbudgeted coach work.

    Never notifies the recipient — a crush is private until the routed coach
    makes the introduction. Reciprocal declarations stay independent leads;
    nothing here inspects or mutates a reverse row.

    Raises ``CrushDeclarationLimitReached`` when the per-event limit is used
    up. May raise ``IntegrityError`` on a same-direction duplicate race —
    callers translate that into the duplicate path.
    """
    with transaction.atomic():
        # Stable per-(requester, event) lock taken BEFORE counting.
        type(requester_registration).objects.select_for_update().get(
            pk=requester_registration.pk
        )
        if crushes_remaining(requester, event) <= 0:
            raise CrushDeclarationLimitReached(
                _("You have already declared your crush for this event.")
            )
        connection = EventConnection.objects.create(
            requester=requester,
            recipient=recipient,
            event=event,
            flow=EventConnection.FLOW_CRUSH,
            requester_note=note,
        )
        # Route the lead (assigned coach -> event coach -> pool). A member
        # without a CrushProfile leaves the lead in the pool for Phase D
        # queue triage rather than failing the declaration.
        from crush_lu.models.profiles import CrushProfile

        if CrushProfile.objects.filter(user=requester).exists():
            connection.assign_coach()
    return connection


def call_by(connection):
    """"Call by" deadline for a crush lead, or ``None`` for legacy rows."""
    return connection.call_by


def coach_action_queue(coach):
    """
    Coach action queue queryset for crush leads (spec §5/§7).

    Only ``flow='crush'`` rows in live actionable statuses whose call is not
    completed, routed to ``coach``, ordered by "call by" (oldest first).
    Historical ``flow='legacy'`` rows never appear.
    """
    return EventConnection.objects.crush_leads_for_coach(coach)


def reminder_due(connection, now=None):
    """
    True when an open crush lead is older than the 24h reminder offset and
    no reminder has been recorded yet (idempotency via ``reminder_sent_at``).

    A scheduled or completed call counts as touched, so it is never due;
    a non-actionable status (decline/block) is never due either.
    """
    if (
        connection.coach_call_completed_at
        or connection.coach_call_scheduled_at
        or connection.reminder_sent_at
    ):
        return False
    if connection.flow != EventConnection.FLOW_CRUSH:
        return False
    if connection.status not in EventConnection.OPEN_LEAD_STATUSES:
        return False
    if not connection.requested_at:
        return False
    now = now or timezone.now()
    return now >= connection.requested_at + REMINDER_AFTER
