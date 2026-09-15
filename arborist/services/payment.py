"""
Bank-transfer details for the arborist.lu booking prepayment.

The account lives in settings (``ARBORIST_PREPAYMENT_*``, read from the
environment), never in code or templates. Until a checksum-valid IBAN is
configured, :func:`get_prepayment_bank_details` returns ``None`` and customers
are told the bank details follow with the appointment confirmation, so a
placeholder or mistyped account can never be published.
"""

import logging
import re
from typing import NamedTuple, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_IBAN_RE = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")


class BankDetails(NamedTuple):
    account_holder: str
    iban: str
    bic: str

    @property
    def iban_display(self) -> str:
        """IBAN in the usual groups of four, e.g. ``LU28 0019 4006 4475 0000``."""
        return " ".join(self.iban[i : i + 4] for i in range(0, len(self.iban), 4))


def normalize_iban(value: Optional[str]) -> str:
    """Upper-case the IBAN and drop all whitespace."""
    return re.sub(r"\s+", "", str(value or "")).upper()


def is_valid_iban(value: Optional[str]) -> bool:
    """ISO 13616 check: the rearranged IBAN, read as a number, is 1 mod 97."""
    iban = normalize_iban(value)
    if not _IBAN_RE.fullmatch(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def get_prepayment_bank_details() -> Optional[BankDetails]:
    """The configured prepayment account, or None when none (valid) is set."""
    iban = normalize_iban(settings.ARBORIST_PREPAYMENT_IBAN)
    if not iban:
        return None
    if not is_valid_iban(iban):
        logger.warning(
            "ARBORIST_PREPAYMENT_IBAN fails the IBAN checksum; bank details hidden"
        )
        return None
    return BankDetails(
        account_holder=settings.ARBORIST_PREPAYMENT_ACCOUNT_HOLDER.strip(),
        iban=iban,
        bic=settings.ARBORIST_PREPAYMENT_BIC.strip().upper(),
    )
