"""Grant seats to selected registrations: the Invite step of curated events.

Moved out of ``EventRegistrationAdmin.confirm_registrations`` so the admin
action and an automatic selection (spec:
ai-memory-hub/specs/2026-10-29-crush-curated-auto-selection.md) share one path.
Behaviour is unchanged: the admin action replays the collected messages
through ``django.contrib.messages`` exactly as it used to emit them.
"""

import logging
from functools import partial

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from crush_lu.models import (
    CuratedEventGroup,
    CuratedEventGroupMembership,
    EventRegistration,
    MeetupEvent,
)
from crush_lu.models.events import SEAT_HOLDING_STATUSES

logger = logging.getLogger(__name__)


class ConfirmationMessages:
    """What a confirmation run said, in order, as ``(level, message)`` pairs.

    The admin action replays them through ``django.contrib.messages``; a
    caller without a request reads ``entries`` instead.
    """

    def __init__(self):
        self.entries = []

    def error(self, message):
        self.entries.append(("error", message))

    def warning(self, message):
        self.entries.append(("warning", message))

    def info(self, message):
        self.entries.append(("info", message))

    def success(self, message):
        self.entries.append(("success", message))


def notify_google_indexing(event_ids, action=None):
    """Ping Google Search Indexing for events changed by a bulk action.

    ``queryset.update()`` fires no signals, so the indexing receivers never see these changes. Runs inside one
    wall-clock budget and never raises — a slow Google must not break an admin
    action whose database work already committed.
    """
    ids = list(dict.fromkeys(event_ids))
    if not ids:
        return

    try:
        from crush_lu.services.google_indexing import (
            INDEXABLE_EVENT_FILTERS,
            notify_events_indexing,
        )

        notify_events_indexing(
            MeetupEvent.objects.filter(pk__in=ids, **INDEXABLE_EVENT_FILTERS),
            action=action,
            max_budget_seconds=12.0,
        )
    except Exception:
        logger.exception(
            "Error sending Google indexing notifications for %d event(s)", len(ids)
        )


def send_application_selection_notification_safely(registration_id):
    """Notify one selected applicant after the selection transaction commits."""

    try:
        registration = EventRegistration.objects.select_related("event", "user").get(
            pk=registration_id
        )
        from crush_lu.services.curated_group_workflow import (
            registration_has_certified_payable_group,
            requires_curated_group_certification,
        )

        if requires_curated_group_certification(
            registration.event
        ) and not registration_has_certified_payable_group(registration):
            logger.warning(
                "Skipped stale curated selection notification for registration %s",
                registration_id,
            )
            return False
        if registration.status == "pending":
            from crush_lu.email_helpers import (
                send_event_payment_pending_notification,
            )

            return bool(send_event_payment_pending_notification(registration))
        elif registration.status == "confirmed":
            from crush_lu.email_helpers import send_event_registration_confirmation

            return bool(send_event_registration_confirmation(registration))
        return False
    except Exception:
        # A mail outage must never roll back or disguise the auditable group
        # selection. The ordinary resend/support path can recover delivery.
        logger.exception(
            "Failed to notify selected event applicant registration %s",
            registration_id,
        )
        return False


def confirm_registrations(queryset, *, messages=None):
    """Confirm selected registrations, skipping anyone who doesn't meet age limits."""
    if messages is None:
        messages = ConfirmationMessages()
    eligible_ids = []
    skipped = 0
    invalid_parallel_entries = []
    for reg in queryset.select_related("event", "user__crushprofile"):
        event = reg.event
        if (
            event.uses_curated_registration
            and event.group_size
            and reg.status != "applied"
        ):
            invalid_parallel_entries.append(reg.pk)
            continue
        if event.min_age > 18 or event.max_age < 99:
            profile = getattr(reg.user, "crushprofile", None)
            age = profile.age if profile else None
            if age is None or not (event.min_age <= age <= event.max_age):
                skipped += 1
                continue
        eligible_ids.append(reg.pk)
    if invalid_parallel_entries:
        messages.error(
            _(
                "Configured parallel groups can only grant seats from "
                "Applied through a complete provisional-group selection. "
                "Nothing was changed."
            ),
        )
        return messages
    # Serials of seats being restored from ANY invalid status — their Apple
    # ticket is currently `voided` and has to be told it is live again.
    # Keyed off SEAT_HOLDING_STATUSES rather than "cancelled" alone, to
    # match what the payload actually voids: `waitlist` and `no_show` are
    # equally invalid, and confirming one of those would otherwise leave a
    # visibly voided ticket on a now-valid seat. Collected before the
    # update, while the old status is still readable.
    restoring = EventRegistration.objects.filter(pk__in=eligible_ids).exclude(
        status__in=SEAT_HOLDING_STATUSES
    )
    restored_serials = list(
        restoring.exclude(apple_wallet_ticket_serial="").values_list(
            "apple_wallet_ticket_serial", flat=True
        )
    )
    # ...and their MEMBER passes. Restoring a seat makes the event eligible
    # for get_next_event_for_pass again, so the card has to stop showing
    # the previous next event (or "No upcoming events"). A holder who never
    # downloaded the ticket has only this serial.
    from crush_lu.models import CrushProfile

    restored_serials += list(
        CrushProfile.objects.filter(user__eventregistration__in=restoring)
        .exclude(apple_pass_serial="")
        .values_list("apple_pass_serial", flat=True)
        .distinct()
    )
    # ...and the GOOGLE member objects, for exactly the reason above: a
    # restored seat is eligible for get_next_event_for_pass again, so the
    # card has to stop showing whatever it fell back to. Materialised HERE,
    # before the update, for the same reason as the serials — `restoring`
    # is a lazy queryset keyed on the old status, and the flip to
    # "confirmed" is about to make it match nothing.
    #
    # NARROWER than `restoring`, which also carries waitlist rows. Their
    # Apple TICKET is voided and does need un-voiding, but a waitlisted
    # registration is already inside PASS_NEXT_EVENT_STATUSES, so the
    # member card already names this event and the rebuild would be
    # byte-identical. Only a seat coming back from OUTSIDE that set
    # (cancelled, no_show) actually changes what the card says.
    # A second narrowing on top of that, the mirror of the one in
    # cancel_events: re-entering the candidate set only changes the card if
    # the restored event would then BE the displayed one. A holder with an
    # earlier eligible registration keeps looking at that, so restoring a
    # later seat rebuilds a byte-identical object and spends a capped,
    # poll-less budget slot that a genuinely-changed holder needed.
    #
    # `<=` on the comparison, not `<`: two events sharing a start time are
    # ordered by whatever the database returns, so a tie is "might change"
    # rather than "cannot".
    from crush_lu.wallet_pass import (
        PASS_NEXT_EVENT_STATUSES,
        get_next_event_registrations,
    )

    now = timezone.now()
    restored_rows = list(
        restoring.exclude(status__in=PASS_NEXT_EVENT_STATUSES).select_related("event")
    )
    restored_google_profiles = []
    if restored_rows:
        # Keyed on the FULL ordering key, not the start time alone. Once
        # _next_event_candidates gained its (date_time, event_id, id)
        # tie-break, "same start time" stopped meaning "might win": on a
        # tie the restored event takes the card only if it also sorts
        # first. Comparing dates alone queued every tied holder — no-ops
        # spending a cap whose overflow is permanent.
        def _key(row):
            return (row.event.date_time, row.event_id, row.pk)

        by_user = {}
        for row in restored_rows:
            event = row.event
            if event.is_cancelled or event.end_time < now:
                continue  # restoring the seat still leaves it off the card
            best = by_user.get(row.user_id)
            if best is None or _key(row) < best:
                by_user[row.user_id] = _key(row)
        displayed = get_next_event_registrations(list(by_user), now=now)
        candidates = CrushProfile.objects.filter(user_id__in=list(by_user)).exclude(
            google_wallet_object_id=""
        )
        for profile in candidates:
            showing = displayed.get(profile.user_id)
            if showing is None or by_user[profile.user_id] < _key(showing):
                restored_google_profiles.append(profile)

    # Only rows arriving from *outside* the seat-holding set change what the
    # page publishes. pending/confirmed/attended -> confirmed leaves
    # remainingAttendeeCapacity and offer availability untouched, and the
    # pending -> confirmed workflow is the common one.
    confirmed_event_ids = list(
        EventRegistration.objects.filter(pk__in=eligible_ids)
        .exclude(status__in=SEAT_HOLDING_STATUSES)
        .values_list("event_id", flat=True)
        .distinct()
    )
    # Selecting an applicant on a PAID curated event owes them a payment
    # request, not a seat. Confirming outright would hand out a seat, a
    # door ticket, check-in eligibility and reminders before any money
    # arrived — `_admitted_status` draws exactly this line at signup, and
    # this is that same rule applied to the selection step.
    #
    # Scoped to "applied" on a paid event so every pre-existing path
    # through this action keeps its current behaviour byte-for-byte: a
    # cancelled or waitlisted row still confirms as it always has, paid or
    # not. Widening that is a separate decision, not this change's to make.
    # Selecting more applicants than there are places overbooks the event
    # outright — and on a curated event that is an ordinary slip, not an
    # edge case, because the applicant pool is *designed* to outnumber the
    # seats. Refuse the whole action rather than promote an arbitrary
    # subset: which applicants get in is the organiser's decision, and
    # silently letting the database's row order make it would be worse
    # than doing nothing.
    #
    # Scoped to applications, like the paid-event rule below. Confirming
    # cancelled or waitlisted rows over capacity has always been possible
    # here and organisers may rely on it to deliberately overbook; taking
    # that away is a separate decision.

    # Local import, as everywhere else in this module: views_events pulls
    # in the payment and wallet stacks at module scope, and admin.events is
    # imported during app registry population.
    from crush_lu.views_events import _admitted_status

    prelock_application_ids = set(
        EventRegistration.objects.filter(
            pk__in=eligible_ids, status="applied"
        ).values_list("pk", flat=True)
    )
    prelock_event_ids = sorted(
        set(
            EventRegistration.objects.filter(
                pk__in=prelock_application_ids,
                status="applied",
            ).values_list("event_id", flat=True)
        )
    )
    curated_notice_generations = {}
    selection_delivery = None
    reserve_notice_ids = []
    reserve_delivery = None

    # The capacity check and the status updates have to be one atomic,
    # locked unit. Reading spots_remaining and then updating in a separate
    # statement lets two admins selecting at the same time both see the
    # last seat free and both spend it — and the seat they overspend comes
    # with a door ticket and check-in eligibility.
    #
    # Gender pools are checked alongside the total: with caps enabled a
    # male applicant can pass a total-capacity test while the male pool is
    # already full, which is exactly the case event_register re-checks
    # under its own lock. Half a guard here would be worse than none,
    # because it reads as protection.
    with transaction.atomic():
        # The member-cancellation path takes the event lock before any
        # registration locks. Keep that global order here too or an admin
        # selection racing a cancellation can deadlock on PostgreSQL.
        locked_events = {
            event.pk: event
            for event in MeetupEvent.objects.select_for_update()
            .filter(pk__in=prelock_event_ids)
            .order_by("pk")
        }
        # Re-read the selected applications under the lock, not just the
        # event. Locking the event alone left the rows themselves stale:
        # two admins selecting the same paid application both materialise
        # it as "applied" before either writes, and the second — finding it
        # is no longer "applied" and so no longer needs payment —
        # would sweep it into confirm_ids and grant the seat WITHOUT
        # payment. Anything another admin (or the member, withdrawing) has
        # already moved out of "applied" is dropped from this run entirely,
        # so a concurrent decision is never silently overwritten.
        #
        # NO select_related() on this query. "user__crushprofile" is a
        # REVERSE one-to-one, so it joins as a LEFT OUTER JOIN, and
        # PostgreSQL refuses FOR UPDATE on the nullable side of an outer
        # join — the whole action would raise NotSupportedError before
        # updating anyone. _promote_from_waitlist carries the same warning
        # for the same reason. SQLite ignores select_for_update() entirely,
        # so the test suite cannot see this; profiles and events are read
        # separately below. "event" is dropped too: joining it here would
        # take event locks implicitly in registration order, after the
        # explicit sorted event-first lock above established the global
        # order shared with member cancellation.
        applications = list(
            EventRegistration.objects.select_for_update()
            .filter(
                pk__in=prelock_application_ids,
                event_id__in=locked_events,
                status="applied",
            )
            .order_by("pk")
        )
        live_application_ids = {reg.pk for reg in applications}
        stale_application_ids = prelock_application_ids - live_application_ids

        profiles_by_user = {}
        if applications:
            # Separate query, for the outer-join reason above.
            profiles_by_user = {
                profile.user_id: profile
                for profile in CrushProfile.objects.filter(
                    user_id__in={reg.user_id for reg in applications}
                )
            }

            by_event = {}
            for reg in applications:
                by_event.setdefault(reg.event_id, []).append(reg)

            for event_id, regs in by_event.items():
                event = locked_events[event_id]
                # A configured parallel night is selected in complete
                # groups, not against the raw venue ceiling. This matters
                # both when the coach committed fewer than the maximum and
                # when max_participants has a remainder smaller than one
                # group. Direct events intentionally keep the same value.
                remaining = event.selection_spots_remaining
                if len(regs) > remaining:
                    messages.error(
                        _(
                            "“%(title)s” has %(remaining)s place(s) left but "
                            "%(selected)s application(s) were selected. Nothing "
                            "was changed — deselect %(excess)s and try again."
                        )
                        % {
                            "title": event.title,
                            "remaining": remaining,
                            "selected": len(regs),
                            "excess": len(regs) - remaining,
                        },
                    )
                    return messages

                # On a parallel curated night, a checkbox alone must not
                # create a payment request. The applicant must belong to a
                # schedule that passed the five-date guarantee and was
                # explicitly promoted to PROVISIONAL. Legacy curated
                # events without group_size retain the existing manual
                # selection workflow.
                if event.group_size:
                    registration_ids = {reg.pk for reg in regs}
                    active_groups = list(
                        CuratedEventGroup.objects.select_for_update()
                        .filter(event_id=event_id)
                        .filter(
                            status__in=(
                                CuratedEventGroup.STATUS_PROVISIONAL,
                                CuratedEventGroup.STATUS_LOCKED,
                            )
                        )
                        .order_by("pk")
                    )
                    current_generation = max(
                        (group.generation for group in active_groups),
                        default=None,
                    )
                    memberships = list(
                        CuratedEventGroupMembership.objects.select_for_update()
                        .select_related("group")
                        .filter(
                            event_id=event_id,
                            registration_id__in=registration_ids,
                            released_at__isnull=True,
                        )
                        .order_by("pk")
                    )
                    payable_ids = {
                        membership.registration_id
                        for membership in memberships
                        if membership.group.status
                        == CuratedEventGroup.STATUS_PROVISIONAL
                        and membership.group.generation == current_generation
                    }
                    unready = registration_ids - payable_ids
                    if unready:
                        messages.error(
                            _(
                                "“%(title)s”: %(count)d selected applicant(s) "
                                "are not in a viable provisional group. Nothing "
                                "was changed — approve the stored group schedule "
                                "before inviting payment."
                            )
                            % {"title": event.title, "count": len(unready)},
                        )
                        return messages

                    touched_group_ids = {
                        membership.group_id for membership in memberships
                    }
                    generation_groups = list(
                        CuratedEventGroup.objects.select_for_update()
                        .filter(
                            event_id=event_id,
                            generation=current_generation,
                        )
                        .order_by("group_number", "pk")
                    )
                    touched_group_numbers = {
                        group.group_number
                        for group in generation_groups
                        if group.pk in touched_group_ids
                    }
                    highest_touched_number = max(touched_group_numbers)
                    prefix_groups = [
                        group
                        for group in generation_groups
                        if group.group_number <= highest_touched_number
                    ]
                    prefix_numbers = {group.group_number for group in prefix_groups}
                    unsatisfied_prefix = prefix_numbers != set(
                        range(1, highest_touched_number + 1)
                    )
                    for prefix_group in prefix_groups:
                        if prefix_group.status not in {
                            CuratedEventGroup.STATUS_PROVISIONAL,
                            CuratedEventGroup.STATUS_LOCKED,
                        }:
                            unsatisfied_prefix = True
                            break
                        if (
                            prefix_group.pk not in touched_group_ids
                            and prefix_group.memberships.filter(
                                released_at__isnull=True,
                                registration__status="applied",
                            ).exists()
                        ):
                            unsatisfied_prefix = True
                            break
                    if unsatisfied_prefix:
                        messages.error(
                            _(
                                "“%(title)s”: groups must be selected in "
                                "fairness order with no skipped group number. "
                                "Invite or resolve every earlier group first."
                            )
                            % {"title": event.title},
                        )
                        return messages
                    roster_memberships = list(
                        CuratedEventGroupMembership.objects.select_for_update()
                        .filter(
                            group_id__in=touched_group_ids,
                            released_at__isnull=True,
                        )
                        .order_by("pk")
                    )
                    still_applied_ids = set(
                        EventRegistration.objects.filter(
                            curated_group_memberships__in=roster_memberships,
                            status="applied",
                        ).values_list("pk", flat=True)
                    )
                    if registration_ids != still_applied_ids:
                        messages.error(
                            _(
                                "“%(title)s”: select every still-applied "
                                "member of each touched provisional group in "
                                "one action. Nothing was changed."
                            )
                            % {"title": event.title},
                        )
                        return messages
                    try:
                        for group in active_groups:
                            if group.pk in touched_group_ids:
                                summary = group.schedule_viability(
                                    evaluate_preferences=False
                                )
                                if summary["schedule_digest"] != group.schedule_digest:
                                    raise ValidationError(
                                        _(
                                            "The roster or schedule changed "
                                            "after provisional approval."
                                        )
                                    )
                    except ValidationError as error:
                        messages.error(
                            _(
                                "“%(title)s”: the stored provisional schedule "
                                "is no longer viable (%(reason)s). Nothing was "
                                "changed."
                            )
                            % {"title": event.title, "reason": error},
                        )
                        return messages

                    for reg in regs:
                        curated_notice_generations[reg.pk] = current_generation

                if not event.gender_limits_active:
                    continue
                selected_per_pool = {}
                for reg in regs:
                    profile = profiles_by_user.get(reg.user_id)
                    gender = getattr(profile, "gender", None)
                    pool = event.get_gender_pool(gender) if gender else None
                    if pool is None:
                        continue
                    selected_per_pool.setdefault(pool, []).append(gender)
                for pool, genders in selected_per_pool.items():
                    gender = genders[0]
                    limit = event.get_gender_pool_limit(gender)
                    if limit is None:
                        continue
                    taken = event.get_confirmed_count_for_gender(gender)
                    if taken + len(genders) > limit:
                        messages.error(
                            _(
                                "“%(title)s”: the %(pool)s places are capped at "
                                "%(limit)s with %(taken)s already taken, but "
                                "%(selected)s application(s) in that group were "
                                "selected. Nothing was changed."
                            )
                            % {
                                "title": event.title,
                                "pool": pool,
                                "limit": limit,
                                "taken": taken,
                                "selected": len(genders),
                            },
                        )
                        return messages

        # Inside the same locked transaction as the checks above: releasing
        # the lock before writing would leave exactly the race the lock was
        # taken to close.
        #
        # Everything is derived from the LOCKED read. Deriving confirm_ids
        # from the pre-lock eligible_ids is what let a row another admin
        # had just moved to "pending" fall through to "confirmed".
        #
        # _admitted_status, not the fee alone. A member who paid, cancelled
        # late and then reapplied keeps payment_confirmed on the REUSED
        # row, and that helper exists precisely so such a row is admitted
        # as "confirmed" instead of being asked to pay a second time for
        # money we already hold — the UI would show payment due while
        # checkout rejected them as already paid.
        pending_application_ids = {
            reg.pk
            for reg in applications
            if _admitted_status(locked_events[reg.event_id], reg) == "pending"
        }
        confirm_ids = [
            pk
            for pk in eligible_ids
            if pk not in pending_application_ids and pk not in stale_application_ids
        ]

        # Rows that get a seat back, read before the update below. Only
        # these drop an earlier waitlist promotion's notice outcome: staff
        # granted this seat and no notice announced it. A row that already
        # held its seat keeps its outcome, so the coach page still warns
        # about a promoted member nothing reached.
        restored_ids = list(
            EventRegistration.objects.filter(pk__in=confirm_ids)
            .exclude(status__in=SEAT_HOLDING_STATUSES)
            .values_list("pk", flat=True)
        )
        updated = EventRegistration.objects.filter(pk__in=confirm_ids).update(
            status="confirmed",
            # QuerySet.update bypasses EventRegistration.save(). A restored
            # registration is a new cancellation-policy cycle and must not
            # retain the previous cancellation's timing classification.
            cancelled_at=None,
        )
        EventRegistration.objects.filter(pk__in=restored_ids).update(
            waitlist_promoted_at=None, promotion_notice=""
        )
        awaiting_payment = 0
        if pending_application_ids:
            # status="applied" in the filter as well as the lock: belt and
            # braces against acting on a row that moved under us.
            awaiting_payment = EventRegistration.objects.filter(
                pk__in=pending_application_ids, status="applied"
            ).update(
                status="pending",
                cancelled_at=None,
                waitlist_promoted_at=None,
                promotion_notice="",
            )

        # Carry a released paid seat's resale claim onto the applicant now
        # taking it.
        #
        # When a paid attendee cancels late, their 50% share is owed once
        # somebody else takes and pays for the seat. On a direct event the
        # replacement picks the claim up automatically: waitlist promotion
        # and event_register both call _attach_unclaimed_resale_claim. A
        # curated event has neither -- no waitlist to promote from, and the
        # sign-up made an application that took no seat -- so the claim sat
        # on the cancelled row and the replacement arrived without it.
        # Settlement then found no resale_source_* links and issued
        # nothing, so the original member LOST THEIR SHARE PERMANENTLY even
        # after the replacement paid in full. Selection is the moment the
        # seat actually changes hands, so it is where the claim moves.
        #
        # AFTER the status writes above, which is what makes this safe.
        # _attach_unclaimed_resale_claim decides a claim is already taken by
        # looking for another row in SEAT_HOLDING_STATUSES carrying it.
        # Called before the updates, every applicant in the run would still
        # be "applied" -- seat-holding to nobody -- and each would be handed
        # the SAME claim. Afterwards they all hold seats, so the existing
        # check separates them with no bookkeeping of our own. Ordering the
        # calls by pk gives the oldest claim to the lowest-numbered
        # applicant, deterministically rather than by database whim.
        resale_claims_attached = 0
        if applications:
            from crush_lu.services.credits import settle_pending_resale_credit
            from crush_lu.views_events import _attach_unclaimed_resale_claim

            for reg in applications:
                # Scoped to applications, like the paid-event rule above:
                # a cancelled or waitlisted row confirmed through this
                # action behaves exactly as it does today.
                #
                # No registration_fee test here. _resale_claim_from already
                # weighs the fee against captured payments and returned
                # credits, and a fee lowered to zero after someone paid
                # still owes them; second-guessing it would drop that case.
                if reg.resale_source_registration_id or reg.resale_source_payment_id:
                    continue  # already carries one
                if (
                    _attach_unclaimed_resale_claim(reg, locked_events[reg.event_id])
                    is None
                ):
                    continue
                resale_claims_attached += 1
                # A returning applicant whose earlier payment still stands
                # is admitted straight to "confirmed", so no later checkout
                # will settle this for them. _promote_from_waitlist settles
                # the same case at the same point.
                if reg.payment_confirmed:
                    settle_pending_resale_credit(reg)

        # Curated invitations use a durable per-generation outbox. A
        # request timeout therefore leaves the exact unsent remainder
        # retryable instead of an unknowable partial email fan-out.
        curated_notice_ids = set(curated_notice_generations).intersection(
            live_application_ids
        )
        if curated_notice_ids:
            from crush_lu.services.curated_group_notifications import (
                enqueue_selection_notifications,
            )

            enqueue_selection_notifications(
                {
                    registration_id: curated_notice_generations[registration_id]
                    for registration_id in curated_notice_ids
                }
            )

            # Everyone still "applied" on the event who holds no place in
            # the invited generation learns that they stay in the pool.
            # Without this, a non-selected applicant kept reading "Your
            # application is in!" indefinitely. Enqueued inside the same
            # transaction as the status writes so the outbox row is as
            # durable as the selection it reports on.
            from crush_lu.services.curated_group_notifications import (
                enqueue_reserve_notifications,
            )

            reserve_scope = {}
            for reg in applications:
                if reg.pk in curated_notice_ids:
                    reserve_scope[reg.event_id] = curated_notice_generations[reg.pk]
            for scoped_event_id, generation in sorted(reserve_scope.items()):
                reserve_notice_ids.extend(
                    enqueue_reserve_notifications(
                        scoped_event_id,
                        generation=generation,
                        exclude_registration_ids=curated_notice_ids,
                    )
                )

        # Legacy/direct event selection keeps its existing single-message
        # callback. The projector's potentially 500-person path never
        # enters this loop.
        for registration_id in sorted(live_application_ids - curated_notice_ids):
            transaction.on_commit(
                partial(
                    send_application_selection_notification_safely,
                    registration_id,
                )
            )

    if curated_notice_ids:
        from crush_lu.services.curated_group_notifications import (
            deliver_curated_group_notifications,
        )

        selection_delivery = deliver_curated_group_notifications(
            registration_ids=curated_notice_ids,
            kinds=["selection"],
        )
        if reserve_notice_ids:
            # Bounded like the invitations: a remainder stays queued for
            # the "Deliver pending curated emails" action.
            reserve_delivery = deliver_curated_group_notifications(
                notice_ids=reserve_notice_ids,
                kinds=["reserve"],
                drain=True,
            )

    # .update() emits no signals, so the per-registration receiver never
    # runs here and the restored tickets would stay voided forever.
    if restored_serials:
        try:
            from crush_lu.wallet.passkit_service import refresh_ticket_serials

            refresh_ticket_serials(
                restored_serials, context="Admin confirm_registrations"
            )
        except Exception:
            logger.exception(
                "Failed scheduling Apple ticket refresh for %s restored "
                "registration(s)",
                len(restored_serials),
            )

    if restored_google_profiles:
        try:
            from crush_lu.wallet.google_api import refresh_google_wallet_objects

            refresh_google_wallet_objects(
                restored_google_profiles,
                context="Admin confirm_registrations",
            )
        except Exception:
            logger.exception(
                "Failed scheduling Google wallet refresh for %s restored "
                "registration(s)",
                len(restored_google_profiles),
            )

    # .update() emits no signals here either, so the EventRegistration
    # receiver that keeps remainingAttendeeCapacity and offer availability
    # fresh in the event JSON-LD never runs for waitlist -> confirmed.
    notify_google_indexing(confirmed_event_ids)

    messages.success(_("Confirmed %(count)s registration(s).") % {"count": updated})
    if stale_application_ids:
        messages.warning(
            _(
                "%(count)s selected application(s) had already been acted on "
                "by someone else and were left untouched."
            )
            % {"count": len(stale_application_ids)},
        )
    if awaiting_payment:
        messages.info(
            _(
                "%(count)s selected application(s) on paid events were set "
                "to Pending Payment rather than Confirmed — their seat is "
                "held until payment arrives."
            )
            % {"count": awaiting_payment},
        )
    if selection_delivery is not None:
        messages.info(
            _(
                "Curated invitation delivery: %(sent)d sent, %(failed)d "
                "failed and remain retryable, %(remaining)d still queued."
            )
            % {
                "sent": selection_delivery.sent,
                "failed": selection_delivery.failed,
                "remaining": selection_delivery.remaining,
            },
        )
    if reserve_delivery is not None:
        messages.info(
            _(
                "Applicants not selected this time: %(sent)d told they stay "
                "in the pool, %(failed)d failed and remain retryable, "
                "%(remaining)d still queued."
            )
            % {
                "sent": reserve_delivery.sent,
                "failed": reserve_delivery.failed,
                "remaining": reserve_delivery.remaining,
            },
        )
    if resale_claims_attached:
        # Worth saying out loud: it commits the organiser to paying someone
        # a 50% share out of this seat, and it is otherwise invisible.
        messages.info(
            _(
                "%(count)s selected applicant(s) took a seat released by a "
                "late cancellation. The member who cancelled earns their "
                "50%% resale share once the new attendee has paid."
            )
            % {"count": resale_claims_attached},
        )
    if skipped:
        messages.warning(
            _(
                "Skipped %(count)s registration(s) that do not meet the event's age requirements."
            )
            % {"count": skipped},
        )
    return messages
