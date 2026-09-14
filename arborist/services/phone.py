"""
Phone-number helpers for arborist.lu bookings.
"""

from typing import Optional

LUXEMBOURG_COUNTRY_CODE = "352"


def to_whatsapp_number(phone: Optional[str]) -> Optional[str]:
    """
    Return ``phone`` as the bare international number ``wa.me`` expects
    (country code + subscriber number, digits only), or None when the country
    cannot be known.

    - ``+49 171 …`` / ``0049 171 …``: already international.
    - ``352 621 …`` (9+ digits): international, written without the ``+``.
      Luxembourg's 9-digit numbers are mobiles starting with 6, so a local
      number never looks like this.
    - ``621 123 456``: Luxembourg has no trunk prefix, so a number that does
      not start with 0 is a local Luxembourg number and gets ``352``.
    - ``0171 …``: a foreign national format (trunk 0) whose country cannot be
      inferred, so no link is built rather than one to the wrong person.
    """
    raw = str(phone or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    if raw.startswith("+"):
        return digits
    if digits.startswith("00"):
        return digits[2:] or None
    if digits.startswith(LUXEMBOURG_COUNTRY_CODE) and len(digits) >= 9:
        return digits
    if digits.startswith("0"):
        return None
    return LUXEMBOURG_COUNTRY_CODE + digits
