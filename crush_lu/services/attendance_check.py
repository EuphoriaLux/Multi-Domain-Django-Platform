"""Signed links for the "Are you still coming?" email.

Paid events are settled in cash at the door, so an unpaid ``pending``
registration holds a seat indefinitely. About two days before the event the
``send_event_reminders`` sweep asks those members to confirm. The "Yes, I'm
coming" button carries a signed token (no login needed; the email itself is
the credential, like the unsubscribe link). Releasing the seat goes through the
ordinary login-gated ``event_cancel`` page.
"""

from django.core import signing

ATTENDANCE_CONFIRM_SALT = "crush-attendance-confirm"
ATTENDANCE_CONFIRM_MAX_AGE = 7 * 24 * 60 * 60  # seconds


def _seat_stamp(registration):
    # A cancel-then-re-register reuses the row but resets registered_at, so
    # binding the token to it keeps an old email's link off the new seat.
    return (
        int(registration.registered_at.timestamp()) if registration.registered_at else 0
    )


def make_attendance_token(registration):
    return signing.dumps(
        {"r": registration.pk, "t": _seat_stamp(registration)},
        salt=ATTENDANCE_CONFIRM_SALT,
    )


def read_attendance_token(token):
    """Return ``(registration_id, seat_stamp)``, or None for a bad/expired token."""
    try:
        data = signing.loads(
            token, salt=ATTENDANCE_CONFIRM_SALT, max_age=ATTENDANCE_CONFIRM_MAX_AGE
        )
        return int(data["r"]), int(data.get("t", -1))
    except (signing.BadSignature, KeyError, TypeError, ValueError, AttributeError):
        return None


def token_matches_seat(registration, seat_stamp):
    return _seat_stamp(registration) == seat_stamp
