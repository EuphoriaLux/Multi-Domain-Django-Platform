"""Single source of truth for "can this registration still be paid for?"

``create_sumup_event_checkout`` (crush_lu/views_payments.py,
``_event_checkout_validation_error``) rejects any checkout whose registration
status is not ``pending``/``confirmed`` or whose event is cancelled. Every
other surface that offers or narrates a payment — the pay-confirm retry
message, the ticket page's "Pay now" strip, and the event-detail status card's
"payment due" tone — must agree with that allowlist, or a member sees a
payment invitation the endpoint will refuse (UX Wave 3 · WP8 follow-up).

Kept deliberately narrow: this only answers the status/event-cancelled
question the callers above need before they render copy. It does not
duplicate the ban check, ownership check, payment_confirmed check or the
curated-group certification check — those stay in
``_event_checkout_validation_error``, which runs at click time regardless.
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
    return (
        registration.status in PAYABLE_REGISTRATION_STATUSES
        and not registration.payment_confirmed
        and not event.is_cancelled
        and event.registration_fee > 0
    )
