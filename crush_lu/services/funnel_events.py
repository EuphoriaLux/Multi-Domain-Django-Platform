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
                      later admission from applied/waitlist
purchase              a payment becoming PAID (post_save)          ``PaymentTransaction`` PAID, provider != CREDIT,
                      (``transaction_id`` = transaction_reference)  summed by ``transaction_reference``
refund                a PAID payment becoming REFUNDED             ``PaymentTransaction`` REFUNDED, provider != CREDIT
event_attended        status becomes ``attended`` (post_save)      ``EventRegistration`` status attended
connect_activated     Connect wizard sets ``onboarded_at``          ``CrushConnectMembership.onboarded_at``
mutual_meeting        coffee date confirmed by both / lobby         ``ConnectCoffeeDate`` CONFIRMED_BY_BOTH,
                      mutual confirmation (queued for both)         mutual ``EventMeetingConfirmation`` pairs
====================  ===========================================  =======================================================

Known limits: the outbox lives in the cache (no migration; the conversions
themselves are already rows in the tables above), so a Redis fault drops it
silently, and two captures for one member in the same instant can lose one
(read-append-write is not atomic). Both undercount, like a refused consent.
Events wait at most ``TTL`` for the member's next visit. ``sign_up`` for an
email signup is delivered after the email is confirmed, on the first logged-in
page.
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


def _queue_key(user_id):
    return f"funnel_outbox:{user_id}"


def _sent_key(dedupe_key):
    return f"funnel_sent:{dedupe_key}"


def _enqueue(user_id, name, dedupe_key, params):
    if dedupe_key and not cache.add(_sent_key(dedupe_key), 1, TTL):
        return
    key = _queue_key(user_id)
    queue = cache.get(key) or []
    # ``eid`` lets the browser refuse a replay of the same script (a page
    # served again from the service-worker cache).
    queue.append({"name": name, "params": params, "eid": uuid.uuid4().hex})
    cache.set(key, queue[-MAX_QUEUED:], TTL)


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
    key = _queue_key(user_id)
    try:
        queue = cache.get(key) or []
        # Only the request whose delete removed the key delivers: two pages
        # rendering at once both read the queue, one of them wins the delete.
        if queue and cache.delete(key):
            return queue
        return []
    except Exception:  # noqa: BLE001
        logger.warning("Funnel outbox not readable", exc_info=True)
        return []


def record_purchase(payment):
    """``purchase`` for a PAID ``PaymentTransaction`` that is new revenue.

    Crush Credit redemptions are excluded: that money was already counted
    when it was paid (or never was money).
    """
    from crush_lu.models import PaymentTransaction

    if payment.provider == PaymentTransaction.Provider.CREDIT:
        return
    record(
        _payment_member_id(payment),
        "purchase",
        dedupe_key=f"purchase:{payment.transaction_reference}",
        transaction_id=payment.transaction_reference,
        value=float(payment.amount),
        currency=payment.currency or "EUR",
        items=[_payment_item(payment)],
    )


def record_refund(payment):
    """``refund`` for a PAID payment that was refunded (same reference)."""
    from crush_lu.models import PaymentTransaction

    if payment.provider == PaymentTransaction.Provider.CREDIT:
        return
    record(
        _payment_member_id(payment),
        "refund",
        dedupe_key=f"refund:{payment.transaction_reference}",
        transaction_id=payment.transaction_reference,
        value=float(payment.amount),
        currency=payment.currency or "EUR",
        items=[_payment_item(payment)],
    )


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
