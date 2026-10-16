"""The "Are you still coming?" check for unpaid seats.

Paid events are settled in cash at the door, so an unpaid ``pending``
registration holds a seat indefinitely. About two days before the event the
``send_event_reminders`` sweep emails those members. The email is only a
reminder and an invitation: the answer is given signed in, on the event page
or the dashboard (in the app or on crush.lu), so no mail scanner or forwarded
email can answer for the member. Releasing the seat is the ordinary
``event_cancel`` page.
"""

from django.utils import timezone


def awaits_attendance_answer(registration):
    """True when this member was asked and has not answered yet.

    Drives the prompt on the event page and the dashboard. Only unpaid
    ``pending`` seats on a future, non-cancelled event are asked.
    """
    if registration is None:
        return False
    event = registration.event
    return bool(
        registration.status == "pending"
        and not registration.payment_confirmed
        and registration.attendance_check_sent_at is not None
        and registration.attendance_confirmed_at is None
        and not event.is_cancelled
        and event.date_time > timezone.now()
    )
