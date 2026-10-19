"""Funnel conversions, captured on the server when they happen (audit A17).

GA4 used to hear about conversions from page renders: ``login`` on every
dashboard view, ``purchase`` for free events keyed by registration ID, nothing
at all for a coach verifying a profile or a member checking in. Each
conversion is now recorded once, at the transition, into a small per-user
outbox, and delivered in that member's browser on their next full page view by
``{% funnel_events %}`` in ``base.html``.

Consent: no event leaves the server. Delivery goes through the same
``ga4_event`` tag as before, so Consent Mode (and iOS suppression) gate it
exactly as they gate every other GA4 event; the cookie banner remains the only
consent mechanism. Parameters never include dating attributes (gender, age,
orientation, answers) or another member's ID.

Contracts -- GA4 name, where it is captured, and the database predicate the
same stage is counted by when reconciling (operational tables are the
truth; GA4 sees only consenting browsers):

====================  ===========================================  =======================================================
GA4 event             captured at                                  database count
====================  ===========================================  =======================================================
login                 ``user_logged_in``                           (none: sessions are not stored per login)
sign_up               signup view / ``user_signed_up`` (social)    ``User.date_joined``
profile_verified      ``claim_profile_verification`` winner,       ``CrushProfile.verification_status='verified'``
                      admin approve actions
event_application     ``event_register``, status ``applied``       ``EventRegistration`` status applied
join_waitlist         ``event_register``, status ``waitlist``      ``EventRegistration`` status waitlist
event_registration    ``event_register``, pending/confirmed, and   ``EventRegistration`` pending/confirmed
                      later admission from applied/waitlist        (once per registration)
                      (incl. a walk-up checked in from waitlist)
purchase              a payment becoming PAID (post_save)          ``PaymentTransaction`` PAID, provider != CREDIT,
                      (``transaction_id`` = transaction_reference)  summed by ``transaction_reference``
refund                a PAID payment becoming REFUNDED             ``PaymentTransaction`` REFUNDED, provider != CREDIT
event_attended        status becomes ``attended`` (post_save)      ``EventRegistration`` status attended
connect_activated     Connect wizard sets ``onboarded_at``          ``CrushConnectMembership.onboarded_at``
mutual_meeting        coffee date confirmed by both / lobby         ``ConnectCoffeeDate`` CONFIRMED_BY_BOTH,
                      mutual confirmation (queued for both)         mutual ``EventMeetingConfirmation`` pairs
====================  ===========================================  =======================================================

Outbox: one cache key per event, numbered by an atomic per-member counter, so
neither a concurrent capture nor a concurrent page view can drop an event; a
page claims each event by deleting its key.

Known limits: the outbox lives in the cache (no migration; the conversions
themselves are already rows in the tables above), so a Redis fault drops it
silently. That undercounts, like a refused consent. Only the latest
``MAX_QUEUED`` events are delivered, and they wait at most ``TTL`` for the
member's next visit. ``sign_up`` for an email signup is delivered after the
email is confirmed, on the first logged-in page.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)

TTL = int(timedelta(days=30).total_seconds())
# EventRegistration status after a successful sign-up -> GA4 event.
REGISTRATION_EVENTS = {
    "applied": "event_application",
    "waitlist": "join_waitlist",
    "pending": "event_registration",
    "confirmed": "event_registration",
}
MAX_QUEUED = 20


def _seq_key(user_id):
    return f"funnel_seq:{user_id}"


def _event_key(user_id, seq):
    return f"funnel_outbox:{user_id}:{seq}"


def _sent_key(dedupe_key):
    return f"funnel_sent:{dedupe_key}"


def _enqueue(user_id, name, dedupe_key, params):
    if dedupe_key and not cache.add(_sent_key(dedupe_key), 1, TTL):
        return
    seq_key = _seq_key(user_id)
    cache.add(seq_key, 0, TTL)
    # INCR is atomic on Redis and LocMem: two captures at once get two keys.
    seq = cache.incr(seq_key)
    if not seq:  # Redis down: IGNORE_EXCEPTIONS returns None instead of raising
        return
    cache.touch(seq_key, TTL)
    # ``eid`` lets the browser refuse a replay of the same script (a page
    # served again from the service-worker cache).
    cache.set(
        _event_key(user_id, seq),
        {"name": name, "params": params, "eid": uuid.uuid4().hex},
        TTL,
    )


def _pending(user_id):
    """``(key, event)`` for the events queued for ``user_id``, oldest first.

    Reads the latest ``MAX_QUEUED`` numbers; delivered ones are gone, and one
    whose number is taken but not yet written is found by a later read.
    """
    last = cache.get(_seq_key(user_id))
    if not last:
        return []
    keys = [
        _event_key(user_id, seq)
        for seq in range(max(1, last - MAX_QUEUED + 1), last + 1)
    ]
    found = cache.get_many(keys)
    return [(key, found[key]) for key in keys if key in found]


def record(user_id, name, dedupe_key=None, **params):
    """Queue GA4 event ``name`` for ``user_id``'s next page view.

    Runs after the surrounding transaction commits, so a rolled-back
    transition queues nothing, and never raises: capture must not fail the
    business action it observes. ``dedupe_key`` makes repeats (a webhook and a
    return URL applying the same payment) queue once.
    """
    if not user_id:
        return

    def _capture():
        try:
            _enqueue(user_id, name, dedupe_key, params)
        except Exception:  # noqa: BLE001
            logger.warning("Funnel event %s not captured", name, exc_info=True)

    transaction.on_commit(_capture)


def pop(user_id):
    """Take every queued event for ``user_id`` (empties the outbox)."""
    if not user_id:
        return []
    try:
        # Each event goes to the request whose delete removed its key: two
        # pages rendering at once never both deliver it, and an event queued
        # after this read keeps its key for the next page.
        return [event for key, event in _pending(user_id) if cache.delete(key)]
    except Exception:  # noqa: BLE001
        logger.warning("Funnel outbox not readable", exc_info=True)
        return []


def record_purchase(payment):
    """``purchase`` for a PAID ``PaymentTransaction`` that is new revenue.

    Crush Credit redemptions are excluded: that money was already counted
    when it was paid (or never was money).
    """
    _record_payment(payment, "purchase")


def record_refund(payment):
    """``refund`` for a PAID payment that was refunded (same reference)."""
    _record_payment(payment, "refund")


def _record_payment(payment, name):
    """Called from the payment ``post_save``: the money is already captured, so
    everything (owner lookup, payload, queue) runs after commit and inside the
    guard. Nothing here can fail or roll back the payment save."""

    def _capture():
        try:
            from crush_lu.models import PaymentTransaction

            if payment.provider == PaymentTransaction.Provider.CREDIT:
                return
            user_id = _payment_member_id(payment)
            if not user_id:
                return
            _enqueue(
                user_id,
                name,
                f"{name}:{payment.transaction_reference}",
                {
                    "transaction_id": payment.transaction_reference,
                    "value": float(payment.amount),
                    "currency": payment.currency or "EUR",
                    "items": [_payment_item(payment)],
                },
            )
        except Exception:  # noqa: BLE001
            logger.warning("Funnel event %s not captured", name, exc_info=True)

    transaction.on_commit(_capture)


def _payment_member_id(payment):
    """The member the payment is for (not a staff recorder), followed to the
    account a merge kept: the duplicate never logs in to drain its outbox."""
    from crush_lu.models.account_merge import AccountMerge

    return AccountMerge.surviving_user_id(payment.owner_id())


def _payment_item(payment):
    """A GA4 item needs an ``item_id`` or ``item_name``."""
    if payment.event_id:
        return {"item_id": f"event-{payment.event_id}", "item_category": "event"}
    if payment.premium_membership_id:
        return {"item_id": "premium", "item_category": "premium"}
    return {"item_id": payment.purpose or "other", "item_category": "other"}


def record_profile_verified(user_id, method):
    """``profile_verified``, once per member whichever path verified them."""
    record(
        user_id,
        "profile_verified",
        dedupe_key=f"profile_verified:{user_id}",
        method=method or "unknown",
    )


def admission_dedupe_key(registration):
    """One ``event_registration`` per registration, however often its status
    moves in and out of a seat (the row is reused on re-registration too),
    matching the database count of registrations."""
    return f"event_admitted:{registration.pk}"


def record_attended(registration):
    """``event_attended``, once per registration (an undo and re-scan of the
    same check-in does not count twice)."""
    record(
        registration.user_id,
        "event_attended",
        dedupe_key=f"event_attended:{registration.pk}",
        event_id=registration.event_id,
    )


def record_mutual_meeting(user_ids, pair_key, source, **params):
    """``mutual_meeting`` for both members of a pair, never naming the other."""
    for user_id in user_ids:
        record(
            user_id,
            "mutual_meeting",
            dedupe_key=f"mutual_meeting:{pair_key}:{user_id}",
            source=source,
            **params,
        )
