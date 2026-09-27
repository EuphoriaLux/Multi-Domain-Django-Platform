"""Single source of truth for the event_detail registration status card's
tone (background/border colors, icon color and message content all read
from the same value instead of three independently-maintained if/elif
chains — see UX Wave 3 WP8 finding 4-11).
"""

from django import template

register = template.Library()


@register.simple_tag
def registration_tone(user_registration, event):
    """Return one tone key for a member's registration on an event.

    Order matters: a waitlisted registration on a paid event has
    ``payment_confirmed`` False too, so "payment due" must never be
    checked before "waitlist" — that ordering bug is what let a
    waitlisted member see "Payment due — your spot is reserved" (#4-10).
    """
    if not user_registration:
        return "other"

    status = user_registration.status

    if status == "applied":
        return "applied"
    if status == "waitlist":
        return "waitlist"
    if event.registration_fee > 0 and not user_registration.payment_confirmed:
        return "payment_due"
    if status in ("confirmed", "attended"):
        return "confirmed"
    return "other"
