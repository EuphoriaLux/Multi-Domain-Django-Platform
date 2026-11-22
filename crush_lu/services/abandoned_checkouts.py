"""Remind, then release, unpaid seats whose online checkout was abandoned.

A paid event holds a seat as ``pending`` from signup until the money lands.
When a member opens the Mollie payment page and leaves, that seat used to stay
held until the event: on 2026-11-20 two such seats blocked two of the sixteen
women's places at Quiz Night #28, and 8 of the 9 members who started a checkout
and never paid were women.

``release_abandoned_event_checkouts`` (the ``AbandonedCheckoutSweep`` timer,
every 10 minutes) walks pending seats with an online checkout in the current
registration cycle and, once every checkout on the seat has ended unpaid:

1. ``REMINDER_AFTER`` after the latest checkout was opened, sends one
   "finish your payment" email with a link back to the event page, whose
   "Pay now" button opens a fresh checkout. The seat is still held.
2. ``RELEASE_AFTER`` after that checkout, and at least
   ``RELEASE_AFTER_REMINDER`` after the email, releases the seat: the row goes
   to ``cancelled`` with ``checkout_released_at`` set, and the waitlist gets
   the seat as on any cancellation. No cancellation email (the reminder said
   this would happen) and no credit (nothing was paid).

What it never touches:

- a seat with a checkout still open or in progress at Mollie (a card payment
  page that is still open, or Pay by Bank waiting for the transfer). Those end
  by themselves at Mollie, and the next run picks the seat up;
- a seat with no online checkout in this cycle (a staff-added seat, a door or
  comp guest, a member who has not clicked "Pay" yet);
- curated speed dating, which has its own selection and payment workflow;
- an event that has started (the door still admits pending seats).

A payment that completes after the release anyway is settled by
``views_payments._settle_capture_for_released_seat``: the seat comes back if
one is still free, otherwise the full value becomes Crush Credit, refundable
on request.
"""

import logging
import time as monotonic_clock
from datetime import datetime, timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from crush_lu.models.events import EventRegistration
from crush_lu.models.payments import EventCheckoutCreationClaim, PaymentTransaction

logger = logging.getLogger(__name__)

# The member may still be retrying on their own: about half of the abandoned
# Quiz #28 checkouts were followed by a paid one within minutes.
REMINDER_AFTER = timedelta(minutes=15)
# "About 30 minutes" after the latest checkout opened (Tom, 2026-11-21).
RELEASE_AFTER = timedelta(minutes=30)
# The reminder promises the seat for a little longer; keep that promise.
RELEASE_AFTER_REMINDER = timedelta(minutes=15)
# When the reminder has been tried and keeps failing to send, do not hold the
# seat for ever. Only a real failed send starts this clock: a seat whose
# checkout ended late, or that the sweep reaches late, still gets its email.
REMINDER_FAILING_FOR = timedelta(minutes=60)
# A row written before Mollie was asked has no payment id for a moment; past
# this it is a request that died between the two writes (as in views_mollie).
PREPARING_GRACE = timedelta(seconds=120)

# The timer's HTTP call times out at 60 s; Mollie reads and email sends can
# each take seconds, so stop starting new seats well before that.
BUDGET_SECONDS = 40
MAX_REGISTRATIONS_PER_RUN = 50
# Where the last run stopped, so seats past a long run of waiting ones (open
# checkouts, Pay by Bank) are reached on the next runs instead of never.
CURSOR_KEY = "abandoned-checkout-sweep:cursor"
CURSOR_TTL_SECONDS = 24 * 60 * 60


class Outcome:
    REMINDED = "reminded"
    REMINDER_SKIPPED = "reminder_skipped"
    REMINDER_FAILED = "reminder_failed"
    RELEASED = "released"
    WAITING = "waiting"
    SKIPPED = "skipped"
    ERROR = "error"


def _cycle_payments(registration):
    """Event payments opened in this registration's current cycle."""
    return list(
        PaymentTransaction.objects.filter(
            event_registration_id=registration.pk,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            created_at__gte=registration.registered_at,
        ).order_by("created_at", "pk")
    )


def candidate_registrations(now=None):
    """Pending seats with an online checkout old enough to look at."""
    now = now or timezone.now()
    return (
        EventRegistration.objects.filter(
            status="pending",
            payment_confirmed=False,
            checkout_released_at__isnull=True,
            event__is_cancelled=False,
            event__registration_fee__gt=0,
            event__date_time__gt=now,
            payment_transactions__provider=PaymentTransaction.Provider.MOLLIE,
            payment_transactions__purpose=(
                PaymentTransaction.Purpose.EVENT_REGISTRATION
            ),
            payment_transactions__created_at__gte=F("registered_at"),
            payment_transactions__created_at__lte=now - REMINDER_AFTER,
        )
        .select_related("event", "user")
        .distinct()
        .order_by("pk")
    )


def _close_unopened(tx, now):
    """Close a PENDING Mollie row that never got a payment id (dead request)."""
    with transaction.atomic():
        locked = PaymentTransaction.objects.select_for_update().get(pk=tx.pk)
        if (
            locked.status != PaymentTransaction.Status.PENDING
            or locked.mollie_payment_id
            or now - locked.created_at < PREPARING_GRACE
        ):
            return
        locked.status = PaymentTransaction.Status.CANCELLED
        locked.failure_reason = "Mollie did not open a checkout for this attempt."
        locked.save(update_fields=["status", "failure_reason", "updated_at"])


def _checkouts_settled(registration, now):
    """Bring each open checkout up to date; return the latest open time or None.

    None means "leave this seat alone for now": a checkout is still open or in
    progress at Mollie, Mollie could not be asked, a payment landed, or
    another checkout is being prepared right now.
    """
    from crush_lu.services import mollie
    from crush_lu.views_mollie import sync_mollie_payment

    for tx in _cycle_payments(registration):
        if tx.status != PaymentTransaction.Status.PENDING:
            continue
        if tx.provider != PaymentTransaction.Provider.MOLLIE:
            return None  # a SumUp or credit checkout: not ours to judge
        if not tx.mollie_payment_id:
            _close_unopened(tx, now)
            continue
        try:
            sync_mollie_payment(tx)
        except mollie.MollieError as exc:
            logger.warning(
                "[abandoned_checkouts] could not re-read %s: %s",
                tx.mollie_payment_id,
                exc,
            )
            return None

    payments = _cycle_payments(registration)
    statuses = {tx.status for tx in payments}
    if (
        PaymentTransaction.Status.PENDING in statuses
        or PaymentTransaction.Status.PAID in statuses
    ):
        return None
    if EventCheckoutCreationClaim.objects.filter(
        registration_id=registration.pk
    ).exists():
        return None
    online = [
        tx for tx in payments if tx.provider == PaymentTransaction.Provider.MOLLIE
    ]
    if not online:
        return None
    return max(tx.created_at for tx in online)


def _failed_key(registration):
    # Keyed on the cycle too, so a re-registration starts with a clean slate.
    return (
        f"abandoned-checkout-reminder-failed:{registration.pk}:"
        f"{registration.registered_at.timestamp():.0f}"
    )


def _reminder_failing_since(registration):
    """When a reminder send for this cycle first failed, or None."""
    try:
        value = cache.get(_failed_key(registration))
    except Exception:  # noqa: BLE001 - cache down: behave as never failed
        return None
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _record_reminder_failure(registration, now):
    try:
        cache.add(_failed_key(registration), now.isoformat(), CURSOR_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - cache down: the seat simply keeps waiting
        logger.warning(
            "[abandoned_checkouts] could not record the failed reminder for %s",
            registration.pk,
        )


def release_due(latest, reminder_at, failing_since, now):
    """Whether a seat whose latest checkout opened at ``latest`` may go now.

    One rule, used both to decide and again under the checkout locks: about
    30 minutes after the checkout and 15 after the reminder; or, only when the
    reminder has really been tried and kept failing for an hour, without it.
    """
    age = now - latest
    if age < RELEASE_AFTER:
        return False
    if reminder_at is not None:
        return now - reminder_at >= RELEASE_AFTER_REMINDER
    return failing_since is not None and now - failing_since >= REMINDER_FAILING_FOR


def _send_reminder(registration, now):
    """Send the one "finish your payment" email and stamp the step."""
    from crush_lu import email_helpers

    try:
        delivered = email_helpers.send_event_checkout_reminder(registration)
    except Exception as exc:  # noqa: BLE001 - one failed send must not stop the run
        logger.warning(
            "[abandoned_checkouts] reminder for registration %s failed: %s",
            registration.pk,
            type(exc).__name__,
        )
        _record_reminder_failure(registration, now)
        return Outcome.REMINDER_FAILED
    if not delivered and email_helpers.can_send_email(
        registration.user, "event_checkout_reminder"
    ):
        # 0 for a member who may receive it is a swallowed send failure.
        _record_reminder_failure(registration, now)
        return Outcome.REMINDER_FAILED
    # Stamped for an unsubscribed member too: the step is done, and the
    # release then follows on the normal clock. The filter re-checks the seat,
    # so a payment or re-registration during the send keeps its own state.
    EventRegistration.objects.filter(
        pk=registration.pk,
        status="pending",
        payment_confirmed=False,
        checkout_reminder_at__isnull=True,
        registered_at=registration.registered_at,
    ).update(checkout_reminder_at=now)
    return Outcome.REMINDED if delivered else Outcome.REMINDER_SKIPPED


def release_seat(registration_id, *, registered_at, now=None):
    """Release one abandoned seat under the checkout locks. Returns True if done.

    Takes the payment-first lock order of the checkout itself
    (``_lock_event_checkout_state``), so a checkout being opened at the same
    moment either commits first, and this sees its PENDING row and stands
    down, or waits and then finds the seat no longer payable. Everything the
    decision rests on is read again under the locks: the registration cycle
    that was inspected (``registered_at``), the latest checkout (a newer one
    that already failed restarts the clock) and the reminder.
    """
    from crush_lu.notification_service import announce_waitlist_promotions
    from crush_lu.views_events import _promote_from_waitlist
    from crush_lu.views_payments import _lock_event_checkout_state

    now = now or timezone.now()
    snapshot = EventRegistration.objects.only("event_id").get(pk=registration_id)
    for _attempt in range(3):
        with transaction.atomic():
            payments, event, registration, scope_changed = _lock_event_checkout_state(
                registration_id, snapshot.event_id
            )
            if scope_changed:
                continue
            if (
                registration.status != "pending"
                or registration.payment_confirmed
                or registration.checkout_released_at is not None
                or registration.registered_at != registered_at
                or event.is_cancelled
                or event.uses_curated_registration
                or event.date_time <= now
            ):
                return False
            cycle = [p for p in payments if p.created_at >= registration.registered_at]
            if any(
                p.status
                in (PaymentTransaction.Status.PENDING, PaymentTransaction.Status.PAID)
                for p in cycle
            ) or not any(
                p.provider == PaymentTransaction.Provider.MOLLIE for p in cycle
            ):
                return False
            if EventCheckoutCreationClaim.objects.filter(
                registration_id=registration.pk
            ).exists():
                return False
            latest = max(
                p.created_at
                for p in cycle
                if p.provider == PaymentTransaction.Provider.MOLLIE
            )
            if not release_due(
                latest,
                registration.checkout_reminder_at,
                _reminder_failing_since(registration),
                now,
            ):
                return False

            registration.status = "cancelled"
            registration.cancelled_at = now
            registration.checkout_released_at = now
            # Promoted below in this transaction, and the member already had
            # the reminder: keep promote_waitlist_on_cancellation from promoting
            # a second time and from sending a cancellation email.
            registration._waitlist_promotion_handled = True
            registration.save()

            promoted = None
            if event.accepts_waitlist_promotion:
                promoted = _promote_from_waitlist(
                    event, registration.user, resale_source_registration=None
                )
            if promoted:
                announce_waitlist_promotions([promoted])
            logger.info(
                "[abandoned_checkouts] released registration %s on event %s "
                "(promoted: %s)",
                registration.pk,
                event.pk,
                getattr(promoted, "pk", None),
            )
            return True
    return False


def process_registration(registration, *, now=None):
    """Move one pending seat along: wait, remind, or release."""
    now = now or timezone.now()
    if registration.event.uses_curated_registration:
        return Outcome.SKIPPED
    latest = _checkouts_settled(registration, now)
    if latest is None:
        return Outcome.WAITING
    registration.refresh_from_db(
        fields=["status", "payment_confirmed", "checkout_reminder_at"]
    )
    if registration.status != "pending" or registration.payment_confirmed:
        return Outcome.SKIPPED
    if now - latest < REMINDER_AFTER:
        return Outcome.WAITING
    reminder_at = registration.checkout_reminder_at
    failing_since = _reminder_failing_since(registration)
    if not release_due(latest, reminder_at, failing_since, now):
        if reminder_at is None:
            return _send_reminder(registration, now)
        return Outcome.WAITING
    if release_seat(registration.pk, registered_at=registration.registered_at, now=now):
        return Outcome.RELEASED
    return Outcome.SKIPPED


def run_sweep(*, dry_run=False, now=None, budget_seconds=BUDGET_SECONDS):
    """One bounded pass over every candidate seat. Returns outcome counts."""
    from crush_lu.services.event_payments import mollie_event_payments_active

    counts = {}
    if not mollie_event_payments_active():
        counts["skipped_inactive"] = 1
        return counts
    now = now or timezone.now()
    started = monotonic_clock.monotonic()
    try:
        cursor = int(cache.get(CURSOR_KEY) or 0)
    except Exception:  # noqa: BLE001 - cache down: start from the beginning
        cursor = 0
    candidates = list(candidate_registrations(now))
    # Resume after the last seat looked at, then wrap round to the start.
    ordered = [r for r in candidates if r.pk > cursor] + [
        r for r in candidates if r.pk <= cursor
    ]
    last_seen = None
    for index, registration in enumerate(ordered):
        if (
            index >= MAX_REGISTRATIONS_PER_RUN
            or monotonic_clock.monotonic() - started > budget_seconds
        ):
            counts["deferred"] = counts.get("deferred", 0) + 1
            continue
        if dry_run:
            counts["candidates"] = counts.get("candidates", 0) + 1
            continue
        last_seen = registration.pk
        try:
            outcome = process_registration(registration, now=now)
        except Exception:  # noqa: BLE001 - one seat must not stop the run
            logger.exception(
                "[abandoned_checkouts] registration %s failed", registration.pk
            )
            outcome = Outcome.ERROR
        counts[outcome] = counts.get(outcome, 0) + 1
    if last_seen is not None:
        # A full pass starts from the beginning next time; a cut-off one
        # resumes after the last seat it reached.
        next_cursor = last_seen if counts.get("deferred") else 0
        try:
            cache.set(CURSOR_KEY, next_cursor, CURSOR_TTL_SECONDS)
        except Exception:  # noqa: BLE001 - cache down: next run starts over
            pass
    return counts
