"""
"My Crush!" crush-lead services.

Spec: docs/superpowers/specs/2026-07-21-crush-my-crush-post-event-flow.md
Phase B — lead model: §7 call-tracking fields, routing tier, coach action
queue integration.
Phase C — member declaration: gender-independent counter under a
per-(requester, event) lock (§5/§7, O9).
Phase D — the 24h untouched-lead reminder sweep (§6/O8) and its coach
notification.
"""

import logging
import time


from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from crush_lu.models.connections import EventConnection

logger = logging.getLogger(__name__)

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


# ``ReminderDeliveryFailed`` used to live here: the sweep raised it to roll
# the claim back inside the transaction. The claim-then-send restructure
# releases with an explicit UPDATE instead, so nothing raised or caught it any
# more. Removed rather than left as a decorative import target.


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

    A missing-VAPID config error has the *same* zero-total shape as an
    opt-out, so it is flagged explicitly by the push helper — otherwise
    every reminder falling due during the outage would be silently consumed.

    A non-dict result (an injected notifier) has no contract to check, so it
    is treated as delivered — such notifiers signal failure by raising.
    """
    if not isinstance(result, dict):
        return False
    if result.get("misconfigured"):
        return True
    return result.get("total", 0) > 0 and result.get("success", 0) == 0


REMINDER_BATCH_LIMIT = 200

# Wall-clock budget for one sweep. The `CrushLeadReminders` timer calls this
# through `_call_admin_endpoint`, which gives up at 60s, and each lead can
# make several serial Web Push calls at up to `PUSH_TIMEOUT` each — so the
# count cap alone does not bound the run: a handful of stalled endpoints can
# burn the caller's whole budget, and the Django request would then keep
# going until gunicorn kills it at 120s, leaving most of the batch late with
# nothing to show for it. Stop early instead and let the next hourly run take
# the rest, the same way the count cap does.
REMINDER_TIME_BUDGET = 45.0


def sweep_lead_reminders(
    now=None,
    notify=None,
    limit=REMINDER_BATCH_LIMIT,
    time_budget=REMINDER_TIME_BUDGET,
    clock=None,
):
    """
    Send the 24h untouched-lead reminder to each routed coach (spec §6/O8).

    **Claim, then send, then release on failure.** ``reminder_sent_at`` is
    both the filter and the record. It is stamped and *committed* under the
    row lock before the push goes out, and cleared again by a separate
    ``UPDATE`` if the push did not land — so the lead stays eligible for the
    next sweep instead of being silently swallowed, while the device-health
    writes the push helper makes along the way survive. See the comment at
    the claim for why the send must not sit inside our transaction.

    Rows are locked with ``skip_locked`` so two overlapping timer deliveries
    divide the work instead of blocking or double-sending: the second
    delivery re-reads a stamped row and finds it no longer due.

    **One transaction per lead, not one for the sweep.** Holding every
    candidate's row lock across the whole serial run would let one stalled
    push endpoint block them all until the request is killed. Each lead is
    therefore claimed and committed on its own, and the batch is bounded by
    ``limit`` *and* by ``time_budget`` — see ``REMINDER_TIME_BUDGET`` for why
    a count alone does not fit inside the caller's 60s timeout.

    ``notify`` and ``clock`` are injectable for tests; they default to the
    coach push notification helper and a monotonic clock.
    """
    now = now or timezone.now()
    clock = clock or time.monotonic
    started = clock()
    if notify is None:
        from crush_lu.coach_notifications import notify_coach_crush_lead_reminder

        # Bound each notification by the sweep's own budget: between-lead
        # checks cannot stop a single multi-device `notify()` from overrunning
        # the caller's 60s timeout on its own — the deadline has to reach
        # inside the device loop.
        sweep_deadline = started + time_budget

        def notify(coach, lead):  # noqa: A001 — matches the injectable's name
            return notify_coach_crush_lead_reminder(
                coach, lead, deadline=sweep_deadline, clock=clock
            )

    sent = 0
    failed = 0
    # Bounded: an hourly timer only has to drain what accumulated in an hour,
    # and a backlog spike must not turn one invocation into a 60s timeout.
    candidate_ids = list(
        reminder_candidates(now).values_list("pk", flat=True)[: limit + 1]
    )
    truncated = len(candidate_ids) > limit
    if truncated:
        candidate_ids = candidate_ids[:limit]
        # Never let a cap read as "swept everything" — the next hourly run
        # picks up the rest, but a persistent backlog needs to be visible.
        logger.warning(
            "[crush_lead_reminders] batch capped at %d; more leads are due",
            limit,
        )

    for index, lead_id in enumerate(candidate_ids):
        if clock() - started >= time_budget:
            # Out of time, not out of work: the remaining leads keep their
            # null `reminder_sent_at` and are picked up by the next run.
            truncated = True
            logger.warning(
                "[crush_lead_reminders] time budget %.0fs exhausted after "
                "%d of %d leads; more are due",
                time_budget,
                index,
                len(candidate_ids),
            )
            break
        try:
            # Claim, then send, then release on failure — three steps, and the
            # send is deliberately *outside* any transaction of ours.
            #
            # It used to be one atomic block: stamp, push, raise on failure so
            # the stamp rolled back. That read well and was wrong in one
            # specific way — `send_coach_push_notification` records device
            # health as it goes (`mark_failure`, and `delete` on a 410), and
            # the rollback discarded those writes too. A permanently dead
            # endpoint therefore never reached its five-failure auto-delete and
            # was retried every hour forever, spending the sweep's time budget
            # on a device that will never accept a push.
            #
            # The trade this makes: a process killed between claim and release
            # leaves the stamp committed, so that one reminder is skipped
            # rather than retried. That is a strictly smaller loss than an
            # endpoint that can never be cleaned up, and the next sweep still
            # covers every *other* lead.
            with transaction.atomic():
                lead = (
                    EventConnection.objects.select_for_update(of=("self",), skip_locked=True)
                    .select_related("assigned_coach__user", "requester", "event")
                    .filter(pk=lead_id)
                    .first()
                )
                # Locked by a concurrent sweep, or no longer eligible because
                # the coach acted between the scan and now — either way it is
                # not ours to send. Re-apply the *whole* predicate, not just
                # the stamp: a call scheduled or completed in that window, or
                # a decline, makes the lead ineligible too, and sending anyway
                # would put a stale reminder naming the requester on a lock
                # screen and consume the stamp for a lead nobody owes a call.
                if lead is None or not reminder_due(lead, now):
                    continue
                # `reminder_due` covers the lead's own state; the routed
                # coach is the other half of `reminder_candidates`, and a
                # deactivation in the same window would leave nobody to
                # notify.
                if lead.assigned_coach is None or not lead.assigned_coach.is_active:
                    continue
                # The claim. Committing this before sending is what still makes
                # two overlapping timer deliveries send exactly once: the
                # second one re-reads a stamped row and `reminder_due` is false.
                lead.reminder_sent_at = now
                lead.save(update_fields=["reminder_sent_at"])
                coach = lead.assigned_coach

            # `notify` can fail two ways and both have to release the claim:
            # it may *return* a zero-success result (the shape that fooled an
            # earlier fix, because the helper catches per-device errors), or it
            # may raise. Treating only the first as failure would leave a
            # raising send with a committed stamp and no retry — the exact
            # regression this restructure could otherwise introduce.
            try:
                delivered = not _delivery_failed(notify(coach, lead))
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[crush_lead_reminders] push raised for lead %s", lead_id
                )
                delivered = False

            if delivered:
                sent += 1
            else:
                # Release the claim so the next sweep retries. Its own
                # transaction, so it cannot take the device-health writes the
                # send just committed down with it — which is the entire point
                # of this restructure.
                #
                # Guarded on the stamp we wrote: if a coach acted in the
                # meantime the row is no longer ours to clear, and blanking it
                # would re-arm a reminder for a lead that no longer needs one.
                released = EventConnection.objects.filter(
                    pk=lead_id, reminder_sent_at=now
                ).update(reminder_sent_at=None)
                failed += 1
                logger.warning(
                    "[crush_lead_reminders] no successful push for lead %s; "
                    "claim %s",
                    lead_id,
                    "released for retry" if released else "left (lead changed)",
                )
        except Exception:  # noqa: BLE001
            # Anything outside the send itself — the lock, the claim write,
            # the release. Nothing is stamped in those paths without the claim
            # having committed, so there is no orphaned stamp to undo here.
            failed += 1
            logger.exception("[crush_lead_reminders] Failed on lead %s", lead_id)

    logger.info(
        "[crush_lead_reminders] sent=%d failed=%d truncated=%s",
        sent,
        failed,
        truncated,
    )
    return {"sent": sent, "failed": failed, "truncated": truncated}
