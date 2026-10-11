"""
Recording no-shows once an event is over.

The door scanner only ever writes ``attended``. A member who held a seat and
never reached the door stays ``confirmed`` (or ``pending``) until someone
records the no-show, and the only bulk way to do that used to be the QuizEvent
admin action. Every other event type kept its no-shows as seat holders: event
27, a mixer, still had 14 never-checked-in rows ``confirmed`` a day after it
ended (measured on prod 2026-10-11), so ``weekly_kpis`` and every no-show
history undercounted. Both admin actions (``admin.quiz`` and the MeetupEvent
admin) now go through this module.

``.update()`` is deliberate: it fires no signals, so recording no-shows never
promotes anyone from the waitlist (see
``signals.promote_waitlist_on_cancellation``). It also means nothing tells the
wallets, which is why ``mark_unattended_no_show`` refreshes them itself.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

#: Seat-holding statuses a member can still be in after missing the event.
#: "pending" is included on purpose: on a paid event a cash-at-the-door
#: registrant who never arrives would otherwise sit as Pending Payment forever,
#: still consuming a seat and never reaching no-show reporting. A SumUp capture
#: that lands afterwards is handled in ``views_payments`` (payment recorded,
#: attendance not restored). "attended", "cancelled", "waitlist" and "applied"
#: are never touched.
NO_SHOW_CANDIDATE_STATUSES = ("confirmed", "pending")


def unattended_registrations(event):
    """Seat holders of *event* who were never checked in."""
    from crush_lu.models.events import EventRegistration

    return EventRegistration.objects.filter(
        event=event,
        status__in=NO_SHOW_CANDIDATE_STATUSES,
        # A row scanned at the door but back at "confirmed" (an admin edit)
        # still records someone who was there.
        checked_in_at__isnull=True,
    )


def checkin_closes_at(event):
    """When the door stops admitting this event's seat holders.

    The QR check-in (``views_checkin.event_checkin_api``) accepts a scan up to
    ``EVENT_CHECKIN_WINDOW_HOURS`` after the start, and refuses any row that no
    longer holds a seat — ``no_show`` included. Recording no-shows before this
    point would turn away late arrivals with nothing at the door to undo it.
    """
    hours = getattr(settings, "EVENT_CHECKIN_WINDOW_HOURS", 12)
    return max(event.end_time, event.date_time + timedelta(hours=hours))


def curated_group_refusal(event):
    """Why the bulk path would bypass a curated group, or None.

    EventRegistration.save() degrades a PROVISIONAL group (and schedules its
    checkout retirement and remedy) when a member leaves the roster, and
    refuses the change outright inside a LOCKED one. .update() does neither,
    so a member still active in such a group is settled through the audited
    group workflow first. A released membership is history.
    """
    if not event.uses_curated_registration:
        return None
    from crush_lu.models.events import CuratedEventGroup, CuratedEventGroupMembership

    if CuratedEventGroupMembership.objects.filter(
        registration__in=unattended_registrations(event),
        released_at__isnull=True,
        group__status__in=(
            CuratedEventGroup.STATUS_PROVISIONAL,
            CuratedEventGroup.STATUS_LOCKED,
        ),
    ).exists():
        return _(
            "some of these members are still in an open curated group; "
            "settle it through the group workflow first"
        )
    return None


def candidate_fingerprint(registration_ids):
    """The exact rows a coach was shown, as the form carries them back.

    The coach page POSTs this so the view marks only the list that was
    confirmed. An offline PWA queues a failed POST and replays it up to 24
    hours later (sw-workbox.js), and two people can act on one event, so the
    list may have changed in between; the view then refuses and asks again.
    """
    return ",".join(str(pk) for pk in sorted(registration_ids))


def no_show_refusal(event, now=None):
    """Why no-shows cannot be recorded for *event* yet, or None if they can.

    Shared by the MeetupEvent admin action and the coach event page. The
    QuizEvent action predates the time guards and applies only
    ``curated_group_refusal``.
    """
    from crush_lu.models.events import EventRegistration

    now = now or timezone.now()
    if event.is_cancelled:
        # Cancelling leaves the registrations untouched, so the roster still
        # reads "confirmed" for a party that never happened.
        return _("it was cancelled, so nobody missed it")
    if checkin_closes_at(event) >= now:
        # Covers future and running events too: a coach standing in the room
        # when the event is scheduled to end must not lock out the stragglers.
        return _("its check-in window has not closed yet")
    if (
        not EventRegistration.objects.filter(event=event)
        .filter(Q(status="attended") | Q(checked_in_at__isnull=False))
        .exists()
    ):
        # The scanner was never used, so "not checked in" says nothing about
        # who came; the whole roster would be marked absent.
        return _("nobody was checked in, so attendance was never recorded")
    return curated_group_refusal(event)


def mark_unattended_no_show(events, *, context, now=None):
    """Flip every unattended seat holder of *events* to ``no_show``.

    Returns ``[(event, updated_count), ...]`` in the order given. The caller
    decides which events are eligible; this applies no time guard.
    ``updated_count`` is None for an event DEFERRED by the Google cap below:
    nothing about it changed, so running the same call again records it.

    The wallet refreshes are bounded by the refresh helpers' own push budgets
    and run ONCE for the whole call, so the request stays bounded however
    many events are selected (production runs tasks inline).
    """
    from crush_lu.models import CrushProfile
    from crush_lu.wallet_pass import get_next_event_registrations

    now = now or timezone.now()
    results = []
    processed = 0
    google_limit = getattr(settings, "WALLET_GOOGLE_BULK_UPDATE_LIMIT", 50)

    # Accumulated across the whole call, then refreshed ONCE: each refresh
    # helper opens its own push budget, so refreshing per event would restart
    # the deadline for every selected row.
    voiding_serials = []
    # The Google half of the same accumulation, keyed by pk to DEDUPE. One
    # holder can be registered for two of the selected events, and unlike a
    # repeated Apple serial each duplicate here is a real extra PATCH spent out
    # of a budget whose overflow leaves other passes permanently stale.
    voiding_google_profiles = {}

    for event in events:
        affected = unattended_registrations(event)
        # GOOGLE member objects print the holder's "Next Event" block and have
        # no Google ticket to carry the change instead. Worked out first, so
        # the cap below can be checked before anything is flipped.
        # Collected before the update, while `affected` still matches.
        #
        # Two ways a holder here needs the PATCH, and the second is the USUAL
        # one, because no-shows are normally recorded after the event:
        #
        #   * the event is still live and is what their card is showing. The
        #     no_show flip takes it out of PASS_NEXT_EVENT_STATUSES, so the
        #     block changes. Crossing the status boundary is necessary but not
        #     sufficient — a holder with an earlier eligible registration is
        #     looking at that instead, and refreshing them writes a
        #     byte-identical object out of a capped, poll-less budget.
        #
        #   * the event has already ENDED. get_next_event_for_pass stopped
        #     selecting it the moment it finished, so it is nobody's displayed
        #     event and the test above answers "refresh nobody" — but the Google
        #     object is SERVER-SIDE state that only changes when we PATCH it,
        #     and the last PATCH went out while the event was still upcoming. It
        #     is therefore still advertising a finished event, and nothing else
        #     recomputes it: there is no "event ended" trigger anywhere in the
        #     estate. Optimising on the current logical selector cannot see
        #     that, because the two diverge the instant an event ends.
        #
        # So an ended event takes the conservative branch. That is the same
        # rule the event-change fan-out follows: over-refreshing spends budget,
        # under-refreshing strands a wrong card with nothing left to correct it.
        event_ended = event.end_time < now
        candidates = list(
            CrushProfile.objects.filter(user__eventregistration__in=affected).exclude(
                google_wallet_object_id=""
            )
        )
        displayed = (
            {}
            if event_ended
            else get_next_event_registrations(
                [profile.user_id for profile in candidates], now=now
            )
        )
        event_google = {}
        for profile in candidates:
            showing = displayed.get(profile.user_id)
            if event_ended or (showing is not None and showing.event_id == event.pk):
                event_google[profile.pk] = profile

        # Google has no poll: a PATCH the refresh helper drops past
        # WALLET_GOOGLE_BULK_UPDATE_LIMIT leaves that card stale for good, and
        # once its row is no_show no rerun can reach it again. So stop BEFORE
        # the flip instead. A deferred event keeps its rows as they are, and
        # running the same action again records it under a fresh budget. The
        # first event always goes through: one event alone over the cap falls
        # back to the helper's own overflow, logged with the object ids.
        if processed and (
            len(voiding_google_profiles.keys() | event_google.keys()) > google_limit
        ):
            results.append((event, None))
            continue
        voiding_google_profiles.update(event_google)

        # Collected before the update, while the seats are still valid.
        # no_show is not a seat-holding status, so the rebuilt ticket is
        # `voided` — but .update() emits no signals, so without this the
        # holder keeps a ticket that still looks valid at the door.
        voiding_serials += list(
            affected.exclude(apple_wallet_ticket_serial="").values_list(
                "apple_wallet_ticket_serial", flat=True
            )
        )
        # ...and their Apple MEMBER passes. no_show is outside the candidate set of
        # get_next_event_for_pass, so this event stops being the holder's next
        # event and the card would otherwise keep advertising an event they
        # were just marked absent from. A holder who never downloaded the
        # ticket has only this serial.
        voiding_serials += list(
            CrushProfile.objects.filter(user__eventregistration__in=affected)
            .exclude(apple_pass_serial="")
            .values_list("apple_pass_serial", flat=True)
            .distinct()
        )
        # .update() skips auto_now, so stamp updated_at by hand: it is the only
        # record of when the no-show was written.
        updated = affected.update(status="no_show", updated_at=now)
        if updated:
            # Leaving SEAT_HOLDING_STATUSES changes the page's remaining
            # capacity. A per-row save would have told Google through
            # trigger_google_indexing_on_registration_save; .update() fires no
            # signal, so schedule the same coalesced, budgeted, on-commit ping.
            from crush_lu.signals import _schedule_event_indexing_notification

            _schedule_event_indexing_notification(event.pk, skip_if_ineligible=True)
        results.append((event, updated))
        processed += 1

    if voiding_serials:
        try:
            from crush_lu.wallet.passkit_service import refresh_ticket_serials

            refresh_ticket_serials(voiding_serials, context=context)
        except Exception:
            logger.exception(
                "Failed scheduling Apple ticket refresh for %s no-show "
                "registration(s)",
                len(voiding_serials),
            )

    if voiding_google_profiles:
        try:
            from crush_lu.wallet.google_api import refresh_google_wallet_objects

            refresh_google_wallet_objects(
                list(voiding_google_profiles.values()), context=context
            )
        except Exception:
            logger.exception(
                "Failed scheduling Google wallet refresh for %s no-show "
                "registration(s)",
                len(voiding_google_profiles),
            )

    return results
