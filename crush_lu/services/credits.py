"""Crush Credit — issuing, reading and spending.

Every credit in the system is minted by :func:`issue_credit` and spent by
:func:`redeem_for_registration`. No view, signal or admin builds a
``CrushCredit`` or a ``CreditRedemption`` row directly, and every read of a
balance goes through :func:`available_credit_cents`. That is the whole point of
this module: the ledger has three invariants and they are only defensible if
there is one door.

⚠️ **Lock order.** ``PaymentTransaction`` locks before ``CrushProfile``
everywhere in this codebase, and ``_apply_paid_checkout`` /
``create_sumup_event_checkout`` both take ``PaymentTransaction`` before
``EventRegistration``. SQLite ignores ``select_for_update`` locally and in CI,
so **no test here can catch a violation** — the ordering is maintained by
reading, not by running. Two rules keep this module out of the cycle:

1. It never takes a lock on ``PaymentTransaction`` or ``CrushProfile``. The
   source payment is read unlocked, for attribution only. ``event_cancel``
   holds ``MeetupEvent`` → ``EventRegistration`` when it calls in here, so
   locking a payment at that point would be exactly the ABBA against a webhook
   for those rows that the checkout path is commented to death about.
2. ``CrushCredit`` is always locked **last**. :func:`redeem_for_registration`
   is the only code anywhere that locks it, and it runs with the
   ``PaymentTransaction`` and ``EventRegistration`` locks for those very rows
   already held by the same transaction. The one lock taken afterwards is
   ``_apply_paid_checkout`` re-reading the ``CREDIT`` transaction it was just
   handed — a row this transaction created and no other can see, plus a
   registration it already holds. Neither can block, so there is nothing for a
   cycle to close on.
"""

import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Min, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from crush_lu.models.credits import CreditRedemption, CrushCredit
from crush_lu.models.payments import PaymentTransaction

logger = logging.getLogger(__name__)

# Inside this many hours of the start, a cancellation earns nothing up front —
# only the 50% resale share, and only if the seat is actually refilled.
DEFAULT_LATE_CANCELLATION_HOURS = 48

# The member's share when a late-cancelled seat is resold. "If we manage to
# fill your seat, we share it back."
DEFAULT_RESALE_SHARE_PERCENT = 50

# What a paid seat is worth in credit when Crush.lu cancels the event. Above
# the €15.50 face value on purpose (§4.3): the premium converts a legal refund
# obligation into goodwill and makes credit the obviously better choice, which
# is how the cash is kept without refusing anyone who asks for it.
DEFAULT_EVENT_CANCELLED_PREMIUM_CENTS = 2000

# Reasons that mean "we have given this seat's money back". Issuing one of
# these against a registration MUST also release ``payment_confirmed`` — see
# :func:`issue_credit`. Goodwill and referral credit are additional money, not
# a return of a payment, so they leave a paid seat alone.
_REASONS_RELEASING_PAYMENT = frozenset(
    {
        CrushCredit.Reason.MEMBER_CANCELLATION,
        CrushCredit.Reason.SEAT_RESOLD,
        CrushCredit.Reason.EVENT_CANCELLED,
    }
)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def spendable_credits(user):
    """Credits this member can still spend, oldest expiry first.

    Oldest-first is the spending order the policy publishes and the only one
    that does not quietly burn a member's soonest-expiring money last.
    Annotated with what is left on each, because a credit is spendable for
    whatever remains on it, not for its face value.
    """
    return (
        CrushCredit.objects.filter(
            user=user,
            status=CrushCredit.Status.ACTIVE,
            expires_at__gt=timezone.now(),
        )
        .annotate(
            _redeemed=Coalesce(Sum("redemptions__amount_cents"), Value(0)),
        )
        .annotate(_remaining=F("amount_cents") - F("_redeemed"))
        .filter(_remaining__gt=0)
        .order_by("expires_at", "id")
    )


def available_credit_cents(user):
    """This member's spendable balance, in cents.

    The single read. Σ of what is left on every ``active`` credit whose
    ``expires_at`` is still in the future — never a stored field, because a
    stored one drifts and the drift is invisible until it costs someone money.

    An expired credit is excluded here whether or not the sweep has got round
    to stamping it ``expired`` (that sweep is PR 2). The clock is the
    authority, not the status flag.
    """
    if user is None or not getattr(user, "pk", None):
        return 0
    return sum(credit._remaining for credit in spendable_credits(user))


def paid_amount_cents(registration):
    """``(cents, payment)`` — what this member actually paid for this seat.

    From the captured payment, not from ``event.registration_fee``: the fee is
    admin-editable, and the policy issues credit "in EUR at the face value
    actually paid". A member who paid €15.50 for a seat whose price has since
    been raised to €20 is owed €15.50.

    Read **unlocked**, deliberately — see this module's lock-order note. Falls
    back to the current fee only when no captured payment is attributable,
    which covers rows confirmed by hand in the shell before this shipped; the
    second element of the tuple is then ``None``.
    """
    tx = (
        PaymentTransaction.objects.filter(
            event_registration_id=registration.pk,
            purpose=PaymentTransaction.Purpose.EVENT_REGISTRATION,
            status=PaymentTransaction.Status.PAID,
        )
        .order_by("-created_at")
        .first()
    )
    if tx is not None:
        return int((tx.amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), tx
    fee = registration.event.registration_fee or Decimal("0.00")
    return int((fee * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), None


def _percent_of(amount_cents, percent):
    """``percent`` of ``amount_cents``, rounded half-up (the member's way)."""
    return int(
        (Decimal(amount_cents) * Decimal(percent) / Decimal(100)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def is_late_cancellation(event, moment=None):
    """True when ``moment`` is inside the no-credit window before the start."""
    moment = moment or timezone.now()
    hours = getattr(
        settings,
        "CRUSH_CREDIT_LATE_CANCELLATION_HOURS",
        DEFAULT_LATE_CANCELLATION_HOURS,
    )
    return (event.date_time - moment).total_seconds() <= hours * 3600


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------


def inherited_expiry(payment):
    """The expiry a credit-funded seat's credit must keep when given back.

    Cancelling a seat that was bought with credit gives the credit back — it
    does not mint new money, and it must not restart the clock. Without this,
    a member sitting on credit about to expire could book any eligible future
    event, cancel it more than 48h out, and walk away with a fresh six months,
    repeating for as long as they liked. Expiry is deferred revenue turning
    into real revenue; an expiry that can be rolled forward indefinitely is no
    expiry at all.

    ⚠️ Keyed on the **payment**, not on the registration, and that distinction
    is the same one that made ``one_resale_credit_per_registration`` crash a
    member-facing cancel. ``EventRegistration`` rows are reused across
    re-registrations, so redemptions from a *previous* credit-funded booking of
    the same event stay attached to the row forever. Reading them by
    registration meant a later seat, paid for by CARD, inherited the expiry of
    credit spent months earlier — frequently a date already in the past, so the
    member's cancellation credit was issued dead on arrival. The redemptions
    that belong to *this* purchase are the ones recorded on the CREDIT
    transaction that paid for it, and nothing else.

    Returns the EARLIEST expiry among the credits actually spent on this seat
    (a seat can be paid from several), so the member is never better off for
    having cycled it. ``None`` for a card payment — that gets an ordinary fresh
    six months, which is right, because real money went in.
    """
    if payment is None or payment.provider != PaymentTransaction.Provider.CREDIT:
        return None
    credit_ids = [
        entry.get("credit_id")
        for entry in (payment.raw_response or {}).get("redemptions", [])
        if isinstance(entry, dict) and entry.get("credit_id")
    ]
    if not credit_ids:
        return None
    return CrushCredit.objects.filter(id__in=credit_ids).aggregate(
        first=Min("expires_at")
    )["first"]


def funding_tranches(payment):
    """Return the original credit tranches used by one CREDIT payment.

    The payment payload is the durable attribution record. Amounts are
    aggregated per original credit defensively because older payloads may
    contain more than one redemption entry for the same ledger row.
    """
    if payment is None or payment.provider != PaymentTransaction.Provider.CREDIT:
        return []

    amounts = {}
    order = []
    for entry in (payment.raw_response or {}).get("redemptions", []):
        if not isinstance(entry, dict):
            continue
        credit_id = entry.get("credit_id")
        amount_cents = int(entry.get("amount_cents") or 0)
        if not credit_id or amount_cents <= 0:
            continue
        if credit_id not in amounts:
            order.append(credit_id)
            amounts[credit_id] = 0
        amounts[credit_id] += amount_cents

    credits = CrushCredit.objects.in_bulk(order)
    return [
        (credits[credit_id], amounts[credit_id])
        for credit_id in order
        if credit_id in credits
    ]


def _allocate_to_tranches(tranches, target_cents):
    """Allocate ``target_cents`` proportionally without losing a cent."""
    target_cents = int(target_cents or 0)
    total = sum(amount for _credit, amount in tranches)
    if target_cents <= 0 or total <= 0:
        return []

    target_cents = min(target_cents, total)
    allocations = []
    allocated = 0
    for index, (credit, amount) in enumerate(tranches):
        if index == len(tranches) - 1:
            share = target_cents - allocated
        else:
            share = (target_cents * amount) // total
            allocated += share
        if share:
            allocations.append((credit, share))
    return allocations


def issue_credit(
    user,
    amount_cents,
    reason,
    *,
    registration=None,
    payment=None,
    cash_refund_eligible=False,
    note="",
    expires_at=None,
    restored_from_credit=None,
):
    """Mint one credit. The only way a ``CrushCredit`` row is ever created.

    ⚠️ **Trap 1 — the ``payment_confirmed`` double-dip. This is the one that
    costs money.** ``_admitted_status`` in ``views_events`` returns
    ``"confirmed"`` for *any* registration with ``payment_confirmed=True``, and
    re-registration reuses the same row rather than making a new one. So a
    member who is handed credit for a seat and still carries
    ``payment_confirmed`` holds the money's worth **and** walks back into the
    same event free — and so does anyone promoted off the waitlist, because
    promotion admits through that same function. Clearing the flag is therefore
    part of issuing, not a follow-up step, and it happens in **this** atomic
    block so no path can commit one without the other.

    Only for the reasons that mean the money went back (see
    ``_REASONS_RELEASING_PAYMENT``). Goodwill credit linked to a registration
    is *additional* money, and stripping ``payment_confirmed`` there would take
    a seat away from someone whose payment we still hold — the mirror of the
    bug above, and the reason this is keyed on the reason rather than on
    "was a registration passed".

    Returns the ``CrushCredit``, or ``None`` when ``amount_cents`` rounds to
    zero (a free seat has no value to give back, and a zero-value credit in a
    member's account is a support ticket waiting to happen).
    """
    amount_cents = int(amount_cents or 0)
    if amount_cents <= 0:
        logger.info(
            "Not issuing a zero-value credit to user %s (reason=%s, registration=%s)",
            getattr(user, "pk", None),
            reason,
            getattr(registration, "pk", None),
        )
        return None

    with transaction.atomic():
        credit = CrushCredit.objects.create(
            user=user,
            amount_cents=amount_cents,
            currency="EUR",
            reason=reason,
            source_registration=registration,
            source_payment=payment,
            cash_refund_eligible=cash_refund_eligible,
            note=note,
            restored_from_credit=restored_from_credit,
            # Falsy leaves the model's own save() to compute a fresh six
            # months. Passed only when giving credit BACK, where restarting the
            # clock would be a gift (see inherited_expiry).
            expires_at=expires_at,
        )

        if registration is not None and reason in _REASONS_RELEASING_PAYMENT:
            if registration.payment_confirmed or registration.payment_date:
                registration.payment_confirmed = False
                registration.payment_date = None
                registration.save(update_fields=["payment_confirmed", "payment_date"])

    logger.info(
        "Issued %s cents of Crush Credit to user %s (reason=%s, registration=%s, "
        "credit=%s, cash_refund_eligible=%s)",
        amount_cents,
        getattr(user, "pk", None),
        reason,
        getattr(registration, "pk", None),
        credit.pk,
        cash_refund_eligible,
    )
    return credit


def _issue_payment_return_credits(
    registration,
    payment,
    amount_cents,
    reason,
    *,
    cash_refund_eligible=False,
    note="",
):
    """Return value from a payment while preserving each funding expiry.

    A card payment creates one fresh credit. A CREDIT payment restores the
    exact tranches it consumed, each on its original clock. This avoids the
    old earliest-expiry collapse, which unfairly shortened later tranches.
    """
    tranches = funding_tranches(payment)
    if not tranches:
        credit = issue_credit(
            registration.user,
            amount_cents,
            reason,
            registration=registration,
            payment=payment,
            cash_refund_eligible=cash_refund_eligible,
            note=note,
        )
        return [credit] if credit is not None else []

    issued = []
    for original_credit, tranche_cents in _allocate_to_tranches(tranches, amount_cents):
        credit = issue_credit(
            registration.user,
            tranche_cents,
            reason,
            registration=registration,
            payment=payment,
            cash_refund_eligible=False,
            note=note,
            expires_at=original_credit.expires_at,
            restored_from_credit=original_credit,
        )
        if credit is not None:
            issued.append(credit)
    return issued


def issue_cancellation_credits(registration, *, moment=None):
    """Plural form of :func:`issue_cancellation_credit` for tranche restores."""
    if not registration.payment_confirmed:
        return []

    amount_cents, payment = paid_amount_cents(registration)
    if amount_cents <= 0:
        return []

    if is_late_cancellation(registration.event, moment):
        logger.info(
            "Registration %s cancelled inside the late window — no credit now; "
            "50%% follows if the replacement pays before the event starts.",
            registration.pk,
        )
        return []

    return _issue_payment_return_credits(
        registration,
        payment,
        amount_cents,
        CrushCredit.Reason.MEMBER_CANCELLATION,
        note=(
            f"Cancelled more than "
            f"{getattr(settings, 'CRUSH_CREDIT_LATE_CANCELLATION_HOURS', DEFAULT_LATE_CANCELLATION_HOURS)}h "
            f"before {registration.event}."
        ),
    )


def issue_cancellation_credit(registration, *, moment=None):
    """Credit a member who has just cancelled their own paid seat.

    Called from ``event_cancel`` with the registration already locked and
    already flipped to ``cancelled``.

    * more than 48h out → 100% of what they paid, straight away;
    * inside 48h → **nothing now**. The seat may still earn them 50% under the
      resale clause, and the state that lets that fire later is simply the row
      itself: ``cancelled`` while ``payment_confirmed`` is still True means we
      hold their money and have given nothing back. The >48h branch destroys
      that state by clearing the flag, which is what stops the resale clause
      paying a second time on a seat already refunded in full.

    Returns the ``CrushCredit`` issued, or ``None``.
    """
    credits = issue_cancellation_credits(registration, moment=moment)
    return credits[0] if credits else None


def maybe_issue_resale_credits(
    cancelled_registration, promoted_registration, *, source_payment=None
):
    """Issue the resale share only after the replacement has actually paid."""
    if promoted_registration is None or cancelled_registration is None:
        return []
    if not promoted_registration.payment_confirmed:
        return []
    if cancelled_registration.status != "cancelled":
        return []
    if not cancelled_registration.payment_confirmed:
        return []

    event = cancelled_registration.event
    now = timezone.now()
    if event.date_time <= now or not is_late_cancellation(event, now):
        return []

    amount_cents, payment = paid_amount_cents(cancelled_registration)
    if source_payment is not None and (
        payment is None or payment.pk != source_payment.pk
    ):
        return []
    share = getattr(
        settings, "CRUSH_CREDIT_RESALE_SHARE_PERCENT", DEFAULT_RESALE_SHARE_PERCENT
    )
    return _issue_payment_return_credits(
        cancelled_registration,
        payment,
        _percent_of(amount_cents, share),
        CrushCredit.Reason.SEAT_RESOLD,
        note=(
            f"Seat released by a late cancellation was paid for by registration "
            f"{promoted_registration.pk} before the event started — "
            f"{share}% shared back."
        ),
    )


def maybe_issue_resale_credit(cancelled_registration, promoted_registration):
    """§4.1, the resale clause: share the seat back when it is actually resold.

    "Costs nothing you didn't collect twice." A member who cancels 30 hours out
    gets nothing *and* watches their seat go to the waitlist — Crush.lu
    collects €31 for one chair, which is defensible on paper and indefensible
    in an inbox. So: 50%, if and only if the seat is refilled before the event
    starts.

    Hung on the replacement **payment succeeding**, not on promotion or the
    cancellation. Promotion reserves the released chair, but no second value
    has been collected until that member pays. Gender-pool rules can also mean
    no candidate is promoted at all; in both cases no credit is due yet.

    Every guard here is load-bearing:

    * ``promoted_registration`` is None → nobody took the seat.
    * the promoted row is not paid → the seat is reserved, not resold.
    * the row is no longer ``cancelled`` → they re-registered in the meantime
      and hold the seat again; there is nothing to give back.
    * ``payment_confirmed`` is False → either they never paid, or credit has
      already been issued against this seat. This is the at-most-once guard,
      and it is the same fact that :func:`issue_credit` clears, so the two
      cannot disagree. The unique constraint on the model backs it up under
      concurrency.
    * not a late cancellation → a >48h cancellation was already paid out in
      full by ``event_cancel``. Re-checked here rather than assumed because
      this is also reachable from the signal path, which fires for admin and
      shell cancellations that never went past the 48h branch at all.
    * the event has started → the clause is explicitly "before the event
      starts".

    Returns the ``CrushCredit`` issued, or ``None``.
    """
    try:
        credits = maybe_issue_resale_credits(
            cancelled_registration, promoted_registration
        )
    except IntegrityError:
        # ``one_resale_credit_per_payment`` already has a row for this capture,
        # so this seat has been shared back once and is done. Treated as
        # "already credited" rather than allowed to propagate: this runs inside
        # ``event_cancel``'s transaction, where an escaping IntegrityError
        # would 500 the member AND roll back the cancellation they asked for —
        # and inside the promotion signal, where the bare handler would swallow
        # the waitlist promotion along with it. ``issue_credit`` opens its own
        # atomic block, so the failed INSERT is confined to that savepoint and
        # the surrounding transaction stays usable.
        logger.info(
            "Resale credit already exists for registration %s payment %s — "
            "not issuing a second one.",
            cancelled_registration.pk,
            None,
        )
        return None
    credit = credits[0] if credits else None
    if credit:
        logger.info(
            "Resale credit %s issued: registration %s refilled by registration %s",
            credit.pk,
            cancelled_registration.pk,
            promoted_registration.pk,
        )
    return credit


def settle_pending_resale_credit(promoted_registration):
    """Settle and clear the durable resale link after replacement payment."""
    source_id = promoted_registration.resale_source_registration_id
    payment_id = promoted_registration.resale_source_payment_id
    if not source_id or not promoted_registration.payment_confirmed:
        return []

    from crush_lu.models.events import EventRegistration

    source = (
        EventRegistration.objects.select_for_update()
        .select_related("event", "user")
        .filter(pk=source_id)
        .first()
    )
    source_payment = (
        PaymentTransaction.objects.filter(pk=payment_id).first() if payment_id else None
    )
    try:
        issued = maybe_issue_resale_credits(
            source, promoted_registration, source_payment=source_payment
        )
    except IntegrityError:
        issued = []
        logger.info(
            "Resale credit already settled for replacement registration %s",
            promoted_registration.pk,
        )

    promoted_registration.resale_source_registration = None
    promoted_registration.resale_source_payment = None
    promoted_registration.save(
        update_fields=["resale_source_registration", "resale_source_payment"]
    )
    return issued


def credit_registration_for_cancelled_event(registration, *, payment=None, note=""):
    """Issue the organiser-cancellation remedy for one locked paid seat."""
    if not registration.payment_confirmed:
        return []
    paid_cents, attributed_payment = paid_amount_cents(registration)
    payment = payment or attributed_payment
    award = max(event_cancelled_premium_cents(), paid_cents)
    refundable = (
        payment is not None
        and payment.provider == PaymentTransaction.Provider.SUMUP
        and payment.status == PaymentTransaction.Status.PAID
    )

    tranches = funding_tranches(payment)
    if not tranches:
        credit = issue_credit(
            registration.user,
            award,
            CrushCredit.Reason.EVENT_CANCELLED,
            registration=registration,
            payment=payment,
            cash_refund_eligible=refundable,
            note=note or f"Crush.lu cancelled {registration.event}.",
        )
        return [credit] if credit else []

    # Restore what was paid on the original clocks. Any premium above it is
    # new goodwill and receives the ordinary six-month lifetime.
    issued = _issue_payment_return_credits(
        registration,
        payment,
        min(award, paid_cents),
        CrushCredit.Reason.EVENT_CANCELLED,
        note=note or f"Crush.lu cancelled {registration.event}.",
    )
    bonus = award - paid_cents
    if bonus > 0:
        credit = issue_credit(
            registration.user,
            bonus,
            CrushCredit.Reason.EVENT_CANCELLED,
            registration=registration,
            payment=payment,
            note=note or f"Crush.lu cancelled {registration.event}: premium bonus.",
        )
        if credit:
            issued.append(credit)
    return issued


def event_cancelled_premium_cents():
    """What a paid seat is worth in credit when Crush.lu cancels the event."""
    return int(
        getattr(
            settings,
            "CRUSH_CREDIT_EVENT_CANCELLED_PREMIUM_CENTS",
            DEFAULT_EVENT_CANCELLED_PREMIUM_CENTS,
        )
    )


def credit_paid_registrations_for_cancelled_event(event, *, note=""):
    """Credit every paid seat at an event Crush.lu is cancelling.

    Issues the §4.3 premium — more than they paid, as an apology — and flags
    each credit ``cash_refund_eligible``. That flag is not decoration: when the
    **organiser** cancels, Luxembourg consumer guidance entitles the member to
    their money back on request and a voucher is not a substitute. The credit
    is what the email leads with; the flag is how a human finds everyone who
    may still ask for cash. Refunding is manual in the SumUp dashboard.

    ⚠️ Each registration is locked ``FOR UPDATE`` before it is read, and the
    whole sweep runs in one transaction. ``issue_credit`` clears
    ``payment_confirmed`` and this only selects rows that still have it, which
    makes it idempotent — but only *sequentially*. Unlocked, two coaches
    double-submitting this action both read ``payment_confirmed=True`` before
    either commits, and both issue a premium credit for the same seat: the same
    double-dip Trap 1 exists to prevent, reached from an unlocked path. Every
    other issuing path in this module already locks the registration first;
    this one was the exception.

    Deliberately **not** backed by a unique constraint on
    ``(source_registration, reason)``. Registration rows are reused across
    re-registrations, so an event cancelled, restored, re-paid and cancelled
    again is legitimately owed a second credit — the same mistake that made
    ``one_resale_credit_per_registration`` crash a member-facing cancel. The
    lock is the guard.

    Returns the list of credits issued.
    """
    from crush_lu.models.events import EventRegistration

    issued = []
    with transaction.atomic():
        # `payment_confirmed=True` is the ONLY predicate needed, because it is
        # by construction the "we hold this money and have given nothing back"
        # marker: issue_credit clears it whenever it returns value.
        #
        # There used to be an `.exclude(status="cancelled")` here and it cost
        # exactly the people least able to argue. A member who cancels INSIDE
        # 48h is owed nothing at that moment and keeps `payment_confirmed`
        # precisely so the resale clause can find them later — so they are
        # `cancelled` AND uncompensated. If Crush.lu then cancels the event,
        # the exclusion dropped them: they paid, the evening never happened,
        # and they got neither credit nor the cash-refund flag. The whole
        # justification for keeping a late canceller's money is that the costs
        # of the evening are already committed, and that reasoning evaporates
        # when there is no evening.
        #
        # A >48h canceller is already excluded correctly, because issuing their
        # 100% credit cleared the flag. Nobody is credited twice.
        registrations = list(
            EventRegistration.objects.select_for_update().filter(
                event=event, payment_confirmed=True
            )
        )
        for registration in registrations:
            issued.extend(
                credit_registration_for_cancelled_event(
                    registration, note=note or f"Crush.lu cancelled {event}."
                )
            )
    return issued


# ---------------------------------------------------------------------------
# Spending
# ---------------------------------------------------------------------------


def redeem_for_registration(user, registration, price_cents):
    """Spend credit on one seat, whole-seat-or-nothing.

    **v1 is deliberately all-or-nothing against the card.** Either credit
    covers the whole seat and SumUp is never called, or the member pays the
    full price by card and their credit is left untouched. Every event is
    €15.50, so a split credit/card payment buys nothing and costs partial
    captures, SumUp minimum-charge thresholds and refund-of-a-part.

    That is *not* the same as all-or-nothing per credit. A single credit is
    spent across several seats — the €20 event-cancellation premium pays a
    €15.50 seat and keeps €4.50 for the next one — so redemption walks the
    member's credits oldest-expiry-first and takes what it needs from each.

    Must be called inside a transaction that already holds the
    ``PaymentTransaction`` and ``EventRegistration`` locks. ``CrushCredit``
    rows are locked here and **nothing is locked after them**; this is the only
    place in the codebase that locks them, which is what keeps the ordering
    acyclic (see the module docstring).

    Returns the list of ``CreditRedemption`` rows written, or ``None`` when the
    balance does not cover ``price_cents`` — in which case nothing at all was
    written and the caller must charge the card.
    """
    if price_cents <= 0:
        return None

    # Lock the candidate rows before deciding. The unlocked balance read the
    # caller used to decide *whether* to offer credit is advisory; this is the
    # read that spends it, and two tabs finishing checkout together must not
    # both see the same cents.
    locked_ids = list(
        CrushCredit.objects.select_for_update()
        .filter(
            user=user,
            status=CrushCredit.Status.ACTIVE,
            expires_at__gt=timezone.now(),
        )
        .order_by("expires_at", "id")
        .values_list("id", flat=True)
    )
    candidates = (
        CrushCredit.objects.filter(id__in=locked_ids)
        .annotate(_redeemed=Coalesce(Sum("redemptions__amount_cents"), Value(0)))
        .annotate(_remaining=F("amount_cents") - F("_redeemed"))
        .filter(_remaining__gt=0)
        .order_by("expires_at", "id")
    )

    plan = []
    outstanding = price_cents
    for credit in candidates:
        if outstanding <= 0:
            break
        take = min(credit._remaining, outstanding)
        plan.append((credit, take))
        outstanding -= take

    if outstanding > 0:
        # Not enough. Write nothing — the caller charges the full price to the
        # card and the member keeps every cent of credit for a seat it covers.
        return None

    redemptions = []
    for credit, take in plan:
        redemptions.append(
            CreditRedemption.objects.create(
                credit=credit,
                event_registration=registration,
                amount_cents=take,
            )
        )
        if credit.remaining_cents <= 0:
            credit.status = CrushCredit.Status.CONSUMED
            credit.save(update_fields=["status"])

    logger.info(
        "Redeemed %s cents of Crush Credit for registration %s across %s credit(s)",
        price_cents,
        registration.pk,
        len(redemptions),
    )
    return redemptions


def credit_transaction_reference(registration):
    """A reference that says what it is at a glance.

    ``CRUSH-EVT-…`` / ``CRUSH-PREM-…`` / ``CRUSH-DON-…`` already tell an
    operator what they are looking at in ``sumup_checkout_status``; a credit
    payment gets its own prefix for the same reason. It also carries no
    ``sumup_checkout_id``, which is what keeps the status command and the
    reconciliation sweep from going hunting for a checkout that never existed.
    """
    return f"CRUSH-CREDIT-{registration.pk}-{uuid.uuid4().hex[:6]}"
