"""
"My Crush!" crush-lead services (coach-facing).

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md
Phase B — lead model: §7 call-tracking fields, routing tier, coach action
queue integration.
Phase D — the 24h untouched-lead reminder sweep (§6/O8) and its coach
notification; member surfaces are Phase C.
"""

import logging

from django.db import transaction
from django.utils import timezone

from crush_lu.models.connections import EventConnection

logger = logging.getLogger(__name__)

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


def reminder_candidates(now=None):
    """
    Open crush leads whose 24h reminder is due, as a queryset.

    Mirrors ``reminder_due`` in SQL so the sweep does not have to walk every
    lead in Python. ``open_crush_leads()`` already restricts to
    ``flow='crush'``, ``OPEN_LEAD_STATUSES`` and an incomplete call — so a
    lead flipped to ``declined`` by a member block or a coach decline drops
    out here rather than being reminded about.
    """
    now = now or timezone.now()
    return (
        EventConnection.objects.open_crush_leads()
        .filter(
            requested_at__lte=now - REMINDER_AFTER,
            reminder_sent_at__isnull=True,
            coach_call_scheduled_at__isnull=True,
            assigned_coach__isnull=False,
            assigned_coach__is_active=True,
        )
        .select_related("assigned_coach__user", "requester", "event")
        .order_by("requested_at", "id")
    )


class ReminderDeliveryFailed(Exception):
    """Push delivery reported no successful send — retry on the next sweep."""


def _delivery_failed(result) -> bool:
    """
    Did a push-notification result represent a real delivery failure?

    ``send_coach_push_notification`` swallows per-device exceptions and
    reports them in its return value, so an unraised failure would otherwise
    let the sweep commit ``reminder_sent_at`` and drop the lead from every
    later sweep — the coach would simply never be reminded.

    "No opted-in device" (``total == 0``) is NOT a failure: the coach muted
    this channel, and retrying hourly forever would never deliver. Only a
    real attempt that produced no success counts.

    A non-dict result (an injected notifier) has no contract to check, so it
    is treated as delivered — such notifiers signal failure by raising.
    """
    if not isinstance(result, dict):
        return False
    return result.get("total", 0) > 0 and result.get("success", 0) == 0


def sweep_lead_reminders(now=None, notify=None):
    """
    Send the 24h untouched-lead reminder to each routed coach (spec §6/O8).

    Idempotent by construction: ``reminder_sent_at`` is both the filter and
    the record, and it is written inside the same savepoint that fires the
    notification — so a failed notification rolls the stamp back and the
    lead stays eligible for the next sweep instead of being silently
    swallowed. Delivering the timer twice therefore produces exactly one
    reminder per lead.

    Rows are locked with ``skip_locked`` so two overlapping timer deliveries
    divide the work instead of blocking or double-sending.

    ``notify`` is injectable for tests; it defaults to the coach push
    notification helper.
    """
    now = now or timezone.now()
    if notify is None:
        from crush_lu.coach_notifications import notify_coach_crush_lead_reminder

        notify = notify_coach_crush_lead_reminder

    sent = 0
    failed = 0
    with transaction.atomic():
        locked_ids = list(
            EventConnection.objects.filter(
                pk__in=reminder_candidates(now).values("pk")
            )
            .select_for_update(skip_locked=True)
            .values_list("pk", flat=True)
        )
        leads = EventConnection.objects.filter(pk__in=locked_ids).select_related(
            "assigned_coach__user", "requester", "event"
        )
        for lead in leads:
            try:
                with transaction.atomic():
                    lead.reminder_sent_at = now
                    lead.save(update_fields=["reminder_sent_at"])
                    if _delivery_failed(notify(lead.assigned_coach, lead)):
                        # Inside the savepoint, so the stamp rolls back and
                        # the lead stays eligible for the next sweep.
                        raise ReminderDeliveryFailed(
                            f"no successful push for lead {lead.pk}"
                        )
                sent += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[crush_lead_reminders] Failed on lead %s", lead.pk
                )
                failed += 1

    logger.info("[crush_lead_reminders] sent=%d failed=%d", sent, failed)
    return {"sent": sent, "failed": failed}
