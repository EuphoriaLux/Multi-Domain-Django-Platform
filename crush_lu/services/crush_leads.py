"""
"My Crush!" crush-lead services (coach-facing).

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md
Phase B — lead model: §7 call-tracking fields, routing tier, coach action
queue integration.

Service/queryset level only. The lead queue UI, the 24h reminder sweep, and
notifications are Phase D; member surfaces are Phase C.
"""

from django.utils import timezone

from crush_lu.models.connections import EventConnection

# 24h untouched-lead reminder offset (spec §6/O8). The sweep itself and its
# scheduler wiring are Phase D; this constant keeps the offset in one place.
REMINDER_AFTER = EventConnection.CRUSH_LEAD_CALL_SLA / 2


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

    A completed call or a non-actionable status (decline/block) is never due.
    """
    if connection.coach_call_completed_at or connection.reminder_sent_at:
        return False
    if connection.flow != EventConnection.FLOW_CRUSH:
        return False
    if connection.status not in EventConnection.OPEN_LEAD_STATUSES:
        return False
    if not connection.requested_at:
        return False
    now = now or timezone.now()
    return now >= connection.requested_at + REMINDER_AFTER
