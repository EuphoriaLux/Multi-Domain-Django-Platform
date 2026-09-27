"""Single source of truth for "can this registration still be paid for?"

``create_sumup_event_checkout`` (crush_lu/views_payments.py,
``_event_checkout_validation_error``) rejects any checkout whose registration
status is not ``pending``/``confirmed``, whose event is cancelled, or whose
curated group is no longer certified. Every
other surface that offers or narrates a payment — the pay-confirm retry
message, the ticket page's "Pay now" strip, and the event-detail status card's
"payment due" tone — must agree with that allowlist, or a member sees a
payment invitation the endpoint will refuse (UX Wave 3 · WP8 follow-up).

The endpoint still checks bans and ownership at click time. The shared
predicate also checks the current curated-group certification so payment
invitations do not point to a checkout that is known to reject the seat.
"""

# Mirrors the ``registration.status not in (...)`` branch of
# ``_event_checkout_validation_error`` — the only two statuses the checkout
# endpoint will still take money for.
PAYABLE_REGISTRATION_STATUSES = ("pending", "confirmed")


def registration_is_payable(registration, event):
    """Whether ``registration`` on ``event`` is still in a payable state.

    ``registration`` and ``event`` are passed separately (rather than read
    off ``registration.event``) so template tags can reuse an ``event``
    already in context without a second query.
    """
    if registration is None:
        return False
    # An already-paid seat (e.g. a replacement checkout B succeeded while
    # checkout A's return URL is reopened) is never payable again: checkout
    # rejects it, so no copy or CTA may offer a retry.
    # The fee check mirrors the checkout endpoint too: an organiser can set
    # the fee to 0 after a checkout was created.
    if not (
        registration.status in PAYABLE_REGISTRATION_STATUSES
        and not registration.payment_confirmed
        and not event.is_cancelled
        and event.registration_fee > 0
    ):
        return False

    from crush_lu.services.curated_group_workflow import (
        registration_has_certified_payable_group,
    )

    return registration_has_certified_payable_group(registration, event=event)
