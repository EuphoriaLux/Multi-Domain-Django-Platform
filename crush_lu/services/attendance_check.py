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


def make_attendance_token(registration_id):
    return signing.dumps({"r": int(registration_id)}, salt=ATTENDANCE_CONFIRM_SALT)


def read_attendance_token(token):
    """Return the registration id, or None for a tampered/expired token."""
    try:
        data = signing.loads(
            token, salt=ATTENDANCE_CONFIRM_SALT, max_age=ATTENDANCE_CONFIRM_MAX_AGE
        )
        return int(data["r"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
